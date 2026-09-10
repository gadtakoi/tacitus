#!/usr/bin/env python3
"""Video crop worker.

Removes a configurable band from the top and the bottom of every recorded
segment, per stream, and puts the result back in place of the original.

The crop is lossless and costs no re-encoding. H.264 and H.265 both carry a
"conformance window" in the SPS -- a rectangle the decoder is required to cut
away before displaying the picture -- and ffmpeg can rewrite that window with a
bitstream filter while copying the coded frames untouched. Processing a segment
is therefore a remux: seconds of I/O, no generation loss, and the file size is
unchanged but for the metadata.

The flip side, and the reason this is not a redaction tool: the cropped-away
pixels are still in the file. Anyone able to reset the conformance window gets
them back. This removes uninteresting parts of the frame (sky, ground, an OSD
timestamp band); it does not hide anything.

Two details of the format are load-bearing:

* Crop offsets are absolute against the *coded* frame, not additive against
  what is displayed. A 1080-line camera is coded as 1088 lines with 8 already
  cropped away, so removing 20% from the bottom means an offset of 216 + 8.
  ffprobe does not report the coded height (MediaMTX segments do not carry it
  at all), so the padding is predicted from macroblock alignment and then
  verified against the result, with one self-correcting retry.
* Re-running the filter with the same absolute offsets is a no-op, but
  recomputing percentages from an already-cropped file is not. Every processed
  segment therefore carries a marker tag recording the original geometry, which
  is what later runs compute against -- including when the settings change or
  cropping is switched off again.
"""

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("video-crop-worker")

RECORDINGS_PATH = os.getenv("RECORDINGS_PATH", "/recordings")
INTERVAL_SECONDS = int(os.getenv("CROP_INTERVAL_SECONDS", "300"))
# A segment written to more recently than this is still open: MediaMTX appends
# to it every ``recordPartDuration`` (1 s). Shares its default with
# ``TACITUS_OPT_MIN_IDLE_SECONDS`` and the archive manager so every service in
# the pipeline agrees on what "closed" means.
MIN_IDLE_SECONDS = int(os.getenv("MIN_IDLE_SECONDS", "300"))
FFMPEG_TIMEOUT_SECONDS = int(os.getenv("CROP_FFMPEG_TIMEOUT_SECONDS", "3600"))
FFPROBE_TIMEOUT_SECONDS = int(os.getenv("CROP_FFPROBE_TIMEOUT_SECONDS", "60"))
NICE = int(os.getenv("CROP_NICE", "10"))
DATABASE_URL = os.getenv("TACITUS_DATABASE_URL", "")

# Custom mp4 tag marking a processed segment. A marker file beside the
# recording would not do: the archive manager uploads everything it finds when
# walking the directory, so markers would end up in S3 as litter.
MARKER_TAG = "tacitus_crop"
MARKER_VERSION = "v1"

# Dot-prefixed and ``.tmp``-suffixed so the archive manager skips it: a
# half-written remux must never reach S3.
TMP_SUFFIX = ".tacitus-crop.tmp"

# H.264/H.265 code whole macroblocks, so a frame height that is not a multiple
# of 16 is padded and the remainder cropped away by the conformance window.
MACROBLOCK_SIZE = 16
# Crop offsets are counted in luma samples and must be a multiple of the SPS
# crop unit, which is 2 for 4:2:0 progressive video.
CROP_UNIT = 2
# A crop leaving less than this is treated as a misconfiguration, not a wish.
MIN_OUTPUT_HEIGHT = 16

BSF_BY_CODEC = {"h264": "h264_metadata", "hevc": "hevc_metadata"}


@dataclass(frozen=True)
class CropSettings:
    """Per-stream crop configuration as stored in the ``streams`` table."""

    enabled: bool
    top_percent: int
    bottom_percent: int

    @property
    def desired(self) -> tuple[int, int]:
        """Percentages actually to be applied.

        Returns:
            ``(top, bottom)``, both zero while cropping is switched off.
        """
        if not self.enabled:
            return (0, 0)
        return (self.top_percent, self.bottom_percent)


@dataclass(frozen=True)
class Marker:
    """Record of what was already done to a segment.

    ``original_height`` and ``padding`` describe the segment as recorded, before
    any crop by this worker. Keeping them is what makes a later change of
    settings -- or switching cropping off -- computable without the original
    file.
    """

    top_percent: int
    bottom_percent: int
    original_height: int
    padding: int


