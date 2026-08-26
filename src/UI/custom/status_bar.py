"""Collection status bar — live, at-a-glance verification of data collection.

A slim strip at the bottom of the app shell showing the collection state
(collecting / paused / auto-paused). On desktop, per-watcher health and
event counts are also shown; on Android the bar is compact (dot + label
only) to reclaim vertical space and avoid unnecessary disk I/O.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import flet as ft

from core.state.app_state import (
    KEY_COLLECTION_AUTO_PAUSED,
    KEY_COLLECTION_PAUSED,
    KEY_COLLECTION_RUNNING,
    KEY_WATCHER_HEALTH,
    get_app_state,
)
from utils.platform import OSType, detect_os

logger = logging.getLogger(__name__)

_STATE_COLORS = {
    "collecting": "#2e7d32",
    "paused": "#e65100",
    "auto_paused": "#1565c0",
    "stopped": "#c62828",
}

_STATE_LABELS = {
    "collecting": "Collecting",
    "paused": "Paused",
    "auto_paused": "Auto-paused \u00b7 screen off",
    "stopped": "Not collecting",
}

_DOT_SIZE = 8.0
_IS_ANDROID = detect_os() == OSType.ANDROID


class CollectionStatusBar(ft.Container):
    """Bottom shell strip; subscribes to app_state and refreshes live."""

    def __init__(
        self,
        storage: Any = None,
        page: ft.Page | None = None,
        refresh_s: float = 15.0,
    ):
        pad_v = 4 if _IS_ANDROID else 6
        super().__init__(
            padding=ft.padding.Padding(left=12, top=pad_v, right=12, bottom=pad_v),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border(
                top=ft.BorderSide(width=1, color=ft.Colors.OUTLINE_VARIANT)
            ),
        )
        self._storage = storage
        self._page = page
        self.refresh_s = max(1.0, refresh_s)

        state = get_app_state()
        self._dot = ft.Container(width=_DOT_SIZE, height=_DOT_SIZE, border_radius=4)
        self._state_text = ft.Text(size=12, weight=ft.FontWeight.BOLD)
        self._watcher_chips: list[ft.Text] = []
        self._count_text = ft.Text(size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._version_text = ft.Text(size=11, color=ft.Colors.ON_SURFACE_VARIANT)

        self._state_row = ft.Row(
            spacing=6,
            controls=[self._dot, self._state_text],
        )
        self._watcher_row = ft.Row(
            spacing=10,
            wrap=True,
            controls=self._watcher_chips,
            expand=True,
        )

        self.content = ft.Row(
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=self._build_controls(),
        )

        for key in (
            KEY_COLLECTION_RUNNING,
            KEY_COLLECTION_PAUSED,
            KEY_COLLECTION_AUTO_PAUSED,
            KEY_WATCHER_HEALTH,
        ):
            state.on_change(key, self._on_state_changed)

        self._refresh()

    def _build_controls(self) -> list[ft.Control]:
        controls: list[ft.Control] = [self._state_row]
        if not _IS_ANDROID:
            controls.extend([self._watcher_row, self._count_text, self._version_text])
        return controls

    # -- App shell wiring --

    def start_refresh(self, page: ft.Page) -> None:
        """Attach the live event-count refresh loop (called once at boot)."""
        self._page = page
        try:
            page.run_task(self._refresh_loop)
        except Exception:
            logger.exception("Failed to start status bar refresh loop")

    # -- State --

    def _collection_state(self) -> str:
        state = get_app_state()
        if not state.collection_running:
            return "stopped"
        if state.collection_auto_paused:
            return "auto_paused"
        if state.collection_paused:
            return "paused"
        return "collecting"

    def _watcher_parts(self) -> list[tuple[str, str]]:
        """(label, color) per watcher: name + last tick time, failure badge."""
        parts: list[tuple[str, str]] = []
        health = get_app_state().watcher_health
        for name in sorted(health):
            watcher = health[name]
            last = ""
            if watcher.last_tick_at is not None:
                last = watcher.last_tick_at.astimezone().strftime("%H:%M:%S")
            failures = " \u2717%d" % watcher.failures if watcher.failures else ""
            parts.append(
                (
                    f"{name} {last}{failures}".strip(),
                    ft.Colors.ON_SURFACE if last else ft.Colors.ON_SURFACE_VARIANT,
                )
            )
        return parts

    def _today_start_local(self) -> int:
        return int(
            (
                datetime.now()
                .astimezone()
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
            * 1000
        )

    def _refresh(self) -> None:
        state_key = self._collection_state()
        self._dot.bgcolor = _STATE_COLORS[state_key]
        self._state_text.value = _STATE_LABELS[state_key]
        self._state_text.color = _STATE_COLORS[state_key]

        if not _IS_ANDROID:
            self._watcher_chips.clear()
            for label, color in self._watcher_parts():
                self._watcher_chips.append(
                    ft.Text(label, size=11, weight=ft.FontWeight.W_500, color=color)
                )
            self._version_text.value = f"v{get_app_state().app_version}"

        self._safe_update()

    def _refresh_count(self) -> None:
        if _IS_ANDROID:
            return
        if self._storage is None:
            try:
                from core.storage import Storage

                self._storage = Storage(close_orphans=False)
            except Exception:
                logger.exception("Status bar could not open storage")
                self._count_text.value = "events: \u003f"
                return
        try:
            count = self._storage.count_events(since=self._today_start_local())
            self._count_text.value = f"{count} events today"
        except Exception:
            logger.exception("Status bar event count failed")
            self._count_text.value = "events: \u003f"

    def _safe_update(self) -> None:
        if self.parent is not None:
            try:
                self.update()
            except Exception:
                logger.debug("Status bar update skipped (not attached)", exc_info=True)

    def _on_state_changed(self, _key: str) -> None:
        self._refresh()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_s)
            self._refresh_count()
            self._safe_update()
            self._refresh()
