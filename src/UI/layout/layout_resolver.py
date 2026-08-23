import logging

from UI.layout.models import (
    AppLayout,
    DialogMetrics,
    DrawerMetrics,
    NavBarMetrics,
    NavigationPattern,
    Orientation,
    ScreenFormFactor,
    SecondaryDrawerMetrics,
    SecondaryNavigationPattern,
    WindowHeightClass,
    WindowWidthClass,
)
from utils.constants import (
    COMPACT_BREAKPOINT,
    COMPACT_HEIGHT_BREAKPOINT,
    EXPANDED_BREAKPOINT,
    EXTENDED_RAIL_MAX_WIDTH,
    EXTENDED_RAIL_MIN_WIDTH,
    LARGE_BREAKPOINT,
    MEDIUM_BREAKPOINT,
    MEDIUM_HEIGHT_BREAKPOINT,
    MIN_PAGE_HEIGHT,
    MIN_PAGE_WIDTH,
    MINI_RAIL_WIDTH,
)

logger = logging.getLogger(__name__)

#: Surface width on desktop — monitors have room for a wider dialog.
_DIALOG_DESKTOP_WIDTH = 500.0

#: Surface width on tablet landscape — mid-range.
_DIALOG_TABLET_LANDSCAPE_WIDTH = 440.0

#: Surface width on tablet portrait / narrow screens.
_DIALOG_NARROW_WIDTH = 420.0

#: Surface height cap for wide form factors; taller content scrolls inside.
_DIALOG_MAX_HEIGHT = 640.0

#: Surface height cap for narrow form factors (phones, portrait tablets).
_MOBILE_MAX_HEIGHT = 600.0

#: Smallest surface height even for one-line notes.
_MIN_SURFACE_HEIGHT = 260.0

#: Fraction of the window height used below the cap.
_DIALOG_HEIGHT_FACTOR = 0.9

#: Mobile gutter factor so the surface never touches the screen edges.
_MOBILE_WIDTH_FACTOR = 0.88

#: Fixed chrome around the notes (header, chips, meta, fixed footer, paddings).
_DIALOG_CHROME_HEIGHT = 280.0


def _resolve_width_class(width: float) -> WindowWidthClass:
    if width < COMPACT_BREAKPOINT:
        return WindowWidthClass.COMPACT
    if width < MEDIUM_BREAKPOINT:
        return WindowWidthClass.MEDIUM
    if width < EXPANDED_BREAKPOINT:
        return WindowWidthClass.EXPANDED
    if width < LARGE_BREAKPOINT:
        return WindowWidthClass.LARGE
    return WindowWidthClass.EXTRA_LARGE


def _resolve_height_class(height: float) -> WindowHeightClass:
    if height < COMPACT_HEIGHT_BREAKPOINT:
        return WindowHeightClass.COMPACT
    if height < MEDIUM_HEIGHT_BREAKPOINT:
        return WindowHeightClass.MEDIUM
    return WindowHeightClass.EXPANDED


def _resolve_orientation(
    width: float, height: float, media_orientation: Orientation | None
) -> Orientation:
    if media_orientation is not None:
        return media_orientation
    return Orientation.LANDSCAPE if width >= height else Orientation.PORTRAIT


def _resolve_form_factor(
    width_class: WindowWidthClass,
    height_class: WindowHeightClass,
    orientation: Orientation,
) -> ScreenFormFactor:
    if width_class is WindowWidthClass.COMPACT:
        return ScreenFormFactor.MOBILE
    if width_class is WindowWidthClass.MEDIUM:
        # Phone landscape: medium width but compact height — stacked layout
        # stays (M3: two-pane layouts are not practical here).
        if height_class is WindowHeightClass.COMPACT:
            return ScreenFormFactor.MOBILE
        return ScreenFormFactor.TABLET_PORTRAIT
    if width_class is WindowWidthClass.EXPANDED:
        return ScreenFormFactor.TABLET_LANDSCAPE
    return ScreenFormFactor.DESKTOP


def _resolve_navigation(form_factor: ScreenFormFactor) -> NavigationPattern:
    match form_factor:
        case ScreenFormFactor.MOBILE:
            return NavigationPattern.BOTTOM_BAR
        case ScreenFormFactor.TABLET_PORTRAIT:
            return NavigationPattern.MINI_RAIL
        case ScreenFormFactor.TABLET_LANDSCAPE | ScreenFormFactor.DESKTOP:
            return NavigationPattern.EXTENDED_RAIL
        case _:
            return NavigationPattern.BOTTOM_BAR


def _resolve_secondary_navigation(
    form_factor: ScreenFormFactor,
) -> SecondaryNavigationPattern:
    match form_factor:
        case ScreenFormFactor.MOBILE:
            return SecondaryNavigationPattern.INLINE
        case ScreenFormFactor.TABLET_PORTRAIT:
            return SecondaryNavigationPattern.INLINE
        case ScreenFormFactor.TABLET_LANDSCAPE:
            return SecondaryNavigationPattern.SIDE_PANEL
        case ScreenFormFactor.DESKTOP:
            return SecondaryNavigationPattern.SIDE_PANEL
        case _:
            return SecondaryNavigationPattern.INLINE


