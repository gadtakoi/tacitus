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

    now = time.time()
    oldest = tmp_path / "cam1" / "oldest.mp4"
    middle = tmp_path / "cam1" / "middle.mp4"
    newest = tmp_path / "cam1" / "newest.mp4"
    _make_file(oldest, 10_000_000, now - 2000)
    _make_file(middle, 10_000_000, now - 1000)
    _make_file(newest, 10_000_000, now)

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
