from typing import Callable, Optional

import flet as ft


def section_scaffold(
    title: str,
    cards: list[ft.Control],
    on_back: Optional[Callable[[], None]] = None,
) -> ft.Column:
    """Standard settings-section layout: optional back header + scrollable cards.

    Every string shown here lives inside its own ``ft.Text``/``ft.Button``
    control so theme styling and wrapping apply uniformly. The column
    scrolls when the card stack exceeds the available height.
    """
    header_controls: list[ft.Control] = []
    if on_back is not None:
        header_controls.append(
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK,
                tooltip="Back",
                on_click=lambda _e: on_back(),
            )
        )
    header_controls.append(
        ft.Text(title, size=20, weight=ft.FontWeight.BOLD, expand=True)
    )

    return ft.Column(
        spacing=16,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[
            ft.Row(
                controls=header_controls,
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Column(
                spacing=16,
                controls=cards,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ],
    )
