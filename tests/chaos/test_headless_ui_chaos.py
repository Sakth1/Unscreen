"""Headless mindless user: operates every interactive control of a live App.

The app boots on a mock page exactly like the default suite does, then the
driver roams: fires every handler with reasonable and garbage events, resizes
the window to hostile dimensions between actions, navigates routes at random,
and re-fires handlers of stale controls left behind by layout changes — the
closest thing to a mindless user without a display. All findings are
collected and reported at the end (seed included for replay).

Run: ``uv run pytest tests/chaos -m chaos``
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

import flet as ft
from chaos_helpers import (
    ChaosRun,
    adversarial_events,
    hostile_dimensions,
    interactive_handlers,
    is_reasonable_event,
    walk_controls,
)
from sweep_helpers import mock_page

_TIMEOUT_S = 2.0


def _describe(obj: Any) -> str:
    text = repr(obj)
    return text if len(text) <= 120 else text[:117] + "..."


def _fire(
    run: ChaosRun, control, handler_name: str, event: Any, extra: str = ""
) -> None:
    handler = getattr(control, handler_name)
    label = f"{type(control).__name__}.{handler_name}{extra} event={_describe(event)}"
    run.log("fire " + label)
    try:
        result = handler(event)
        if inspect.iscoroutine(result):
            asyncio.run(asyncio.wait_for(result, timeout=_TIMEOUT_S))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record("fire " + label, exc, garbage=not is_reasonable_event(event))


def _resize(run: ChaosRun, app, width: float, height: float) -> None:
    page = app.page
    page.window.width = width
    page.window.height = height
    page.width = width
    page.height = height
    run.log(f"resize {width}x{height}")
    try:
        app._handle_page_resize(None)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"resize {width}x{height}", exc)


def _navigate(run: ChaosRun, app, route: str) -> None:
    run.log(f"navigate {route}")
    try:
        app.route_manager.navigate(route)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"navigate {route}", exc)


def _operate(run: ChaosRun, control, method_name: str, args: tuple) -> None:
    method = getattr(control, method_name)
    label = f"{type(control).__name__}.{method_name}({_describe(args)})"
    run.log("operate " + label)
    try:
        result = method(*args)
        if inspect.iscoroutine(result):
            asyncio.run(asyncio.wait_for(result, timeout=_TIMEOUT_S))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record("operate " + label, exc)


def _discover_routes(app) -> list[str]:
    """Every registered route, discovered generically from the route manager."""
    routes: list[str] = []
    for attr in dir(app.route_manager):
        if attr.startswith("_"):
            continue
        value = getattr(app.route_manager, attr, None)
        if isinstance(value, dict):
            for key in value:
                if isinstance(key, str) and key.startswith("/") and key not in routes:
                    routes.append(key)
    return routes


def _app_roots(app) -> list[Any]:
    roots: list[Any] = [
        app.shell,
        app.content_container,
        app.status_bar,
        app.navigation_rail,
        app.page.navigation_bar,
    ]
    roots += [dest.view for dest in app.destinations]
    roots += list(getattr(app.route_manager, "_section_views", {}).values())
    return [root for root in roots if root is not None]


def test_headless_mindless_user() -> None:
    from app import App

    page = mock_page()
    page.window.width = 1280
    page.window.height = 800
    page.width = 1280
    page.height = 800

    app = App(page)
    run = ChaosRun()

    controls = walk_controls(*_app_roots(app))
    interactive = [
        (control, interactive_handlers(control))
        for control in controls
        if interactive_handlers(control)
    ]
    assert interactive, "no interactive controls discovered — harness is broken"

    selectors = [c for c in controls if callable(getattr(c, "select_index", None))]
    routes = _discover_routes(app)
    garbage_events = adversarial_events()
    stale: list[tuple[Any, str, Any]] = []

    for _ in range(run.steps):
        roll = run.rng.random()
        if roll < 0.40 and interactive:
            control, handlers = run.rng.choice(interactive)
            name = run.rng.choice(sorted(handlers))
            if run.rng.random() < 0.5:
                event = ft.Event(name=name, control=control, data=None)
            else:
                event = SimpleNamespace(control=control, data=None)
            _fire(run, control, name, event)
        elif roll < 0.55:
            stale = [
                (c, h, ft.Event(name=n, control=c, data=None))
                for c, h in interactive
                for n in sorted(h)
            ]
            width, height = run.rng.choice(hostile_dimensions(run.rng))
            _resize(run, app, width, height)
            controls = walk_controls(*_app_roots(app))
            interactive = [
                (control, interactive_handlers(control))
                for control in controls
                if interactive_handlers(control)
            ]
        elif roll < 0.65 and routes:
            _navigate(run, app, run.rng.choice(routes))
            app._update_layout()
        elif roll < 0.75 and stale:
            control, handler_name, event = run.rng.choice(stale)
            _fire(run, control, handler_name, event, extra=" [stale]")
        elif roll < 0.80 and interactive:
            control, handlers = run.rng.choice(interactive)
            name = run.rng.choice(sorted(handlers))
            event = ft.Event(name=name, control=control, data=None)
            _fire(run, control, name, event)
            _fire(run, control, name, event, extra=" [re-fire]")
        elif roll < 0.90 and selectors:
            selector = run.rng.choice(selectors)
            n = len(
                getattr(selector, "final_destinations", None)
                or getattr(selector, "all_destinations", None)
                or getattr(selector, "destinations", None)
                or ()
            )
            if n:
                _operate(
                    run,
                    selector,
                    "select_index",
                    (run.rng.choice([-1, 0, n, n + 5, run.rng.randint(-2, n + 2)]),),
                )
        else:
            if interactive:
                control, handlers = run.rng.choice(interactive)
                name = run.rng.choice(sorted(handlers))
                _, event, _ = run.rng.choice(garbage_events)
                _fire(run, control, name, event, extra=" [garbage]")
            elif routes:
                _navigate(run, app, run.rng.choice(routes))
                app._update_layout()

    run.fail_if_any()
