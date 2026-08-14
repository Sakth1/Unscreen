"""Collection status bar headless tests (no page, no real storage)."""

from datetime import datetime, timezone

from core.state.app_state import (
    reset_app_state,
)
from UI.custom.status_bar import _STATE_LABELS, CollectionStatusBar


def _all_text(control) -> str:
    parts = []

    def walk(node) -> None:
        value = getattr(node, "value", None)
        if value is not None:
            parts.append(str(value))
        content = getattr(node, "content", None)
        if isinstance(content, str):
            parts.append(content)
        for c in getattr(node, "controls", []):
            walk(c)
        if content is not None and content is not node and not isinstance(content, str):
            walk(content)

    walk(control)
    return " ".join(parts)


class TestCollectionStatusBar:
    def test_zero_arg_construction_is_safe(self):
        bar = CollectionStatusBar()
        assert bar._storage is None
        assert bar._page is None
        assert bar.parent is None

    def test_stopped_when_not_running(self):
        reset_app_state()
        bar = CollectionStatusBar()
        assert bar._state_text.value == _STATE_LABELS["stopped"]

    def test_collecting_when_running(self):
        state = reset_app_state()
        state.set_collection_running(True)
        bar = CollectionStatusBar()
        assert bar._state_text.value == _STATE_LABELS["collecting"]

    def test_paused_label(self):
        state = reset_app_state()
        state.set_collection_running(True)
        state.set_collection_paused(True)
        bar = CollectionStatusBar()
        assert bar._state_text.value == _STATE_LABELS["paused"]

    def test_auto_paused_label(self):
        state = reset_app_state()
        state.set_collection_running(True)
        state.set_collection_auto_paused(True)
        bar = CollectionStatusBar()
        assert bar._state_text.value == _STATE_LABELS["auto_paused"]

    def test_state_follows_live_changes(self):
        state = reset_app_state()
        bar = CollectionStatusBar()
        assert bar._state_text.value == _STATE_LABELS["stopped"]
        state.set_collection_running(True)
        assert bar._state_text.value == _STATE_LABELS["collecting"]
        state.set_collection_running(False)
        assert bar._state_text.value == _STATE_LABELS["stopped"]

    def test_watcher_chips_show_last_tick_and_failures(self):
        state = reset_app_state()
        state.ensure_watcher("foreground")
        state.record_tick(_tick("foreground"))
        state.ensure_watcher("afk")
        state.set_watcher_health("afk", failures=3, last_error="boom")
        bar = CollectionStatusBar()
        text = " ".join(c.value for c in bar._watcher_chips)
        assert "foreground" in text
        assert "afk" in text
        assert "\u27173" in text

    def test_watcher_chip_time_is_local(self):
        state = reset_app_state()
        tick = _tick("foreground")
        state.ensure_watcher("foreground")
        state.record_tick(tick)
        bar = CollectionStatusBar()
        local = tick.timestamp.astimezone().strftime("%H:%M:%S")
        assert any(local in c.value for c in bar._watcher_chips)

    def test_count_shown_from_injected_storage(self):
        class _FakeStorage:
            def count_events(self, since=None, until=None, event_type=None):
                return 42

        reset_app_state()
        bar = CollectionStatusBar(storage=_FakeStorage())
        bar._refresh_count()
        assert bar._count_text.value == "42 events today"

    def test_count_error_shows_question_mark(self):
        class _BrokenStorage:
            def count_events(self, **kwargs):
                raise RuntimeError("db broken")

        reset_app_state()
        bar = CollectionStatusBar(storage=_BrokenStorage())
        bar._refresh_count()
        assert bar._count_text.value == "events: ?"

    def test_version_text_present(self):
        state = reset_app_state()
        state.app_version = "0.4.9"
        bar = CollectionStatusBar()
        assert bar._version_text.value == "v0.4.9"

    def test_layout_contains_all_sections(self):
        reset_app_state()
        bar = CollectionStatusBar()
        text = _all_text(bar)
        assert _STATE_LABELS["stopped"] in text


def _tick(name):
    class _Tick:
        pass

    tick = _Tick()
    tick.watcher = name
    tick.timestamp = datetime(2026, 8, 14, 12, 3, 22, tzinfo=timezone.utc)
    tick.data = {}
    return tick
