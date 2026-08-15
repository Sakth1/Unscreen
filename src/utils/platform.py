import os
import platform
import sys
from enum import Enum
from pathlib import Path


class OSType(Enum):
    UNKNOWN = 0
    WINDOWS = 1
    ANDROID = 2


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
