import logging
import types
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from core.config_manager import ConfigManager


class _FakeSwitch:
    """Minimal stand-in for a Flet control carrying a boolean ``value``."""

    def __init__(self, value):
        self.value = value


def _event(value):
    return types.SimpleNamespace(control=_FakeSwitch(value))


def _config(tmp_path) -> ConfigManager:
    return ConfigManager(path=str(tmp_path / "config.json"))


def _walk(control):
    """Yield a control and all its descendants (controls + Container.content)."""
    yield control
    for child in getattr(control, "controls", []) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)


# ═══════════════════════════════════════════════════════════════════
#  AFK watcher — config-driven thresholds
#  ═══════════════════════════════════════════════════════════════════


class TestAfkThresholds:
    @pytest.mark.asyncio
    async def test_default_thresholds(self):
        from core.collectors.windows.afk import AfkWatcher

        watcher = AfkWatcher()
        with patch("core.collectors.windows.afk._idle_seconds", return_value=30):
            assert (await watcher.tick()).data["status"] == "active"
        with patch("core.collectors.windows.afk._idle_seconds", return_value=120):
            assert (await watcher.tick()).data["status"] == "idle"
        with patch("core.collectors.windows.afk._idle_seconds", return_value=400):
            assert (await watcher.tick()).data["status"] == "away"

    @pytest.mark.asyncio
    async def test_config_thresholds(self, tmp_path):
        from core.collectors.windows.afk import AfkWatcher

        config = _config(tmp_path)
        config.afk_idle_threshold_s = 10
        config.afk_away_threshold_s = 30
        watcher = AfkWatcher(app_config=config)

        with patch("core.collectors.windows.afk._idle_seconds", return_value=5):
            assert (await watcher.tick()).data["status"] == "active"
        with patch("core.collectors.windows.afk._idle_seconds", return_value=20):
            assert (await watcher.tick()).data["status"] == "idle"
        with patch("core.collectors.windows.afk._idle_seconds", return_value=31):
            assert (await watcher.tick()).data["status"] == "away"

    @pytest.mark.asyncio
    async def test_zero_threshold_keeps_away_at_boundary(self, tmp_path):
        from core.collectors.windows.afk import AfkWatcher

        config = _config(tmp_path)
        config.afk_away_threshold_s = 0
        watcher = AfkWatcher(app_config=config)
        with patch("core.collectors.windows.afk._idle_seconds", return_value=1):
            assert (await watcher.tick()).data["status"] == "away"


# ═══════════════════════════════════════════════════════════════════
#  Logging — apply_root_level
#  ═══════════════════════════════════════════════════════════════════


