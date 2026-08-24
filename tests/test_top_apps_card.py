"""Tests for the dashboard Top Apps card (milestone #26 UI)."""

import datetime

import flet as ft
import pytest

from core.analytics import AppTotal
from UI.components.empty_state import EmptyState
from UI.components.top_apps_card import (
    _ACCENT_COLORS,
    RANGE_LAST_7,
    RANGE_LAST_30,
    RANGE_MONTH,
    RANGE_TODAY,
    RANGE_WEEK,
    RANGE_YESTERDAY,
    TopAppsCard,
    _all_apps_dialog,
    _app_row,
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
    title = getattr(control, "title", None)
    if isinstance(title, ft.Control):
        yield from _walk(title)
    for action in getattr(control, "actions", []) or []:
        yield from _walk(action)


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


class TestAccentColors:
    def test_ranks_get_distinct_accents(self):
        rows = _rows(("a", "Alpha", 60, 50.0), ("b", "Beta", 60, 50.0))
        column = _build_rows(rows)
        avatars = [c for c in _walk(column) if isinstance(c, ft.CircleAvatar)]
        assert len(avatars) == 2
        assert avatars[0].bgcolor == _ACCENT_COLORS[0]
        assert avatars[1].bgcolor == _ACCENT_COLORS[1]
        assert avatars[0].bgcolor != avatars[1].bgcolor
        assert avatars[0].content.color == ft.Colors.WHITE

    def test_donut_arc_matches_rank_accent(self):
        rows = _rows(("a", "Alpha", 60, 100.0), ("b", "Beta", 60, 100.0))
        column = _build_rows(rows)
        rings = _rings(column)
        assert rings[0].color == _ACCENT_COLORS[0]
        assert rings[1].color == _ACCENT_COLORS[1]

    def test_ranks_cycle_past_palette_end(self):
        rows = _rows(
            *[(f"k{i}", f"App{i}", 60, 100.0) for i in range(len(_ACCENT_COLORS) + 1)]
        )
        column = _build_rows(rows)
        avatars = [c for c in _walk(column) if isinstance(c, ft.CircleAvatar)]
        assert avatars[0].bgcolor == avatars[-1].bgcolor == _ACCENT_COLORS[0]


class TestAllAppsDialog:
    def _rows(self):
        return _rows(("a", "Alpha", 60, 50.0), ("b", "Beta", 60, 50.0))

    def test_builds_dialog_with_every_app(self):
        dialog = _all_apps_dialog("Today", self._rows())
        assert dialog.title.value == "All apps — Today"
        texts = _texts(dialog)
        assert "Alpha" in texts and "Beta" in texts
        assert any("2 apps" in t for t in texts)
        assert any("2 m 0 s" in t for t in texts)
        buttons = [c for c in _walk(dialog) if isinstance(c, ft.TextButton)]
        assert len(buttons) == 1
        assert buttons[0].content == "Close"

    def test_close_callback_invoked(self):
        closed = []
        dialog = _all_apps_dialog(
            "Today", self._rows(), on_close=lambda: closed.append(True)
        )
        buttons = [c for c in _walk(dialog) if isinstance(c, ft.TextButton)]
        buttons[0].on_click(None)
        assert closed == [True]

    def test_empty_rows_still_render_header(self):
        dialog = _all_apps_dialog("Today", [])
        assert "All apps — Today" in _texts(dialog)
        assert any("0 apps" in t for t in _texts(dialog))


class TestAppIcons:
    """F9c: site-bucket favicons in row avatars with initial fallback."""

    def _avatar(self, row_control):
        return next(c for c in _walk(row_control) if isinstance(c, ft.CircleAvatar))

    def test_icon_bytes_set_as_foreground_without_overlay_initial(self):
        row = _app_row(
            _rows(("browser:youtube", "YouTube", 60, 100.0))[0], 0, b"\x89PNG!"
        )
        avatar = self._avatar(row)
        assert avatar.foreground_image_src == b"\x89PNG!"
        assert avatar.content is None

    def test_missing_icon_keeps_initial_avatar(self):
        row = _app_row(_rows(("browser:youtube", "YouTube", 60, 100.0))[0], 0)
        avatar = self._avatar(row)
        assert avatar.foreground_image_src is None
        assert avatar.content.value == "Y"

    def test_build_rows_maps_icons_by_app_key(self):
        rows = _rows(
            ("browser:youtube", "YouTube", 60, 60.0), ("browser", "Browser", 40, 40.0)
        )
        column = _build_rows(rows, icons={"browser:youtube": b"PNG"})
        avatars = [c for c in _walk(column) if isinstance(c, ft.CircleAvatar)]
        assert avatars[0].foreground_image_src == b"PNG"
        assert avatars[0].content is None
        assert avatars[1].foreground_image_src is None
        assert avatars[1].content.value == "B"

    def test_all_apps_dialog_renders_icons(self):
        rows = _rows(("browser:youtube", "YouTube", 60, 100.0))
        dialog = _all_apps_dialog("Today", rows, icons={"browser:youtube": b"PNG"})
        avatar = self._avatar(dialog)
        assert avatar.foreground_image_src == b"PNG"


class TestSiteIconWiring:
    """The card's background favicon pass (`_load_site_icons`)."""

    @staticmethod
    def _rows():
        return _rows(
            ("browser:youtube", "YouTube", 60, 50.0),
            ("browser", "Browser", 30, 25.0),
            ("editor", "Editor", 30, 25.0),
        )

    @pytest.mark.asyncio
    async def test_resolves_site_icons_and_rerenders(self, monkeypatch):
        store = FakeStore(self._rows())
        card = TopAppsCard(store=store)
        fake_png = b"\x89PNG\r\n\x1a\n"

        def fake_fetch(key):
            return fake_png if key == "browser:youtube" else None

        monkeypatch.setattr(
            "UI.components.top_apps_card.fetch_site_favicon", fake_fetch
        )
        await card._load_icons()
        assert card._icons == {"browser:youtube": fake_png}
        assert "browser" not in card._icons
        # The section re-ran (a second store call) and re-rendered content.
        assert store.calls.count(("daily", None, card._limit)) == 2
        assert isinstance(card._section.controls[0], ft.Column)
        avatars = [
            c
            for c in _walk(card._section.controls[0])
            if isinstance(c, ft.CircleAvatar)
        ]
        assert avatars[0].foreground_image_src == fake_png
        assert avatars[1].foreground_image_src is None

    @pytest.mark.asyncio
    async def test_failed_fetch_is_remembered_and_not_retried(self, monkeypatch):
        store = FakeStore(self._rows())
        card = TopAppsCard(store=store)
        calls = []

        def fake_fetch(key):
            calls.append(key)
            return None

        monkeypatch.setattr(
            "UI.components.top_apps_card.fetch_site_favicon", fake_fetch
        )
        await card._load_icons()
        await card._load_icons()
        assert calls == ["browser:youtube"]
        assert "browser:youtube" in card._icon_failed
        assert card._icons == {}

    @pytest.mark.asyncio
    async def test_already_resolved_keys_not_refetched(self, monkeypatch):
        store = FakeStore(self._rows())
        card = TopAppsCard(store=store)
        calls = []
        fake_png = b"PNG"

        def fake_fetch(key):
            calls.append(key)
            return fake_png

        monkeypatch.setattr(
            "UI.components.top_apps_card.fetch_site_favicon", fake_fetch
        )
        await card._load_icons()
        await card._load_icons()
        assert calls == ["browser:youtube"]

    @pytest.mark.asyncio
    async def test_load_error_leaves_icons_untouched(self, monkeypatch):
        store = FakeStore(error=RuntimeError("boom"))
        card = TopAppsCard(store=store)
        monkeypatch.setattr(
            "UI.components.top_apps_card.fetch_site_favicon", lambda k: None
        )
        await card._load_icons()
        assert card._icons == {}


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

    def test_tap_noop_without_page(self):
        card = TopAppsCard(store=FakeStore())
        card._on_tap(None)  # no page attached → no-op, nothing raised
        assert card._dialog is None

    def test_close_all_apps_noop_without_page(self):
        card = TopAppsCard(store=FakeStore())
        card._close_all_apps()
        assert card._dialog is None

    @pytest.mark.asyncio
    async def test_open_all_apps_shows_dialog_with_full_list(self, monkeypatch):
        store = FakeStore(
            _rows(("chrome.exe", "Chrome", 900, 60.0), ("editor", "Editor", 600, 40.0))
        )
        card = TopAppsCard(store=store)

        shown = []

        class FakePage:
            def show_dialog(self, dialog):
                shown.append(dialog)

            def update(self):
                pass

        monkeypatch.setattr(TopAppsCard, "page", property(lambda self: FakePage()))
        await card._open_all_apps()
        assert len(shown) == 1
        assert "All apps — Today" in _texts(shown[0])
        assert "Chrome" in _texts(shown[0])
        assert "Editor" in _texts(shown[0])
        assert store.calls == [("daily", None, None)]

    @pytest.mark.asyncio
    async def test_open_all_apps_uses_card_range_label(self, monkeypatch):
        store = FakeStore(_rows(("a", "App", 60, 100.0)))
        card = TopAppsCard(store=store, range_key=RANGE_WEEK)

        shown = []

        class FakePage:
            def show_dialog(self, dialog):
                shown.append(dialog)

            def update(self):
                pass

        monkeypatch.setattr(TopAppsCard, "page", property(lambda self: FakePage()))
        await card._open_all_apps()
        assert shown[0].title.value == "All apps — This week"

    @pytest.mark.asyncio
    async def test_tap_schedules_dialog_via_page(self, monkeypatch):
        import asyncio

        store = FakeStore(_rows(("a", "App", 60, 100.0)))
        card = TopAppsCard(store=store)
        shown = []

        class FakePage:
            def show_dialog(self, dialog):
                shown.append(dialog)

            def update(self):
                pass

            def run_task(self, fn):
                return asyncio.ensure_future(fn())

        monkeypatch.setattr(TopAppsCard, "page", property(lambda self: FakePage()))
        card._on_tap(None)
        await asyncio.sleep(0.01)
        assert len(shown) == 1
