import logging
import os
import subprocess
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _write_startup_log(message: str) -> None:
    """Write a timestamped line to the startup log file.

    Mirrors app._write_startup_log so we can log before importing app.
    """
    from pathlib import Path

    try:
        data_dir = Path(os.environ.get("UNSCREEN_DATA_DIR") or ".")
        log_file = data_dir / "startup.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


_bootstrap_done = threading.Event()
_BOOTSTRAP_TIMEOUT = 30
_APP_START_TIME = time.monotonic()

_CREATE_NO_WINDOW = 0x08000000


def _start_bootstrap_watchdog() -> None:
    """If entrypoint hasn't fired within _BOOTSTRAP_TIMEOUT seconds, spawn a
    fresh copy of the exe and exit.  The stuck process's window disappears;
    the new one appears — worst case the user sees a brief flash.

    Safety notes:
    - Only fires in embedded mode (FLET_PLATFORM set) — manual launches
      always reach entrypoint immediately.
    - The stuck process never acquired the mutex (App.__init__ never ran),
      so the fresh instance's mutex acquisition succeeds.
    - 30s threshold is far above normal handshake time (~1-2s) but below
      user-noticeable delay.
    """
    if not os.environ.get("FLET_PLATFORM"):
        return

    def _watchdog():
        _bootstrap_done.wait(timeout=_BOOTSTRAP_TIMEOUT)
        if _bootstrap_done.is_set():
            return
        _write_startup_log(
            f"bootstrap watchdog: entrypoint did not fire within "
            f"{_BOOTSTRAP_TIMEOUT}s — self-relaunching"
        )
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


def main():
    _write_startup_log("python runtime booted — calling ft.run")
    _start_bootstrap_watchdog()
    import flet as ft

    from app import entrypoint

    ft.run(main=entrypoint)


if __name__ == "__main__":
    main()
