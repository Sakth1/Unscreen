import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_CREATE_NO_WINDOW = 0x08000000
_BOOTSTRAP_TIMEOUT = 30
_APP_START_TIME = time.monotonic()


def _get_data_dir() -> Path:
    """Resolve the canonical data directory.

    Uses the same logic as ``utils.paths.get_data_dir`` but avoids importing
    the app module (which pulls in heavy dependencies).  The import chain
    ``utils.paths → utils.platform`` is stdlib-only and safe this early.
    """
    from utils.paths import get_data_dir

    return Path(get_data_dir())


def _write_startup_log(message: str) -> None:
    """Write a timestamped line to the startup log file."""
    try:
        log_file = _get_data_dir() / "startup.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def _start_bootstrap_watchdog() -> None:
    """If entrypoint hasn't fired within _BOOTSTRAP_TIMEOUT seconds, spawn a
    fresh copy of the exe and exit — but only once per update cycle.

    The flag file ``.bootstrap_retried`` prevents infinite restart loops:
    the first post-update launch gets the watchdog; if it fires, the flag
    is written and the retried launch runs without a watchdog.  The flag
    is cleared when entrypoint fires successfully (normal operation).

    Only activates in embedded mode (``FLET_PLATFORM`` set) — manual
    launches always reach entrypoint immediately.
    """
    if not os.environ.get("FLET_PLATFORM"):
        return

    data_dir = _get_data_dir()
    retried_flag = data_dir / ".bootstrap_retried"

    # Already retried once — no watchdog this time to prevent infinite loop.
    if retried_flag.exists():
        return

    def _watchdog():
        _bootstrap_done.wait(timeout=_BOOTSTRAP_TIMEOUT)
        if _bootstrap_done.is_set():
            return
        _write_startup_log(
            f"bootstrap watchdog: entrypoint did not fire within "
            f"{_BOOTSTRAP_TIMEOUT}s — self-relaunching (once)"
        )
        # Mark that we retried so the next launch skips the watchdog.
        with contextlib.suppress(Exception):
            retried_flag.write_text("1", encoding="utf-8")
        try:
            subprocess.Popen(
                [sys.executable],
                cwd=os.path.dirname(sys.executable) or ".",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
                close_fds=True,
            )
        except Exception:
            _write_startup_log("bootstrap watchdog: self-relaunch FAILED")
            return
        os._exit(0)

    threading.Thread(target=_watchdog, daemon=True).start()


_bootstrap_done = threading.Event()


def main():
    _write_startup_log("python runtime booted — calling ft.run")
    _start_bootstrap_watchdog()
    import flet as ft

    from app import entrypoint

    ft.run(main=entrypoint)


if __name__ == "__main__":
    main()
