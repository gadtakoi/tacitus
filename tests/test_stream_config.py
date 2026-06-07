"""Tests for stream configuration schemas and service."""

import pytest
from pydantic import ValidationError

from app.schemas.streams import (
    StreamConfig,
    StreamCreateRequest,
    StreamCredentials,
    StreamUpdateRequest,
)
from app.services import stream_config_service as svc

# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestStreamConfigValidation:
    """Test StreamConfig schema validation."""

    def test_valid_stream_config(self) -> None:
        config = StreamConfig(name="cam1", source="publisher")
        assert config.name == "cam1"
        assert config.source == "publisher"
        assert config.record_enabled is True
        assert config.created_at is not None
        assert config.updated_at is not None

    def test_valid_stream_config_with_rtsp_source(self) -> None:
        config = StreamConfig(name="cam_front", source="rtsp://192.168.1.10/stream")
        assert config.source == "rtsp://192.168.1.10/stream"

    def test_valid_stream_config_with_rtsps_source(self) -> None:
        config = StreamConfig(name="cam_secure", source="rtsps://192.168.1.10/stream")
        assert config.source == "rtsps://192.168.1.10/stream"

    def test_name_with_underscore(self) -> None:
        config = StreamConfig(name="cam_front_door", source="publisher")
        assert config.name == "cam_front_door"

    def test_name_with_hyphen(self) -> None:
        config = StreamConfig(name="cam-front", source="publisher")
        assert config.name == "cam-front"

    def test_name_empty_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="", source="publisher")
        assert "String should have at least 1 character" in str(exc_info.value)

    def test_name_starting_with_hyphen_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="-cam", source="publisher")
        assert "must not start or end with a hyphen" in str(exc_info.value)

    def test_name_ending_with_hyphen_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="cam-", source="publisher")
        assert "must not start or end with a hyphen" in str(exc_info.value)

    def test_name_with_invalid_chars_rejected(self) -> None:
        invalid_names = ["cam space", "cam@home", "cam#1", "cam.front", "cam/front"]
        for name in invalid_names:
            with pytest.raises(ValidationError) as exc_info:
                StreamConfig(name=name, source="publisher")
            assert "ASCII letters, digits, underscore and hyphen" in str(exc_info.value)

    def test_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="a" * 65, source="publisher")
        assert "String should have at most 64 characters" in str(exc_info.value)

    def test_source_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="cam1", source="http://invalid")
        assert "Source must be 'publisher' or a valid RTSP/RTSPS/RTMP URL" in str(exc_info.value)

    def test_source_empty_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            StreamConfig(name="cam1", source="")
        assert "Source cannot be empty" in str(exc_info.value)

    def test_credentials_defaults(self) -> None:
        config = StreamConfig(name="cam1", source="publisher")
        assert config.credentials.publish_user is None
        assert config.credentials.publish_pass is None
        assert config.credentials.read_user is None
        assert config.credentials.read_pass is None

    def test_credentials_set(self) -> None:
        creds = StreamCredentials(publish_user="pub", publish_pass="pubpass")
        config = StreamConfig(name="cam1", source="publisher", credentials=creds)
        assert config.credentials.publish_user == "pub"
        assert config.credentials.publish_pass == "pubpass"


class TestStreamCreateRequestValidation:
    """Test StreamCreateRequest schema validation."""

    def test_valid_create_request(self) -> None:
        req = StreamCreateRequest(name="live", source="publisher")
        assert req.name == "live"
        assert req.record_enabled is True

    def test_name_validation_same_as_stream_config(self) -> None:
        with pytest.raises(ValidationError):
            StreamCreateRequest(name="-invalid", source="publisher")

    def test_source_validation_same_as_stream_config(self) -> None:
        with pytest.raises(ValidationError):
            StreamCreateRequest(name="cam1", source="ftp://bad")


class TestStreamUpdateRequestValidation:
    """Test StreamUpdateRequest schema validation."""

    def test_empty_update_allowed(self) -> None:
        req = StreamUpdateRequest()
        assert req.source is None
        assert req.record_enabled is None
        assert req.credentials is None

    def test_partial_update(self) -> None:
        req = StreamUpdateRequest(record_enabled=False)
        assert req.record_enabled is False
        assert req.source is None

    def test_source_validation(self) -> None:
        with pytest.raises(ValidationError):
            StreamUpdateRequest(source="bad://url")


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_streams() -> None:
    """Clear all streams before each test."""
    svc.clear_all_streams()


class TestStreamConfigService:
    """Test stream configuration service CRUD operations."""

    def test_create_stream(self) -> None:
        req = StreamCreateRequest(name="cam1", source="publisher")
        config = svc.create_stream(req)
        assert config.name == "cam1"
        assert svc.stream_exists("cam1") is True

    def test_create_duplicate_raises(self) -> None:
        req = StreamCreateRequest(name="cam1", source="publisher")
        svc.create_stream(req)
        with pytest.raises(svc.StreamConfigError, match="already exists"):
            svc.create_stream(req)

    def test_list_streams_ordered_by_time(self) -> None:
        svc.create_stream(StreamCreateRequest(name="cam1", source="publisher"))
        svc.create_stream(StreamCreateRequest(name="cam2", source="publisher"))
        streams = svc.list_streams()
        assert len(streams) == 2
        assert streams[0].name == "cam1"
        assert streams[1].name == "cam2"

    def test_get_stream_found(self) -> None:
        svc.create_stream(StreamCreateRequest(name="cam1", source="publisher"))
        found = svc.get_stream("cam1")
        assert found is not None
        assert found.name == "cam1"

    def test_get_stream_not_found(self) -> None:
        assert svc.get_stream("nonexistent") is None

    def test_update_stream(self) -> None:
        svc.create_stream(StreamCreateRequest(name="cam1", source="publisher"))
        updated = svc.update_stream(
            "cam1",
            StreamUpdateRequest(source="rtsp://192.168.1.1/stream", record_enabled=False),
        )
        assert updated.source == "rtsp://192.168.1.1/stream"
        assert updated.record_enabled is False
        assert updated.updated_at > updated.created_at

    def test_update_nonexistent_raises(self) -> None:
        with pytest.raises(svc.StreamConfigError, match="not found"):
            svc.update_stream("missing", StreamUpdateRequest(record_enabled=False))

    def test_delete_stream(self) -> None:
        svc.create_stream(StreamCreateRequest(name="cam1", source="publisher"))
        svc.delete_stream("cam1")
        assert svc.get_stream("cam1") is None

    def test_delete_nonexistent_raises(self) -> None:
        with pytest.raises(svc.StreamConfigError, match="not found"):
            svc.delete_stream("missing")

    def test_stream_exists(self) -> None:
        assert svc.stream_exists("cam1") is False
        svc.create_stream(StreamCreateRequest(name="cam1", source="publisher"))
        assert svc.stream_exists("cam1") is True

    def test_thread_safety_race_create(self) -> None:
        """Simulate concurrent creation of same stream; only one succeeds."""
        import threading
        import time

        errors = []
        successes = []

        def create() -> None:
            try:
                time.sleep(0.001)
                svc.create_stream(StreamCreateRequest(name="race", source="publisher"))
                successes.append(True)
            except svc.StreamConfigError as e:
                errors.append(str(e))

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(errors) == 9
        assert svc.stream_exists("race") is True
