"""Dashboard summary card (milestone #26).

Aggregates the currently selected dashboard range into three at-a-glance
stats: total tracked time, number of apps, and the most-used app. Shares
the range vocabulary and store mapping with :class:`TopAppsCard` via
:func:`fetch_range`.

Lifecycle contract: constructed headless-safe with zero arguments —
storage is only touched when :meth:`run` starts the data load. Pass a
``store`` in tests; ``now`` can be injected for deterministic range math.
"""

from __future__ import annotations

import datetime

import flet as ft

from core.analytics import AnalyticsStore, AppTotal
from core.storage import Storage
from UI.components.card_section import CardSection
from UI.components.data_section import DataSection
from UI.components.empty_state import EmptyState
from UI.components.skeleton import status_card_skeleton
from UI.components.top_apps_card import RANGE_TODAY, _fmt_duration, fetch_range


def _stat(label: str, value: str) -> ft.Column:
    """One stat: muted label above a prominent value."""
    return ft.Column(
        spacing=2,
        controls=[
            ft.Text(
                label,
                size=11,
                color=ft.Colors.ON_SURFACE_VARIANT,
                weight=ft.FontWeight.W_500,
            ),
            ft.Text(
                value,
                size=20,
                weight=ft.FontWeight.BOLD,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ],
    )


def _build_summary(rows: list[AppTotal]) -> ft.Control:
    """Render the stat strip from the range's app totals."""
    total_s = sum(row.total_s for row in rows)
    top = rows[0] if rows else None
    top_value = f"{top.app_name} · {top.share_pct:.0f}%" if top else ""
    return ft.Row(
        spacing=28,
        wrap=True,
        run_spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.START,
        controls=[
            _stat("Time tracked", _fmt_duration(total_s)),
            _stat("Apps", str(len(rows))),
            _stat("Most used", top_value),
        ],
    )


class DashboardSummaryCard(CardSection):
    """Summary stats for a dashboard time range.

    Wrapped in a ``DataSection``: the load shows a skeleton, an empty
    state, or an error card with retry. ``limit`` defaults to ``None`` so
    the totals cover the full range, not the top-N slice.
    """

    def __init__(
        self,
        range_key: str = RANGE_TODAY,
        store: AnalyticsStore | None = None,
        now: datetime.datetime | None = None,
    ):
        self._range_key = range_key
        self._store = store
        self._now = now
        section = DataSection(
            load=self._load,
            content=_build_summary,
            skeleton=status_card_skeleton(),
            empty_when=lambda rows: not rows,
            empty=EmptyState(
                icon=ft.Icons.INSIGHTS,
                headline="No tracked time yet",
                body="Start tracking and your usage summary will appear here.",
                height=120,
            ),
            error_message="Couldn't load usage summary",
        )
        self._section = section
        super().__init__(title="Time tracked", controls=[section])

    async def run(self, show_placeholder: bool = True) -> None:
        """Start (or re-run) the summary's data load.

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
        return fetch_range(
            self._lazy_store(), self._range_key, now=self._now, limit=None
        )
