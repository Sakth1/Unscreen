"""Tests for the dashboard Top Apps card (milestone #26 UI)."""

import datetime

import flet as ft
import pytest

from core.analytics import AppTotal
from UI.components.empty_state import EmptyState
from UI.components.top_apps_card import (
    RANGE_LAST_7,
    RANGE_LAST_30,
    RANGE_MONTH,
    RANGE_TODAY,
    RANGE_WEEK,
    RANGE_YESTERDAY,
    TopAppsCard,
    _build_rows,
    _donut,
    _fmt_duration,
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


def _rings(control):
    return [c for c in _walk(control) if isinstance(c, ft.ProgressRing)]


def _rows(*totals):
    return [
        AppTotal(app_key=key, app_name=name, total_s=secs, share_pct=pct)
        for key, name, secs, pct in totals
    ]


class FakeStore:
    """Records the range calls made by the card."""

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


NOW = datetime.datetime(2026, 8, 20, 15, 30)


def _ms(dt):
    return int(dt.timestamp() * 1000)


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(0) == "0 s"
        assert _fmt_duration(45) == "45 s"

    def test_minutes_and_seconds(self):
        assert _fmt_duration(1111) == "18 m 31 s"

    def test_hours_and_minutes(self):
        assert _fmt_duration(3725) == "1 h 2 m"


class TestDonut:
    def test_renders_ring_with_share_value_and_percentage(self):
        donut = _donut(44.4)
        rings = _rings(donut)
        assert len(rings) == 1
        assert rings[0].value == pytest.approx(0.444)
        assert rings[0].color == ft.Colors.PRIMARY
        assert _texts(donut) == ["44%"]

    def test_value_clamped(self):
        assert _rings(_donut(150))[0].value == 1.0
        assert _rings(_donut(-5))[0].value == 0.0


class TestBuildRows:
    def test_renders_one_row_per_app(self):
        rows = _rows(
            ("chrome.exe", "Chrome", 900, 50.0),
            ("editor", "Editor", 900, 50.0),
        )
        column = _build_rows(rows)
        texts = _texts(column)
        assert "Chrome" in texts
        assert "15 m 0 s" in texts
        assert "50%" in texts
        assert "Editor" in texts
        assert len(_rings(column)) == 2
        avatars = [c for c in _walk(column) if isinstance(c, ft.CircleAvatar)]
        assert [a.content.value for a in avatars] == ["C", "E"]

    def test_empty_rows_render_empty_column(self):
        column = _build_rows([])
        assert column.controls == []


class TestTopAppsCard:
    def test_constructs_headless_with_defaults(self):
        card = TopAppsCard()
        assert _texts(card)[0] == "Top Apps"
        assert not any(isinstance(c, ft.ProgressRing) for c in _walk(card))

    @pytest.mark.asyncio
    async def test_run_renders_totals(self):
        store = FakeStore(
            _rows(("chrome.exe", "Chrome", 900, 60.0), ("editor", "Editor", 600, 40.0))
        )
        card = TopAppsCard(store=store)
        await card.run()
        texts = _texts(card)
        assert "Chrome" in texts and "15 m 0 s" in texts
        assert "60%" in texts
        assert len(_rings(card)) == 2
        assert not any(isinstance(c, ft.Shimmer) for c in _walk(card))
        assert not any(isinstance(c, EmptyState) for c in _walk(card))

    @pytest.mark.asyncio
    async def test_empty_range_shows_empty_state(self):
        card = TopAppsCard(store=FakeStore([]))
        await card.run()
        assert "No usage in this range" in _texts(card)
        assert not _rings(card)

    @pytest.mark.asyncio
    async def test_failure_renders_error_card_with_retry(self):
        card = TopAppsCard(store=FakeStore(error=RuntimeError("db locked")))
        await card.run()
        assert "Couldn't load app usage" in _texts(card)
        buttons = [c for c in _walk(card) if isinstance(c, ft.FilledButton)]
        assert len(buttons) == 1

    @pytest.mark.asyncio
    async def test_retry_recovers_after_failure(self):
        store = FakeStore(error=RuntimeError("boom"))
        card = TopAppsCard(store=store)
        await card.run()
        assert "Couldn't load app usage" in _texts(card)
        store.error = None
        store.rows = _rows(("a", "App", 60, 100.0))
        retry = next(c for c in _walk(card) if isinstance(c, ft.FilledButton))
        retry.on_click(None)
        await card.run()
        assert "App" in _texts(card)
        assert "1 m 0 s" in _texts(card)

    @pytest.mark.asyncio
    async def test_skeleton_shown_before_load(self):
        card = TopAppsCard(store=FakeStore())
        assert any(isinstance(c, ft.Shimmer) for c in _walk(card))

    def test_range_keys_map_to_store_calls(self):
        expected = {
            RANGE_TODAY: [("daily", None, 5)],
            RANGE_YESTERDAY: [("daily", NOW.date() - datetime.timedelta(days=1), 5)],
            RANGE_WEEK: [("weekly", None, 5)],
            RANGE_LAST_7: [("totals", _ms(NOW) - 7 * 24 * 60 * 60 * 1000, _ms(NOW), 5)],
            RANGE_MONTH: [
                (
                    "totals",
                    _ms(datetime.datetime(2026, 8, 1)),
                    _ms(NOW),
                    5,
                )
            ],
            RANGE_LAST_30: [
                ("totals", _ms(NOW) - 30 * 24 * 60 * 60 * 1000, _ms(NOW), 5)
            ],
        }
        for key, calls in expected.items():
            store = FakeStore()
            card = TopAppsCard(range_key=key, store=store, now=NOW)
            card._load()
            assert store.calls == calls, key

    def test_unknown_range_key_raises(self):
        card = TopAppsCard(range_key="nope", store=FakeStore(), now=NOW)
        with pytest.raises(ValueError):
            card._load()

    def test_limit_applied_to_store_calls(self):
        store = FakeStore()
        card = TopAppsCard(store=store, limit=3, now=NOW)
        card._load()
        assert store.calls == [("daily", None, 3)]
