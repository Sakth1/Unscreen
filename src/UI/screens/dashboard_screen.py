"""Dashboard screen (milestone #26).

Shows the app-usage picture for the selected time range: a range
selector on the top-left, a summary card, and the top-apps card. Every
card is aware of the selected range and refreshes through a shared
store mapping (``fetch_range``).

Auto-layout: content is capped and centered at the layout's
``content_max_width`` on wide form factors; the card grid stacks in a
single column on phones/portrait tablets and becomes a two-column grid
on tablet-landscape/desktop layouts. A periodic refresh loop re-runs the
card loads in place (no skeleton flash) while the screen is attached.

Lifecycle contract: constructed headless-safe with zero arguments —
no page or storage access until :meth:`start_refresh` wires the page and
the cards' loads start. Pass a ``store`` in tests; ``now`` can be
injected for deterministic range math.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

import flet as ft

from core.analytics import AnalyticsStore
from UI.components.dashboard_summary import DashboardSummaryCard
from UI.components.error_boundary import spawn
from UI.components.top_apps_card import (
    RANGE_LAST_7,
    RANGE_LAST_30,
    RANGE_MONTH,
    RANGE_TODAY,
    RANGE_WEEK,
    RANGE_YESTERDAY,
    TopAppsCard,
)
from UI.layout.models import AppLayout, ScreenFormFactor
from UI.screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

#: Ordered (range key, dropdown label) pairs; the first is the default.
_RANGE_OPTIONS: list[tuple[str, str]] = [
    (RANGE_TODAY, "Today"),
    (RANGE_YESTERDAY, "Yesterday"),
    (RANGE_WEEK, "This week"),
    (RANGE_LAST_7, "Last 7 days"),
    (RANGE_MONTH, "This month"),
    (RANGE_LAST_30, "Last 30 days"),
]

_WIDE_FORM_FACTORS = (ScreenFormFactor.TABLET_LANDSCAPE, ScreenFormFactor.DESKTOP)


def _date_span(start: datetime.date, end: datetime.date) -> str:
    """Compact date span: "Aug 17 – 23", month shown on the end only if it differs."""
    end_fmt = f"{end:%d}" if start.month == end.month else f"{end:%b %d}"
    return f"{start:%b %d} – {end_fmt}"


def _range_subtitle(range_key: str, now: datetime.datetime | None = None) -> str:
    """Human date span for the selected range (e.g. "Aug 17 – 23")."""
    now = now or datetime.datetime.now()
    today = now.date()
    if range_key == RANGE_TODAY:
        return today.strftime("%A, %b %d")
    if range_key == RANGE_YESTERDAY:
        return (today - datetime.timedelta(days=1)).strftime("%A, %b %d")
    if range_key == RANGE_WEEK:
        monday = today - datetime.timedelta(days=today.weekday())
        return _date_span(monday, monday + datetime.timedelta(days=6))
    if range_key == RANGE_LAST_7:
        return _date_span(today - datetime.timedelta(days=6), today)
    if range_key == RANGE_MONTH:
        return today.strftime("%B %Y")
    if range_key == RANGE_LAST_30:
        return _date_span(today - datetime.timedelta(days=29), today)
    return ""


class Dashboard(BaseScreen):
    """Range-aware usage dashboard with auto-layouting card grid."""

    def __init__(
        self,
        store: AnalyticsStore | None = None,
        page: ft.Page | None = None,
        refresh_s: float = 30.0,
        now: datetime.datetime | None = None,
    ):
        super().__init__()
        self.title = "Dashboard"
        self._store = store
        self._page = page
        self.refresh_s = max(5.0, refresh_s)
        self._now = now
        self._layout: AppLayout | None = None
        self._range_key = RANGE_TODAY

        self._range_dropdown = ft.Dropdown(
            width=170,
            dense=True,
            options=[
                ft.dropdown.Option(key=key, text=label) for key, label in _RANGE_OPTIONS
            ],
            value=self._range_key,
            on_select=self._on_range_selected,
        )
        self._heading = ft.Text("Dashboard", size=22, weight=ft.FontWeight.BOLD)
        self._subtitle = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self._header_row = ft.Row(
            spacing=16,
            wrap=True,
            run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(
                    ft.Icons.DATE_RANGE,
                    size=18,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                self._range_dropdown,
                ft.Column(
                    spacing=2,
                    controls=[self._heading, self._subtitle],
                ),
            ],
        )

        self._cards: list[ft.Control] = []
        self._stacked = ft.Column(
            spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
        )
        self._grid = ft.ResponsiveRow(spacing=16)
        self._scroll = ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=16,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[self._header_row, self._stacked],
        )
        self._inner = ft.Container(content=self._scroll)
        self.alignment = ft.Alignment.CENTER
        self.content = self._inner

        self._build_cards()
        self._update_subtitle()

    # ── App shell wiring ────────────────────────────────────────────────────

    def start_refresh(self, page: ft.Page) -> None:
        """Attach the periodic card refresh loop (called once at boot)."""
        self._page = page
        try:
            page.run_task(self._refresh_loop)
        except Exception:
            logger.exception("Failed to start dashboard refresh loop")

    # ── Range selection ─────────────────────────────────────────────────────

    def _on_range_selected(self, event) -> None:
        value = getattr(event.control, "value", None) or getattr(event, "data", None)
        keys = {key for key, _ in _RANGE_OPTIONS}
        if value not in keys or value == self._range_key:
            return
        self._range_key = value
        self._range_dropdown.value = value
        self._build_cards()
        self._update_subtitle()
        self._safe_update()
        self._run_cards()

    def _update_subtitle(self) -> None:
        self._subtitle.value = _range_subtitle(self._range_key, self._now)

    # ── Card lifecycle ──────────────────────────────────────────────────────

    def _make_cards(self) -> list[ft.Control]:
        summary = DashboardSummaryCard(
            range_key=self._range_key, store=self._store, now=self._now
        )
        top = TopAppsCard(range_key=self._range_key, store=self._store, now=self._now)
        return [summary, top]

    def _active_container(self) -> ft.Control:
        wide = self._layout is not None and (
            self._layout.screen_form_factor in _WIDE_FORM_FACTORS
        )
        return self._grid if wide else self._stacked

    def _build_cards(self) -> None:
        """Rebuild the cards for the current range into the active container."""
        self._cards = self._make_cards()
        for card in self._cards:
            card.col = {"sm": 12, "lg": 6}
        self._stacked.controls = self._cards
        self._grid.controls = self._cards
        active = self._active_container()
        self._scroll.controls = [self._header_row, active]

    def _run_cards(self, show_placeholder: bool = True) -> None:
        page = self._page
        if page is not None:
            for card in self._cards:
                try:
                    page.run_task(card.run, show_placeholder=show_placeholder)
                except RuntimeError as exc:
                    if "destroyed session" in str(exc):
                        logger.warning(
                            "Dashboard card load skipped: flet session destroyed (duplicate instance?)"
                        )
                        return
                    logger.exception("Failed to schedule dashboard card load")
                except Exception:
                    logger.exception("Failed to schedule dashboard card load")
            return
        for card in self._cards:
            spawn(card.run(show_placeholder=show_placeholder))

    def _refresh_once(self) -> None:
        if self.parent is None:
            return
        self._update_subtitle()
        self._run_cards(show_placeholder=False)
        self._safe_update()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_s)
            self._refresh_once()

    # ── Layout ──────────────────────────────────────────────────────────────

    def apply_layout(self, layout: AppLayout) -> None:
        """Cap/center content and switch the card grid on form-factor change."""
        self._layout = layout
        cap = layout.content_max_width or None
        self._inner.width = cap
        self._inner.alignment = ft.Alignment.CENTER if cap else None
        active = self._active_container()
        if self._scroll.controls != [self._header_row, active]:
            self._scroll.controls = [self._header_row, active]
        if self.parent is not None:
            self.update()

    def _safe_update(self) -> None:
        if self.parent is not None:
            try:
                self.update()
            except Exception:
                logger.debug("Dashboard update skipped (not attached)", exc_info=True)
