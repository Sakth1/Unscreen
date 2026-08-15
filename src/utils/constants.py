import os
from pathlib import Path

#: Absolute path to the ``src`` directory; used as the anchor for bundled assets.
ROOT_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directory containing app images, icons, fonts, and chess piece artwork.
ASSET_DIR = Path(ROOT_DIR, "assets")


# ── Layout defaults (pixels) ────────────────────────────────────────────────

#: Fallback page width used when the Flet viewport reports ``0``.
DEFAULT_PAGE_WIDTH = 960

#: Fallback page height used when the Flet viewport reports ``0``.
DEFAULT_PAGE_HEIGHT = 800

#: Phone-size defaults used when a mobile platform reports no window size.
MOBILE_DEFAULT_WIDTH = 400

#: Phone-size default height used when a mobile platform reports no window size.
MOBILE_DEFAULT_HEIGHT = 800

#: Minimum viewport width enforced after safe-area padding is subtracted.
MIN_PAGE_WIDTH = 320.0

#: Minimum viewport height enforced after safe-area padding is subtracted.
MIN_PAGE_HEIGHT = 480.0

# ── Responsive breakpoints (Material 3 window size classes) ──────────────────

#: Width below which the window is classified as compact (phones portrait).
COMPACT_BREAKPOINT = 600

#: Width below which the window is classified as medium (tablets portrait).
MEDIUM_BREAKPOINT = 840

#: Width below which the window is classified as expanded (tablets landscape).
EXPANDED_BREAKPOINT = 1200

#: Width below which the window is classified as large.
LARGE_BREAKPOINT = 1600

#: Height below which the window is classified as compact-height (phone landscape).
COMPACT_HEIGHT_BREAKPOINT = 480

#: Height below which the window is classified as medium-height.
MEDIUM_HEIGHT_BREAKPOINT = 900


# ── Other constants ──────────────────────────────────────────────────────────

#: Width of the collapsed mini rail (icon-only, tablet portrait).
MINI_RAIL_WIDTH = 60

#: Lower bound of the extended drawer width (tablet landscape).
EXTENDED_RAIL_MIN_WIDTH = 120

#: Upper bound of the extended drawer width (desktop).
EXTENDED_RAIL_MAX_WIDTH = 200

#: GitHub API endpoint returning the latest release of the app repository.
LATEST_RELEASE_REPO_URL = "https://api.github.com/repos/sakth1/Unscreen/releases/latest"

#: GitHub API endpoint listing releases (stable and prereleases, newest first).
RELEASES_REPO_URL = "https://api.github.com/repos/sakth1/Unscreen/releases?per_page=100"

#: Human-readable releases page, used as the manual-update fallback.
RELEASES_PAGE_URL = "https://github.com/sakth1/Unscreen/releases/latest"

#: Version reported when package metadata is unavailable (e.g. unbundled runs).
FALLBACK_APP_VERSION = "0.4.10-dev1"
