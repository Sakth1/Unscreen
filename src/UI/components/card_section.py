import flet as ft


class CardSection(ft.Card):
    """Section card with a bold title and stacked controls."""

    def __init__(self, title: str, controls: list[ft.Control]):
        super().__init__(variant=ft.CardVariant.FILLED)
        self.content = ft.Container(
            padding=16,
            content=ft.Column(
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                controls=[
                    ft.Text(title, size=14, weight=ft.FontWeight.BOLD),
                    *controls,
                ],
            ),
        )