def _resolve_padding(form_factor: ScreenFormFactor) -> float:
    match form_factor:
        case ScreenFormFactor.MOBILE:
            return 12
        case ScreenFormFactor.TABLET_PORTRAIT:
            return 16
        case ScreenFormFactor.TABLET_LANDSCAPE:
            return 20
        case ScreenFormFactor.DESKTOP:
            return 24
        case _:
            return 16


def _resolve_content_max_width(form_factor: ScreenFormFactor) -> float:
    match form_factor:
        case ScreenFormFactor.MOBILE | ScreenFormFactor.TABLET_PORTRAIT:
            return 0.0  # unconstrained: screens use the full width
        case ScreenFormFactor.TABLET_LANDSCAPE:
            return 1000
        case ScreenFormFactor.DESKTOP:
            return 1200
        case _:
            return 0.0


def _resolve_spacing(form_factor: ScreenFormFactor) -> float:
    match form_factor:
        case ScreenFormFactor.MOBILE:
            return 4
        case ScreenFormFactor.TABLET_PORTRAIT:
            return 4
        case ScreenFormFactor.TABLET_LANDSCAPE:
            return 8
        case ScreenFormFactor.DESKTOP:
            return 8
        case _:
            return 4


def resolve_drawer_metrics(
    navigation: NavigationPattern, width: float, padding: float
) -> DrawerMetrics:
    """Derive drawer/rail metrics from the navigation pattern and viewport.

    Mini rail (tablet portrait): fixed narrow width — icons only, labels
    hidden. Extended rail (tablet landscape and desktop): width scales with
    the viewport between :data:`EXTENDED_RAIL_MIN_WIDTH` and
    :data:`EXTENDED_RAIL_MAX_WIDTH`, with a roomier destination padding on
    wide layouts.
    """
    if navigation is NavigationPattern.MINI_RAIL:
        return DrawerMetrics(
            width=float(MINI_RAIL_WIDTH),
            destination_padding=8.0,
            item_spacing=4.0,
        )

    width = min(max(width * 0.22, EXTENDED_RAIL_MIN_WIDTH), EXTENDED_RAIL_MAX_WIDTH)
    wide = padding >= 20  # tablet landscape / desktop spacing scale
    return DrawerMetrics(
        width=float(width),
        destination_padding=12.0 if wide else 8.0,
        item_spacing=8.0 if wide else 4.0,
    )


def resolve_secondary_drawer_metrics(
    navigation: SecondaryNavigationPattern, width: float, padding: float
) -> SecondaryDrawerMetrics:
    """Derive secondary side-panel metrics from the navigation pattern and viewport.

    Only side-panel form factors (tablet landscape and desktop) render a real
    panel; inline sections render no panel, so their metrics are zeroed.
    """
    if navigation is SecondaryNavigationPattern.INLINE:
        return SecondaryDrawerMetrics(
            width=0.0,
            destination_padding=8.0,
            item_spacing=4.0,
        )

    width = min(max(width * 0.18, EXTENDED_RAIL_MIN_WIDTH), EXTENDED_RAIL_MAX_WIDTH)
    wide = padding >= 20
    return SecondaryDrawerMetrics(
        width=float(width),
        destination_padding=12.0 if wide else 8.0,
        item_spacing=8.0 if wide else 4.0,
    )


def resolve_navbar_metrics(
    safe_padding: tuple[float, float, float, float],
    width: float,
    height_class: WindowHeightClass,
) -> NavBarMetrics:
    """Derive floating bottom bar metrics from the viewport and safe insets.

    The bottom margin always clears the system gesture area (safe inset),
    so the pill never collides with the Android navigation bar. Phone
    landscape (compact height) sits lower and wider.
    """
    _, _, _, safe_bottom = safe_padding
    compact_height = height_class is WindowHeightClass.COMPACT
    wide = width >= COMPACT_BREAKPOINT

    margin_h = 24.0 if wide else 16.0
    margin_bottom = (16.0 if compact_height else 24.0) + safe_bottom
    destination_padding = 10.0 if wide else 8.0

    return NavBarMetrics(
        margin_left=margin_h,
        margin_right=margin_h,
        margin_bottom=margin_bottom,
        destination_padding=destination_padding,
        item_spacing=4.0,
    )


