"""Top-apps card for the dashboard (milestone #26).

Renders the top-N ``AppTotal`` rows for a dashboard time range as a
filled card: one row per app with a colored avatar initial, the app
name, the duration, and a small donut chart showing the app's share of
the range's total time. Each rank gets its own accent color (F9) so the
dashboard reads colorful at a glance.

Tapping anywhere on the card (F9b) opens an all-apps dialog listing
every app in the range, not just the top-N slice.

Browser site buckets (``browser:youtube``, ...) resolve a real favicon
in the background (F9c) and render it in the row avatar; the fallback
chain is favicon -> colored-initial avatar, so a missing or failed
icon never leaves a broken image.

flet 0.86 ships no chart controls, so the donut is a determinate
``ft.ProgressRing`` (the arc sweeps the share, the remainder renders as
the track) overlaid with the share percentage in its center.

Lifecycle contract: constructed headless-safe with zero arguments —
storage is only touched when :meth:`run` starts the data load. Pass a
``store`` in tests; ``now`` can be injected for deterministic range
math. The range keys and their store mapping live in :func:`fetch_range`,
shared with the dashboard summary card.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Callable

import flet as ft

from core.analytics import AnalyticsStore, AppTotal
from core.config_manager import ConfigManager
from core.icons.icon_resolver import fetch_site_favicon, is_site_bucket
from core.storage import Storage
from UI.components.card_section import CardSection
from UI.components.data_section import DataSection
from UI.components.empty_state import EmptyState
from UI.components.skeleton import list_row_skeleton
from utils.flet_helpers import safe_pop_dialog, safe_update

logger = logging.getLogger(__name__)

#: Dashboard range keys; the dropdown in ``dashboard_screen`` maps to these.
RANGE_TODAY = "today"
RANGE_YESTERDAY = "yesterday"
RANGE_WEEK = "week"
RANGE_LAST_7 = "last_7"
RANGE_MONTH = "month"
RANGE_LAST_30 = "last_30"

_DEFAULT_LIMIT = 5
_DONUT_SIZE = 32.0
_DONUT_STROKE = 4.0
_DAY_MS = 24 * 60 * 60 * 1000

#: Per-rank accent palette (F9): each top-apps row gets a distinct color so
#: the dashboard reads colorful at a glance. Material 3 roles resolve in
#: both light and dark themes.
_ACCENT_COLORS = [
    ft.Colors.INDIGO,
    ft.Colors.TEAL,
    ft.Colors.PURPLE,
    ft.Colors.ORANGE,
    ft.Colors.PINK,
    ft.Colors.CYAN,
    ft.Colors.GREEN,
    ft.Colors.BLUE_GREY,
]

_ALL_APPS_DIALOG_WIDTH = 420.0
_ALL_APPS_DIALOG_HEIGHT = 440.0

#: Human labels for the all-apps dialog title.
_RANGE_LABELS = {
    RANGE_TODAY: "Today",
    RANGE_YESTERDAY: "Yesterday",
    RANGE_WEEK: "This week",
    RANGE_LAST_7: "Last 7 days",
    RANGE_MONTH: "This month",
    RANGE_LAST_30: "Last 30 days",
}


def _ms(dt: datetime.datetime) -> int:
    return int(dt.timestamp() * 1000)


def fetch_range(
    store: AnalyticsStore,
    range_key: str,
    *,
    now: datetime.datetime | None = None,
    limit: int | None = None,
) -> list[AppTotal]:
    """Resolve a dashboard range key to an ``AnalyticsStore`` call.

    Shared by the dashboard cards (summary + top apps) so the range
    vocabulary lives in one place. ``now`` may be injected for
    deterministic range math in tests.
    """
    now = now or datetime.datetime.now()
    if range_key == RANGE_TODAY:
        return store.daily_totals(limit=limit)
    if range_key == RANGE_YESTERDAY:
        return store.daily_totals(now.date() - datetime.timedelta(days=1), limit=limit)
    if range_key == RANGE_WEEK:
        return store.weekly_totals(limit=limit)
    if range_key == RANGE_LAST_7:
        end = _ms(now)
        return store.totals(end - 7 * _DAY_MS, end, limit=limit)
    if range_key == RANGE_MONTH:
        start = _ms(datetime.datetime(now.year, now.month, 1))
        return store.totals(start, _ms(now), limit=limit)
    if range_key == RANGE_LAST_30:
        end = _ms(now)
        return store.totals(end - 30 * _DAY_MS, end, limit=limit)
    raise ValueError(f"unknown range key: {range_key!r}")


def _fmt_duration(total_s: float) -> str:
    """Format a duration in seconds as a compact human string."""
    total = round(total_s)
    if total < 60:
        return f"{total} s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} m {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} m"


def _donut(
    share_pct: float,
    color: str = ft.Colors.PRIMARY,
    size: float = _DONUT_SIZE,
) -> ft.Control:
    """Small donut chart: arc sweeps ``share_pct`` of the ring."""
    return ft.Stack(
        width=size,
        height=size,
        alignment=ft.Alignment.CENTER,
        controls=[
            ft.ProgressRing(
                value=max(0.0, min(share_pct / 100.0, 1.0)),
                width=size,
                height=size,
                stroke_width=_DONUT_STROKE,
                stroke_cap=ft.StrokeCap.ROUND,
                color=color,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            ),
            ft.Text(
                f"{round(share_pct)}%",
                size=10,
                weight=ft.FontWeight.BOLD,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ],
    )


def _app_row(
    total: AppTotal,
    rank: int = 0,
    icon_bytes: bytes | None = None,
) -> ft.Control:
    accent = _ACCENT_COLORS[rank % len(_ACCENT_COLORS)]
    avatar = ft.CircleAvatar(radius=16, bgcolor=accent)
    if icon_bytes is not None:
        # The image renders on top of the colored circle (F9c); the
        # initial is omitted so it does not overlay the icon. A failed
        # image falls back to the plain colored circle.
        avatar.foreground_image_src = icon_bytes
    else:
        avatar.content = ft.Text(
            total.app_name[:1].upper(),
            size=12,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.WHITE,
        )
    return ft.Row(
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            avatar,
            ft.Column(
                spacing=2,
                expand=True,
                controls=[
                    ft.Text(
                        total.app_name,
                        size=14,
                        weight=ft.FontWeight.W_500,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        _fmt_duration(total.total_s),
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
            ),
            _donut(total.share_pct, color=accent),
        ],
    )


def _build_rows(
    rows: list[AppTotal],
    icons: dict[str, bytes] | None = None,
) -> ft.Column:
    icons = icons or {}
    return ft.Column(
        spacing=4,
        controls=[
            _app_row(row, rank, icons.get(row.app_key)) for rank, row in enumerate(rows)
        ],
    )


def _skeletons(count: int) -> ft.Column:
    return ft.Column(spacing=4, controls=[list_row_skeleton() for _ in range(count)])


def _all_apps_dialog(
    range_label: str,
    rows: list[AppTotal],
    icons: dict[str, bytes] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ft.AlertDialog:
    """Modal dialog listing every app in the range (F9b).

    Pure builder — headless-safe to construct and inspect in tests; the
    caller decides how to show/close it. ``icons`` (already-resolved PNG
    bytes keyed by app_key) render in the row avatars when available;
    every other row keeps its colored-initial avatar (F9c fallback).
    """
    total_s = sum(row.total_s for row in rows)
    body = ft.Column(
        spacing=4,
        scroll=ft.ScrollMode.AUTO,
        controls=[
            _app_row(row, rank, icons.get(row.app_key) if icons else None)
            for rank, row in enumerate(rows)
        ],
    )
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"All apps — {range_label}", weight=ft.FontWeight.BOLD),
        content=ft.Container(
            width=_ALL_APPS_DIALOG_WIDTH,
            height=_ALL_APPS_DIALOG_HEIGHT,
            padding=ft.padding.Padding.only(top=4),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Text(
                        f"{len(rows)} apps · {_fmt_duration(total_s)}",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    body,
                ],
            ),
        ),
        actions=[
            ft.TextButton(
                "Close",
                on_click=lambda _: on_close() if on_close is not None else None,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    return dialog


class TopAppsCard(CardSection):
    """Top-N apps for a dashboard time range, with share donuts.

    Wrapped in a ``DataSection``: the load shows row skeletons, an
    empty state, or an error card with retry. Headless-safe — pass a
    ``store`` in tests, or let the card open the app's ``Storage()``
    lazily on the first :meth:`run`.
    """

    def __init__(
        self,
        range_key: str = RANGE_TODAY,
        store: AnalyticsStore | None = None,
        limit: int = _DEFAULT_LIMIT,
        now: datetime.datetime | None = None,
    ):
        self._range_key = range_key
        self._store = store
        self._limit = limit
        self._now = now
        section = DataSection(
            load=self._load,
            content=self._render_rows,
            skeleton=_skeletons(limit),
            empty_when=lambda rows: not rows,
            empty=EmptyState(
                icon=ft.Icons.INSERT_CHART_OUTLINED,
                headline="No usage in this range",
                body="Tracked app sessions will show up here.",
                height=120,
            ),
            error_message="Couldn't load app usage",
        )
        self._section = section
        super().__init__(title="Top Apps", controls=[section])
        # F9b: the whole card is tappable — tapping anywhere opens the
        # all-apps dialog. Buttons inside (retry) win the gesture arena,
        # so they keep working.
        self.content = ft.GestureDetector(
            content=self.content,
            on_tap=self._on_tap,
        )
        self._dialog: ft.AlertDialog | None = None
        self._rows: list[AppTotal] = []
        self._icons: dict[str, bytes] = {}
        self._icon_failed: set[str] = set()

    def _render_rows(self, rows: list[AppTotal]) -> ft.Control:
        """Content builder: remember the rows, render with current icons.

        Kept as an instance method so the icon pass can re-render the
        section in place with the latest resolved favicons.
        """
        self._rows = rows
        return _build_rows(rows, icons=self._icons)

    def _current_page(self) -> ft.Page | None:
        """The owning page, or None while the card is detached."""
        try:
            return self.page
        except RuntimeError:
            return None

    def _on_tap(self, _event) -> None:
        page = self._current_page()
        if page is None:
            return
        try:
            page.run_task(self._open_all_apps)
        except Exception:
            logger.exception("Failed to open all-apps dialog")

    async def _open_all_apps(self) -> None:
        """Load the full range (no top-N limit) and show the all-apps dialog."""
        try:
            rows = fetch_range(
                self._lazy_store(),
                self._range_key,
                now=self._now,
                limit=None,
            )
        except Exception:
            logger.exception("Failed to load all apps")
            return
        page = self._current_page()
        if page is None:
            return
        self._dialog = _all_apps_dialog(
            _RANGE_LABELS.get(self._range_key, self._range_key),
            rows,
            icons=self._icons,
            on_close=self._close_all_apps,
        )
        page.show_dialog(self._dialog)
        safe_update(page)

    def _close_all_apps(self) -> None:
        page = self._current_page()
        if page is None:
            return
        safe_pop_dialog(page)
        self._dialog = None

    async def _load_site_icons(self) -> None:
        """Resolve favicons for the card's site buckets in the background.

        Fetches run off the UI thread (``asyncio.to_thread``); failures
        are remembered so a dead favicon is not re-fetched on every
        dashboard refresh. Newly resolved icons re-render the section in
        place; unresolved rows keep the colored-initial fallback.
        """
        try:
            rows = fetch_range(
                self._lazy_store(),
                self._range_key,
                now=self._now,
                limit=self._limit,
            )
        except Exception:
            logger.debug("Icon pass: range load failed", exc_info=True)
            return
        found: dict[str, bytes] = {}
        for row in rows:
            key = row.app_key
            if (
                not is_site_bucket(key)
                or key in self._icons
                or key in self._icon_failed
            ):
                continue
            png = await asyncio.to_thread(fetch_site_favicon, key)
            if png is not None:
                found[key] = png
            else:
                self._icon_failed.add(key)
        if not found:
            return
        self._icons.update(found)
        await self._section.run(show_placeholder=False)

    async def run(self, show_placeholder: bool = True) -> None:
        """Start (or re-run) the card's data load.

        Headless construction (no store, not yet attached) is a no-op so
        the lifecycle sweep never opens a real database. After the rows
        render, site-bucket favicons resolve in the background (F9c).
        """
        if self._store is None and self.parent is None:
            return
        await self._section.run(show_placeholder=show_placeholder)
        page = self._current_page()
        if page is not None:
            try:
                page.run_task(self._load_site_icons)
            except Exception:
                logger.debug("Failed to schedule site-icon pass", exc_info=True)

    def _lazy_store(self) -> AnalyticsStore:
        if self._store is None:
            config = ConfigManager()
            config.load()
            self._store = AnalyticsStore(
                Storage(close_orphans=False),
                exclude_system_apps=config.hide_system_apps,
                hidden_app_keys=tuple(config.hidden_app_keys),
            )
        return self._store

    def _load(self) -> list[AppTotal]:
        """Fetch the totals for the card's range key."""
        return fetch_range(
            self._lazy_store(),
            self._range_key,
            now=self._now,
            limit=self._limit,
        )
