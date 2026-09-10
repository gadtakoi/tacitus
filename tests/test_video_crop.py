"""Tests for scripts/video-crop-worker.py.

The arithmetic tests need no ffmpeg: they pin the rules that decide what
offsets get written, which is where a mistake silently destroys footage
(the worker replaces the recorded segment in place).

The tests at the bottom drive real ffmpeg end to end and are skipped when it is
not installed. They are what proves the arithmetic corresponds to how the
conformance window actually behaves.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import ModuleType

import pytest

WORKER_PATH = "scripts/video-crop-worker.py"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe are not installed")


def _load_worker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("video_crop_worker", WORKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker() -> ModuleType:
    """Load the worker module fresh for each test."""
    return _load_worker()


# ---------------------------------------------------------------------------
# Crop arithmetic
# ---------------------------------------------------------------------------


class TestPredictPadding:
    """Padding between the displayed frame and the coded macroblock grid."""

    @pytest.mark.parametrize(
        ("height", "expected"),
        [
            (1080, 8),  # the common case: 1080 is coded as 1088
            (720, 0),  # already macroblock-aligned
            (1088, 0),
            (486, 10),
            (1050, 6),
        ],
    )
    def test_padding_follows_macroblock_alignment(
        self, worker: ModuleType, height: int, expected: int
    ) -> None:
        assert worker.predict_padding(height) == expected


class TestAlignDown:
    """Crop offsets must be whole crop units (2 lines for 4:2:0)."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, 0), (1, 0), (2, 2), (3.9, 2), (162.0, 162), (163, 162)],
    )
    def test_rounds_down_to_even(self, worker: ModuleType, value: float, expected: int) -> None:
        assert worker.align_down(value) == expected


class TestPlanCrop:
    """Offsets are absolute against the coded frame, not additive."""

    def test_padding_is_added_to_the_bottom_offset(self, worker: ModuleType) -> None:
        """A 1080p source hides 8 coded lines behind its conformance window.

        Ignoring them would leave 710 visible lines instead of 702 — the exact
        bug this arithmetic exists to avoid.
        """
        plan = worker.plan_crop(1080, 8, 15, 20)
        assert plan.absolute_top == 162
        assert plan.absolute_bottom == 216 + 8
        assert plan.expected_height == 702

    def test_aligned_source_needs_no_correction(self, worker: ModuleType) -> None:
        plan = worker.plan_crop(720, 0, 15, 20)
        assert plan.absolute_top == 108
        assert plan.absolute_bottom == 144
        assert plan.expected_height == 468

    def test_zero_percent_restores_the_original_window(self, worker: ModuleType) -> None:
        """Switching cropping off is expressible as a plan, not a special case."""
        plan = worker.plan_crop(1080, 8, 0, 0)
        assert plan.absolute_top == 0
        assert plan.absolute_bottom == 8
        assert plan.expected_height == 1080

    def test_offsets_are_always_even(self, worker: ModuleType) -> None:
        plan = worker.plan_crop(1080, 0, 7, 13)
        assert plan.absolute_top % 2 == 0
        assert plan.absolute_bottom % 2 == 0

    def test_crop_leaving_nothing_is_refused(self, worker: ModuleType) -> None:
        assert worker.plan_crop(100, 0, 45, 45) is None

    def test_tiny_frame_is_refused(self, worker: ModuleType) -> None:
        assert worker.plan_crop(20, 0, 45, 45) is None


class TestMarker:
    """The marker records the original geometry, not the current one."""

    def test_roundtrip(self, worker: ModuleType) -> None:
        marker = worker.parse_marker(worker.format_marker(15, 20, 1080, 8))
        assert marker == worker.Marker(
            top_percent=15, bottom_percent=20, original_height=1080, padding=8
        )

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "garbage",
            "v1:15:20:1080",  # too few fields
            "v1:15:20:1080:8:9",  # too many
            "v2:15:20:1080:8",  # unknown version
            "v1:a:20:1080:8",  # not a number
            "v1:15:20:0:8",  # impossible height
            "v1:15:20:1080:-1",  # impossible padding
        ],
    )
    def test_unusable_markers_are_treated_as_absent(self, worker: ModuleType, raw: str) -> None:
        """A junk tag must not stop the segment being processed normally."""
        assert worker.parse_marker(raw) is None


