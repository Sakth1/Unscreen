from UI.custom.secondary_navigation_panel import (
    SecondaryNavigationDestination,
    SecondaryNavigationPanel,
)


class TestDestinationConstruction:
    def test_constructs_without_page(self):
        dest = SecondaryNavigationDestination(
            icon="HOME", label="App Info", selected=True
        )
        assert len(dest.content.controls) == 2
        assert dest.bgcolor is not None

    def test_toggle_label_swaps_row_controls(self):
        dest = SecondaryNavigationDestination(icon="HOME", label="App Info")
        assert len(dest.content.controls) == 2

        dest.toggle_label()
        assert len(dest.content.controls) == 1

        dest.toggle_label()
        assert len(dest.content.controls) == 2

    def test_set_selected_renders_without_crashing(self):
        dest = SecondaryNavigationDestination(icon="HOME", label="App Info")
        assert dest.set_selected(True) is True
        assert dest.selected is True
        assert dest.set_selected(True) is False


class TestDestinationMetrics:
    def test_apply_metrics_sets_padding_and_spacing(self):
        from UI.layout.models import SecondaryDrawerMetrics

        dest = SecondaryNavigationDestination(icon="HOME", label="App Info")
        metrics = SecondaryDrawerMetrics(
            width=220.0, destination_padding=12.0, item_spacing=8.0
        )

        dest.apply_metrics(metrics)

        assert dest.padding.left == 12.0
        assert dest.padding.right == 12.0
        assert dest.content.spacing == 8.0
        assert dest.content.alignment is not None

    def test_apply_metrics_centers_icon_when_label_hidden(self):
        from UI.layout.models import SecondaryDrawerMetrics

        dest = SecondaryNavigationDestination(icon="HOME", label="App Info")
        dest.toggle_label()
        metrics = SecondaryDrawerMetrics(
            width=0.0, destination_padding=8.0, item_spacing=4.0
        )

        dest.apply_metrics(metrics)

        assert dest.content.spacing == 0
        assert len(dest.content.controls) == 1


class TestPanelConstruction:
    def test_constructs_with_destinations(self):
        first = SecondaryNavigationDestination(icon="HOME", label="App Info")
        second = SecondaryNavigationDestination(icon="SETTINGS", label="General")
        SecondaryNavigationPanel(
            destinations=[first, second],
            selected_index=0,
        )
        assert first.selected is True
        assert second.selected is False

    def test_select_index_updates_selection(self):
        first = SecondaryNavigationDestination(icon="HOME", label="App Info")
        second = SecondaryNavigationDestination(icon="SETTINGS", label="General")
        panel = SecondaryNavigationPanel(
            destinations=[first, second],
            selected_index=0,
        )

        panel.select_index(1)

        assert first.selected is False
        assert second.selected is True
        assert panel.selected_index == 1

    def test_no_event_when_index_unchanged(self):
        first = SecondaryNavigationDestination(icon="HOME", label="App Info")
        second = SecondaryNavigationDestination(icon="SETTINGS", label="General")
        panel = SecondaryNavigationPanel(
            destinations=[first, second],
            selected_index=0,
        )
        events = []
        panel.on_change = lambda e: events.append(e)

        panel.select_index(0)

        assert events == []

    def test_select_index_fires_on_change_with_route_data(self):
        from UI.layout.models import SecondaryNavigationChangeData

        first = SecondaryNavigationDestination(
            icon="HOME", label="App Info", route="/settings/app-info"
        )
        second = SecondaryNavigationDestination(
            icon="SETTINGS", label="General", route="/settings/general"
        )
        panel = SecondaryNavigationPanel(
            destinations=[first, second],
            selected_index=0,
        )
        events = []
        panel.on_change = lambda e: events.append(e)

        panel.select_index(1)

        assert len(events) == 1
        assert events[0].control is panel
        assert isinstance(events[0].data, SecondaryNavigationChangeData)
        assert events[0].data.index == 1
        assert events[0].data.label == "General"
        assert events[0].data.route == "/settings/general"

    def test_clicking_destination_selects_and_fires_change(self):
        first = SecondaryNavigationDestination(
            icon="HOME", label="App Info", route="/settings/app-info"
        )
        second = SecondaryNavigationDestination(
            icon="SETTINGS", label="General", route="/settings/general"
        )
        panel = SecondaryNavigationPanel(
            destinations=[first, second],
            selected_index=0,
        )
        events = []
        panel.on_change = lambda e: events.append(e)

        second._handle_click(None)

        assert panel.selected_index == 1
        assert len(events) == 1
        assert events[0].data.index == 1
        assert events[0].data.route == "/settings/general"


class TestPanelResponsiveLayout:
    @staticmethod
    def _panel():
        return SecondaryNavigationPanel(
            destinations=[
                SecondaryNavigationDestination(icon="HOME", label="App Info"),
                SecondaryNavigationDestination(icon="SETTINGS", label="General"),
            ],
            selected_index=0,
        )

    def test_inline_layout_collapses_panel(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        layout = app_layout_resolver(800, 1280)  # tablet portrait -> inline

        panel.apply_layout(layout)

        assert panel.extended is False
        assert panel.width == 0.0

    def test_side_panel_layout_shows_labels(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        layout = app_layout_resolver(1280, 800)  # desktop -> side panel

        panel.apply_layout(layout)

        assert panel.extended is True
        assert panel.width == 200.0  # 1280 * 0.18 clamped to the rail max
        assert len(panel.final_destinations[0].content.controls) == 2  # icon + label

    def test_layout_always_wins_over_initial_state(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        panel.apply_layout(app_layout_resolver(800, 1280))
        assert panel.extended is False

        panel.apply_layout(app_layout_resolver(1280, 800))
        assert panel.extended is True

    def test_reapplying_same_layout_is_idempotent(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        layout = app_layout_resolver(1280, 800)

        panel.apply_layout(layout)
        first = [len(d.content.controls) for d in panel.final_destinations]

        panel.apply_layout(layout)
        assert [len(d.content.controls) for d in panel.final_destinations] == first
        assert panel.extended is True

    def test_width_scales_with_viewport(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        layout = app_layout_resolver(900, 600)
        panel.apply_layout(layout)
        assert panel.width == 900 * 0.18

    def test_width_clamps_to_rail_max(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        layout = app_layout_resolver(1920, 1080)
        panel.apply_layout(layout)
        assert panel.width == 200.0

    def test_inline_to_side_panel_restores_labels(self):
        from UI.layout.layout_resolver import app_layout_resolver

        panel = self._panel()
        panel.apply_layout(app_layout_resolver(800, 1280))
        assert len(panel.final_destinations[0].content.controls) == 1

        panel.apply_layout(app_layout_resolver(1280, 800))
        assert len(panel.final_destinations[0].content.controls) == 2
