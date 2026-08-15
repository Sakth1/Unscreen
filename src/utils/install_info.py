"""Detect where the Windows installer put Unscreen.

The Inno installer (``packaging/windows/installer.iss``) can install both
per-machine (HKLM, requires elevation) and per-user (HKCU, no elevation).
The auto-updater needs to know which, so the silent upgrade is launched with
the matching ``/ALLUSERS`` / ``/CURRENTUSER`` switch and an explicit install
directory, otherwise setup would default to ``{autopf}`` and could create a
second, split installation.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Literal

from utils.win32 import get_winreg

logger = logging.getLogger(__name__)

#: Registration key written by the installer (Inno AppId) at install time.
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\D2E3F4A5-B6C7-48D9-A0B1-C2D3E4F5A6B7_is1"
)

_KEY_WOW64_32KEY = 0x0200

InstallMode = Literal["per-user", "per-machine"]


def _key_exists(hive: int, subkey: str, view_flag: int = 0) -> bool:
    winreg = get_winreg()
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view_flag):
            return True
    except OSError:
        return False


def detect_install_mode() -> InstallMode | None:
    """Return ``per-user`` or ``per-machine`` for the installed app, or None.

    HKCU is checked first (a per-user install never writes to HKLM, and a
    per-machine install rarely also leaves an HKCU entry).
    """
    winreg = get_winreg()
    if winreg is None:
        return None
    if _key_exists(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
        return "per-user"
    if _key_exists(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY):
        return "per-machine"
    # A 64-bit OS may host the record under the 32-bit view.
    if _key_exists(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, _KEY_WOW64_32KEY):
        return "per-machine"
    return None


def install_dir() -> Path | None:
    """Directory containing the running packaged executable, if any."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    exe = Path(sys.executable).name.lower()
    if exe.endswith(".exe") and not exe.startswith(("python", "flet")):
        return Path(sys.executable).parent
    return None


def installer_scope_args(mode: InstallMode | None) -> list[str]:
    """Return the Inno ``/ALLUSERS`` / ``/CURRENTUSER`` switch for ``mode``."""
    if mode == "per-machine":
        return ["/ALLUSERS"]
    if mode == "per-user":
        return ["/CURRENTUSER"]
    return []


def installer_dir_args(directory: str | os.PathLike | None) -> list[str]:
    if not directory:
        return []
    return [f'/DIR="{Path(directory)}"']


def build_installer_args(
    directory: str | os.PathLike | None = None,
    mode: InstallMode | None = None,
) -> list[str]:
    """Compose the extra command-line args for a silent upgrade."""
    args: list[str] = []
    args.extend(installer_scope_args(mode))
    args.extend(installer_dir_args(directory))
    return args