@dataclass(frozen=True)
class Probe:
    """What ffprobe reports about a segment."""

    codec: str
    height: int
    field_order: str
    marker: Marker | None


@dataclass(frozen=True)
class CropPlan:
    """Absolute conformance-window offsets to write, and the expected result."""

    absolute_top: int
    absolute_bottom: int
    expected_height: int


def align_down(value: float) -> int:
    """Round a line count down to a whole crop unit.

    Args:
        value: Number of lines to remove, possibly fractional.

    Returns:
        The largest multiple of ``CROP_UNIT`` not exceeding ``value``.
    """
    return int(value) // CROP_UNIT * CROP_UNIT


def predict_padding(display_height: int) -> int:
    """Predict how many coded lines sit outside the displayed frame.

    Encoders pad the picture up to a whole number of macroblocks and cut the
    remainder off with the conformance window, so the padding follows from the
    displayed height alone. This is a prediction: it is always verified against
    the actual result before a segment is replaced.

    Args:
        display_height: Frame height as displayed.

    Returns:
        Number of padding lines, zero when the height is already aligned.
    """
    aligned = math.ceil(display_height / MACROBLOCK_SIZE) * MACROBLOCK_SIZE
    return aligned - display_height


def format_marker(top_percent: int, bottom_percent: int, original_height: int, padding: int) -> str:
    """Render the marker tag value.

    Args:
        top_percent: Percentage removed from the top.
        bottom_percent: Percentage removed from the bottom.
        original_height: Displayed height of the segment as recorded.
        padding: Coded lines outside the displayed frame as recorded.

    Returns:
        The tag value to store in the container.
    """
    return f"{MARKER_VERSION}:{top_percent}:{bottom_percent}:{original_height}:{padding}"


def parse_marker(raw: str | None) -> Marker | None:
    """Read a marker tag written by a previous run.

    Anything unparseable is treated as absent rather than as an error: the tag
    is a cache of this worker's own bookkeeping, and a segment carrying junk in
    it is still a perfectly good recording.

    Args:
        raw: Tag value, or ``None`` when the segment carries no marker.

    Returns:
        The parsed marker, or ``None``.
    """
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 5 or parts[0] != MARKER_VERSION:
        return None
    try:
        top, bottom, height, padding = (int(part) for part in parts[1:])
    except ValueError:
        return None
    if height <= 0 or padding < 0:
        return None
    return Marker(
        top_percent=top,
        bottom_percent=bottom,
        original_height=height,
        padding=padding,
    )


