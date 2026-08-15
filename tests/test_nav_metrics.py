"""Tests for the layout-driven metrics of the custom navigation controls.

Metrics are resolved inside :mod:`UI.layout.layout_resolver` and carried on
:class:`AppLayout` (``drawer_metrics``, ``nav_bar_metrics``,
``secondary_navigation_metrics``), so the drawer and floating bar behaviour
on every window size is pinned here without needing a live Flet client.
"""

from __future__ import annotations

from types import SimpleNamespace

from UI.layout.layout_resolver import (
    app_layout_resolver,
    resolve_drawer_metrics,
    resolve_navbar_metrics,
)
from UI.layout.models import NavigationPattern, WindowHeightClass


class TestDrawerMetrics:
    def test_mini_rail_width_fixed(self):
        layout = app_layout_resolver(800, 1280)
        assert layout.navigation is NavigationPattern.MINI_RAIL
        assert layout.drawer_metrics.width == 60

    def test_tablet_landscape_extended_width_scales_with_viewport(self):
        layout = app_layout_resolver(900, 600)
        assert 120 <= layout.drawer_metrics.width <= 200
        assert layout.drawer_metrics.width == 900 * 0.22

    def test_desktop_extended_width_capped_at_max(self):
        layout = app_layout_resolver(3000, 1600)
        assert layout.drawer_metrics.width == 200

    def test_extended_uses_roomier_padding_on_wide_layouts(self):
        landscape = app_layout_resolver(960, 600).drawer_metrics
        desktop = app_layout_resolver(1280, 800).drawer_metrics
        assert landscape.destination_padding == 12
        assert desktop.destination_padding == 12
        assert landscape.item_spacing == 8
        assert desktop.item_spacing == 8

    def test_resolver_is_pure_per_parameters(self):
        mini = resolve_drawer_metrics(NavigationPattern.MINI_RAIL, 800, 16)
        assert mini.width == 60
        extended = resolve_drawer_metrics(NavigationPattern.EXTENDED_RAIL, 900, 20)
        assert extended.width == 900 * 0.22


class TestSecondaryDrawerMetrics:
    def test_side_panel_scales_with_viewport(self):
        layout = app_layout_resolver(1280, 800)
        metrics = layout.secondary_navigation_metrics
        assert 180 <= metrics.width <= 260
        assert metrics.destination_padding == 12
        assert metrics.item_spacing == 8

    def test_tablet_portrait_inline_renders_no_panel(self):
        layout = app_layout_resolver(800, 1280)
        assert layout.secondary_navigation_metrics.width == 0.0


class TestNavBarMetrics:
    def test_phone_portrait_margins(self):
        metrics = app_layout_resolver(400, 800).nav_bar_metrics
        assert metrics.margin_left == 16
        assert metrics.margin_right == 16
        assert metrics.margin_bottom == 24
        assert metrics.destination_padding == 8

    def test_phone_landscape_sits_lower_and_roomier(self):
        metrics = app_layout_resolver(700, 400).nav_bar_metrics
        assert metrics.margin_left == 24
        assert metrics.margin_bottom == 16  # compact height → lower to the floor
        assert metrics.destination_padding == 10

    def test_bottom_margin_clears_gesture_inset(self):
        layout = app_layout_resolver(
            400,
            800,
            media=SimpleNamespace(
                orientation=None,
                padding=SimpleNamespace(left=0, top=0, right=0, bottom=34),
            ),
        )
        metrics = layout.nav_bar_metrics
        assert metrics.margin_bottom == 24 + 34

    def test_resolver_is_pure_per_parameters(self):
        metrics = resolve_navbar_metrics(
            (0.0, 0.0, 0.0, 34.0), 400, WindowHeightClass.MEDIUM
        )
        assert metrics.margin_bottom == 24 + 34
