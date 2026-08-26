"""Collection status bar headless tests (no page, no real storage)."""

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

    def test_layout_contains_all_sections(self):
        reset_app_state()
        bar = CollectionStatusBar()
        text = _all_text(bar)
        assert _STATE_LABELS["stopped"] in text

    def test_content_only_has_dot_and_label(self):
        reset_app_state()
        bar = CollectionStatusBar()
        assert len(bar.content.controls) == 2
        assert bar.content.controls[1] is bar._state_text
