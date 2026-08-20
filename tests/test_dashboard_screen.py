"""Tests for the dashboard screen (milestone #26 UI)."""

import asyncio
import datetime
from types import SimpleNamespace

import flet as ft
import pytest
from sweep_helpers import mock_page

from core.analytics import AppTotal
from UI.components.dashboard_summary import DashboardSummaryCard
from UI.components.top_apps_card import TopAppsCard
from UI.layout.layout_resolver import app_layout_resolver
from UI.screens.dashboard_screen import (
    RANGE_LAST_7,
    RANGE_MONTH,
    RANGE_TODAY,
    RANGE_WEEK,
    RANGE_YESTERDAY,
    Dashboard,
    _range_subtitle,
)


def _walk(control):
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _texts(control):
    return [c.value for c in _walk(control) if isinstance(c, ft.Text)]


NOW = datetime.datetime(2026, 8, 20, 15, 30)


class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.calls = []

    def totals(self, since_ms, until_ms, device_id=None, limit=None):
        self.calls.append(("totals", since_ms, until_ms, limit))
        return self.rows

    def daily_totals(self, day=None, device_id=None, limit=None):
        self.calls.append(("daily", day, limit))
        return self.rows

    def weekly_totals(self, day=None, device_id=None, limit=None):
        self.calls.append(("weekly", day, limit))
        return self.rows


def _rows(*totals):
    return [
        AppTotal(app_key=key, app_name=name, total_s=secs, share_pct=pct)
        for key, name, secs, pct in totals
    ]


def _range_event(value):
    return SimpleNamespace(control=SimpleNamespace(value=value), data=None)


class TestRangeSubtitle:
    def test_today(self):
        assert _range_subtitle(RANGE_TODAY, NOW) == "Thursday, Aug 20"

    def test_yesterday(self):
        assert _range_subtitle(RANGE_YESTERDAY, NOW) == "Wednesday, Aug 19"

    def test_week(self):
        assert _range_subtitle(RANGE_WEEK, NOW) == "Aug 17 – 23"

    def test_month(self):
        assert _range_subtitle(RANGE_MONTH, NOW) == "August 2026"

    def test_unknown_key_returns_empty(self):
        assert _range_subtitle("nope", NOW) == ""

    def test_none_now_uses_real_clock(self):
        assert _range_subtitle(RANGE_TODAY) != ""


class TestDashboardConstruction:
    def test_constructs_headless(self):
        dashboard = Dashboard()
        assert "Dashboard" in _texts(dashboard)
        assert dashboard._range_dropdown.value == RANGE_TODAY
        assert len(dashboard._range_dropdown.options) == 6
        kinds = {type(c) for c in dashboard._cards}
        assert kinds == {DashboardSummaryCard, TopAppsCard}

    def test_cards_start_unloaded(self):
        store = FakeStore()
        Dashboard(store=store)
        assert store.calls == []

    def test_stacked_layout_is_default(self):
        dashboard = Dashboard()
        assert dashboard._scroll.controls == [
            dashboard._header_row,
            dashboard._stacked,
        ]


class TestRangeSelection:
    def test_selecting_new_range_rebuilds_cards(self):
        store = FakeStore()
        dashboard = Dashboard(store=store, now=NOW)
        old_cards = dashboard._cards
        dashboard._on_range_selected(_range_event(RANGE_LAST_7))
        assert dashboard._range_key == RANGE_LAST_7
        assert dashboard._range_dropdown.value == RANGE_LAST_7
        assert dashboard._cards is not old_cards
        assert dashboard._cards[0]._range_key == RANGE_LAST_7
        assert dashboard._subtitle.value == "Aug 14 – 20"

    def test_unknown_range_is_ignored(self):
        dashboard = Dashboard()
        dashboard._on_range_selected(_range_event("nope"))
        assert dashboard._range_key == RANGE_TODAY

    def test_same_range_is_ignored(self):
        dashboard = Dashboard()
        dashboard._on_range_selected(_range_event(RANGE_TODAY))
        assert dashboard._range_key == RANGE_TODAY

    @pytest.mark.asyncio
    async def test_selection_triggers_fetch_for_new_range(self):
        store = FakeStore(_rows(("a", "App", 60, 100.0)))
        dashboard = Dashboard(store=store, now=NOW)
        dashboard._on_range_selected(_range_event(RANGE_LAST_7))
        await asyncio.sleep(0.01)
        assert any(c[0] == "totals" for c in store.calls)
        since, until = next(c[1:3] for c in store.calls if c[0] == "totals")
        assert until - since == 7 * 24 * 60 * 60 * 1000


class TestAutoLayout:
    def _mobile(self):
        return app_layout_resolver(400, 800, is_mobile=True)

    def _desktop(self):
        return app_layout_resolver(1200, 800)

    def test_mobile_stacks_cards(self):
        dashboard = Dashboard()
        dashboard.apply_layout(self._mobile())
        assert dashboard._scroll.controls == [
            dashboard._header_row,
            dashboard._stacked,
        ]
        assert dashboard._inner.width is None

    def test_desktop_uses_grid_and_caps_width(self):
        dashboard = Dashboard()
        dashboard.apply_layout(self._desktop())
        assert dashboard._scroll.controls == [dashboard._header_row, dashboard._grid]
        assert dashboard._inner.width == 1200
        assert dashboard._inner.alignment == ft.Alignment.CENTER

    def test_layout_switch_back_to_mobile(self):
        dashboard = Dashboard()
        dashboard.apply_layout(self._desktop())
        dashboard.apply_layout(self._mobile())
        assert dashboard._scroll.controls == [
            dashboard._header_row,
            dashboard._stacked,
        ]

    def test_layout_preserves_cards_without_refetch(self):
        store = FakeStore()
        dashboard = Dashboard(store=store)
        dashboard.apply_layout(self._desktop())
        assert store.calls == []


class TestRefresh:
    def test_start_refresh_wires_page_loop(self):
        dashboard = Dashboard()
        page = mock_page()
        dashboard.start_refresh(page)
        page.run_task.assert_called_once()

    def test_refresh_once_skips_when_detached(self):
        store = FakeStore()
        dashboard = Dashboard(store=store)
        dashboard._refresh_once()
        assert store.calls == []

    @pytest.mark.asyncio
    async def test_refresh_once_runs_cards_in_place(self, monkeypatch):
        store = FakeStore(_rows(("a", "App", 60, 100.0)))
        dashboard = Dashboard(store=store)
        monkeypatch.setattr(Dashboard, "parent", property(lambda self: object()))
        dashboard._refresh_once()
        await asyncio.sleep(0.01)
        assert store.calls != []
        assert "App" in _texts(dashboard)
