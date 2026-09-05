"""Tests for logging configuration."""

import json
import logging
import os
import sys
import tempfile

from app.core.logging_config import JsonFormatter, RequestIdFilter, setup_logging
from app.core.security_logging import log_security_event


def test_json_formatter_outputs_valid_json() -> None:
    """JsonFormatter should produce valid JSON with expected fields."""
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "hello"
    assert parsed["request_id"] == "req-123"
    assert "timestamp" in parsed


def test_request_id_filter_injects_request_id() -> None:
    """RequestIdFilter should inject request_id into log record."""
    filt = RequestIdFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    assert not hasattr(record, "request_id")
    filt.filter(record)
    assert record.request_id == "unknown"  # type: ignore[attr-defined]


def test_setup_logging_creates_handlers() -> None:
    """setup_logging should create RotatingFileHandler and StreamHandler."""
    setup_logging()
    root_logger = logging.getLogger()
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root_logger.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)


def test_log_writes_to_file() -> None:
    """Application logs should be written to the configured file."""
    setup_logging()
    logger = logging.getLogger("test_app_logger")
    test_msg = "test_log_writes_to_file_message"
    logger.info(test_msg)

    log_file_path = os.environ.get("TACITUS_LOG_FILE_PATH", "/tmp/app.log")
    with open(log_file_path) as f:
        content = f.read()
    assert test_msg in content


def test_security_log_writes_to_file() -> None:
    """Security events should be written to security.log."""
    setup_logging()
    log_security_event("auth.login", severity="info", ip="127.0.0.1", user="testuser")

    log_file_path = os.environ.get("TACITUS_LOG_FILE_PATH", "/tmp/app.log")
    security_log_path = os.path.join(os.path.dirname(log_file_path), "security.log")
    with open(security_log_path) as f:
        content = f.read()
    assert "auth.login" in content
    assert "testuser" in content


def test_text_formatter_outputs_plain_text() -> None:
    """Text formatter should produce human-readable plain text."""
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | request_id=%(request_id)s | %(message)s",
    )
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    output = formatter.format(record)
    assert "INFO" in output
    assert "hello" in output
    assert "req-123" in output


def test_log_level_debug_includes_debug_messages() -> None:
    """DEBUG level should allow debug messages through."""
    logger = logging.getLogger("level_debug_test")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger.handlers = [handler]
    assert logger.isEnabledFor(logging.DEBUG)


def test_log_level_warning_filters_info() -> None:
    """WARNING level should filter out INFO messages."""
    logger = logging.getLogger("level_warning_test")
    logger.setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    handler.setLevel(logging.WARNING)
    logger.handlers = [handler]
    assert not logger.isEnabledFor(logging.INFO)


def test_log_rotation_creates_backup() -> None:
    """RotatingFileHandler should create backup files when maxBytes exceeded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "test.log")
        handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=10,
            backupCount=2,
        )
        logger = logging.getLogger("rotation_test")
        logger.setLevel(logging.DEBUG)
        logger.handlers = [handler]
        for _ in range(20):
            logger.info("x" * 20)
        handler.close()
        backups = [f for f in os.listdir(tmpdir) if f.startswith("test.log")]
        assert len(backups) >= 2


def test_json_formatter_includes_the_traceback() -> None:
    """``logger.exception`` must survive JSON formatting.

    The formatter emitted only the message, so every ``logger.exception`` call
    lost its traceback — 14 of them exist precisely for diagnosis, and `json`
    is the default format, so production was the case that suffered. A 500 in
    the logs read as one line with no cause attached.
    """
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Database error while registering user",
            args=(),
            exc_info=sys.exc_info(),
        )

    parsed = json.loads(formatter.format(record))

    assert parsed["message"] == "Database error while registering user"
    assert "ValueError: boom" in parsed["exception"]
    assert "Traceback" in parsed["exception"]


def test_json_formatter_omits_exception_key_without_one() -> None:
    """An ordinary record gains no empty ``exception`` field."""
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="nothing wrong",
        args=(),
        exc_info=None,
    )

    assert "exception" not in json.loads(formatter.format(record))