def plan_crop(
    original_height: int,
    padding: int,
    top_percent: int,
    bottom_percent: int,
) -> CropPlan | None:
    """Work out the conformance-window offsets for a requested crop.

    Offsets are absolute against the coded frame, which is why the padding is
    added to the bottom one, and why they are computed from the original
    geometry rather than from whatever the segment currently displays.

    Args:
        original_height: Displayed height of the segment as recorded.
        padding: Coded lines outside that displayed frame.
        top_percent: Percentage of the original height to remove from the top.
        bottom_percent: Percentage of the original height to remove from the
            bottom.

    Returns:
        The plan, or ``None`` if it would leave too little of the frame.
    """
    cut_top = align_down(original_height * top_percent / 100)
    cut_bottom = align_down(original_height * bottom_percent / 100)
    expected_height = original_height - cut_top - cut_bottom
    if expected_height < MIN_OUTPUT_HEIGHT:
        return None
    return CropPlan(
        absolute_top=cut_top,
        absolute_bottom=cut_bottom + padding,
        expected_height=expected_height,
    )


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a child process with a hard timeout.

    Args:
        command: Argument vector.
        timeout: Seconds before the child is killed.

    Returns:
        The completed process, including a non-zero return code.

    Raises:
        subprocess.TimeoutExpired: If the child outlives ``timeout``.
    """
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def probe(path: Path) -> Probe | None:
    """Read codec, geometry and marker from a segment.

    Args:
        path: Segment to inspect.

    Returns:
        The probe result, or ``None`` when the file cannot be read as video.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        f"stream=codec_name,height,field_order:format_tags={MARKER_TAG}",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = _run(command, FFPROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out", extra={"segment": path.name})
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        height = int(stream["height"])
    except ValueError, KeyError, IndexError:
        return None
    if height <= 0:
        return None
    tags = payload.get("format", {}).get("tags", {})
    return Probe(
        codec=str(stream.get("codec_name", "")),
        height=height,
        field_order=str(stream.get("field_order", "progressive")),
        marker=parse_marker(tags.get(MARKER_TAG)),
    )


def apply_crop(source: Path, target: Path, bsf: str, plan: CropPlan, marker: str) -> bool:
    """Write a cropped copy of a segment.

    ``use_metadata_tags`` is not optional: without it the mp4 muxer drops every
    non-standard tag, including markers left by other services in the pipeline
    as well as this worker's own.

    Args:
        source: Segment to read.
        target: Path to write.
        bsf: Bitstream filter name for the codec at hand.
        plan: Offsets to write.
        marker: Marker tag value to record.

    Returns:
        ``True`` if ffmpeg succeeded.
    """
    command = [
        "nice",
        "-n",
        str(NICE),
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0",
        "-c",
        "copy",
        "-bsf:v",
        f"{bsf}=crop_top={plan.absolute_top}:crop_bottom={plan.absolute_bottom}",
        "-metadata",
        f"{MARKER_TAG}={marker}",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof+use_metadata_tags",
        "-f",
        "mp4",
        str(target),
    ]
    try:
        completed = _run(command, FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out", extra={"segment": source.name})
        return False
    if completed.returncode != 0:
        logger.warning(
            "ffmpeg failed",
            extra={"segment": source.name, "returncode": completed.returncode},
        )
        return False
    return True


def is_ready(path: Path, now: float) -> bool:
    """Check that a file is a closed segment nobody is writing.

    Args:
        path: Candidate file.
        now: Current wall-clock time.

    Returns:
        ``True`` if the file may be processed.
    """
    if path.name.startswith("."):
        return False
    try:
        return now - path.stat().st_mtime >= MIN_IDLE_SECONDS
    except OSError:
        return False


def process_segment(path: Path, settings: CropSettings) -> str:
    """Bring one segment in line with its stream's crop settings.

    The original is replaced only after the result has been verified. On any
    doubt the temporary file is removed and the recording is left untouched:
    the cropped-away band cannot be recovered from a replaced segment.

    Args:
        path: Segment to process.
        settings: Crop settings of the stream that produced it.

    Returns:
        A short outcome label for the log.
    """
    desired_top, desired_bottom = settings.desired
    probed = probe(path)
    if probed is None:
        return "skipped:unreadable"
    if probed.codec not in BSF_BY_CODEC:
        return f"skipped:codec={probed.codec or 'unknown'}"
    if probed.field_order not in ("progressive", "unknown"):
        # Interlaced content uses a vertical crop unit of 4, and none of the
        # cameras in scope produce it. Skipping is honest; guessing is not.
        return f"skipped:field_order={probed.field_order}"

    if probed.marker is not None:
        if (probed.marker.top_percent, probed.marker.bottom_percent) == (
            desired_top,
            desired_bottom,
        ):
            return "skipped:already-current"
        original_height = probed.marker.original_height
        padding = probed.marker.padding
        padding_is_known = True
    else:
        if (desired_top, desired_bottom) == (0, 0):
            # Nothing to do and nothing to record: rewriting the segment just to
            # stamp "no crop applied" on it would be churn.
            return "skipped:no-crop-configured"
        original_height = probed.height
        padding = predict_padding(probed.height)
        padding_is_known = False

    plan = plan_crop(original_height, padding, desired_top, desired_bottom)
    if plan is None:
        return "skipped:crop-too-large"

    tmp = path.with_name(f".{path.name}{TMP_SUFFIX}")
    marker = format_marker(desired_top, desired_bottom, original_height, padding)
    try:
        if not apply_crop(path, tmp, BSF_BY_CODEC[probed.codec], plan, marker):
            return "failed:ffmpeg"

        result = probe(tmp)
        if result is None:
            return "failed:unreadable-result"

        if result.height != plan.expected_height and not padding_is_known:
            # The padding guess was wrong. The difference is exactly how far off
            # it was, so the corrected value is known rather than searched for,
            # and one retry settles it.
            padding += result.height - plan.expected_height
            if padding < 0:
                return "failed:unexpected-geometry"
            corrected = plan_crop(original_height, padding, desired_top, desired_bottom)
            if corrected is None:
                return "skipped:crop-too-large"
            plan = corrected
            marker = format_marker(desired_top, desired_bottom, original_height, padding)
            if not apply_crop(path, tmp, BSF_BY_CODEC[probed.codec], plan, marker):
                return "failed:ffmpeg"
            result = probe(tmp)
            if result is None:
                return "failed:unreadable-result"

        if result.height != plan.expected_height:
            return f"failed:height={result.height}:expected={plan.expected_height}"

        # Age is what the archive manager sorts by. Without carrying the
        # original mtime across, processing would make every recording look
        # brand new and nothing would ever be archived.
        source_stat = path.stat()
        os.utime(tmp, (source_stat.st_atime, source_stat.st_mtime))

        # The archive manager may have uploaded and deleted the segment while
        # ffmpeg was running. Putting the result back now would resurrect a file
        # that already lives in S3 and have it uploaded a second time.
        if not path.exists():
            return "skipped:archived-meanwhile"

        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

    return f"cropped:{probed.height}->{plan.expected_height}"


async def load_settings() -> dict[str, CropSettings]:
    """Read crop settings for every configured stream.

    Read straight from PostgreSQL rather than through the HTTP API: this is an
    internal component of the recording pipeline, and giving it a JWT so it
    could ask the application for its own configuration would buy nothing.

    Returns:
        Settings keyed by stream name.
    """
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=1, max_overflow=0)
    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(
                    Stream.name,
                    Stream.crop_enabled,
                    Stream.crop_top_percent,
                    Stream.crop_bottom_percent,
                )
            )
            return {
                row.name: CropSettings(
                    enabled=row.crop_enabled,
                    top_percent=row.crop_top_percent,
                    bottom_percent=row.crop_bottom_percent,
                )
                for row in result
            }
    finally:
        await engine.dispose()


