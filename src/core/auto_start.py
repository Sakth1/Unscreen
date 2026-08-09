import logging
import sys
from pathlib import Path

from utils.platform import get_winreg

winreg = get_winreg()

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Unscreen"


def _get_target_path() -> str | None:
    if winreg is None:
        return None
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "main.py"
    if script.exists():
        return f'"{pythonw}" "{script}"'
    launcher = _find_launcher_exe()
    if launcher is not None:
        return launcher
    logger.warning("main.py not found for dev-mode auto-start")
    return None


def _find_launcher_exe() -> str | None:
    for parent in Path(sys.executable).resolve().parents:
        for exe in parent.glob("*.exe"):
            name = exe.stem.lower()
            if name not in ("python", "pythonw", "pip", "py", "python3"):
                return str(exe)
        if parent.parent == parent:
            break
    return None


def enable() -> bool:
    target = _get_target_path()
    if target is None:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, target)
        winreg.CloseKey(key)
        logger.info("Auto-start enabled: %s", target)
        return True
    except Exception:
        logger.exception("Failed to enable auto-start")
        return False


def disable() -> bool:
    """Delete the auto-start entry from both the current user and machine hives.

    Machine installs (Inno "install for anyone") write to HKLM, so toggling
    the setting off must clear that hive too - the per-user hive alone would
    leave the app starting at every logon.
    """
    if winreg is None:
        return False
    result = True
    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(root_key, RUN_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, VALUE_NAME)
            winreg.CloseKey(key)
            logger.info("Auto-start disabled in %r", root_key)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Failed to disable auto-start in %r", root_key)
            result = False
    return result


def is_enabled() -> bool:
    if winreg is None:
        return False
    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(root_key, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
            winreg.QueryValueEx(key, VALUE_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to query auto-start in %r", root_key)
    return False
