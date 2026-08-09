import logging
import os
from typing import Any, Callable, Optional

import flet as ft

from core.application.export_service import ExportService
from core.config_manager import ConfigManager
from core.logging_setup import (
    apply_root_level,
    clear_logs,
    get_log_path,
    read_log_lines,
)
from UI.screens.settings.builders import section_scaffold
from UI.screens.settings.settings_card import SettingsCard
from utils.flet_helpers import safe_update, show_snack_bar
from utils.paths import get_export_dir

logger = logging.getLogger(__name__)

_LOG_LEVELS = ["INFO", "DEBUG", "WARNING", "ERROR"]

_EMPTY_LOG_MESSAGE = "No log data yet."


class DataDiagnostics(ft.Container):
    """Data & diagnostics section rendered under ``/settings/data``.

    Exposes log management, data export, and destructive operations.
    Destructive actions require confirmation before running.
    """

    def __init__(
        self,
        config: ConfigManager,
        collection_manager: Any = None,
        page: ft.Page | None = None,
        on_back: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._config = config or ConfigManager()
        self._collection_manager = collection_manager
        self._page = page
        self._log_dialog: ft.AlertDialog | None = None

        self._log_level_dropdown = ft.Dropdown(
            label="Log level",
            options=[
                ft.dropdown.Option(key=level, text=level) for level in _LOG_LEVELS
            ],
            value=(
                self._config.log_level
                if self._config.log_level in _LOG_LEVELS
                else "INFO"
            ),
        )
        self._log_level_dropdown.on_change = self._log_level_changed

        self._export_csv_btn = ft.OutlinedButton(
            "Export as CSV",
            icon=ft.Icons.DOWNLOAD,
            on_click=self._export_csv,
        )
        self._export_json_btn = ft.OutlinedButton(
            "Export as JSON",
            icon=ft.Icons.DOWNLOAD,
            on_click=self._export_json,
        )

        self._view_logs_btn = ft.OutlinedButton(
            "View recent logs",
            icon=ft.Icons.DESCRIPTION,
            on_click=self._view_logs,
        )
        self._clear_logs_btn = ft.OutlinedButton(
            "Clear logs",
            icon=ft.Icons.DELETE_SWEEP,
            on_click=self._clear_logs,
        )

        self._clear_data_btn = ft.FilledButton(
            "Clear all data",
            icon=ft.Icons.DELETE_FOREVER,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ERROR_CONTAINER),
            on_click=self._confirm_clear_all_data,
        )

        cards = [
            SettingsCard(
                "Logging",
                [
                    self._log_level_dropdown,
                    ft.Text(
                        f"Log file: {get_log_path() or 'not created yet'}", size=12
                    ),
                ],
            ),
            SettingsCard(
                "Export",
                [
                    ft.Text("Download all collected raw events to a file."),
                    ft.Row(
                        controls=[self._export_csv_btn, self._export_json_btn],
                        wrap=True,
                        run_spacing=8,
                    ),
                ],
            ),
            SettingsCard(
                "Logs",
                [self._view_logs_btn, self._clear_logs_btn],
            ),
            SettingsCard(
                "Danger zone",
                [
                    ft.Text(
                        "Permanently delete all locally stored usage data. "
                        "This cannot be undone.",
                        size=12,
                    ),
                    self._clear_data_btn,
                ],
            ),
        ]

        self.content = section_scaffold("Data & diagnostics", cards, on_back=on_back)

    # ── Handlers ──────────────────────────────────────────────────────────

    def _log_level_changed(self, _event) -> None:
        level = self._log_level_dropdown.value
        if level not in _LOG_LEVELS:
            return
        self._config.log_level = level
        self._config.save()
        apply_root_level(level)
        self._toast(f"Log level set to {level}")

    def _export_csv(self, _event) -> None:
        self._export(csv_format=True)

    def _export_json(self, _event) -> None:
        self._export(csv_format=False)

    def _export(self, csv_format: bool) -> None:
        cm = self._collection_manager
        if cm is None:
            self._toast("Collection services unavailable")
            return
        try:
            rows = cm.storage.get_raw_events()
        except Exception:
            logger.exception("Failed to read events for export")
            self._toast("Could not read the database")
            return
        if not rows:
            self._toast("Nothing to export yet")
            return
        try:
            filename, data = (
                ExportService.prepare_raw_events_csv(rows)
                if csv_format
                else ExportService.prepare_raw_events(rows)
            )
            export_dir = get_export_dir()
            path = os.path.join(export_dir, filename)
            with open(path, "wb") as fp:
                fp.write(data)
        except Exception:
            logger.exception("Export failed")
            self._toast("Export failed")
            return
        self._toast(f"Exported to {path}")

    def _view_logs(self, _event) -> None:
        if self._page is None:
            return
        lines = read_log_lines(500)
        content = "\n".join(lines) if lines else _EMPTY_LOG_MESSAGE
        dialog = ft.AlertDialog(
            title=ft.Text("Recent logs"),
            content=ft.Container(
                width=600,
                height=400,
                padding=8,
                content=ft.Column(
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    controls=[
                        ft.Text(
                            content,
                            font_family="monospace",
                            size=12,
                            selectable=True,
                        )
                    ],
                ),
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self._close_dialog(dialog))
            ],
        )
        self._log_dialog = dialog
        self._page.show_dialog(dialog)
        safe_update(self._page)

    def _clear_logs(self, _event) -> None:
        try:
            clear_logs()
        except Exception:
            logger.exception("Failed to clear logs")
            self._toast("Could not clear logs")
            return
        self._toast("Logs cleared")

    def _confirm_clear_all_data(self, _event) -> None:
        if self._page is None:
            return
        from UI.dialogs import show_confirm_dialog

        show_confirm_dialog(
            self._page,
            "Clear all data",
            "This permanently deletes every collected event from this device. "
            "Are you sure?",
            confirm_text="Clear",
            on_confirm=self._clear_all_data,
        )

    def _clear_all_data(self) -> None:
        cm = self._collection_manager
        if cm is None:
            logger.warning("No collection manager; data not cleared")
            return
        try:
            cm.clear_all_data()
        except Exception:
            logger.exception("Failed to clear all data")
            self._toast("Could not clear data")
            return
        self._toast("All data cleared")

    def _close_dialog(self, dialog: ft.AlertDialog) -> None:
        dialog.open = False
        if self._page is not None:
            self._page.update()

    def _toast(self, message: str) -> None:
        if self._page is not None:
            show_snack_bar(self._page, message)

    def on_sub_route(self, route: str) -> None:
        """Refresh control values when the section becomes visible."""
        level = self._config.log_level
        self._log_level_dropdown.value = level if level in _LOG_LEVELS else "INFO"
        if self.parent is not None:
            self.update()
