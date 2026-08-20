"""Per-section error boundary with retry (M3 error card).

flet's declarative rendering has no render-phase exception boundary, so
errors are caught at the *load* step: an async (or sync) ``load`` callable
produces the section's data or raises, and the boundary swaps between a
loading placeholder, the rendered content, and an error card whose Retry
button re-runs the load. Exceptions are logged for debugging.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Optional

import flet as ft

from UI.components.motion import ERROR_FADE_MS, entrance
from utils.flet_helpers import safe_update

logger = logging.getLogger(__name__)

_Load = Callable[[], Any]
_Content = Callable[[Any], ft.Control]
_Runner = Callable[[Awaitable[Any]], Any]


def spawn(coro: Awaitable[Any], runner: Optional[_Runner] = None) -> None:
    """Run ``coro`` via the page runner, the current loop, or a fresh loop.

    ``runner`` receives an *awaitable* (it may be the loop's ``create_task``
    or a test spy). flet's ``page.run_task`` must NOT be passed here: it
    schedules coroutine *functions* (``handler(*args)``) and raises
    ``TypeError`` on coroutine objects — call ``page.run_task(handler,
    *args)`` directly instead. Without a runner the coroutine is scheduled
    on the running loop, or executed with a fresh loop when none exists
    (headless tests).
    """
    if runner is not None:
        runner(coro)
        return
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def error_card(
    message: str,
    on_retry: Optional[Callable[[], None]] = None,
    retry_label: str = "Retry",
) -> ft.Card:
    """M3 error card: error icon, message, and an optional Retry button."""
    actions = []
    if on_retry is not None:
        actions.append(ft.FilledButton(retry_label, on_click=lambda _e: on_retry()))
    return ft.Card(
        variant=ft.CardVariant.FILLED,
        content=ft.Container(
            padding=16,
            content=ft.Column(
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.ERROR),
                    ft.Text(message, text_align=ft.TextAlign.CENTER),
                    *actions,
                ],
            ),
        ),
    )


class ErrorBoundary(ft.Column):
    """Wraps a data load so failures render an error card with retry.

    ``load`` may return data directly or an awaitable; ``content`` maps the
    loaded data to a control. ``placeholder`` (if given) is shown while the
    load is in flight. Call :meth:`run` to start the load; Retry re-runs
    it.
    """

    def __init__(
        self,
        load: _Load,
        content: _Content,
        placeholder: ft.Control | None = None,
        error_message: str = "Something went wrong",
        retry_label: str = "Retry",
        runner: Optional[_Runner] = None,
    ):
        super().__init__(spacing=8)
        self._load = load
        self._content = content
        self._placeholder = placeholder
        self._error_message = error_message
        self._retry_label = retry_label
        self._runner = runner
        self._running = False
        if placeholder is not None:
            self.controls = [placeholder]

    async def run(self, show_placeholder: bool = True) -> None:
        """Run (or re-run) the load and render content or the error card.

        ``show_placeholder`` swaps in the loading placeholder first; pass
        ``False`` to refresh in place (no skeleton flash on periodic reloads).
        """
        if self._running:
            return
        self._running = True
        if show_placeholder and self._placeholder is not None:
            self._show(self._placeholder)
        try:
            result = self._load()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("Error boundary caught a section failure")
            self._show(self._build_error())
        else:
            self._render(result)
        finally:
            self._running = False

    def _render(self, result: Any) -> None:
        self._show(self._content(result))

    def _build_error(self) -> ft.Control:
        return error_card(
            self._error_message,
            on_retry=self._retry,
            retry_label=self._retry_label,
        )

    def _retry(self) -> None:
        spawn(self.run(), runner=self._runner)

    def _show(self, control: ft.Control) -> None:
        self.controls = [control]
        safe_update(self)
        entrance(control, fade_ms=ERROR_FADE_MS)
