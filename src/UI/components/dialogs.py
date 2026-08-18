from typing import Callable, Optional

import flet as ft

from utils.flet_helpers import safe_pop_dialog, safe_update


def show_alert_dialog(
    page: ft.Page,
    title: str,
    message: str,
    button_text: str = "OK",
    on_close: Optional[Callable[[], None]] = None,
) -> None:
    """Show a modal info dialog with a single action button."""
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, weight=ft.FontWeight.BOLD),
        content=ft.Text(message, text_align=ft.TextAlign.CENTER),
        actions=[
            ft.TextButton(
                button_text,
                on_click=lambda _: _handle_alert_close(page, on_close),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.CENTER,
    )
    page.show_dialog(dialog)
    safe_update(page)


def _handle_alert_close(page: ft.Page, on_close: Optional[Callable]) -> None:
    safe_pop_dialog(page)
    if on_close is not None:
        on_close()


def show_confirm_dialog(
    page: ft.Page,
    title: str,
    message: str,
    on_confirm: Callable[[], None],
    confirm_text: str = "Delete",
    cancel_text: str = "Cancel",
) -> None:
    """Show a modal yes/no dialog; ``on_confirm`` runs after the dialog closes."""

    def _confirm(_) -> None:
        safe_pop_dialog(page)
        on_confirm()

    def _cancel(_) -> None:
        safe_pop_dialog(page)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, weight=ft.FontWeight.BOLD),
        content=ft.Text(message, text_align=ft.TextAlign.CENTER),
        actions=[
            ft.TextButton(cancel_text, on_click=_cancel),
            ft.Button(confirm_text, on_click=_confirm),
        ],
        actions_alignment=ft.MainAxisAlignment.CENTER,
    )
    page.show_dialog(dialog)
    safe_update(page)


def show_permission_dialog(page: ft.Page):
    dlg = ft.AlertDialog(
        title=ft.Text("Usage Access Required"),
        content=ft.Text(
            "This app needs Usage Access permission to track "
            "which apps are in the foreground.\n\n"
            "Please enable it in:\n"
            "Settings → Apps → Special App Access → Usage Access",
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: _close_dialog(page, dlg)),
            ft.Button("Open Settings", on_click=lambda e: _open_settings(page, dlg)),
        ],
    )
    page.show_dialog(dlg)


def _close_dialog(page: ft.Page, dlg: ft.AlertDialog):
    dlg.open = False
    page.update()


def _open_settings(page: ft.Page, dlg: ft.AlertDialog):
    dlg.open = False
    page.update()
    from core.collectors.android.usage_stats import open_usage_access_settings

    open_usage_access_settings()
