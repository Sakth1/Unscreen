"""Update dialog: overlay mounting, responsive metrics, AppState wiring, and headless construction."""

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


def _overlay_dialog(page):
    return page.overlay[0]


def _surface(dialog):
    return dialog.controls[1]


def _buttons(dialog):
    found = _collect(dialog, ft.FilledButton) + _collect(dialog, ft.TextButton)
    return [b for b in found if isinstance(getattr(b, "content", None), str)]


def _button(dialog, text):
    return next(b for b in _buttons(dialog) if b.content == text)


def _set_layout(width, height):
    from UI.layout.layout_resolver import app_layout_resolver

    get_app_state().set_layout(app_layout_resolver(width, height))


class TestSurfaceMetrics:
    def test_mobile_width_scales_to_page(self):
        reset_app_state()
        _set_layout(360, 800)
        page = mock_page()
        page.width = 360
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        assert _surface(_overlay_dialog(page)).width == 360 * 0.88

    def test_desktop_width_is_fixed(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        assert _surface(_overlay_dialog(page)).width == 500.0

    def test_long_notes_capped_on_tall_windows(self):
        reset_app_state()
        page = mock_page()
        page.height = 1000
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(release_notes="\n".join(["* x"] * 60)), "0.4.10-dev3"
        )
        assert _surface(_overlay_dialog(page)).height == 640.0

    def test_height_scales_to_small_windows(self):
        reset_app_state()
        page = mock_page()
        page.height = 500
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(release_notes="\n".join(["* x"] * 60)), "0.4.10-dev3"
        )
        assert _surface(_overlay_dialog(page)).height == 450.0

    def test_short_notes_compact_surface(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(release_notes="* One short bullet"), "0.4.10-dev3"
        )
        assert _surface(_overlay_dialog(page)).height < 400.0

    def test_surface_never_below_minimum(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(release_notes=""), "0.4.10-dev3")
        assert _surface(_overlay_dialog(page)).height == 280.0

    def test_mobile_cap_lower_than_desktop(self):
        reset_app_state()
        _set_layout(360, 800)
        page = mock_page()
        page.width = 360
        page.height = 1000
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(release_notes="\n".join(["* x"] * 60)), "0.4.10-dev3"
        )
        assert _surface(_overlay_dialog(page)).height == 600.0

    def test_open_surface_resizes_on_layout_change(self):
        reset_app_state()
        _set_layout(1280, 800)
        page = mock_page()
        page.width = 1280
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(release_notes="\n".join(["* x"] * 60)), "0.4.10-dev3"
        )
        surface = _surface(_overlay_dialog(page))
        assert surface.width == 500.0
        assert surface.height == 640.0
        _set_layout(360, 500)
        assert surface.width == 360 * 0.88
        assert surface.height == 450.0


class TestNotesHeightEstimate:
    def test_empty_notes(self):
        from UI.custom.update_dialog import _estimate_notes_height

        assert _estimate_notes_height("", 420.0) == 0.0

    def test_blank_lines_count_as_lines(self):
        from UI.custom.update_dialog import _estimate_notes_height

        assert _estimate_notes_height("\n", 420.0) == 34.0

    def test_long_line_wraps(self):
        from UI.custom.update_dialog import _estimate_notes_height

        single = _estimate_notes_height("* x", 420.0)
        wrapped = _estimate_notes_height("* " + "word " * 200, 420.0)
        assert wrapped > single * 5

    def test_headings_taller_than_paragraphs(self):
        from UI.custom.update_dialog import _estimate_notes_height

        assert _estimate_notes_height("## Heading", 420.0) > _estimate_notes_height(
            "plain", 420.0
        )

    def test_narrow_width_wraps_more(self):
        from UI.custom.update_dialog import _estimate_notes_height

        long_line = "word " * 100
        assert _estimate_notes_height(long_line, 200.0) > _estimate_notes_height(
            long_line, 420.0
        )


