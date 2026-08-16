import logging
import os
import struct
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from utils.net import is_trackable_url, normalize_url

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    url: str | None
    method: str | None  # "uia", "snss", or None
    confidence: str = "high"


BROWSER_TO_DISCOVERY_KEY = {
    "Brave": "brave",
    "Chrome": "chrome",
    "Edge": "edge",
    "Firefox": "firefox",
    "Opera": "opera",
    "Vivaldi": "vivaldi",
}

BROWSER_SESSION_PATHS = {
    "brave": os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "BraveSoftware",
        "Brave-Browser",
        "User Data",
    ),
    "chrome": os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
    ),
    "edge": os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"
    ),
    "firefox": os.path.join(
        os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles"
    ),
    "opera": os.path.join(
        os.environ.get("APPDATA", ""), "Opera Software", "Opera Stable"
    ),
    "vivaldi": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Vivaldi", "User Data"),
}

SNSS_MAGIC = b"SNSS"
CMD_TAB_WINDOW = 0
CMD_UPDATE_NAV = 6
CMD_SEL_NAV_INDEX = 7
CMD_SEL_TAB = 8
CMD_SET_ACTIVE_WINDOW = 20


def _align4(pos: int) -> int:
    return (pos + 3) & ~3


def _read_string(data: bytes, pos: int) -> tuple[str | None, int]:
    if pos + 4 > len(data):
        return None, pos
    length = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if length == 0 or length > 500000 or pos + length > len(data):
        return None, pos
    try:
        s = data[pos : pos + length].decode("utf-8", errors="replace")
    except Exception:
        s = None
    pos = _align4(pos + length)
    return s, pos


def _read_string16(data: bytes, pos: int) -> tuple[str | None, int]:
    if pos + 4 > len(data):
        return None, pos
    length = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if length == 0 or length > 250000 or pos + length * 2 > len(data):
        return None, pos
    try:
        s = data[pos : pos + length * 2].decode("utf-16-le", errors="replace")
    except Exception:
        s = None
    pos = _align4(pos + length * 2)
    return s, pos


def _parse_session_file(path: str | os.PathLike) -> list[dict]:
    data = Path(path).read_bytes()
    if len(data) < 8 or data[:4] != SNSS_MAGIC:
        return []

    version = struct.unpack_from("<I", data, 4)[0]
    if version not in (1, 3):
        return []

    pos = 8
    tabs: dict[int, dict] = {}
    windows: dict[int, list[int]] = {}
    selected_tabs: dict[int, int] = {}
    active_window_id: int | None = None

    while pos + 3 <= len(data):
        cmd_size = struct.unpack_from("<h", data, pos)[0]
        if cmd_size <= 0:
            pos += 2
            continue
        cmd_id = data[pos + 2]
        payload = data[pos + 3 : pos + cmd_size + 2]
        pos += cmd_size + 2

        if cmd_id == CMD_TAB_WINDOW and len(payload) >= 8:
            tab_id = struct.unpack_from("<i", payload, 0)[0]
            window_id = struct.unpack_from("<i", payload, 4)[0]
            windows.setdefault(window_id, []).append(tab_id)

        elif cmd_id == CMD_UPDATE_NAV and len(payload) >= 20:
            p = 4
            tab_id = struct.unpack_from("<i", payload, p)[0]
            p += 4
            nav_index = struct.unpack_from("<i", payload, p)[0]
            p += 4
            url, p = _read_string(payload, p)
            title, _p = _read_string16(payload, p)
            if url is None:
                continue
            tabs[tab_id] = {"url": url, "title": title, "nav_index": nav_index}

        elif cmd_id == CMD_SEL_TAB and len(payload) >= 8:
            window_id = struct.unpack_from("<i", payload, 0)[0]
            tab_index = struct.unpack_from("<i", payload, 4)[0]
            selected_tabs[window_id] = tab_index

        elif cmd_id == CMD_SET_ACTIVE_WINDOW and len(payload) >= 4:
            active_window_id = struct.unpack_from("<i", payload, 0)[0]

    result = []
    for win_id, tab_ids in windows.items():
        sel_index = selected_tabs.get(win_id, 0)
        if sel_index < len(tab_ids):
            tab_id = tab_ids[sel_index]
            info = tabs.get(tab_id)
            if info:
                is_active = win_id == active_window_id
                result.append(
                    {
                        "window_id": win_id,
                        "tab_id": tab_id,
                        "url": info["url"],
                        "title": info["title"],
                        "nav_index": info["nav_index"],
                        "is_active_window": is_active,
                    }
                )
    return result


