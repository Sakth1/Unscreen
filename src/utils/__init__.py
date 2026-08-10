"""Shared utilities for the Unscreen codebase.

Modules must import from sibling submodules directly (e.g. ``utils.paths``)
rather than from the package root to avoid circular imports.
"""

from UI.layout.layout_resolver import app_layout_resolver
from utils.android import get_activity
from utils.bus import TickBus
from utils.constants import (
    ASSET_DIR,
    DEFAULT_PAGE_HEIGHT,
    DEFAULT_PAGE_WIDTH,
    FALLBACK_APP_VERSION,
    LATEST_RELEASE_REPO_URL,
    MIN_PAGE_HEIGHT,
    MIN_PAGE_WIDTH,
    RELEASES_PAGE_URL,
    ROOT_DIR,
)
from utils.files import remove_file, timestamped_filename
from utils.flet_helpers import safe_pop_dialog, safe_update
from utils.models import (
    AppLayout,
    NavigationPattern,
    Orientation,
    OSType,
    RawEvent,
    ScreenFormFactor,
    Tick,
    WatcherConfig,
    WindowHeightClass,
    WindowWidthClass,
)
from utils.net import extract_domain, is_trackable_url, normalize_url
from utils.paths import get_data_dir, get_export_dir
from utils.platform import detect_os, get_winreg, is_android, is_packaged
from utils.time_utils import (
    day_start_ms,
    fmt_timestamp,
    get_current_time_ms,
    utc_now,
    utc_timestamp,
)
from utils.versions import (
    compare_versions,
    get_current_version,
    normalize_version,
    parse_version,
)

__all__ = [
    "ASSET_DIR",
    "AppLayout",
    "DEFAULT_PAGE_HEIGHT",
    "DEFAULT_PAGE_WIDTH",
    "FALLBACK_APP_VERSION",
    "LATEST_RELEASE_REPO_URL",
    "MIN_PAGE_HEIGHT",
    "MIN_PAGE_WIDTH",
    "NavigationPattern",
    "Orientation",
    "OSType",
    "RawEvent",
    "RELEASES_PAGE_URL",
    "ROOT_DIR",
    "ScreenFormFactor",
    "Tick",
    "TickBus",
    "WatcherConfig",
    "WindowHeightClass",
    "WindowWidthClass",
    "app_layout_resolver",
    "compare_versions",
    "day_start_ms",
    "detect_os",
    "extract_domain",
    "fmt_timestamp",
    "get_activity",
    "get_current_time_ms",
    "get_current_version",
    "get_data_dir",
    "get_export_dir",
    "get_winreg",
    "is_android",
    "is_packaged",
    "is_trackable_url",
    "normalize_url",
    "normalize_version",
    "parse_version",
    "remove_file",
    "safe_pop_dialog",
    "safe_update",
    "timestamped_filename",
    "utc_now",
    "utc_timestamp",
]
