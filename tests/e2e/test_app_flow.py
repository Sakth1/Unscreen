"""E2E layer: the real app against a real Flutter client (flet.testing).

This layer is optional and excluded from the default suite
(``--ignore=tests/e2e``). Run it with ``uv run pytest tests/e2e -m e2e``
on a machine that has:

- the Flutter SDK (or set ``FLET_TEST_FLUTTER_EXE``),
- the flet client shell (set ``FLET_TEST_FLUTTER_APP_DIR``).

Without either, every test here skips with a generic availability guard —
never a named-skip. Every test drives the *real* ``App`` on a *real*
``ft.Page`` through ``FletTestApp``, so rendering and pointer events are
exercised end to end.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app import App
from utils.models import ScreenFormFactor

FLUTTER_EXE = os.environ.get("FLET_TEST_FLUTTER_EXE") or shutil.which("flutter")
FLUTTER_APP_DIR = os.environ.get("FLET_TEST_FLUTTER_APP_DIR") or (
    Path(__file__).resolve().parents[2] / "client"
)

if FLUTTER_EXE is None or not Path(FLUTTER_APP_DIR).is_dir():
    pytest.skip(
        "flet E2E requires the Flutter SDK (FLET_TEST_FLUTTER_EXE) and the "
        "flet client shell (FLET_TEST_FLUTTER_APP_DIR); skipping",
        allow_module_level=True,
    )

pytestmark = pytest.mark.e2e


@pytest.fixture
async def flet_app(tmp_path, monkeypatch):
    from flet.testing import FletTestApp

    monkeypatch.setenv("UNSCREEN_DATA_DIR", str(tmp_path))

    app_holder = {}

    def build_app(page):
        app_holder["app"] = App(page)

    ftapp = FletTestApp(
        flutter_app_dir=FLUTTER_APP_DIR,
        flet_app_main=build_app,
        test_path=str(__file__),
        test_platform="windows",
        disable_fvm=True,
    )
    await ftapp.start()
    yield ftapp, app_holder
    await ftapp.teardown()


async def test_app_boots_against_real_client(flet_app):
    ftapp, app_holder = flet_app

    app = app_holder["app"]
    assert ftapp.page.title == "Unscreen"
    assert app.layout.screen_form_factor in ScreenFormFactor
    assert app.content_container.content is app.dashboard_page
    assert ftapp.page.controls


async def test_navigation_bar_switches_screens(flet_app):
    ftapp, app_holder = flet_app

    app = app_holder["app"]
    ftapp.resize_page(400, 800)
    await ftapp.tester.pump_and_settle()
    assert app.layout.screen_form_factor is ScreenFormFactor.MOBILE

    assert ftapp.page.navigation_bar is not None
    timeline = await ftapp.tester.find_by_icon("timeline")
    assert timeline.exists
    await ftapp.tester.tap(timeline)
    await ftapp.tester.pump_and_settle()

    assert app.content_container.content is app.timeline_page
    assert app.route_manager.current_route == "/timeline"
