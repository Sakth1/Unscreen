import asyncio
import logging

import flet as ft
import pytest

from UI.components.data_section import DataSection
from UI.components.empty_state import EmptyState
from UI.components.error_boundary import ErrorBoundary, spawn
from UI.components.motion import (
    SKELETON_PULSE_MS,
    entrance,
    is_reduced_motion,
    set_reduced_motion,
)
from UI.components.skeleton import list_row_skeleton, status_card_skeleton


def _walk(control):
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


def _texts(control):
    return [c.value for c in _walk(control) if isinstance(c, ft.Text)]


@pytest.fixture(autouse=True)
def _reset_motion_flag():
    set_reduced_motion(False)
    yield
    set_reduced_motion(False)


# ═══════════════════════════════════════════════════════════════════
#  Motion preferences
#  ═══════════════════════════════════════════════════════════════════


class TestMotion:
    def test_flag_defaults_off(self):
        assert is_reduced_motion() is False

    def test_flag_toggle(self):
        set_reduced_motion(True)
        assert is_reduced_motion() is True

    def test_entrance_restores_visible_state(self):
        control = ft.Container()
        entrance(control)
        assert control.opacity == 1
        assert control.scale == 1.0
        assert control.animate_opacity is not None

    def test_entrance_reduced_motion_skips_scale(self):
        set_reduced_motion(True)
        control = ft.Container()
        entrance(control)
        assert control.opacity == 1
        assert control.animate_scale is None


# ═══════════════════════════════════════════════════════════════════
#  EmptyState
#  ═══════════════════════════════════════════════════════════════════


class TestEmptyState:
    def test_renders_icon_headline_body_action(self):
        action = ft.FilledButton("Go")
        state = EmptyState(
            icon=ft.Icons.INBOX_OUTLINED,
            headline="No data yet",
            body="Start collecting",
            action=action,
        )
        icons = [c for c in _walk(state) if isinstance(c, ft.Icon)]
        assert len(icons) == 1
        assert icons[0].icon == ft.Icons.INBOX_OUTLINED
        assert icons[0].size == 96
        assert icons[0].color == ft.Colors.with_opacity(0.38, ft.Colors.ON_SURFACE)
        assert _texts(state) == ["No data yet", "Start collecting"]
        buttons = [c for c in _walk(state) if isinstance(c, ft.FilledButton)]
        assert buttons == [action]

    def test_action_click_invokes_callback(self):
        calls = []
        action = ft.FilledButton("Go", on_click=lambda _e: calls.append(True))
        state = EmptyState(
            icon=ft.Icons.INBOX_OUTLINED, headline="Empty", action=action
        )
        buttons = [c for c in _walk(state) if isinstance(c, ft.FilledButton)]
        assert len(buttons) == 1
        buttons[0].on_click(None)
        assert calls == [True]

    def test_body_and_action_optional(self):
        state = EmptyState(icon=ft.Icons.INBOX_OUTLINED, headline="Only headline")
        assert _texts(state) == ["Only headline"]
        assert not any(isinstance(c, ft.FilledButton) for c in _walk(state))

    def test_expanded_caps_width_compact_stretches(self):
        wide = EmptyState(icon=ft.Icons.INBOX_OUTLINED, headline="H")
        assert wide.content.width == 400
        narrow = EmptyState(icon=ft.Icons.INBOX_OUTLINED, headline="H", compact=True)
        assert narrow.content.width is None

    def test_reduced_motion_removes_scale_animation(self):
        set_reduced_motion(True)
        state = EmptyState(icon=ft.Icons.INBOX_OUTLINED, headline="H")
        assert state.animate_scale is None
        assert state.animate_opacity is not None
        set_reduced_motion(False)
        state = EmptyState(icon=ft.Icons.INBOX_OUTLINED, headline="H")
        assert state.animate_scale is not None


# ═══════════════════════════════════════════════════════════════════
#  Skeletons
#  ═══════════════════════════════════════════════════════════════════


class TestSkeleton:
    def test_status_card_wraps_in_shimmer(self):
        skeleton = status_card_skeleton()
        shimmers = [c for c in _walk(skeleton) if isinstance(c, ft.Shimmer)]
        assert len(shimmers) == 1
        assert shimmers[0].period == SKELETON_PULSE_MS
        assert shimmers[0].loop == 0

    def test_list_row_has_avatar_and_lines(self):
        skeleton = list_row_skeleton()
        assert len([c for c in _walk(skeleton) if isinstance(c, ft.Shimmer)]) == 1
        boxes = [
            c
            for c in _walk(skeleton)
            if isinstance(c, ft.Container)
            and c.bgcolor == ft.Colors.SURFACE_CONTAINER_HIGH
        ]
        assert len(boxes) == 3

    def test_reduced_motion_renders_static(self):
        set_reduced_motion(True)
        skeleton = status_card_skeleton()
        assert not any(isinstance(c, ft.Shimmer) for c in _walk(skeleton))