def resolve_dialog_metrics(
    form_factor: ScreenFormFactor, width: float, height: float
) -> DialogMetrics:
    """Derive update-dialog surface limits from the form factor and viewport.

    Desktop gets the widest surface (500px); tablet landscape gets 440px;
    narrower form factors scale with the viewport so the surface never
    touches the screen edges.  The height cap is form-factor relative and
    window relative (90% of the window height); the fixed chrome around
    the notes stays constant.
    """
    if form_factor == ScreenFormFactor.DESKTOP:
        surface_width = _DIALOG_DESKTOP_WIDTH
    elif form_factor == ScreenFormFactor.TABLET_LANDSCAPE:
        surface_width = _DIALOG_TABLET_LANDSCAPE_WIDTH
    else:
        surface_width = min(_DIALOG_NARROW_WIDTH, width * _MOBILE_WIDTH_FACTOR)

    wide = form_factor in (ScreenFormFactor.TABLET_LANDSCAPE, ScreenFormFactor.DESKTOP)
    max_height = _DIALOG_MAX_HEIGHT if wide else _MOBILE_MAX_HEIGHT
    return DialogMetrics(
        width=float(surface_width),
        max_height=float(min(max_height, height * _DIALOG_HEIGHT_FACTOR)),
        min_height=_MIN_SURFACE_HEIGHT,
        chrome_height=_DIALOG_CHROME_HEIGHT,
    )


def app_layout_resolver(
    page_width: float,
    page_height: float,
    *,
    media=None,
    is_mobile: bool = False,
    **kwargs,
) -> AppLayout:
    """Return a responsive layout tuned to the available viewport.

    Classifies the viewport into Material 3 window size classes (width and
    height separately), derives a :class:`ScreenFormFactor`, and resolves the
    design metrics (padding, spacing, content cap, navigation pattern) that
    the shell and navigation controls consume.

    Args:
        page_width: Current page width reported by Flet.
        page_height: Current page height reported by Flet.
        media: Optional ``page.media`` object exposing ``orientation`` and
            ``padding`` (left, top, right, bottom system insets). ``None`` is
            tolerated for headless runs and early page loads.
        is_mobile: True when the app runs on a phone platform. Phones always
            use the mobile form factor (bottom bar + inline sections); the
            reported window size is unreliable there (mobile windows report
            no size), so it must never reclassify a phone as a tablet or
            desktop layout.

    Returns:
        A complete immutable layout snapshot for the current page size.
    """

    width_raw = float(page_width or 0)
    height_raw = float(page_height or 0)
    logger.debug("Resolving app layout: width=%s, height=%s", width_raw, height_raw)

    # Classify on the true window size (falling back to minimums only when the
    # viewport is still reporting 0); clamp only the stored values so a real
    # phone-landscape window (e.g. 700x400) is not reclassified.
    width_class = _resolve_width_class(width_raw or MIN_PAGE_WIDTH)
    height_class = _resolve_height_class(height_raw or MIN_PAGE_HEIGHT)
    width = max(width_raw, MIN_PAGE_WIDTH)
    height = max(height_raw, MIN_PAGE_HEIGHT)

    media_orientation = None
    if media is not None:
        media_orientation = getattr(media, "orientation", None)
    orientation = _resolve_orientation(width, height, media_orientation)

    form_factor = (
        ScreenFormFactor.MOBILE
        if is_mobile
        else _resolve_form_factor(width_class, height_class, orientation)
    )

    safe_padding = (0.0, 0.0, 0.0, 0.0)
    if media is not None:
        padding = getattr(media, "padding", None)
        if padding is not None:
            safe_padding = (
                getattr(padding, "left", 0.0) or 0.0,
                getattr(padding, "top", 0.0) or 0.0,
                getattr(padding, "right", 0.0) or 0.0,
                getattr(padding, "bottom", 0.0) or 0.0,
            )

    padding: float = _resolve_padding(form_factor)
    navigation: NavigationPattern = _resolve_navigation(form_factor)
    secondary_navigation: SecondaryNavigationPattern = _resolve_secondary_navigation(
        form_factor
    )
    content_max_width: float = _resolve_content_max_width(form_factor)
    spacing: float = _resolve_spacing(form_factor)
    drawer_metrics: DrawerMetrics = resolve_drawer_metrics(navigation, width, padding)
    secondary_navigation_metrics: SecondaryDrawerMetrics = (
        resolve_secondary_drawer_metrics(secondary_navigation, width, padding)
    )
    nav_bar_metrics: NavBarMetrics = resolve_navbar_metrics(
        safe_padding, width, height_class
    )
    dialog_metrics: DialogMetrics = resolve_dialog_metrics(form_factor, width, height)

    return AppLayout(
        screen_form_factor=form_factor,
        width=width,
        height=height,
        padding=padding,
        orientation=orientation,
        width_class=width_class,
        height_class=height_class,
        navigation=navigation,
        secondary_navigation=secondary_navigation,
        safe_padding=safe_padding,
        content_max_width=content_max_width,
        spacing=spacing,
        drawer_metrics=drawer_metrics,
        secondary_navigation_metrics=secondary_navigation_metrics,
        nav_bar_metrics=nav_bar_metrics,
        dialog_metrics=dialog_metrics,
    )
