"""Windows-only process helpers (registry, named mutex)."""

import logging
import sys

if sys.platform != "win32":
    winreg = None
else:
    try:
        import winreg
    except ImportError:
        winreg = None

logger = logging.getLogger(__name__)


def get_winreg():
    """Return the ``winreg`` module or ``None`` on non-Windows platforms."""
    return winreg


_win_mutex_handles: list[int] = []

_ERROR_ALREADY_EXISTS = 183


def acquire_instance_mutex(name: str) -> int | None:
    """Hold a named Windows mutex for the lifetime of the process.

    The Inno installer declares ``AppMutex`` with the same name, so an upgrade
    can detect and close a running app instead of failing on locked files.
    Returns ``None`` on non-Windows or when the mutex is already held by
    another instance (``ERROR_ALREADY_EXISTS``); handles are kept alive so
    the mutex is only released when the process exits.
    """
    if sys.platform != "win32":
        logger.info("acquire_instance_mutex: not Windows, skipping")
        return None
    import ctypes

    logger.info("acquire_instance_mutex: attempting to acquire mutex '%s'", name)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        last_error = ctypes.get_last_error()
        logger.error(
            "acquire_instance_mutex: CreateMutexW failed with error %s", last_error
        )
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        logger.warning(
            "acquire_instance_mutex: mutex '%s' already exists (another instance running)",
            name,
        )
        return None
    _win_mutex_handles.append(handle)
    logger.info(
        "acquire_instance_mutex: successfully acquired mutex '%s' (handle=%s)",
        name,
        handle,
    )
    return handle