# ═══════════════════════════════════════════════════════════════════
#  ErrorBoundary
#  ═══════════════════════════════════════════════════════════════════


class TestErrorBoundary:
    @pytest.mark.asyncio
    async def test_success_renders_content(self):
        boundary = ErrorBoundary(
            load=lambda: "payload", content=lambda d: ft.Text(f"value={d}")
        )
        await boundary.run()
        assert _texts(boundary) == ["value=payload"]

    @pytest.mark.asyncio
    async def test_awaitable_load_is_awaited(self):
        async def load():
            return "async data"

        boundary = ErrorBoundary(load=load, content=lambda d: ft.Text(d))
        await boundary.run()
        assert _texts(boundary) == ["async data"]

    @pytest.mark.asyncio
    async def test_failure_renders_error_card_and_logs(self, caplog):
        def boom():
            raise ValueError("kaboom")

        boundary = ErrorBoundary(
            load=boom,
            content=lambda d: ft.Text("never"),
            error_message="Export failed",
        )
        with caplog.at_level(logging.ERROR, logger="UI.components.error_boundary"):
            await boundary.run()
        assert "kaboom" in caplog.text
        assert _texts(boundary) == ["Export failed"]
        assert any(
            isinstance(c, ft.FilledButton) and c.content == "Retry"
            for c in _walk(boundary)
        )

    @pytest.mark.asyncio
    async def test_retry_reloads_and_recovers(self):
        calls = {"n": 0}

        def load():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("boom")
            return "ok"

        boundary = ErrorBoundary(load=load, content=lambda d: ft.Text(f"data={d}"))
        await boundary.run()
        assert _texts(boundary) == ["Something went wrong"]
        retry = next(c for c in _walk(boundary) if isinstance(c, ft.FilledButton))
        retry.on_click(None)
        await asyncio.sleep(0)
        assert _texts(boundary) == ["data=ok"]
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_retry_uses_runner_when_provided(self):
        scheduled = []

        def runner(coro):
            scheduled.append(coro)
            return asyncio.create_task(coro)

        boundary = ErrorBoundary(
            load=lambda: "d", content=lambda d: ft.Text("x"), runner=runner
        )
        boundary._retry()
        assert len(scheduled) == 1
        await asyncio.sleep(0)
        assert _texts(boundary) == ["x"]

    def test_spawn_runs_inline_without_loop(self):
        done = []

        async def coro():
            done.append(1)

        spawn(coro())
        assert done == [1]


# ═══════════════════════════════════════════════════════════════════
#  DataSection
#  ═══════════════════════════════════════════════════════════════════


class TestDataSection:
    @pytest.mark.asyncio
    async def test_skeleton_shown_then_content(self):
        section = DataSection(
            load=lambda: [1, 2, 3],
            content=lambda data: ft.Text(f"n={len(data)}"),
            skeleton=status_card_skeleton(),
        )
        assert any(isinstance(c, ft.Shimmer) for c in _walk(section))
        await section.run()
        assert _texts(section) == ["n=3"]
        assert not any(isinstance(c, ft.Shimmer) for c in _walk(section))

    @pytest.mark.asyncio
    async def test_empty_predicate_shows_empty_state(self):
        section = DataSection(
            load=lambda: [],
            content=lambda data: ft.Text("content"),
            empty_when=lambda data: not data,
            empty=EmptyState(
                icon=ft.Icons.FILE_DOWNLOAD_OFF,
                headline="Nothing to export yet",
            ),
        )
        await section.run()
        assert "Nothing to export yet" in _texts(section)
        assert "content" not in _texts(section)

    @pytest.mark.asyncio
    async def test_default_empty_state_when_omitted(self):
        section = DataSection(
            load=lambda: None,
            content=lambda data: ft.Text("content"),
            empty_when=lambda data: data is None,
        )
        await section.run()
        assert "Nothing here yet" in _texts(section)

    @pytest.mark.asyncio
    async def test_failure_renders_error_card(self):
        def boom():
            raise RuntimeError("db locked")

        section = DataSection(
            load=boom,
            content=lambda data: ft.Text("content"),
            skeleton=status_card_skeleton(),
            error_message="Export failed",
        )
        await section.run()
        assert _texts(section) == ["Export failed"]
        assert not any(isinstance(c, ft.Shimmer) for c in _walk(section))

    @pytest.mark.asyncio
    async def test_concurrent_run_ignored(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def load():
            started.set()
            await release.wait()
            return "data"

        section = DataSection(load=load, content=lambda d: ft.Text(d))
        first = asyncio.create_task(section.run())
        await started.wait()
        second = asyncio.create_task(section.run())
        await asyncio.sleep(0)
        release.set()
        await first
        await second
        assert _texts(section) == ["data"]
