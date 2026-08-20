"""Tests for the dashboard summary card (milestone #26 UI)."""

import datetime

import flet as ft
import pytest

from core.analytics import AppTotal
from UI.components.dashboard_summary import DashboardSummaryCard, _build_summary


def _walk(control):
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _texts(control):
    return [c.value for c in _walk(control) if isinstance(c, ft.Text)]


def _rows(*totals):
    return [
        AppTotal(app_key=key, app_name=name, total_s=secs, share_pct=pct)
        for key, name, secs, pct in totals
    ]


class FakeStore:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls = []

    def _checked(self):
        if self.error is not None:
            raise self.error
        return self.rows

    def totals(self, since_ms, until_ms, device_id=None, limit=None):
        self.calls.append(("totals", since_ms, until_ms, limit))
        return self._checked()

    def daily_totals(self, day=None, device_id=None, limit=None):
        self.calls.append(("daily", day, limit))
        return self._checked()

    def weekly_totals(self, day=None, device_id=None, limit=None):
        self.calls.append(("weekly", day, limit))
        return self._checked()


class TestBuildSummary:
    def test_renders_total_apps_and_top_app(self):
        rows = _rows(
            ("chrome.exe", "Chrome", 900, 60.0),
            ("editor", "Editor", 600, 40.0),
        )
        texts = _texts(_build_summary(rows))
        assert "Time tracked" in texts
        assert "25 m 0 s" in texts
        assert "Apps" in texts
        assert "2" in texts
        assert "Most used" in texts
        assert "Chrome · 60%" in texts

    def test_empty_rows_render_blank_most_used(self):
        texts = _texts(_build_summary([]))
        assert "0 s" in texts
        assert "" in texts


class TestDashboardSummaryCard:
    def test_constructs_headless_with_defaults(self):
        card = DashboardSummaryCard()
        assert _texts(card)[0] == "Time tracked"
        assert not any(isinstance(c, ft.ProgressRing) for c in _walk(card))

    @pytest.mark.asyncio
    async def test_run_renders_stats(self):
        store = FakeStore(_rows(("a", "App", 60, 100.0)))
        card = DashboardSummaryCard(store=store)
        await card.run()
        texts = _texts(card)
        assert "1 m 0 s" in texts
        assert "App · 100%" in texts

    @pytest.mark.asyncio
    async def test_empty_range_shows_empty_state(self):
        card = DashboardSummaryCard(store=FakeStore([]))
        await card.run()
        assert "No tracked time yet" in _texts(card)

    @pytest.mark.asyncio
    async def test_failure_renders_error_card(self):
        card = DashboardSummaryCard(store=FakeStore(error=RuntimeError("boom")))
        await card.run()
        assert "Couldn't load usage summary" in _texts(card)
        buttons = [c for c in _walk(card) if isinstance(c, ft.FilledButton)]
        assert len(buttons) == 1

    @pytest.mark.asyncio
    async def test_run_noop_when_headless_without_store(self):
        card = DashboardSummaryCard()
        await card.run()
        assert _texts(card)[0] == "Time tracked"

    def test_fetch_uses_full_range(self):
        now = datetime.datetime(2026, 8, 20, 15, 30)
        store = FakeStore()
        card = DashboardSummaryCard(store=store, now=now)
        card._load()
        assert store.calls == [("daily", None, None)]