class UrlExtractor:
    def __init__(self, max_stale_s: float = 300):
        self._max_stale_s = max_stale_s

    def extract(
        self,
        browser_name: str,
        window_title: str | None = None,
        window_pid: int | None = None,
    ) -> ExtractionResult:
        url = self._try_uia(browser_name, window_title, window_pid)
        if url:
            logger.debug("URL extracted via UIA: %s", url)
            return ExtractionResult(url=url, method="uia")

        url = self._try_session_files(browser_name)
        if url:
            logger.debug("URL extracted via session files: %s", url)
            return ExtractionResult(url=url, method="snss")

        return ExtractionResult(url=None, method=None, confidence="low")

    def _try_uia(
        self,
        browser_name: str,
        window_title: str | None = None,
        window_pid: int | None = None,
    ) -> str | None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                from pywinauto import Application
        except ImportError:
            return None

        addr_name = {
            "Brave": "Address and search bar",
            "Chrome": "Address and search bar",
            "Edge": "Address and search bar",
            "Firefox": "Search or enter address",
            "Opera": "Address field",
            "Vivaldi": "Search or enter an address",
        }.get(browser_name)
        if addr_name is None:
            return None

        if window_title is not None:
            if browser_name.lower() not in window_title.lower():
                return None
            fg_pid = window_pid
        else:
            fg = self._get_foreground_window()
            if fg is None:
                return None
            fg_pid, fg_title = fg
            if browser_name.lower() not in fg_title.lower():
                return None

        if fg_pid is None:
            return None

        try:
            app = Application(backend="uia").connect(process=fg_pid)
            dlg = app.top_window()
            for e in dlg.descendants(control_type="Edit"):
                try:
                    if e.element_info.name == addr_name:
                        url = e.get_value()
                        url = normalize_url(url) if url else None
                        if url and is_trackable_url(url):
                            return url
                except Exception:
                    continue
        except Exception:
            logger.debug("UIA address-bar probe failed for pid %d", fg_pid)
        return None

    @staticmethod
    def _get_foreground_window() -> tuple[int, str] | None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            length = user32.GetWindowTextLengthW(handle) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(handle, buf, length)
            title = buf.value or ""
            return pid.value, title
        except Exception:
            return None

    def _try_session_files(self, browser_name: str) -> str | None:
        key = BROWSER_TO_DISCOVERY_KEY.get(browser_name)
        if key is None:
            return None

        if key == "firefox":
            return self._try_firefox_session()
        return self._try_chromium_session(key)

    def _try_chromium_session(self, key: str) -> str | None:
        base = BROWSER_SESSION_PATHS.get(key)
        if not base or not os.path.isdir(base):
            return None

        local_state = os.path.join(base, "Local State")
        profiles = ["Default"]
        if os.path.isfile(local_state):
            try:
                import json

                with open(local_state, encoding="utf-8") as f:
                    data = json.load(f)
                info = data.get("profile", {}).get("info_cache", {})
                for prof in info:
                    profiles.append(prof)
            except Exception:
                logger.debug("Failed to read 'Local State' of %s", key)

        seen_dirs: set[str] = set()
        for prof in profiles:
            sess_dir = os.path.join(base, prof, "Sessions")
            if not os.path.isdir(sess_dir) or sess_dir in seen_dirs:
                continue
            seen_dirs.add(sess_dir)
            url = self._read_chromium_sessions(sess_dir)
            if url:
                return url
        return None

    def _read_chromium_sessions(self, session_dir: str) -> str | None:
        sess_path = Path(session_dir)
        session_files = sorted(
            sess_path.glob("Session_*"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not session_files:
            return None

        tabs_info = []
        for sf in session_files:
            if sf.stat().st_size == 0:
                continue
            if not self._is_recent(sf):
                continue
            try:
                entries = _parse_session_file(str(sf))
                tabs_info.extend(entries)
            except Exception:
                continue
            if tabs_info:
                break

        active_tabs = [t for t in tabs_info if t["is_active_window"]]
        if active_tabs:
            active_tabs.sort(key=lambda t: t["window_id"])
            url = active_tabs[0]["url"]
            if is_trackable_url(url):
                return url

        url = self._try_chromium_tabs_file(session_dir)
        if url:
            return url

        if tabs_info:
            url = tabs_info[0]["url"]
            if is_trackable_url(url):
                return url

        return None

    def _try_chromium_tabs_file(self, session_dir: str) -> str | None:
        sess_path = Path(session_dir)
        tabs_files = sorted(
            sess_path.glob("Tabs_*"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for tf in tabs_files:
            if tf.stat().st_size == 0:
                continue
            if not self._is_recent(tf):
                continue
            try:
                entries = _parse_session_file(str(tf))
                active = [t for t in entries if t["is_active_window"]]
                if active:
                    active.sort(key=lambda t: t["window_id"])
                    url = active[0]["url"]
                    if is_trackable_url(url):
                        return url
                if entries:
                    url = entries[0]["url"]
                    if is_trackable_url(url):
                        return url
            except Exception:
                continue
        return None

    def _try_firefox_session(self) -> str | None:
        base = BROWSER_SESSION_PATHS.get("firefox")
        if not base or not os.path.isdir(base):
            return None

        try:
            import lz4.block
        except ImportError:
            return None

        for prof_dir in Path(base).iterdir():
            if not prof_dir.is_dir():
                continue
            backups = prof_dir / "sessionstore-backups"
            if not backups.is_dir():
                continue
            for name in ("recovery.jsonlz4", "recovery.baklz4", "previous.jsonlz4"):
                rpath = backups / name
                if not rpath.is_file():
                    continue
                if not self._is_recent(rpath):
                    continue
                try:
                    raw = rpath.read_bytes()
                    if raw[:8] != b"mozLz40\0":
                        continue
                    decompressed = lz4.block.decompress(raw[8:])
                    import json

                    session = json.loads(decompressed)
                    tabs_info = self._parse_firefox_session(session)
                    for t in tabs_info:
                        if (
                            t["is_active_window"]
                            and t["is_selected_tab"]
                            and is_trackable_url(t["url"])
                        ):
                            return t["url"]
                    if tabs_info:
                        url = tabs_info[0]["url"]
                        if is_trackable_url(url):
                            return url
                except Exception:
                    continue
        return None

    def _parse_firefox_session(self, session: dict) -> list[dict]:
        tabs_info: list[dict] = []
        windows = session.get("windows", [])
        active_win_index = session.get("selectedWindow", 0)

        for win_index, win in enumerate(windows):
            sel_tab_index = win.get("selected", 1) - 1
            tabs = win.get("tabs", [])
            for tab_index, tab in enumerate(tabs):
                entries = tab.get("entries", [])
                entry_index = tab.get("index", 1) - 1
                if 0 <= entry_index < len(entries):
                    entry = entries[entry_index]
                    tabs_info.append(
                        {
                            "window_index": win_index,
                            "tab_index": tab_index,
                            "is_active_window": win_index == active_win_index,
                            "is_selected_tab": tab_index == sel_tab_index,
                            "url": entry.get("url"),
                            "title": entry.get("title"),
                        }
                    )

        active = [
            t for t in tabs_info if t["is_active_window"] and t["is_selected_tab"]
        ]
        if active:
            return active
        active_win = [t for t in tabs_info if t["is_active_window"]]
        if active_win:
            return active_win
        return tabs_info

    def _is_recent(self, path: Path) -> bool:
        try:
            return time.time() - path.stat().st_mtime < self._max_stale_s
        except OSError:
            return False
