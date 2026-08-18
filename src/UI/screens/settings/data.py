import asyncio
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
from UI.components.card_section import CardSection
from UI.components.data_section import DataSection
from UI.components.empty_state import EmptyState
from UI.components.error_boundary import spawn
from UI.components.skeleton import status_card_skeleton
from UI.screens.settings.builders import section_scaffold
from utils.flet_helpers import safe_update, show_snack_bar
from utils.paths import get_export_dir
from utils.platform import is_android

logger = logging.getLogger(__name__)

_LOG_LEVELS = ["INFO", "DEBUG", "WARNING", "ERROR"]


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
            on_select=self._log_level_changed,
        )

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
        self._export_db_btn = ft.OutlinedButton(
            "Export database",
            icon=ft.Icons.SAVE_ALT,
            on_click=self._export_db,
        )
        self._file_picker = ft.FilePicker()
        self._export_status = ft.Column(controls=[], spacing=8)

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
            CardSection(
                "Logging",
                [
                    self._log_level_dropdown,
                    ft.Text(
                        f"Log file: {get_log_path() or 'not created yet'}", size=12
                    ),
                ],
            ),
            CardSection(
                "Export",
                [
                    ft.Text(
                        "Download all collected raw events, or a copy of the "
                        "local database, to a file."
                    ),
                    ft.Row(
                        controls=[
                            self._export_csv_btn,
                            self._export_json_btn,
                            self._export_db_btn,
                        ],
                        wrap=True,
                        run_spacing=8,
                    ),
                    self._export_status,
                ],
            ),
            CardSection(
                "Logs",
                [self._view_logs_btn, self._clear_logs_btn],
            ),
            CardSection(
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

    def _log_level_changed(self, event) -> None:
        level = self._log_level_dropdown.value or getattr(event, "data", None)
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
        if self._collection_manager is None:
            self._toast("Collection services unavailable")
            return
        runner = self._page.run_task if self._page is not None else None
        section = DataSection(
            load=lambda: self._export_data(csv_format),
            content=lambda path: ft.Text(
                f"Exported to {path}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            skeleton=status_card_skeleton(height=48),
            empty_when=lambda path: path is None,
            empty=EmptyState(
                icon=ft.Icons.FILE_DOWNLOAD_OFF,
                headline="Nothing to export yet",
                body="Collect some data first.",
                compact=True,
            ),
            error_message="Export failed",
            runner=runner,
        )
        self._export_status.controls = [section]
        safe_update(self)
        spawn(section.run(), runner=runner)

    def _export_data(self, csv_format: bool) -> str | None:
        """Read events and write the export file; ``None`` when empty."""
        cm = self._collection_manager
        if cm is None:
            raise RuntimeError("collection services unavailable")
        rows = cm.storage.get_raw_events()
        if not rows:
            return None
        filename, data = (
            ExportService.prepare_raw_events_csv(rows)
            if csv_format
            else ExportService.prepare_raw_events(rows)
        )
        export_dir = get_export_dir()
        path = os.path.join(export_dir, filename)
        with open(path, "wb") as fp:
            fp.write(data)
        return path

    def _export_db(self, _event) -> None:
        if self._collection_manager is None:
            self._toast("Collection services unavailable")
            return
        if self._page is not None:
            self._page.run_task(self._export_db_pick_location)
            return
        self._export_db_direct()

    async def _export_db_pick_location(self) -> None:
        # The FilePicker is a Service control: attaching it to the overlay
        # makes the client render it as an ErrorControl ("Unknown control:
        # FilePicker" - a red block). Services self-register with the page's
        # service registry, so save_file/get_directory_path work without any
        # attach. The VACUUM-INTO snapshot runs off the event loop so a large
        # database cannot freeze the UI.
        snapshot = await asyncio.to_thread(self._db_snapshot)
        if snapshot is None:
            return
        filename, data = snapshot
        picker = self._file_picker
        try:
            if is_android():
                path = await picker.save_file(
                    file_name=filename,
                    src_bytes=data,
                )
            else:
                directory = await picker.get_directory_path(
                    dialog_title="Choose export folder",
                )
                if directory is None:
                    return
                path = os.path.join(directory, filename)
                with open(path, "wb") as fp:
                    fp.write(data)
        except Exception:
            logger.exception("Database export failed")
            self._toast("Export failed")
            return
        if path:
            self._toast(f"Exported to {path}")

    def _export_db_direct(self) -> None:
        snapshot = self._db_snapshot()
        if snapshot is None:
            return
        filename, data = snapshot
        try:
            path = os.path.join(get_export_dir(), filename)
            with open(path, "wb") as fp:
                fp.write(data)
        except Exception:
            logger.exception("Database export failed")
            self._toast("Export failed")
            return
        self._toast(f"Exported to {path}")

    def _db_snapshot(self) -> tuple[str, bytes] | None:
        try:
            snapshot = ExportService.prepare_db_snapshot(
                self._collection_manager.storage.db_path
            )
        except Exception:
            logger.exception("Failed to snapshot the database")
            self._toast("Could not read the database")
            return None
        if snapshot is None:
            self._toast("Nothing to export yet")
            return None
        return snapshot

    def _view_logs(self, _event) -> None:
        if self._page is None:
            return
        lines = read_log_lines(500)
        if lines:
            content = ft.Column(
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                controls=[
                    ft.Text(
                        "\n".join(lines),
                        font_family="monospace",
                        size=12,
                        selectable=True,
                    )
                ],
            )
        else:
            content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=EmptyState(
                    icon=ft.Icons.DESCRIPTION,
                    headline="No log data yet",
                    body="Logs will appear here once events are collected.",
                    compact=True,
                    height=None,
                ),
            )
        dialog = ft.AlertDialog(
            title=ft.Text("Recent logs"),
            content=ft.Container(
                width=600,
                height=400,
                padding=8,
                content=content,
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
        from UI.components.dialogs import show_confirm_dialog

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
