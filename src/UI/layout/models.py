"""Models owned by the UI layer: responsive layout and navigation chrome.

These describe how the shell sizes and arranges navigation controls for the
current window; nothing outside ``UI`` should need them. The layout resolver
(``UI.layout.layout_resolver``) produces :class:`AppLayout` instances.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ScreenFormFactor(Enum):
    UNKNOWN = 0
    MOBILE = 1
    TABLET_PORTRAIT = 2
    TABLET_LANDSCAPE = 3
    DESKTOP = 4


class WindowWidthClass(Enum):
    """Material 3 width window size classes."""

    COMPACT = 0  # < 600dp  — phones portrait
    MEDIUM = 1  # 600-839dp — tablets portrait, foldables
    EXPANDED = 2  # 840-1199dp — tablets landscape
    LARGE = 3  # 1200-1599dp — large tablets / small desktops
    EXTRA_LARGE = 4  # >= 1600dp — desktops


class WindowHeightClass(Enum):
    """Material 3 height window size classes."""

    COMPACT = 0  # < 480dp — phones landscape
    MEDIUM = 1  # 480-899dp — phones portrait, tablets landscape
    EXPANDED = 2  # >= 900dp — tablets portrait


class NavigationPattern(Enum):
    """Which navigation chrome fits the current window size."""

    BOTTOM_BAR = 0
    MINI_RAIL = 1
    EXTENDED_RAIL = 2


class SecondaryNavigationPattern(Enum):
    """Which navigation chrome fits the current window size."""

    SIDE_PANEL = 0
    INLINE = 1


class Orientation(Enum):
    PORTRAIT = 0
    LANDSCAPE = 1


@dataclass(frozen=True)
class DrawerMetrics:
    """Numbers the custom drawer needs for the current layout."""

    width: float
    destination_padding: float
    item_spacing: float


@dataclass(frozen=True)
class SecondaryDrawerMetrics:
    """Numbers the secondary side panel needs for the current layout."""

    width: float
    destination_padding: float
    item_spacing: float


@dataclass(frozen=True)
class NavigationChangeData:
    """Payload attached to navigation ``on_change`` events.

    Identifies the destination selected in a custom navigation control
    (bottom bar or drawer) when its ``on_change`` fires.
    """

    index: int
    label: str = ""


@dataclass(frozen=True)
class SecondaryNavigationChangeData:
    """Payload attached to ``SecondaryNavigationPanel.on_change`` events.

    Identifies the section destination selected in the in-screen secondary
    navigation panel; ``route`` is the sub-route it owns.
    """

    index: int
    label: str = ""
    route: str = ""


@dataclass(frozen=True)
class NavigationDestination:
    """One top-level navigation destination.

    Single source of truth for a main-nav entry: the route it owns, the
    label shown in the bar/rail, the icon, and the screen instance rendered
    for it.
    """

    route: str
    label: str
    icon: str
    view: Any


@dataclass(frozen=True)
class NavBarMetrics:
    """Numbers the floating bottom navigation bar needs for the current layout."""

    margin_left: float
    margin_right: float
    margin_bottom: float
    destination_padding: float
    item_spacing: float


@dataclass(frozen=True)
class DialogMetrics:
    """Window-derived limits the custom update dialog needs for the current layout.

    The dialog composes its content-aware surface height (fixed chrome plus
    the estimated release-notes height) inside ``max_height`` / ``min_height``;
    ``width`` is the form-factor surface width.
    """

    width: float
    max_height: float
    min_height: float
    chrome_height: float


@dataclass(frozen=True)
class AppLayout:
    """Resolved responsive metrics for the current page size.

    The dataclass is frozen because it represents one layout calculation. When
    the page changes size, callers ask :func:`app_layout_resolver` for a new
    instance and pass it to each control.

    ``safe_padding`` mirrors the system insets (notch, status bar, gesture
    navigation bar, keyboard) reported by ``page.media``; design spacing lives
    in ``padding`` and ``spacing``. Both are consumed by the shell and the
    custom navigation controls to derive their margins automatically.
    """

    screen_form_factor: ScreenFormFactor
    width: float
    height: float
    drawer_metrics: DrawerMetrics
    secondary_navigation_metrics: SecondaryDrawerMetrics
    nav_bar_metrics: NavBarMetrics
    dialog_metrics: DialogMetrics
    padding: float = 16.0
    orientation: Orientation = Orientation.LANDSCAPE
    width_class: WindowWidthClass = WindowWidthClass.EXTRA_LARGE
    height_class: WindowHeightClass = WindowHeightClass.MEDIUM
    navigation: NavigationPattern = NavigationPattern.EXTENDED_RAIL
    secondary_navigation: SecondaryNavigationPattern = (
        SecondaryNavigationPattern.SIDE_PANEL
    )
    safe_padding: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    content_max_width: float = 0.0
    spacing: float = 4.0
