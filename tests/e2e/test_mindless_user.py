"""E2E mindless user: a real client session driven by source-derived actions.

A real (device-mode) flet app is launched and a bot with no knowledge of the
app beyond what it reads from the source code taps buttons, clicks switches,
types hostile text, double-clicks tooltips, fuzzes coordinates and resizes
the window — with a tripwire on the app's own logs. Any unhandled exception
or crash traceback written by the app fails the run; everything is collected
and reported at the end.

Requires a Flutter toolchain (device mode): skipped otherwise.

Run: ``uv run pytest tests/e2e/test_mindless_user.py -m "chaos and e2e"``
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import flet as ft
import pytest
from chaos_helpers import (
    ChaosRun,
    ControlInventory,
    LogTripwire,
    hostile_dimensions,
    hostile_strings,
)

pytestmark = [pytest.mark.chaos, pytest.mark.e2e]

pytest.importorskip(
    "flet.testing",
    reason="flet[test] extra required for e2e tests",
)

_HAS_FLUTTER = bool(os.environ.get("FLET_TEST_FLUTTER_EXE") or shutil.which("flutter"))
if not _HAS_FLUTTER:
    pytest.skip(
        "Flutter toolchain not found; device-mode e2e requires FLET_TEST_FLUTTER_EXE",
        allow_module_level=True,
    )

_DATA_DIR = tempfile.mkdtemp(prefix="unscreen-e2e-chaos-")
os.environ["UNSCREEN_DATA_DIR"] = _DATA_DIR


def _cleanup() -> None:
    try:
        shutil.rmtree(_DATA_DIR)
    except OSError:
        pass


def _enter_text(
    tester: Any,
    run: ChaosRun,
    key: str,
    text: str,
    submit: bool = False,
) -> None:
    run.log(f"enter_text key={key} text={text!r}")
    try:
        finder = tester.find_by_key(key)
        tester.enter_text(finder, text)
        if submit:
            tester.pump()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"enter_text {key}", exc)


def _tap(
    tester: Any,
    run: ChaosRun,
    finder: Callable[[], Any],
    label: str,
) -> None:
    run.log(f"tap {label}")
    try:
        tester.tap(finder())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"tap {label}", exc)


def _double_click(
    tester: Any,
    run: ChaosRun,
    finder: Callable[[], Any],
    label: str,
) -> None:
    run.log(f"double-click {label}")
    try:
        tester.mouse_double_click(finder())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"double-click {label}", exc)


def _tap_at(tester: Any, run: ChaosRun, x: float, y: float) -> None:
    run.log(f"tap_at ({x:.0f},{y:.0f})")
    try:
        tester.tap_at(ft.Offset(x=x, y=y))
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"tap_at ({x:.0f},{y:.0f})", exc)


def _resize(flet_app: Any, run: ChaosRun, width: float, height: float) -> None:
    run.log(f"resize_page {width}x{height}")
    try:
        flet_app.resize_page(width, height)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        run.record(f"resize_page {width}x{height}", exc)


def test_mindless_user(flet_app) -> None:
    tester = flet_app.tester
    run = ChaosRun()
    tripwire = LogTripwire(root_dir=Path(_DATA_DIR))
    inventory = ControlInventory.from_source()

    keys = sorted(inventory.keys)
    texts = sorted(inventory.texts)
    tooltips = sorted(inventory.tooltips)
    icons = sorted(inventory.icons)
    fields = sorted(inventory.text_fields)
    hostile = hostile_strings(run.rng)
    dimensions = hostile_dimensions(run.rng)

    tester.pump_and_settle()

    for _ in range(run.steps):
        roll = run.rng.random()
        try:
            if roll < 0.25 and keys:
                key = run.rng.choice(keys)
                _tap(tester, run, lambda k=key: tester.find_by_key(k), f"key={key}")
            elif roll < 0.45 and texts:
                text = run.rng.choice(texts)
                _tap(
                    tester,
                    run,
                    lambda t=text: tester.find_by_text(t),
                    f"text={text!r}",
                )
            elif roll < 0.60 and icons:
                icon = run.rng.choice(icons)
                _tap(
                    tester,
                    run,
                    lambda i=icon: tester.find_by_icon(getattr(ft.Icons, i)),
                    f"icon={icon}",
                )
            elif roll < 0.68 and tooltips:
                tip = run.rng.choice(tooltips)
                _double_click(
                    tester,
                    run,
                    lambda t=tip: tester.find_by_tooltip(t),
                    f"tooltip={tip!r}",
                )
            elif roll < 0.80 and fields:
                key = run.rng.choice(fields)
                _enter_text(
                    tester,
                    run,
                    key,
                    run.rng.choice(hostile),
                    submit=run.rng.random() < 0.5,
                )
            elif roll < 0.90:
                _tap_at(
                    tester,
                    run,
                    run.rng.uniform(-10, 1400),
                    run.rng.uniform(-10, 900),
                )
            else:
                width, height = run.rng.choice(dimensions)
                _resize(flet_app, run, width, height)
                tester.pump_and_settle()
        finally:
            tester.pump_and_settle()

    for line in tripwire.lines:
        run.record("app log", RuntimeError(line), garbage=True)

    _cleanup()
    run.fail_if_any()
