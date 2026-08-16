import logging
import os
from logging.handlers import RotatingFileHandler

from utils.paths import get_data_dir

LOG_DIR = "logs"
LOG_FILE = "app.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

FLET_LOGGERS = ("flet", "flet_object_patch", "flet_components")

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _attach_flet_loggers(handler: logging.Handler) -> None:
    """Route flet's internal loggers into the file handler.

    Their level stays NOTSET so the effective level follows the root
    logger (apply_root_level), and propagate is disabled to avoid
    duplicate lines via the root handler.
    """
    for name in FLET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.NOTSET)
        logger.propagate = False
        logger.addHandler(handler)


def setup_file_logging() -> str | None:
    log_dir = os.path.join(get_data_dir(), LOG_DIR)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as e:
        logging.warning("Cannot create log directory %s: %s", log_dir, e)
        return None

    log_path = os.path.join(log_dir, LOG_FILE)
    try:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(_formatter)
        logging.getLogger().addHandler(handler)
        _attach_flet_loggers(handler)
        logging.getLogger().info("File logging initialized: %s", log_path)
        return log_path
    except OSError as e:
        logging.warning("Cannot create log file %s: %s", log_path, e)
        return None


def get_log_path() -> str | None:
    log_path = os.path.join(get_data_dir(), LOG_DIR, LOG_FILE)
    if os.path.isfile(log_path):
        return log_path
    return None


def apply_root_level(level: str) -> None:
    """Set the root logger and file-handler level to ``level`` (e.g. "DEBUG")."""
    normalized = str(level).upper()
    if normalized not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        logging.getLogger(__name__).warning("Ignoring unknown log level: %r", level)
        return
    logging.getLogger().setLevel(normalized)
    for handler in logging.getLogger().handlers:
        if isinstance(handler, (RotatingFileHandler, logging.StreamHandler)):
            handler.setLevel(normalized)
    logging.getLogger(__name__).info("Log level set to %s", normalized)


def clear_logs() -> None:
    log_dir = os.path.join(get_data_dir(), LOG_DIR)
    if not os.path.isdir(log_dir):
        return
    for name in FLET_LOGGERS:
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    for handler in logging.getLogger().handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            logging.getLogger().removeHandler(handler)
            handler.close()
    for i in range(BACKUP_COUNT + 1):
        name = LOG_FILE if i == 0 else f"{LOG_FILE}.{i}"
        path = os.path.join(log_dir, name)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logging.warning("Failed to remove log file: %s", path)
    setup_file_logging()


def read_log_lines(max_lines: int = 500) -> list[str]:
    path = get_log_path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-max_lines:]
    except OSError as e:
        logging.warning("Cannot read log file %s: %s", path, e)
        return []
