"""Table-driven tests for the responsive layout resolver and its metrics.

The resolver is a pure function: width/height (+ optional ``media``) in,
an immutable :class:`AppLayout` out. These tests pin the M3 window size
class mapping, the orientation fallback, safe-area merging, and the design
metrics (padding, spacing, content cap, navigation pattern).
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from UI.layout.layout_resolver import app_layout_resolver
from UI.layout.models import (
    AppLayout,
    NavigationPattern,
    Orientation,
    ScreenFormFactor,
    SecondaryNavigationPattern,
    WindowHeightClass,
    WindowWidthClass,
)


def _media(
    orientation: Orientation | None = None,
    *,
    left: float = 0,
    top: float = 0,
    right: float = 0,
    bottom: float = 0,
):
    if orientation is None:
        return None
    return SimpleNamespace(
        orientation=orientation,
        padding=SimpleNamespace(left=left, top=top, right=right, bottom=bottom),
    )


class TestWidthAndHeightClasses:
    @pytest.mark.parametrize(
        ("width", "expected"),
        [
            (320, WindowWidthClass.COMPACT),
            (599, WindowWidthClass.COMPACT),
            (600, WindowWidthClass.MEDIUM),
            (839, WindowWidthClass.MEDIUM),
            (840, WindowWidthClass.EXPANDED),
            (1199, WindowWidthClass.EXPANDED),
            (1200, WindowWidthClass.LARGE),
            (1599, WindowWidthClass.LARGE),
            (1600, WindowWidthClass.EXTRA_LARGE),
        ],
    )
    def test_width_class_boundaries(self, width, expected):
        layout = app_layout_resolver(width, 800)
        assert layout.width_class is expected

    @pytest.mark.parametrize(
        ("height", "expected"),
        [
            (320, WindowHeightClass.COMPACT),
            (479, WindowHeightClass.COMPACT),
            (480, WindowHeightClass.MEDIUM),
            (899, WindowHeightClass.MEDIUM),
            (900, WindowHeightClass.EXPANDED),
        ],
    )
    def test_height_class_boundaries(self, height, expected):
        layout = app_layout_resolver(800, height)
        assert layout.height_class is expected


class TestFormFactorMapping:
    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            (400, 800, ScreenFormFactor.MOBILE),  # phone portrait
            (320, 480, ScreenFormFactor.MOBILE),  # tiny window
            (700, 400, ScreenFormFactor.MOBILE),  # phone landscape (compact height)
            (599, 900, ScreenFormFactor.MOBILE),  # compact width, tall
            (800, 1280, ScreenFormFactor.TABLET_PORTRAIT),  # tablet portrait
            (700, 800, ScreenFormFactor.TABLET_PORTRAIT),  # small tablet portrait
            (960, 600, ScreenFormFactor.TABLET_LANDSCAPE),  # tablet landscape
            (1000, 700, ScreenFormFactor.TABLET_LANDSCAPE),  # tablet landscape
            (1280, 800, ScreenFormFactor.DESKTOP),  # laptop
            (1920, 1080, ScreenFormFactor.DESKTOP),  # desktop
        ],
    )
    def test_form_factor(self, width, height, expected):
        layout = app_layout_resolver(width, height)
        assert layout.screen_form_factor is expected


class TestOrientation:
    def test_derived_portrait(self):
        layout = app_layout_resolver(800, 1280)
        assert layout.orientation is Orientation.PORTRAIT

    def test_derived_landscape(self):
        layout = app_layout_resolver(1280, 800)
        assert layout.orientation is Orientation.LANDSCAPE

    def test_square_defaults_to_landscape(self):
        layout = app_layout_resolver(800, 800)
        assert layout.orientation is Orientation.LANDSCAPE

    def test_media_orientation_wins_over_aspect_ratio(self):
        layout = app_layout_resolver(1280, 800, media=_media(Orientation.PORTRAIT))
        assert layout.orientation is Orientation.PORTRAIT


class TestNavigationPattern:
    def test_mobile_gets_bottom_bar(self):
        assert app_layout_resolver(400, 800).navigation is NavigationPattern.BOTTOM_BAR

    def test_phone_landscape_keeps_bottom_bar(self):
        assert app_layout_resolver(700, 400).navigation is NavigationPattern.BOTTOM_BAR

    def test_tablet_portrait_gets_mini_rail(self):
        assert app_layout_resolver(800, 1280).navigation is NavigationPattern.MINI_RAIL

    def test_tablet_landscape_gets_extended_rail(self):
        assert (
            app_layout_resolver(960, 600).navigation is NavigationPattern.EXTENDED_RAIL
        )

    def test_desktop_gets_extended_rail(self):
        assert (
            app_layout_resolver(1280, 800).navigation is NavigationPattern.EXTENDED_RAIL
        )

    def test_is_mobile_forces_mobile_on_wide_viewport(self):
        layout = app_layout_resolver(1280, 800, is_mobile=True)
        assert layout.screen_form_factor is ScreenFormFactor.MOBILE
        assert layout.navigation is NavigationPattern.BOTTOM_BAR
        assert layout.secondary_navigation is SecondaryNavigationPattern.INLINE
        assert layout.padding == 12

    def test_is_mobile_when_window_reports_no_size(self):
        layout = app_layout_resolver(None, None, is_mobile=True)
        assert layout.screen_form_factor is ScreenFormFactor.MOBILE
        assert layout.navigation is NavigationPattern.BOTTOM_BAR

    def test_default_is_size_driven_not_platform_forced(self):
        layout = app_layout_resolver(1280, 800)
        assert layout.screen_form_factor is ScreenFormFactor.DESKTOP


class TestDesignMetrics:
    @pytest.mark.parametrize(
        ("width", "height", "padding", "spacing", "content_max"),
        [
            (400, 800, 12, 4, 0.0),  # mobile: full width
            (800, 1280, 16, 4, 0.0),  # tablet portrait: full width
            (960, 600, 20, 8, 1000),  # tablet landscape: capped
            (1280, 800, 24, 8, 1200),  # desktop: capped
        ],
    )
    def test_metric_scale(self, width, height, padding, spacing, content_max):
        layout = app_layout_resolver(width, height)
        assert layout.padding == padding
        assert layout.spacing == spacing
        assert layout.content_max_width == content_max


class TestSafePadding:
    def test_media_padding_is_merged(self):
        layout = app_layout_resolver(
            400,
            800,
            media=_media(Orientation.PORTRAIT, left=10, top=24, right=10, bottom=48),
        )
        assert layout.safe_padding == (10, 24, 10, 48)

    def test_zero_safe_padding_without_media(self):
        layout = app_layout_resolver(400, 800)
        assert layout.safe_padding == (0.0, 0.0, 0.0, 0.0)

    def test_missing_media_attributes_tolerated(self):
        layout = app_layout_resolver(400, 800, media=SimpleNamespace(padding=None))
        assert layout.safe_padding == (0.0, 0.0, 0.0, 0.0)


class TestEdgeCases:
    def test_zero_dimensions_clamped_to_minimums(self):
        layout = app_layout_resolver(0, 0)
        assert layout.width == 320
        assert layout.height == 480

    def test_none_dimensions_tolerated(self):
        layout = app_layout_resolver(None, None)
        assert layout.width == 320
        assert layout.height == 480

    def test_returns_frozen_dataclass(self):
        layout = app_layout_resolver(400, 800)
        assert isinstance(layout, AppLayout)
        with pytest.raises(dataclasses.FrozenInstanceError):
            layout.width = 999  # type: ignore[misc]
