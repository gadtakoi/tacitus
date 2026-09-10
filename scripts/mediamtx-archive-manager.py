#!/usr/bin/env python3
"""MediaMTX archive manager.

Enforces max-age and max-size limits on local recordings by uploading
older files to S3/MinIO and deleting the local copies.
"""

import contextlib
import logging
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

RECORDINGS_PATH = os.getenv("RECORDINGS_PATH", "/recordings")
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "7"))
MAX_SIZE_GB = int(os.getenv("MAX_SIZE_GB", "50"))

# A file written to more recently than this is still being appended to and must
# not be uploaded: MediaMTX writes the open segment every ``recordPartDuration``
# (1 s), and recording-optimizer replaces a segment in place once it closes.
# Uploading either mid-write ships a truncated file to S3 and then deletes the
# local copy, which is unrecoverable. Same default as
# ``TACITUS_OPT_MIN_IDLE_SECONDS`` so the two services agree on what "closed"
# means.
MIN_IDLE_SECONDS = int(os.getenv("MIN_IDLE_SECONDS", "300"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "mediamtx-archives/")


def get_s3_client():
    session = boto3.session.Session()
    kwargs = {
        "aws_access_key_id": S3_ACCESS_KEY,
        "aws_secret_access_key": S3_SECRET_KEY,
    }
    if S3_ENDPOINT:
        kwargs["endpoint_url"] = S3_ENDPOINT
    return session.client("s3", **kwargs)


def list_files(base: Path):
    """Collect the recordings that are safe to archive.

    Two kinds of file are deliberately invisible here:

    * anything whose name starts with a dot -- recording-optimizer writes its
      transcode to ``.<name>.tacitus.tmp`` beside the segment it is rewriting,
      and a half-finished transcode must never reach S3;
    * anything touched within ``MIN_IDLE_SECONDS`` -- see that constant.

    Returns:
        List of ``(path, mtime, size)`` tuples for closed, complete files.
    """
    files = []
    cutoff = time.time() - MIN_IDLE_SECONDS
    for item in base.rglob("*"):
        if not item.is_file() or item.name.startswith("."):
            continue
        stat = item.stat()
        if stat.st_mtime > cutoff:
            continue
        files.append((item, stat.st_mtime, stat.st_size))
    return files


def upload_and_delete(s3, local_path: Path, key: str) -> bool:
    try:
        s3.upload_file(str(local_path), S3_BUCKET, key)
        local_path.unlink()
        logging.info("Uploaded and deleted %s -> s3://%s/%s", local_path, S3_BUCKET, key)
        # prune empty parent directories
        with contextlib.suppress(OSError):
            local_path.parent.rmdir()
        return True
    except ClientError as exc:
        logging.error("Failed to upload %s: %s", local_path, exc)
        return False


def main():
    if not S3_BUCKET:
        logging.error("S3_BUCKET is not set; aborting")
        sys.exit(1)

    s3 = get_s3_client()
    base = Path(RECORDINGS_PATH)
    if not base.exists():
        logging.warning("Recordings path does not exist: %s", base)
        return

    files = list_files(base)
    now = time.time()
    max_age_seconds = MAX_AGE_DAYS * 86400
    max_size_bytes = MAX_SIZE_GB * 1024 * 1024 * 1024

    # 1. Age-based cleanup
    for fpath, mtime, _size in files:
        if now - mtime > max_age_seconds:
            rel = fpath.relative_to(base)
            key = S3_PREFIX + str(rel).replace("\\", "/")
            upload_and_delete(s3, fpath, key)

    # 2. Size-based cleanup (oldest first)
    files = list_files(base)
    total_size = sum(s for _, _, s in files)
    if total_size > max_size_bytes:
        files_sorted = sorted(files, key=lambda x: x[1])
        for fpath, _mtime, size in files_sorted:
            if total_size <= max_size_bytes:
                break
            rel = fpath.relative_to(base)
            key = S3_PREFIX + str(rel).replace("\\", "/")
            if upload_and_delete(s3, fpath, key):
                total_size -= size

    logging.info(
        "Archive manager finished. Local size: %.2f GB",
        total_size / (1024**3),
    )


if __name__ == "__main__":
    main()