class TestCropSettings:
    """Disabled cropping is expressed as zero percentages, not as a flag."""

    def test_enabled_settings_are_used_as_given(self, worker: ModuleType) -> None:
        settings = worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        assert settings.desired == (15, 20)

    def test_disabled_settings_ask_for_no_crop(self, worker: ModuleType) -> None:
        """Stored percentages survive the switch so the UI can toggle it back."""
        settings = worker.CropSettings(enabled=False, top_percent=15, bottom_percent=20)
        assert settings.desired == (0, 0)


# ---------------------------------------------------------------------------
# Segment selection
# ---------------------------------------------------------------------------


class TestIsReady:
    """Only closed, complete segments may be touched."""

    def test_recently_written_segment_is_skipped(self, worker: ModuleType, tmp_path: Path) -> None:
        """MediaMTX appends to the open segment every second."""
        segment = tmp_path / "now.mp4"
        segment.write_bytes(b"data")
        assert worker.is_ready(segment, time.time()) is False

    def test_idle_segment_is_ready(self, worker: ModuleType, tmp_path: Path) -> None:
        segment = tmp_path / "old.mp4"
        segment.write_bytes(b"data")
        old = time.time() - worker.MIN_IDLE_SECONDS - 60
        os.utime(segment, (old, old))
        assert worker.is_ready(segment, time.time()) is True

    def test_temporary_file_is_never_picked_up(self, worker: ModuleType, tmp_path: Path) -> None:
        """A half-written remux must not be mistaken for a recording."""
        leftover = tmp_path / f".old.mp4{worker.TMP_SUFFIX}"
        leftover.write_bytes(b"data")
        old = time.time() - worker.MIN_IDLE_SECONDS - 60
        os.utime(leftover, (old, old))
        assert worker.is_ready(leftover, time.time()) is False

    def test_missing_file_is_not_ready(self, worker: ModuleType, tmp_path: Path) -> None:
        assert worker.is_ready(tmp_path / "gone.mp4", time.time()) is False


