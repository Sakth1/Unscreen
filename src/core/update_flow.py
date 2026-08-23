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
from utils.paths import get_data_dir

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
    old_pid: int | None = None,
) -> Path:
    """Write a .cmd that relaunches the app once the old processes have exited.

    The watchdog is spawned by the (non-elevated) app before it exits, runs
    in the user session, polls the elevated setup process *and* the previous
    app instance (``old_pid``, defaulting to the current process), and only
    then starts the app again with normal privileges. Waiting for the old
    instance matters because a freshly relaunched app must not race the
    previous one for the single-instance mutex or the database files.
    """
    location = Path(directory) if directory else Path(tempfile.gettempdir())
    location.mkdir(parents=True, exist_ok=True)
    path = location / f"unscreen-update-watchdog-{os.getpid()}-{setup_pid}.cmd"

    pid = int(setup_pid)
    previous = int(old_pid) if old_pid else os.getpid()
    app = str(Path(app_exe)).replace('"', '^"')
    app_dir = str(Path(app_exe).parent).replace('"', '^"')
    # File sentinel survives setlocal/start env isolation — env var alone
    # is lost with `setlocal` + `start` (see flet #6101 post-update blank).
    try:
        sentinel = str(Path(get_data_dir()) / ".post_update_flag")
    except Exception:
        sentinel = str(Path(tempfile.gettempdir()) / "unscreen-post-update.flag")
    sentinel_escaped = sentinel.replace('"', '""')

    lines = [
        "@echo off",
        "setlocal",
        # Purge stale FLET transport tokens inherited from the old process.
        # The host sets its own FLET_DART_BRIDGE_PORT; an inherited value
        # pointing at a dead VM's keyed channel causes the bridge handshake
        # to stall permanently (blank window, no entrypoint).
        "set FLET_DART_BRIDGE_PORT=",
        "set FLET_SERVER_PORT=",
        f'set "TARGETPID={pid}"',
        f'set "TARGETPID2={previous}"',
        f'set "APP={app}"',
        f'set "APPDIR={app_dir}"',
        f'set "SENTINEL={sentinel_escaped}"',
        ":wait",
        'tasklist /fi "PID eq %TARGETPID%" 2>nul | findstr /r /c:"%TARGETPID%" >nul',
        "if not errorlevel 1 goto :sleep",
        'tasklist /fi "PID eq %TARGETPID2%" 2>nul | findstr /r /c:"%TARGETPID2%" >nul',
        "if not errorlevel 1 goto :sleep",
        "goto :prelaunch",
        ":sleep",
        "ping 127.0.0.1 -n 2 >nul",
        "goto :wait",
        ":prelaunch",
        "timeout /t 3 /nobreak >nul",
        "goto :launch",
        ":launch",
        'type nul > "%SENTINEL%"',
        'set "UNSCREEN_POST_UPDATE=1"',
        'start "" /d "%APPDIR%" "%APP%"',
        'del "%~f0"',
        "exit /b 0",
    ]
    with path.open("w", encoding="ascii", newline="\r\n") as fp:
        fp.write("\n".join(lines) + "\n")
    logger.info(
        "Wrote relaunch watchdog %s (setup_pid=%s, old_pid=%s, app=%s, delay=3s)",
        path,
        pid,
        previous,
        app,
    )
    return path


def spawn_watchdog(path: str | os.PathLike[str]) -> None:
    """Start the watchdog detached and windowless."""
    logger.info("Spawning relaunch watchdog: %s", path)
    try:
        subprocess.Popen(
            [str(path)],
            cwd=str(Path(path).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
            close_fds=True,
        )
        logger.info("Watchdog spawned successfully: %s", path)
    except Exception:
        logger.exception("Failed to spawn watchdog: %s", path)
        raise


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
        logger.info(
            "apply_update called: version=%s installer=%s relaunch=%s is_windows=%s",
            update.version,
            installer_path,
            relaunch,
            _is_windows(),
        )
        try:
            outcome = self.checker.apply(update, installer_path, extra_args)
        except ApplyError as exc:
            logger.error("apply_update failed: error=%s", exc)
            raise UpdateApplyError(str(exc)) from exc
        logger.info(
            "apply_update outcome: result=%s process_id=%s",
            outcome.result,
            outcome.process_id,
        )
        if (
            outcome.result is ApplyResult.APPLIED
            and outcome.process_id is not None
            and relaunch
            and _is_windows()
        ):
            logger.info("Arming relaunch watchdog for setup_pid=%s", outcome.process_id)
            self._arm_relaunch(outcome.process_id)
        return outcome

    def _arm_relaunch(self, setup_pid: int) -> None:
        directory = install_dir()
        exe = (
            directory / Path(sys.executable).name if directory else Path(sys.executable)
        )
        logger.info(
            "_arm_relaunch: setup_pid=%s app_exe=%s install_dir=%s",
            setup_pid,
            exe,
            directory,
        )
        watchdog = write_relaunch_watchdog(setup_pid, exe)
        spawn_watchdog(watchdog)
