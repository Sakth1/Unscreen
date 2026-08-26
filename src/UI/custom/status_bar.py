"""Collection status bar — live, at-a-glance verification of data collection.

A slim strip at the bottom of the app shell showing the collection state
(collecting / paused / auto-paused).  Compact by design: dot + label only,
no per-watcher chips, event counts, or version text.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import flet as ft

from core.state.app_state import (
    KEY_COLLECTION_AUTO_PAUSED,
    KEY_COLLECTION_PAUSED,
    KEY_COLLECTION_RUNNING,
    KEY_WATCHER_HEALTH,
    get_app_state,
)

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


class CollectionStatusBar(ft.Container):
    """Bottom shell strip; subscribes to app_state and refreshes live."""

    def __init__(
        self,
        storage: Any = None,
        page: ft.Page | None = None,
        refresh_s: float = 15.0,
    ):
        super().__init__(
            padding=ft.padding.Padding(left=12, top=4, right=12, bottom=4),
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

        self.content = ft.Row(
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[self._dot, self._state_text],
        )

        for key in (
            KEY_COLLECTION_RUNNING,
            KEY_COLLECTION_PAUSED,
            KEY_COLLECTION_AUTO_PAUSED,
            KEY_WATCHER_HEALTH,
        ):
            state.on_change(key, self._on_state_changed)

        self._refresh()

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

    def _refresh(self) -> None:
        state_key = self._collection_state()
        self._dot.bgcolor = _STATE_COLORS[state_key]
        self._state_text.value = _STATE_LABELS[state_key]
        self._state_text.color = _STATE_COLORS[state_key]
        self._safe_update()

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
            self._refresh()
