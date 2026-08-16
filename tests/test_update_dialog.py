"""Update dialog: responsive metrics, AppState wiring, and headless construction."""

from __future__ import annotations

import flet as ft
from sweep_helpers import mock_page

from core.state.app_state import UpdateStatus, get_app_state, reset_app_state
from core.update_checker import UpdateInfo


def _update(**overrides) -> UpdateInfo:
    fields = dict(
        version="0.4.9",
        tag_name="v0.4.9",
        release_notes="## What's Changed\n* Something",
        published_at="2026-08-01T16:51:33Z",
        prerelease=False,
        html_url="https://github.com/sakth1/Unscreen/releases/tag/v0.4.9",
        asset_name="Unscreen-0.4.9-setup.exe",
        asset_url="https://github.com/sakth1/Unscreen/releases/download/v0.4.9/a.exe",
        asset_size=12_345_678,
        asset_digest="abc",
    )
    fields.update(overrides)
    return UpdateInfo(**fields)


class TestNotesHeight:
    def _height(self, page_height: float, form_factor) -> float:
        from UI.components.update_dialog import _notes_height

        return _notes_height(page_height, form_factor)

    def test_mobile_scales_with_screen(self):
        from UI.layout.models import ScreenFormFactor

        assert self._height(800, ScreenFormFactor.MOBILE) == 280.0

    def test_mobile_floor_on_tiny_screens(self):
        from UI.layout.models import ScreenFormFactor

        assert self._height(300, ScreenFormFactor.MOBILE) == 140.0

    def test_tablet_portrait_is_taller(self):
        from UI.layout.models import ScreenFormFactor

        assert self._height(900, ScreenFormFactor.TABLET_PORTRAIT) == 360.0

    def test_desktop_is_bounded(self):
        from UI.layout.models import ScreenFormFactor

        assert self._height(2000, ScreenFormFactor.DESKTOP) == 320.0
        assert self._height(600, ScreenFormFactor.DESKTOP) == 200.0

    def test_tablet_landscape_matches_desktop(self):
        from UI.layout.models import ScreenFormFactor

        assert self._height(800, ScreenFormFactor.TABLET_LANDSCAPE) == 240.0


class TestDialogWidth:
    def test_wide_form_factors_get_constrained_width(self):
        from UI.components.update_dialog import _dialog_width
        from UI.layout.models import ScreenFormFactor

        assert _dialog_width(ScreenFormFactor.DESKTOP) == 420.0
        assert _dialog_width(ScreenFormFactor.TABLET_LANDSCAPE) == 420.0

    def test_mobile_uses_platform_width(self):
        from UI.components.update_dialog import _dialog_width
        from UI.layout.models import ScreenFormFactor

        assert _dialog_width(ScreenFormFactor.MOBILE) is None
        assert _dialog_width(ScreenFormFactor.TABLET_PORTRAIT) is None


class TestReleaseDate:
    def test_formats_iso_timestamp(self):
        from UI.components.update_dialog import _format_release_date

        assert _format_release_date("2026-08-01T16:51:33Z") == "Aug 1, 2026"

    def test_empty_and_garbage(self):
        from UI.components.update_dialog import _format_release_date

        assert _format_release_date("") == ""
        assert _format_release_date("not-a-date") == ""


