"""Four-state data container: loading → content | empty | error.

Composes the skeleton / empty / error / content state machine used by
data-driven sections so no screen shows content pop-in, blind progress
rings, or uncaught section failures.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import flet as ft

from UI.components.empty_state import EmptyState
from UI.components.error_boundary import ErrorBoundary, _Content, _Load, _Runner
from UI.components.motion import EMPTY_FADE_MS, entrance
from utils.flet_helpers import safe_update


class DataSection(ErrorBoundary):
    """Runs ``load`` and shows one of: skeleton, content, empty, or error.

    ``skeleton`` (usually from :mod:`UI.components.skeleton`) is shown
    while the load is in flight; ``content(data)`` renders the result
    unless ``empty_when(data)`` holds, in which case ``empty`` (an
    :class:`EmptyState`) is shown instead. Failures render the error card
    and Retry re-runs the load. When ``empty`` is omitted a default empty
    state is used.
    """

    def __init__(
        self,
        load: _Load,
        content: _Content,
        skeleton: ft.Control | None = None,
        empty_when: Callable[[Any], bool] | None = None,
        empty: ft.Control | None = None,
        error_message: str = "Something went wrong",
        retry_label: str = "Retry",
        runner: Optional[_Runner] = None,
    ):
        super().__init__(
            load=load,
            content=content,
            placeholder=skeleton,
            error_message=error_message,
            retry_label=retry_label,
            runner=runner,
        )
        self._empty_when = empty_when
        self._empty = empty or EmptyState(
            icon=ft.Icons.INBOX_OUTLINED,
            headline="Nothing here yet",
        )

    def _render(self, result: Any) -> None:
        if self._empty_when is not None and self._empty_when(result):
            self.controls = [self._empty]
            safe_update(self)
            entrance(self._empty, fade_ms=EMPTY_FADE_MS)
        else:
            self._show(self._content(result))