class TestCleanupStaleTemporaries:
    """A worker killed mid-remux leaves a partial file behind."""

    def test_leftovers_are_removed_and_recordings_kept(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        stream = tmp_path / "cam1"
        stream.mkdir()
        leftover = stream / f".seg.mp4{worker.TMP_SUFFIX}"
        leftover.write_bytes(b"partial")
        recording = stream / "seg.mp4"
        recording.write_bytes(b"real")

        worker.cleanup_stale_temporaries(tmp_path)

        assert not leftover.exists()
        assert recording.exists()


class TestSweep:
    """Streams without stored settings are none of the worker's business."""

    def test_stream_without_settings_is_left_alone(
        self, worker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = tmp_path / "unknown-cam"
        stream.mkdir()
        segment = stream / "seg.mp4"
        segment.write_bytes(b"data")
        old = time.time() - worker.MIN_IDLE_SECONDS - 60
        os.utime(segment, (old, old))

        calls: list[Path] = []
        monkeypatch.setattr(worker, "process_segment", lambda path, settings: calls.append(path))

        worker.sweep(tmp_path, {})

        assert calls == []


# ---------------------------------------------------------------------------
# End-to-end against real ffmpeg
# ---------------------------------------------------------------------------


def _make_clip(path: Path, width: int, height: int, seconds: int = 2) -> None:
    """Encode a synthetic test clip with audio."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "25",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _visible_height(path: Path) -> int:
    """Read the height a player would render."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(json.loads(completed.stdout)["streams"][0]["height"])


def _age(path: Path, seconds: int) -> None:
    """Backdate a file so the worker considers it closed."""
    old = time.time() - seconds
    os.utime(path, (old, old))


@requires_ffmpeg
class TestProcessSegmentWithFfmpeg:
    """The crop arithmetic against the real bitstream filter."""

    def test_crop_replaces_the_segment_and_keeps_audio(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        """1080 lines, 15% off the top and 20% off the bottom, leaves 702."""
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1920, 1080)
        original_size = segment.stat().st_size

        outcome = worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        assert outcome.startswith("cropped:"), outcome
        assert _visible_height(segment) == 702
        # A remux, not a re-encode: the coded frames are copied byte for byte.
        assert abs(segment.stat().st_size - original_size) < original_size * 0.05
        streams = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(segment),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "audio" in streams

    def test_macroblock_aligned_source_needs_no_correction(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1280, 720)

        worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        assert _visible_height(segment) == 468

    def test_second_run_is_a_no_op(self, worker: ModuleType, tmp_path: Path) -> None:
        """Re-cropping an already-cropped segment would shrink it again."""
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1920, 1080)
        settings = worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)

        worker.process_segment(segment, settings)
        _age(segment, 0)
        size_after_first = segment.stat().st_size

        outcome = worker.process_segment(segment, settings)

        assert outcome == "skipped:already-current"
        assert _visible_height(segment) == 702
        assert segment.stat().st_size == size_after_first

    def test_changed_settings_recompute_from_the_original_geometry(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        """The original file is gone, so the marker has to carry its geometry."""
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1920, 1080)

        worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )
        worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=10, bottom_percent=10)
        )

        # 1080 * 0.80, computed from the original 1080 rather than from 702.
        assert _visible_height(segment) == 864

    def test_disabling_the_crop_restores_the_full_frame(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1920, 1080)

        worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )
        worker.process_segment(
            segment, worker.CropSettings(enabled=False, top_percent=15, bottom_percent=20)
        )

        assert _visible_height(segment) == 1080

    def test_modification_time_is_preserved(self, worker: ModuleType, tmp_path: Path) -> None:
        """Retention is driven by age; processing must not rejuvenate a segment."""
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1280, 720)
        _age(segment, 3 * 86400)
        before = segment.stat().st_mtime

        worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        assert abs(segment.stat().st_mtime - before) < 2

    def test_foreign_marker_survives_the_crop(self, worker: ModuleType, tmp_path: Path) -> None:
        """recording-optimizer marks segments it has already thinned.

        Dropping that tag would make it re-encode the same segment forever.
        """
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1280, 720)
        marked = tmp_path / "marked.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(segment),
                "-map",
                "0",
                "-c",
                "copy",
                "-metadata",
                "tacitus_decimated=1",
                "-movflags",
                "+frag_keyframe+empty_moov+default_base_moof+use_metadata_tags",
                "-f",
                "mp4",
                str(marked),
            ],
            check=True,
            capture_output=True,
        )

        worker.process_segment(
            marked, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        tags = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags",
                "-of",
                "json",
                str(marked),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "tacitus_decimated" in tags
        assert "tacitus_crop" in tags

    def test_unconfigured_crop_leaves_a_fresh_segment_untouched(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1280, 720)
        before = segment.read_bytes()

        outcome = worker.process_segment(
            segment, worker.CropSettings(enabled=False, top_percent=0, bottom_percent=0)
        )

        assert outcome == "skipped:no-crop-configured"
        assert segment.read_bytes() == before

    def test_broken_input_leaves_the_original_in_place(
        self, worker: ModuleType, tmp_path: Path
    ) -> None:
        """Nothing is replaced unless the result verified correctly."""
        segment = tmp_path / "seg.mp4"
        segment.write_bytes(b"not a video at all")

        outcome = worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        assert outcome == "skipped:unreadable"
        assert segment.read_bytes() == b"not a video at all"
        assert not list(tmp_path.glob(f"*{worker.TMP_SUFFIX}"))

    def test_failed_ffmpeg_removes_the_temporary_file(
        self, worker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        segment = tmp_path / "seg.mp4"
        _make_clip(segment, 1280, 720)
        before = segment.read_bytes()
        monkeypatch.setattr(worker, "apply_crop", lambda *args, **kwargs: False)

        outcome = worker.process_segment(
            segment, worker.CropSettings(enabled=True, top_percent=15, bottom_percent=20)
        )

        assert outcome == "failed:ffmpeg"
        assert segment.read_bytes() == before
        assert not list(tmp_path.glob(f"*{worker.TMP_SUFFIX}"))