class TestShowUpdateDialog:
    def test_records_available_state_and_mounts_overlay(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        state = get_app_state()
        assert state.update_status is UpdateStatus.AVAILABLE
        assert state.update_info is not None
        assert state.update_info.version == "0.4.9"
        assert len(page.overlay) == 1
        assert _overlay_dialog(page) is page.overlay[0]

    def test_installable_dialog_has_install_button(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.custom.update_dialog.is_packaged", lambda: True)
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        labels = [b.content for b in _buttons(_overlay_dialog(page))]
        assert "Download and install" in labels
        assert "Later" in labels
        assert "Open releases page" not in labels

    def test_manual_only_dialog_omits_install(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.custom.update_dialog.is_packaged", lambda: False)
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(
            page, _update(asset_url=None, asset_name=None), "0.4.10-dev3"
        )
        labels = [b.content for b in _buttons(_overlay_dialog(page))]
        assert "Download and install" not in labels
        assert "Open releases page" in labels

    def test_release_notes_rendered_as_markdown(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        markdown_controls = _collect(_surface(_overlay_dialog(page)), ft.Markdown)
        assert len(markdown_controls) == 1
        assert "## What's Changed" in markdown_controls[0].value

    def test_missing_notes_falls_back_to_plain_text(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(release_notes=""), "0.4.10-dev3")
        surface = _surface(_overlay_dialog(page))
        assert any(
            isinstance(t, ft.Text) and "No release notes" in (t.value or "")
            for t in _collect(surface, ft.Text)
        )

    def test_close_removes_overlay_and_resets_state(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        _button(_overlay_dialog(page), "Later").on_click(None)
        assert page.overlay == []
        assert get_app_state().update_status is UpdateStatus.IDLE

    def test_close_unsubscribes_layout_observer(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        _button(_overlay_dialog(page), "Later").on_click(None)
        assert get_app_state()._observers.get("layout") == []

    def test_close_releases_surface_entrance(self):
        reset_app_state()
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        surface = _surface(_overlay_dialog(page))
        assert surface.animate_opacity is not None
        assert surface.opacity == 1
        assert surface.animate_scale is not None
        assert surface.scale == 1.0

    def test_reduced_motion_skips_scale(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.components.motion.is_reduced_motion", lambda: True)
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        surface = _surface(_overlay_dialog(page))
        assert surface.animate_opacity is not None
        assert surface.opacity == 1
        assert surface.animate_scale is None

    def test_install_flow_updates_state(self, monkeypatch):
        reset_app_state()
        monkeypatch.setattr("UI.custom.update_dialog.is_packaged", lambda: True)
        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        _button(_overlay_dialog(page), "Download and install").on_click(None)
        assert get_app_state().update_status is UpdateStatus.DOWNLOADING
        page.run_task.assert_called_once()


class TestKeyboard:
    def test_escape_closes_and_restores_prior_handler(self):
        import unittest.mock

        reset_app_state()
        prior = unittest.mock.MagicMock()
        page = mock_page()
        page.on_keyboard_event = prior
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        assert page.on_keyboard_event is not prior
        page.on_keyboard_event(unittest.mock.MagicMock(key="Escape"))
        assert page.overlay == []
        assert get_app_state().update_status is UpdateStatus.IDLE
        assert page.on_keyboard_event is prior

    def test_other_keys_fall_through_to_prior_handler(self):
        import unittest.mock

        reset_app_state()
        prior = unittest.mock.MagicMock()
        page = mock_page()
        page.on_keyboard_event = prior
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        event = unittest.mock.MagicMock(key="ArrowDown")
        page.on_keyboard_event(event)
        prior.assert_called_once_with(event)
        assert len(page.overlay) == 1


class TestAndroidInstallFinishesActivity:
    def test_applied_install_destroys_window(self, monkeypatch, tmp_path):
        import asyncio
        import unittest.mock
        from types import SimpleNamespace

        from core.update_checker import ApplyResult

        reset_app_state()
        monkeypatch.setattr("UI.custom.update_dialog.is_packaged", lambda: True)
        monkeypatch.setattr("UI.custom.update_dialog.is_android", lambda: True)
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")

        class FakeUpdater:
            def apply_update(self, update, apk_path, relaunch=False):
                assert relaunch is False
                return SimpleNamespace(result=ApplyResult.APPLIED)

        class FakeChecker:
            def download(self, update, dest, on_progress):
                return str(apk)

        monkeypatch.setattr("UI.custom.update_dialog.Updater", FakeUpdater)
        monkeypatch.setattr("UI.custom.update_dialog.UpdateChecker", FakeChecker)

        page = mock_page()
        page.window.destroy = unittest.mock.AsyncMock()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        _button(_overlay_dialog(page), "Download and install").on_click(None)

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
        monkeypatch.setattr("UI.custom.update_dialog.is_packaged", lambda: True)
        monkeypatch.setattr("UI.custom.update_dialog.is_android", lambda: True)
        apk = tmp_path / "app.apk"
        apk.write_bytes(b"apk")

        class FakeUpdater:
            def apply_update(self, update, apk_path, relaunch=False):
                return SimpleNamespace(result=ApplyResult.MANUAL_REQUIRED)

        class FakeChecker:
            def download(self, update, dest, on_progress):
                return str(apk)

        monkeypatch.setattr("UI.custom.update_dialog.Updater", FakeUpdater)
        monkeypatch.setattr("UI.custom.update_dialog.UpdateChecker", FakeChecker)

        page = mock_page()
        page.height = 800
        from UI.custom.update_dialog import show_update_dialog

        show_update_dialog(page, _update(), "0.4.10-dev3")
        _button(_overlay_dialog(page), "Download and install").on_click(None)

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
