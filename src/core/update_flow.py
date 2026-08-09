"""Update download → install → relaunch orchestration.

Kept free of UI imports so the whole flow is unit-testable. The Windows path
hands over to the Inno installer elevated (``runas``); the app must exit so
the installer can replace the running executable, and a tiny ``.cmd``
"watchdog" dropped in the temp directory reopens the app (in the user's own
session, not elevated) once setup has finished.
"""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from core.update_checker import (
    ApplyError,
    ApplyOutcome,
    ApplyResult,
    UpdateChecker,
    UpdateInfo,
)
from utils.install_info import build_installer_args, detect_install_mode, install_dir

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000


class UpdateApplyError(Exception):
    """The installer could not be started (platform/launch failure)."""


def _is_windows() -> bool:
    return sys.platform == "win32"


def installer_extra_args() -> list[str]:
    """Extra Inno switches for a silent upgrade of the *current* install."""
    return build_installer_args(install_dir(), detect_install_mode())


def write_relaunch_watchdog(
    setup_pid: int,
    app_exe: str | os.PathLike[str],
    directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Write a .cmd that relaunches the app once ``setup_pid`` has exited.

    The watchdog is spawned by the (non-elevated) app before it exits, runs
    in the user session, polls the elevated setup process, and finally starts
    the app again with normal privileges.
    """
    location = Path(directory) if directory else Path(tempfile.gettempdir())
    location.mkdir(parents=True, exist_ok=True)
    path = location / f"unscreen-update-watchdog-{os.getpid()}-{setup_pid}.cmd"

    pid = int(setup_pid)
    app = str(Path(app_exe)).replace('"', '^"')

    lines = [
        "@echo off",
        "setlocal",
        f'set "TARGETPID={pid}"',
        f'set "APP={app}"',
        ":wait",
        'tasklist /fi "PID eq %TARGETPID%" 2>nul | findstr /r /c:"%TARGETPID%" >nul',
        "if not errorlevel 1 (",
        "  ping 127.0.0.1 -n 2 >nul",
        "  goto :wait",
        ")",
        'start "" "%APP%"',
        'del "%~f0"',
        "exit /b 0",
    ]
    with path.open("w", encoding="ascii", newline="\r\n") as fp:
        fp.write("\n".join(lines) + "\n")
    logger.info("Wrote relaunch watchdog %s", path)
    return path


def spawn_watchdog(path: str | os.PathLike[str]) -> None:
    """Start the watchdog detached and windowless."""
    subprocess.Popen(
        [str(path)],
        cwd=str(Path(path).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
        close_fds=True,
    )
    logger.info("Spawned relaunch watchdog %s", path)


class Updater:
    """Orchestrates apply + optional relaunch around :class:`UpdateChecker`."""

    def __init__(self, checker: UpdateChecker | None = None):
        self._checker = checker or UpdateChecker()

    @property
    def checker(self) -> UpdateChecker:
        return self._checker

    def apply_update(
        self,
        update: UpdateInfo,
        installer_path: str | os.PathLike[str],
        relaunch: bool = True,
        extra_args: Sequence[str] | None = None,
    ) -> ApplyOutcome:
        """Launch the installer; optionally arm the post-install relaunch.

        On Windows, when ``relaunch`` is true a watchdog is written and
        spawned immediately after a successful (not user-canceled) launch; the
        caller should then exit the app so the installer can replace files.
        Returns :class:`ApplyOutcome` with ``CANCELED`` when UAC is declined.
        Raises :class:`UpdateApplyError` when the installer cannot be started.
        """
        try:
            outcome = self.checker.apply(update, installer_path, extra_args)
        except ApplyError as exc:
            raise UpdateApplyError(str(exc)) from exc
        if (
            outcome.result is ApplyResult.APPLIED
            and outcome.process_id is not None
            and relaunch
            and _is_windows()
        ):
            self._arm_relaunch(outcome.process_id)
        return outcome

    def _arm_relaunch(self, setup_pid: int) -> None:
        exe = install_dir() or Path(sys.executable)
        watchdog = write_relaunch_watchdog(setup_pid, exe)
        spawn_watchdog(watchdog)
