"""Top-apps card for the dashboard (milestone #26).

Renders the top-N ``AppTotal`` rows for a dashboard time range as a
filled card: one row per app with an avatar initial, the app name, the
duration, and a small donut chart showing the app's share of the
range's total time.

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

import datetime

import flet as ft

from core.analytics import AnalyticsStore, AppTotal
from core.storage import Storage
from UI.components.card_section import CardSection
from UI.components.data_section import DataSection
from UI.components.empty_state import EmptyState
from UI.components.skeleton import list_row_skeleton

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


def _donut(share_pct: float, size: float = _DONUT_SIZE) -> ft.Control:
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
                color=ft.Colors.PRIMARY,
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


def _app_row(total: AppTotal) -> ft.Control:
    return ft.Row(
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.CircleAvatar(
                radius=16,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
                content=ft.Text(
                    total.app_name[:1].upper(),
                    size=12,
                    weight=ft.FontWeight.BOLD,
                ),
            ),
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
            _donut(total.share_pct),
        ],
    )


def _build_rows(rows: list[AppTotal]) -> ft.Column:
    return ft.Column(spacing=4, controls=[_app_row(row) for row in rows])


def _skeletons(count: int) -> ft.Column:
    return ft.Column(spacing=4, controls=[list_row_skeleton() for _ in range(count)])


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
            content=_build_rows,
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

    async def run(self, show_placeholder: bool = True) -> None:
        """Start (or re-run) the card's data load.

        Headless construction (no store, not yet attached) is a no-op so
        the lifecycle sweep never opens a real database.
        """
        if self._store is None and self.parent is None:
            return
        await self._section.run(show_placeholder=show_placeholder)

    def _lazy_store(self) -> AnalyticsStore:
        if self._store is None:
            self._store = AnalyticsStore(Storage())
        return self._store

    def _load(self) -> list[AppTotal]:
        """Fetch the totals for the card's range key."""
        return fetch_range(
            self._lazy_store(),
            self._range_key,
            now=self._now,
            limit=self._limit,
        )
