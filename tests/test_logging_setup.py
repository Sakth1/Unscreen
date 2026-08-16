"""Logging setup: file handler wiring and flet logger routing.

F8: flet's internal loggers (flet, flet_object_patch, flet_components)
must flow into the rotating file handler, following the root level —
without duplicating lines through the root handler.
"""

import logging

import pytest

from core import logging_setup
from core.logging_setup import (
    FLET_LOGGERS,
    apply_root_level,
    clear_logs,
    get_log_path,
    read_log_lines,
    setup_file_logging,
)


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "get_data_dir", lambda: str(tmp_path))
    for name in FLET_LOGGERS:
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    root_level = logging.getLogger().level
    yield tmp_path
    clear_logs()
    logging.getLogger().setLevel(root_level)


def _log_text() -> str:
    return "\n".join(read_log_lines())


def test_flet_loggers_route_into_file_handler(log_dir):
    setup_file_logging()
    apply_root_level("DEBUG")
    logging.getLogger("flet").info("flet-probe")
    assert "flet-probe" in _log_text()


def test_flet_loggers_do_not_duplicate_via_root(log_dir):
    setup_file_logging()
    apply_root_level("DEBUG")
    logging.getLogger("flet_object_patch").warning("w-probe")
    assert _log_text().count("w-probe") == 1


def test_flet_loggers_follow_root_level(log_dir):
    setup_file_logging()
    apply_root_level("DEBUG")
    logging.getLogger("flet_components").debug("debug-probe")
    apply_root_level("WARNING")
    logging.getLogger("flet_components").debug("swallowed-probe")
    text = _log_text()
    assert "debug-probe" in text
    assert "swallowed-probe" not in text


def _file_handlers(name: str) -> list:
    """RotatingFileHandlers on a logger — pytest attaches its own
    LogCaptureHandlers to propagate=False loggers, so filter by type."""
    return [
        h
        for h in logging.getLogger(name).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]


def test_single_setup_attaches_one_file_handler_per_flet_logger(log_dir):
    setup_file_logging()
    for name in FLET_LOGGERS:
        assert len(_file_handlers(name)) == 1


def test_clear_logs_removes_flet_handlers(log_dir):
    setup_file_logging()
    clear_logs()
    for name in FLET_LOGGERS:
        assert len(_file_handlers(name)) == 1  # clear_logs restarts file logging
    assert get_log_path() is not None
