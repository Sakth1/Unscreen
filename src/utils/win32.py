"""Windows-only process helpers (registry, named mutex)."""

import sys

if sys.platform != "win32":
    winreg = None
else:
    try:
        import winreg
    except ImportError:
        winreg = None


def get_winreg():
    """Return the ``winreg`` module or ``None`` on non-Windows platforms."""
    return winreg


_win_mutex_handles: list[int] = []


def acquire_instance_mutex(name: str) -> int | None:
    """Hold a named Windows mutex for the lifetime of the process.

    The Inno installer declares ``AppMutex`` with the same name, so an upgrade
    can detect and close a running app instead of failing on locked files.
    Returns ``None`` on non-Windows; handles are kept alive so the mutex is
    only released when the process exits.
    """
    if sys.platform != "win32":
        return None
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if handle:
        _win_mutex_handles.append(handle)
    return handle
