import os
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from utils.models import OSType

if TYPE_CHECKING:
    import winreg
else:
    try:
        import winreg
    except ImportError:
        winreg = None


def get_winreg():
    """Return the ``winreg`` module or ``None`` on non-Windows platforms."""
    return winreg


def is_android() -> bool:
    """True when running on Android.

    ``platform.system()`` cannot identify Android on its own: CPython built
    for Android reports ``"Android"`` from 3.13 (PEP 738) but ``"Linux"`` on
    earlier versions, so the interpreter and runtime markers are checked
    first. ``sys.getandroidapilevel`` only exists in CPython built for
    Android; ``FLET_PLATFORM`` and ``MAIN_ACTIVITY_HOST_CLASS_NAME`` are set
    by the Flet build template and the serious-python Android plugin.
    """
    if getattr(sys, "getandroidapilevel", None) is not None:
        return True
    if os.environ.get("FLET_PLATFORM") == "android":
        return True
    if os.environ.get("MAIN_ACTIVITY_HOST_CLASS_NAME"):
        return True
    return platform.system() == "Android"


def detect_os() -> OSType:
    if is_android():
        return OSType.ANDROID
    match platform.system():
        case "Windows":
            return OSType.WINDOWS
        case _:
            return OSType.UNKNOWN


def is_packaged() -> bool:
    """True when running from a bundled executable (not from source)."""
    if getattr(sys, "frozen", False):
        return True
    if is_android():
        return True
    exe = Path(sys.executable).name.lower()
    return exe.endswith(".exe") and not exe.startswith(("python", "flet"))


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
