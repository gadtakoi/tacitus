"""Tests for scripts/mediamtx-archive-manager.py."""

import importlib.util
import os
import time
from pathlib import Path
from types import ModuleType

import pytest


def _load_archive_manager() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "archive_manager", "scripts/mediamtx-archive-manager.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def archive_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Load archive manager with an isolated recordings directory."""
    module = _load_archive_manager()
    monkeypatch.setenv("RECORDINGS_PATH", str(tmp_path))
    monkeypatch.setenv("MAX_AGE_DAYS", "1")
    monkeypatch.setenv("MAX_SIZE_GB", "1")
    monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("S3_BUCKET", "tacitus-files")
    monkeypatch.setenv("S3_PREFIX", "mediamtx-archives/")
    # Reload constants from env
    monkeypatch.setattr(module, "RECORDINGS_PATH", str(tmp_path))
    monkeypatch.setattr(module, "MAX_AGE_DAYS", 1)
    monkeypatch.setattr(module, "MAX_SIZE_GB", 1)
    monkeypatch.setattr(module, "S3_BUCKET", "tacitus-files")
    monkeypatch.setattr(module, "S3_PREFIX", "mediamtx-archives/")
    return module


class _FakeS3Client:
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str]] = []

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploaded.append((filename, key))


def _make_file(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def test_age_cleanup_deletes_old_files(
    archive_manager: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(archive_manager, "get_s3_client", lambda: fake)

    now = time.time()
    old_file = tmp_path / "cam1" / "old.mp4"
    new_file = tmp_path / "cam1" / "new.mp4"
    _make_file(old_file, 100, now - 2 * 86400)
    _make_file(new_file, 100, now)

    archive_manager.main()

    assert not old_file.exists()
    assert new_file.exists()
    assert any(str(old_file) in upload[0] for upload in fake.uploaded)


def test_size_cleanup_deletes_oldest_when_over_limit(
    archive_manager: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(archive_manager, "get_s3_client", lambda: fake)

    # All three are past MIN_IDLE_SECONDS: this test is about ordering by age,
    # and a file younger than that threshold is invisible to the archiver by
    # design (MediaMTX may still be writing it). That case has its own test,
    # test_size_cleanup_leaves_the_active_segment_alone.
    now = time.time()
    oldest = tmp_path / "cam1" / "oldest.mp4"
    middle = tmp_path / "cam1" / "middle.mp4"
    newest = tmp_path / "cam1" / "newest.mp4"
    _make_file(oldest, 10_000_000, now - 3000)
    _make_file(middle, 10_000_000, now - 2000)
    _make_file(newest, 10_000_000, now - 1000)

    # limit 20 MB -> need to remove at least one file
    monkeypatch.setattr(archive_manager, "MAX_SIZE_GB", 0.02)
    archive_manager.main()

    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()
    assert any(str(oldest) in upload[0] for upload in fake.uploaded)


def test_no_action_when_under_limits(
    archive_manager: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(archive_manager, "get_s3_client", lambda: fake)

    now = time.time()
    small = tmp_path / "cam1" / "small.mp4"
    _make_file(small, 100, now)

    archive_manager.main()

    assert small.exists()
    assert fake.uploaded == []


def test_missing_bucket_exits(archive_manager: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setattr(archive_manager, "S3_BUCKET", "")
    with pytest.raises(SystemExit):
        archive_manager.main()


def test_missing_recordings_path_is_noop(
    archive_manager: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeS3Client()
    monkeypatch.setattr(archive_manager, "get_s3_client", lambda: fake)
    missing = tmp_path / "missing"
    monkeypatch.setattr(archive_manager, "RECORDINGS_PATH", str(missing))
    # should log warning and return without error
    archive_manager.main()
    assert fake.uploaded == []


def test_optimizer_temporary_files_are_never_uploaded(
    archive_manager: ModuleType, tmp_path: Path
) -> None:
    """A transcode in progress must not reach S3.

    recording-optimizer writes ``.<name>.tacitus.tmp`` beside the segment it is
    rewriting. Uploading that file would ship a truncated video and then delete
    the local original, which nothing can undo.
    """
    old = time.time() - 10 * 86400
    _make_file(tmp_path / "cam1" / "segment.mp4", 100, old)
    _make_file(tmp_path / "cam1" / ".segment.mp4.tacitus.tmp", 100, old)

    found = {path.name for path, _, _ in archive_manager.list_files(tmp_path)}

    assert found == {"segment.mp4"}


def test_freshly_written_files_are_never_uploaded(
    archive_manager: ModuleType, tmp_path: Path
) -> None:
    """The segment MediaMTX is still appending to stays on disk.

    ``recordPartDuration`` is one second, so the open segment changes constantly.
    Age-based cleanup could never pick it, but size-based cleanup sorts by mtime
    and, on a full disk, would eventually reach it — upload it half-written and
    delete the local copy.
    """
    _make_file(tmp_path / "cam1" / "active.mp4", 100, time.time())

    assert archive_manager.list_files(tmp_path) == []


def test_size_cleanup_leaves_the_active_segment_alone(
    archive_manager: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even over the size limit, the file being written is not archived."""
    fake = _FakeS3Client()
    monkeypatch.setattr(archive_manager, "get_s3_client", lambda: fake)
    monkeypatch.setattr(archive_manager, "MAX_SIZE_GB", 0)

    _make_file(tmp_path / "cam1" / "active.mp4", 1024, time.time())
    _make_file(tmp_path / "cam1" / "closed.mp4", 1024, time.time() - 10 * 86400)

    archive_manager.main()

    uploaded = {Path(name).name for name, _ in fake.uploaded}
    assert uploaded == {"closed.mp4"}
    assert (tmp_path / "cam1" / "active.mp4").exists()