class TestApplyRootLevel:
    @pytest.fixture
    def restore_level(self):
        previous = logging.getLogger().level
        yield
        logging.getLogger().setLevel(previous)

    def test_applies_valid_level(self, restore_level):
        from core.logging_setup import apply_root_level

        apply_root_level("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_rejects_unknown_level(self, restore_level):
        from core.logging_setup import apply_root_level

        logging.getLogger().setLevel(logging.DEBUG)
        apply_root_level("NOISY")
        assert logging.getLogger().level == logging.DEBUG

    def test_sets_file_handler_level(self, restore_level):
        import io

        from core.logging_setup import apply_root_level

        handler = logging.StreamHandler(io.StringIO())
        logging.getLogger().addHandler(handler)
        try:
            apply_root_level("WARNING")
            assert handler.level == logging.WARNING
        finally:
            logging.getLogger().removeHandler(handler)


# ═══════════════════════════════════════════════════════════════════
#  Settings sections — headless construction & handler behavior
#  ═══════════════════════════════════════════════════════════════════


class TestGeneralSection:
    def test_constructs_headless(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        assert section.content is not None

    def test_collection_switch_writes_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        section._on_collection_changed(_event(False))
        assert section._config.collection_enabled is False

    def test_url_switch_writes_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        section._on_url_changed(_event(False))
        assert section._config.url_extraction_enabled is False

    def test_watcher_toggle_adds_and_removes(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        section._on_watcher_toggled(_event(True), "power")
        assert section._config.watchers_enabled == ["afk", "foreground", "power"]

        section._on_watcher_toggled(_event(False), "afk")
        assert "afk" not in section._config.watchers_enabled

    def test_interval_submit_parses_and_persists(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=_FakeSwitch("12.5"))
        section._on_interval_submitted(event, "foreground")
        assert section._config.get_interval("foreground", 2.0) == 12.5

    def test_interval_submit_rejects_garbage(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=_FakeSwitch("abc"))
        section._on_interval_submitted(event, "foreground")
        assert section._config.get_interval("foreground", 2.0) == 2.0

    def test_wrapping_rows_never_contain_expand_children(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo
        from UI.screens.settings.data import DataDiagnostics
        from UI.screens.settings.general import General

        for section in (
            General(config=_config(tmp_path)),
            DataDiagnostics(config=_config(tmp_path)),
            AppInfo(config=_config(tmp_path)),
        ):
            for control in _walk(section.content):
                if isinstance(control, ft.Row) and control.wrap:
                    assert not any(
                        getattr(child, "expand", False) for child in control.controls
                    ), "wrapped Row must not contain expand children"

    def test_theme_row_wraps_on_narrow_widths(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        wrappers = [
            c for c in _walk(section.content) if isinstance(c, ft.Row) and c.wrap
        ]
        assert wrappers, "no wrapping rows in the General section"
        rows_with_theme = [
            c for c in wrappers if section._theme_btn in (c.controls or [])
        ]
        assert rows_with_theme, "theme segmented button is not in a wrapping row"

    def test_watcher_rows_wrap_on_narrow_widths(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        wrappers = {
            id(c): c for c in _walk(section.content) if isinstance(c, ft.Row) and c.wrap
        }
        captured = [c for c in wrappers.values() if (c.controls or [])]
        for toggle in section._watcher_toggles.values():
            assert any(
                toggle in (row.controls or []) for row in captured
            ), "watcher toggle is not in a wrapping row"

    def test_theme_change_writes_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=types.SimpleNamespace(selected={"dark"}))
        section._on_theme_changed(event)
        assert section._config.theme_mode == "dark"

    def test_accent_theme_picker_defaults_to_purple(self, tmp_path):
        from core.theme import theme_names
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        assert section._theme_picker.value == "purple"
        assert len(section._theme_picker.options) == len(theme_names())

    def test_accent_theme_picker_wired_via_on_select(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        assert section._theme_picker.on_select == section._on_accent_theme_changed
        assert "on_change" not in ft.Dropdown.__dataclass_fields__, (
            "Dropdown has no on_change event in this flet version; "
            "post-init on_change assignment never dispatches"
        )

    def test_accent_theme_change_writes_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=types.SimpleNamespace(value="teal"))
        section._on_accent_theme_changed(event)
        assert section._config.theme == "teal"

    def test_accent_theme_change_ignores_unknown(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=types.SimpleNamespace(value="neon"))
        section._on_accent_theme_changed(event)
        assert section._config.theme == "purple"

    def test_accent_theme_change_applies_to_page(self, tmp_path):
        from unittest.mock import MagicMock

        from UI.screens.settings.general import General

        page = MagicMock()
        section = General(config=_config(tmp_path), page=page)
        event = types.SimpleNamespace(control=types.SimpleNamespace(value="blue"))
        section._on_accent_theme_changed(event)
        assert section._config.theme == "blue"
        assert page.theme is not None
        assert page.dark_theme is not None
        page.update.assert_called_once()

    def test_maximized_switch_writes_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        section._on_maximized_changed(_event(False))
        assert section._config.start_maximized is False

    def test_autostart_syncs_registry(self, tmp_path, monkeypatch):
        from UI.screens.settings.general import General

        calls = []
        fake = types.SimpleNamespace(
            enable=lambda: calls.append("enable") or True,
            disable=lambda: calls.append("disable") or True,
        )
        monkeypatch.setattr("UI.screens.settings.general.auto_start", fake)

        section = General(config=_config(tmp_path))
        section._on_autostart_changed(_event(True))
        assert calls == ["enable"]
        assert section._config.auto_start_enabled is True

        section._on_autostart_changed(_event(False))
        assert calls == ["enable", "disable"]
        assert section._config.auto_start_enabled is False

    def test_autostart_failure_keeps_config(self, tmp_path, monkeypatch):
        from UI.screens.settings.general import General

        fake = types.SimpleNamespace(
            enable=lambda: False,
            disable=lambda: False,
        )
        monkeypatch.setattr("UI.screens.settings.general.auto_start", fake)

        section = General(config=_config(tmp_path))
        section._on_autostart_changed(_event(True))
        assert section._config.auto_start_enabled is False

    def test_afk_thresholds_write_config(self, tmp_path):
        from UI.screens.settings.general import General

        section = General(config=_config(tmp_path))
        event = types.SimpleNamespace(control=_FakeSwitch("15"))
        section._on_idle_threshold_changed(event)
        assert section._config.afk_idle_threshold_s == 15.0

        event = types.SimpleNamespace(control=_FakeSwitch("45"))
        section._on_away_threshold_changed(event)
        assert section._config.afk_away_threshold_s == 45.0

    def test_watcher_toggle_restarts_running_collection(self, tmp_path):
        from UI.screens.settings.general import General

        cm = MagicMock()
        cm.is_running = True
        page = MagicMock()
        section = General(config=_config(tmp_path), collection_manager=cm, page=page)
        section._on_watcher_toggled(_event(True), "power")
        page.run_task.assert_called_once_with(cm.restart)
        assert section._config.watchers_enabled == ["afk", "foreground", "power"]

    def test_watcher_toggle_does_not_restart_when_stopped(self, tmp_path):
        from UI.screens.settings.general import General

        cm = MagicMock()
        cm.is_running = False
        page = MagicMock()
        section = General(config=_config(tmp_path), collection_manager=cm, page=page)
        section._on_watcher_toggled(_event(True), "power")
        page.run_task.assert_not_called()

    def test_cards_stretch_across_full_width(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo
        from UI.screens.settings.data import DataDiagnostics
        from UI.screens.settings.general import General

        for section in (
            General(config=_config(tmp_path)),
            DataDiagnostics(config=_config(tmp_path)),
            AppInfo(config=_config(tmp_path)),
        ):
            scaffold = section.content
            assert isinstance(scaffold, ft.Column)
            assert (
                scaffold.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
            ), "scaffold must stretch its children across the full width"
            cards_column = scaffold.controls[1]
            assert (
                cards_column.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
            ), "cards column must stretch cards across the full width"
            assert cards_column.controls, "no cards in the section"
            for card in cards_column.controls:
                assert isinstance(card, ft.Card), "card stack must contain cards"
                inner = next(
                    c
                    for c in _walk(card)
                    if isinstance(c, ft.Column) and c.spacing == 8
                )
                assert (
                    inner.horizontal_alignment == ft.CrossAxisAlignment.STRETCH
                ), "card content must span the card width"

    def test_on_sub_route_refreshes_values(self, tmp_path):
        from UI.screens.settings.general import General

        config = _config(tmp_path)
        section = General(config=config)
        config.collection_enabled = False
        config.auto_start_enabled = True
        section.on_sub_route("/settings/general")
        assert section._collection_switch.value is False
        assert section._autostart_switch.value is True


class TestDataDiagnosticsSection:
    def test_constructs_headless(self, tmp_path):
        from UI.screens.settings.data import DataDiagnostics

        section = DataDiagnostics(config=_config(tmp_path))
        assert section.content is not None

    def test_log_level_change_persists_and_applies(self, tmp_path):
        from UI.screens.settings.data import DataDiagnostics

        with patch("UI.screens.settings.data.apply_root_level") as apply:
            section = DataDiagnostics(config=_config(tmp_path))
            section._log_level_dropdown.value = "DEBUG"
            section._log_level_changed(None)
        assert section._config.log_level == "DEBUG"
        apply.assert_called_once_with("DEBUG")

    def test_log_level_dropdown_wired_via_on_select(self, tmp_path):
        from UI.screens.settings.data import DataDiagnostics

        section = DataDiagnostics(config=_config(tmp_path))
        assert section._log_level_dropdown.on_select == section._log_level_changed

    def test_export_writes_files(self, tmp_path, monkeypatch):
        from UI.screens.settings.data import DataDiagnostics

        storage = MagicMock()
        storage.get_raw_events.return_value = [
            {
                "id": 1,
                "device_id": "dev",
                "platform": "windows",
                "event_type": "foreground_transition",
                "timestamp": 1000.0,
                "collected_at": 1000.0,
                "payload": {"app": "Code.exe"},
                "source": "foreground",
            }
        ]
        cm = MagicMock()
        cm.storage = storage
        monkeypatch.setattr(
            "UI.screens.settings.data.get_export_dir", lambda: str(tmp_path)
        )

        section = DataDiagnostics(config=_config(tmp_path), collection_manager=cm)
        section._export_csv(None)
        section._export_json(None)

        files = [p.name for p in tmp_path.iterdir()]
        assert any(f.startswith("raw_events") and f.endswith(".csv") for f in files)
        assert any(f.startswith("raw_events") and f.endswith(".json") for f in files)

    def test_export_empty_data_is_noop(self, tmp_path, monkeypatch):
        from UI.screens.settings.data import DataDiagnostics

        storage = MagicMock()
        storage.get_raw_events.return_value = []
        cm = MagicMock()
        cm.storage = storage
        monkeypatch.setattr(
            "UI.screens.settings.data.get_export_dir", lambda: str(tmp_path)
        )

        section = DataDiagnostics(config=_config(tmp_path), collection_manager=cm)
        section._export_csv(None)
        assert list(tmp_path.iterdir()) == []

    def test_export_buttons_row_wraps_on_narrow_widths(self, tmp_path):
        from UI.screens.settings.data import DataDiagnostics

        section = DataDiagnostics(config=_config(tmp_path))
        rows = [c for c in _walk(section.content) if isinstance(c, ft.Row) and c.wrap]
        assert any(
            section._export_csv_btn in (row.controls or []) for row in rows
        ), "export buttons are not in a wrapping row"

    def test_clear_logs_calls_cleanup(self, tmp_path, monkeypatch):
        from UI.screens.settings.data import DataDiagnostics

        cleared = []
        monkeypatch.setattr(
            "UI.screens.settings.data.clear_logs", lambda: cleared.append(True)
        )
        section = DataDiagnostics(config=_config(tmp_path))
        section._clear_logs(None)
        assert cleared == [True]

    def test_clear_all_data_requires_collection_manager(self, tmp_path):
        from UI.screens.settings.data import DataDiagnostics

        section = DataDiagnostics(config=_config(tmp_path))
        cm = MagicMock()
        section._collection_manager = cm
        section._clear_all_data()
        cm.clear_all_data.assert_called_once()


class TestAppInfoSection:
    def test_constructs_headless(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo

        section = AppInfo(config=_config(tmp_path))
        assert section.content is not None

    def test_auto_update_switch_writes_config(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo

        section = AppInfo(config=_config(tmp_path))
        section._on_auto_update_changed(_event(False))
        assert section._config.auto_update_enabled is False

    def test_update_buttons_row_wraps_on_narrow_widths(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo

        section = AppInfo(config=_config(tmp_path))
        rows = [c for c in _walk(section.content) if isinstance(c, ft.Row) and c.wrap]
        assert any(
            section._check_btn in (row.controls or []) for row in rows
        ), "update buttons are not in a wrapping row"

    def test_check_for_updates_noop_without_page(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo

        section = AppInfo(config=_config(tmp_path))
        section._check_for_updates(None)
        assert section._checking is False

    def test_run_update_check_up_to_date(self, tmp_path, monkeypatch):
        import asyncio

        from UI.screens.settings.app_info import AppInfo

        checker = MagicMock()
        checker.check_for_update.return_value = None
        monkeypatch.setattr(
            "UI.screens.settings.app_info.UpdateChecker", lambda: checker
        )

        section = AppInfo(config=_config(tmp_path))
        asyncio.run(section._run_update_check())
        checker.check_for_update.assert_called_once_with(include_prereleases=False)
        assert section._checking is False

    def test_run_update_check_passes_prerelease_flag(self, tmp_path, monkeypatch):
        import asyncio

        from UI.screens.settings.app_info import AppInfo

        config = _config(tmp_path)
        config.check_prereleases = True
        checker = MagicMock()
        checker.check_for_update.return_value = None
        monkeypatch.setattr(
            "UI.screens.settings.app_info.UpdateChecker", lambda: checker
        )

        section = AppInfo(config=config)
        asyncio.run(section._run_update_check())
        checker.check_for_update.assert_called_once_with(include_prereleases=True)

    def test_prerelease_switch_writes_config(self, tmp_path):
        from UI.screens.settings.app_info import AppInfo

        section = AppInfo(config=_config(tmp_path))
        section._on_prerelease_changed(_event(True))
        assert section._config.check_prereleases is True
        assert (tmp_path / "config.json").exists()
        section._on_prerelease_changed(_event(False))
        assert section._config.check_prereleases is False


class TestSettingsScreen:
    def test_builds_three_sections(self, tmp_path):
        from UI.screens.settings_screen import Settings

        screen = Settings(config=_config(tmp_path))
        routes = {d.route for d in screen._get_secondary_options()}
        assert routes == {"/settings/general", "/settings/data", "/settings/app-info"}
        assert screen.general_section.content is not None
        assert screen.data_section.content is not None
        assert screen.app_info_section.content is not None

    def test_sections_scroll_and_wrap_content(self, tmp_path):
        from UI.screens.settings_screen import Settings

        screen = Settings(config=_config(tmp_path))
        for section in (
            screen.general_section,
            screen.data_section,
            screen.app_info_section,
        ):
            assert section.content.scroll is not None
            assert len(section.content.controls) == 2  # header row + cards column

    def test_on_back_callback_adds_back_button(self, tmp_path):
        from UI.screens.settings_screen import Settings

        screen = Settings(config=_config(tmp_path), on_back=lambda: None)
        for section in (
            screen.general_section,
            screen.data_section,
            screen.app_info_section,
        ):
            header = section.content.controls[0]
            assert isinstance(header, ft.Row)
            assert any(c.icon == ft.Icons.ARROW_BACK for c in header.controls)

    def test_section_back_button_triggers_callback(self, tmp_path):
        from UI.screens.settings_screen import Settings

        clicked = []
        screen = Settings(config=_config(tmp_path), on_back=lambda: clicked.append(1))
        header = screen.general_section.content.controls[0]
        back = next(c for c in header.controls if c.icon == ft.Icons.ARROW_BACK)
        back.on_click(None)
        assert clicked == [1]

    def test_no_on_back_means_no_back_button(self, tmp_path):
        from UI.screens.settings_screen import Settings

        screen = Settings(config=_config(tmp_path))
        header = screen.general_section.content.controls[0]
        assert all(
            getattr(c, "icon", None) != ft.Icons.ARROW_BACK for c in header.controls
        )
