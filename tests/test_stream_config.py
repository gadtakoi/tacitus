"""Tests for stream configuration schemas, repository and service."""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.streams import (
    MAX_CROP_PERCENT,
    MAX_CROP_TOTAL_PERCENT,
    StreamConfig,
    StreamCreateRequest,
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

    def test_schema_carries_no_credentials(self) -> None:
        """Credentials have no place in the stream configuration schema.

        MediaMTX 1.15.1 has no per-path credential parameters — access comes
        from the global ``authInternalUsers`` list (ADR-012). Accepting these
        fields again would reintroduce plaintext passwords that no component
        can consume, so the schema must reject them outright.
        """
        config = StreamConfig(name="cam1", source="publisher")
        for field in ("credentials", "publish_user", "publish_pass", "read_user", "read_pass"):
            assert not hasattr(config, field), f"StreamConfig exposes {field}"


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

    def test_partial_update(self) -> None:
        req = StreamUpdateRequest(record_enabled=False)
        assert req.record_enabled is False
        assert req.source is None

    def test_source_validation(self) -> None:
        with pytest.raises(ValidationError):
            StreamUpdateRequest(source="bad://url")

    def test_crop_fields_are_optional(self) -> None:
        req = StreamUpdateRequest()
        assert req.crop_enabled is None
        assert req.crop_top_percent is None
        assert req.crop_bottom_percent is None

    def test_combined_limit_is_not_applied_to_a_partial_update(self) -> None:
        """One edge alone says nothing about the total; the service decides."""
        req = StreamUpdateRequest(crop_top_percent=MAX_CROP_PERCENT)
        assert req.crop_top_percent == MAX_CROP_PERCENT

    def test_per_edge_bound_still_applies(self) -> None:
        with pytest.raises(ValidationError):
            StreamUpdateRequest(crop_top_percent=MAX_CROP_PERCENT + 1)


class TestCropValidation:
    """Crop settings replace footage in place, so the bounds matter."""

    def test_crop_defaults_to_disabled(self) -> None:
        """An upgrade must not start rewriting recordings on its own."""
        config = StreamConfig(name="cam1")
        assert config.crop_enabled is False
        assert config.crop_top_percent == 0
        assert config.crop_bottom_percent == 0

    def test_valid_crop_accepted(self) -> None:
        config = StreamConfig(
            name="cam1", crop_enabled=True, crop_top_percent=15, crop_bottom_percent=20
        )
        assert config.crop_top_percent == 15
        assert config.crop_bottom_percent == 20

    def test_one_sided_crop_may_take_almost_the_whole_frame(self) -> None:
        """A camera aimed mostly at the sky is a legitimate configuration."""
        config = StreamConfig(name="cam1", crop_enabled=True, crop_top_percent=90)
        assert config.crop_top_percent == 90

    @pytest.mark.parametrize("value", [-1, MAX_CROP_PERCENT + 1])
    def test_percentage_outside_bounds_rejected(self, value: int) -> None:
        with pytest.raises(ValidationError):
            StreamCreateRequest(name="cam1", crop_top_percent=value)

    def test_combined_crop_leaving_too_little_rejected(self) -> None:
        with pytest.raises(ValidationError, match="together must not exceed"):
            StreamCreateRequest(name="cam1", crop_top_percent=60, crop_bottom_percent=31)

    def test_combined_crop_at_the_limit_accepted(self) -> None:
        req = StreamCreateRequest(
            name="cam1", crop_top_percent=60, crop_bottom_percent=MAX_CROP_TOTAL_PERCENT - 60
        )
        assert req.crop_top_percent + req.crop_bottom_percent == MAX_CROP_TOTAL_PERCENT


# ---------------------------------------------------------------------------
# Service tests (with real PostgreSQL)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestStreamConfigService:
    """Test stream configuration service CRUD operations via database."""

    async def test_create_stream(self, db_session: AsyncSession) -> None:
        req = StreamCreateRequest(name="cam1", source="publisher")
        config = await svc.create_stream(db_session, req)
        assert config.name == "cam1"
        assert await svc.stream_exists(db_session, "cam1") is True

    async def test_create_duplicate_raises(self, db_session: AsyncSession) -> None:
        req = StreamCreateRequest(name="cam1", source="publisher")
        await svc.create_stream(db_session, req)
        with pytest.raises(svc.StreamConfigError, match="already exists"):
            await svc.create_stream(db_session, req)

    async def test_list_streams_ordered_by_time(self, db_session: AsyncSession) -> None:
        await svc.create_stream(db_session, StreamCreateRequest(name="cam1", source="publisher"))
        await svc.create_stream(db_session, StreamCreateRequest(name="cam2", source="publisher"))
        streams = await svc.list_streams(db_session)
        assert len(streams) == 2
        assert streams[0].name == "cam1"
        assert streams[1].name == "cam2"

    async def test_get_stream_found(self, db_session: AsyncSession) -> None:
        await svc.create_stream(db_session, StreamCreateRequest(name="cam1", source="publisher"))
        found = await svc.get_stream(db_session, "cam1")
        assert found is not None
        assert found.name == "cam1"

    async def test_get_stream_not_found(self, db_session: AsyncSession) -> None:
        assert await svc.get_stream(db_session, "nonexistent") is None

    async def test_update_stream(self, db_session: AsyncSession) -> None:
        await svc.create_stream(db_session, StreamCreateRequest(name="cam1", source="publisher"))
        updated = await svc.update_stream(
            db_session,
            "cam1",
            StreamUpdateRequest(source="rtsp://192.168.1.1/stream", record_enabled=False),
        )
        assert updated.source == "rtsp://192.168.1.1/stream"
        assert updated.record_enabled is False
        assert updated.updated_at > updated.created_at

    async def test_update_nonexistent_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(svc.StreamConfigError, match="not found"):
            await svc.update_stream(
                db_session, "missing", StreamUpdateRequest(record_enabled=False)
            )

    async def test_delete_stream(self, db_session: AsyncSession) -> None:
        await svc.create_stream(db_session, StreamCreateRequest(name="cam1", source="publisher"))
        await svc.delete_stream(db_session, "cam1")
        assert await svc.get_stream(db_session, "cam1") is None

    async def test_delete_nonexistent_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(svc.StreamConfigError, match="not found"):
            await svc.delete_stream(db_session, "missing")

    async def test_stream_exists(self, db_session: AsyncSession) -> None:
        assert await svc.stream_exists(db_session, "cam1") is False
        await svc.create_stream(db_session, StreamCreateRequest(name="cam1", source="publisher"))
        assert await svc.stream_exists(db_session, "cam1") is True

    async def test_crop_settings_round_trip(self, db_session: AsyncSession) -> None:
        await svc.create_stream(
            db_session,
            StreamCreateRequest(
                name="cam1", crop_enabled=True, crop_top_percent=15, crop_bottom_percent=20
            ),
        )
        config = await svc.get_stream(db_session, "cam1")
        assert config is not None
        assert config.crop_enabled is True
        assert config.crop_top_percent == 15
        assert config.crop_bottom_percent == 20

    async def test_partial_update_checks_the_merged_total(self, db_session: AsyncSession) -> None:
        """40 is legal alone and illegal on top of a stored 60."""
        await svc.create_stream(
            db_session,
            StreamCreateRequest(name="cam1", crop_top_percent=60, crop_bottom_percent=20),
        )

        with pytest.raises(svc.StreamConfigValidationError):
            await svc.update_stream(db_session, "cam1", StreamUpdateRequest(crop_bottom_percent=40))

        config = await svc.get_stream(db_session, "cam1")
        assert config is not None
        assert config.crop_bottom_percent == 20

    async def test_disabling_the_crop_keeps_the_percentages(self, db_session: AsyncSession) -> None:
        """The worker restores the full frame; the numbers wait for a re-enable."""
        await svc.create_stream(
            db_session,
            StreamCreateRequest(
                name="cam1", crop_enabled=True, crop_top_percent=15, crop_bottom_percent=20
            ),
        )

        config = await svc.update_stream(
            db_session, "cam1", StreamUpdateRequest(crop_enabled=False)
        )

        assert config.crop_enabled is False
        assert config.crop_top_percent == 15

    async def test_update_missing_stream_raises_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(svc.StreamConfigNotFoundError):
            await svc.update_stream(db_session, "missing", StreamUpdateRequest(crop_enabled=True))

    async def test_concurrent_create_unique_constraint(self, db_session: AsyncSession) -> None:
        """Verify database unique constraint prevents duplicate names."""
        await svc.create_stream(
            db_session, StreamCreateRequest(name="unique_cam", source="publisher")
        )
        with pytest.raises(svc.StreamConfigError, match="already exists"):
            await svc.create_stream(
                db_session, StreamCreateRequest(name="unique_cam", source="publisher")
            )