class TestShowUpdateDialog:
    def test_records_available_state_and_shows(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        state = get_app_state()
        assert state.update_status is UpdateStatus.AVAILABLE
        assert state.update_info is not None
        assert state.update_info.version == "0.4.9"
        page.show_dialog.assert_called_once()

    def test_installable_dialog_has_install_button(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.components.update_dialog.is_packaged", lambda: True)
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        labels = [b.content for b in dialog.actions]
        assert "Download & install" in labels
        assert "Later" in labels
        assert "Open releases page" in labels

    def test_manual_only_dialog_omits_install(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.components.update_dialog.is_packaged", lambda: False)
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(asset_url=None, asset_name=None), "0.4.10-dev3"
        )
        dialog = page.show_dialog.call_args.args[0]
        labels = [b.content for b in dialog.actions]
        assert "Download & install" not in labels
        assert "Open releases page" in labels

    def test_release_notes_rendered_as_markdown(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        markdown_controls = _collect(dialog, ft.Markdown)
        assert len(markdown_controls) == 1
        assert "## What's Changed" in markdown_controls[0].value

    def test_missing_notes_falls_back_to_plain_text(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(release_notes=""), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        assert any(
            isinstance(t, ft.Text) and "No release notes" in (t.value or "")
            for t in _collect(dialog, ft.Text)
        )

    def test_close_resets_state_to_idle(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        later = next(b for b in dialog.actions if b.content == "Later")
        later.on_click(None)
        assert get_app_state().update_status is UpdateStatus.IDLE

    def test_install_flow_updates_state(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.components.update_dialog.is_packaged", lambda: True)
        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        install = next(b for b in dialog.actions if b.content == "Download & install")
        install.on_click(None)
        assert get_app_state().update_status is UpdateStatus.DOWNLOADING
        page.run_task.assert_called_once()


class TestAndroidInstallFinishesActivity:
    def test_applied_install_destroys_window(self, monkeypatch, tmp_path):
        import asyncio
        import unittest.mock
        from types import SimpleNamespace

        from core.update_checker import ApplyResult

        reset_app_state()
        monkeypatch.setattr("UI.components.update_dialog.is_packaged", lambda: True)
        monkeypatch.setattr("UI.components.update_dialog.is_android", lambda: True)
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")

        class FakeUpdater:
            def apply_update(self, update, apk_path, relaunch=False):
                assert relaunch is False
                return SimpleNamespace(result=ApplyResult.APPLIED)

        class FakeChecker:
            def download(self, update, dest, on_progress):
                return str(apk)

        monkeypatch.setattr("UI.components.update_dialog.Updater", FakeUpdater)
        monkeypatch.setattr("UI.components.update_dialog.UpdateChecker", FakeChecker)

        page = mock_page()
        page.window.destroy = unittest.mock.AsyncMock()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        install = next(b for b in dialog.actions if b.content == "Download & install")
        install.on_click(None)

        run_install = page.run_task.call_args.args[0]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_install())
            for task in [t for t in asyncio.all_tasks(loop)]:
                loop.run_until_complete(task)
        finally:
            loop.close()

        page.window.destroy.assert_awaited_once()

    def test_manual_required_keeps_app_open(self, monkeypatch, tmp_path):
        import asyncio
        from types import SimpleNamespace

        from core.update_checker import ApplyResult

        reset_app_state()
        monkeypatch.setattr("UI.components.update_dialog.is_packaged", lambda: True)
        monkeypatch.setattr("UI.components.update_dialog.is_android", lambda: True)
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")

        class FakeUpdater:
            def apply_update(self, update, apk_path, relaunch=False):
                return SimpleNamespace(result=ApplyResult.MANUAL_REQUIRED)

        class FakeChecker:
            def download(self, update, dest, on_progress):
                return str(apk)

        monkeypatch.setattr("UI.components.update_dialog.Updater", FakeUpdater)
        monkeypatch.setattr("UI.components.update_dialog.UpdateChecker", FakeChecker)

        page = mock_page()
        page.height = 800
        from UI.components.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        dialog = page.show_dialog.call_args.args[0]
        install = next(b for b in dialog.actions if b.content == "Download & install")
        install.on_click(None)

        run_install = page.run_task.call_args.args[0]
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_install())
            for task in [t for t in asyncio.all_tasks(loop)]:
                loop.run_until_complete(task)
        finally:
            loop.close()

        page.window.destroy.assert_not_called()


def _collect(control, cls):
    found = []
    for child in getattr(control, "controls", []) or []:
        found.extend(_collect(child, cls))
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        found.extend(_collect(content, cls))
    if isinstance(control, cls):
        found.append(control)
    return found