def sweep(base: Path, settings_by_stream: dict[str, CropSettings]) -> None:
    """Walk the recordings directory once.

    Args:
        base: Recordings root; each direct child is a stream directory.
        settings_by_stream: Crop settings keyed by stream name.
    """
    now = time.time()
    processed = 0
    for stream_dir in sorted(base.iterdir()):
        if not stream_dir.is_dir() or stream_dir.name.startswith("."):
            continue
        settings = settings_by_stream.get(stream_dir.name)
        if settings is None:
            continue
        for segment in sorted(stream_dir.glob("*.mp4")):
            if not is_ready(segment, now):
                continue
            started = time.monotonic()
            outcome = process_segment(segment, settings)
            if outcome.startswith("skipped:"):
                continue
            processed += 1
            logger.info(
                "Segment processed",
                extra={
                    "stream": stream_dir.name,
                    "segment": segment.name,
                    "outcome": outcome,
                    "seconds": round(time.monotonic() - started, 1),
                },
            )
    if processed:
        logger.info("Sweep finished", extra={"segments": processed})


def cleanup_stale_temporaries(base: Path) -> None:
    """Remove leftovers from a run that was killed mid-remux.

    Args:
        base: Recordings root.
    """
    for leftover in base.rglob(f"*{TMP_SUFFIX}"):
        leftover.unlink(missing_ok=True)


def main() -> None:
    """Run sweeps until the process is stopped."""
    if not DATABASE_URL:
        logger.error("TACITUS_DATABASE_URL is not set; aborting")
        sys.exit(1)

    base = Path(RECORDINGS_PATH)
    if not base.is_dir():
        logger.error("Recordings path does not exist: %s", base)
        sys.exit(1)

    logger.info(
        "Video crop worker started",
        extra={"recordings": str(base), "interval": INTERVAL_SECONDS},
    )
    cleanup_stale_temporaries(base)

    while True:
        try:
            settings_by_stream = asyncio.run(load_settings())
        except Exception:
            # A database blip must not kill the loop: the next sweep retries,
            # and until then nothing is touched.
            logger.exception("Could not load crop settings; skipping this sweep")
        else:
            sweep(base, settings_by_stream)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
