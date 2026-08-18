# UI Architecture — Material Design 3

## Overview

The UI layer is a set of Flet-based screens, custom controls, and layout
services organized by role. The shell (`src/app.py`) wires theme, routing,
responsive layout, and the custom navigation chrome; screens render the
feature content; the update flow (startup snackbar, App Info section, and
the custom update dialog) is shared.

## Directory Layout

```
src/UI/
├── __init__.py
├── theme.py                    # Theme glue: accent seeding (apply_accent_theme)
├── routing.py                  # RouteManager — route → screen resolution
├── layout/                     # Layout-driven metrics (see below)
│   ├── models.py               # AppLayout + metric dataclasses + enums
│   ├── layout_resolver.py      # Pure resolvers (window → metrics)
│   └── __init__.py
├── custom/                     # Full custom controls (stateful, app-owned)
│   ├── navigation_bar.py       # Floating bottom pill (phone)
│   ├── navigation_drawer.py    # Mini rail / extended rail (tablet, desktop)
│   ├── secondary_navigation_panel.py  # In-screen section navigation
│   ├── status_bar.py           # Live collection status strip
│   └── update_dialog.py        # Update offer overlay + release-notes rendering
├── components/                 # Small reusable components + helpers
│   ├── card_section.py         # Filled card with bold title + stacked controls
│   ├── data_section.py         # Section scaffold with skeleton/error states
│   ├── dialogs.py              # AlertDialog helpers (alert, confirm, permission)
│   ├── empty_state.py          # Icon + message + optional action
│   ├── error_boundary.py       # try/except wrapper with fallback UI
│   ├── motion.py               # Entrance animation + reduced-motion helpers
│   └── skeleton.py             # Shimmer wrappers
└── screens/
    ├── base_screen.py          # BaseScreen — layout observer base class
    ├── dashboard_screen.py
    ├── timeline_screen.py
    ├── analytics_screen.py
    ├── settings_screen.py      # Settings top screen + section routing
    └── settings/               # Settings sections
        ├── builders.py         # section_scaffold
        ├── general.py          # Appearance / startup
        ├── data.py             # Data management (export, logs, storage)
        └── app_info.py         # App info + update check
```

Shared runtime state lives in the core layer, not under `UI/`:
`src/core/state/app_state.py` holds platform/device facts, collection
health, the resolved layout, route, and update status, and is consumed by
both core and UI code via a property + observer pattern
(`on_change(key, callback)` / `set_*` writers).

## Layout Architecture

### Resolution pipeline

Window-derived numbers never live in controls. A pure resolver maps the
viewport to an immutable snapshot, and controls read numbers off the
snapshot:

1. `app_layout_resolver(page_width, page_height, media, is_mobile)` in
   `UI/layout/layout_resolver.py` classifies the viewport into Material 3
   window size classes (width + height separately), derives a
   `ScreenFormFactor`, and resolves every design metric.
2. The result is an immutable `AppLayout` dataclass
   (`UI/layout/models.py`) carrying padding, spacing, content cap, safe
   padding, navigation pattern, and per-control metric bundles
   (`drawer_metrics`, `nav_bar_metrics`, `secondary_navigation_metrics`,
   `dialog_metrics`).
3. The app shell stores it via `get_app_state().set_layout(...)`; controls
   subscribe to `KEY_LAYOUT` and re-apply metrics on every change (window
   resize / media change).

| Width class | Range | Height class | Range | Form factor | Navigation |
|-------------|-------|--------------|-------|-------------|------------|
| Compact | <600dp | Compact | <480dp | MOBILE | NavigationBar (bottom pill) |
| Medium | 600–839dp | Compact | <480dp | MOBILE (phone landscape) | NavigationBar (bottom pill, lower/wider) |
| Medium | 600–839dp | Medium+ | ≥480dp | TABLET_PORTRAIT | Mini rail (icon-only) |
| Expanded | 840–1199dp | any | — | TABLET_LANDSCAPE | Extended rail (scales 120–200dp) |
| Large | 1200–1599dp | any | — | DESKTOP | Extended rail |
| Extra-large | ≥1600dp | any | — | DESKTOP | Extended rail |

Derived metrics (all layout-driven, never hardcoded in controls):

- **Page padding:** 12 / 16 / 20 / 24 dp per form factor.
- **Safe area:** system insets from `page.media.padding` are merged into
  `AppLayout.safe_padding`; the bottom bar clears the Android gesture bar
  automatically; the content container pads past notches/status bars.
- **Content cap:** screens are centered and capped at 1000dp (tablet
  landscape) / 1200dp (desktop); mobile/tablet portrait use full width.
- **Spacing grid:** 4dp (compact) / 8dp (wide) gaps between nav items.
- **Navigation pattern:** bottom bar for phones, mini rail for tablet
  portrait, extended rail for tablet landscape and desktop — fully
  layout-driven, no manual hamburger toggle.
- **Update dialog:** `resolve_dialog_metrics` gives the surface width
  (fixed 420dp on wide form factors, viewport-relative on mobile), the
  height cap (form-factor cap vs 90% of the window), the minimum height,
  and the fixed chrome height. The dialog composes the content-aware
  height on top (chrome + estimated notes height) and re-sizes on
  `KEY_LAYOUT` changes.

### Custom control conventions

Two construction styles coexist, chosen by lifecycle:

- **Declarative** (`@ft.control`, e.g. navigation bar, drawer, secondary
  panel): class attributes are the parameter surface (`metadata={"skip": True}`
  on Python-side fields), `init()` builds the control when flet mounts it.
- **Classic headless-safe `__init__`** (status bar, update dialog):
  nothing touches `page` or storage until the shell calls an explicit
  wiring method (`start_refresh`, `show`). Required because the sweep
  tests construct these controls with zero arguments and flet never
  mounts them.

Shared conventions: controls read layout metrics from `AppLayout` via
`apply_layout`/`apply_metrics` (or subscribe to `KEY_LAYOUT`), update only
when attached (`safe_update` / `self.parent is not None` guards), log
structurally, and document their lifecycle contract in the module
docstring.

## Screens

| Route | Screen | Notes |
|-------|--------|-------|
| `/dashboard` | Dashboard | Live AFK status, foreground card, top apps, battery |
| `/timeline` | Timeline | Session history with date picker and filters |
| `/analytics` | Analytics | Usage analytics |
| `/settings` | Settings | Section picker (inline on phones / side panel on wide) |
| `/settings/general` | Settings > General | Theme, startup, watchers |
| `/settings/data` | Settings > Data | Export, logs, storage management |
| `/settings/app-info` | Settings > App Info | Version, update check |

Screens extend `BaseScreen` (layout observer) and render their own content
inside the shell's padded content container; the section picker is inline
(phone/tablet portrait) or a `SecondaryNavigationPanel` (tablet landscape /
desktop).

## State Management

### AppState Singleton — `src/core/state/app_state.py`

Simple property + callback pattern (not reactive). Reads go through public
attributes; mutations go through `set_*` / `record_*` methods that notify
subscribers registered via `on_change(key, callback)`:

- Environment: OS type, platform name, packaged flag, app version, device ID, data dir
- Collection: running / paused / auto-paused flags, start time, per-watcher health (failures, paused, last tick), latest tick per watcher
- UI: resolved layout (`AppLayout`), current route
- Update: status (IDLE/CHECKING/AVAILABLE/DOWNLOADING/READY/APPLYING/FAILED), release info, download progress, error

`get_app_state()` returns the process-wide singleton; `reset_app_state()`
replaces it (used by tests). Wiring: `CollectionManager` pushes collection
state and ticks, `RouteManager` pushes the route, `App` pushes the layout.
Update-state writes are pushed by the update UI: the startup snackbar and
the update dialog (`UI/custom/update_dialog.py`) record
AVAILABLE → DOWNLOADING → READY → APPLYING (or FAILED / IDLE on cancel)
plus download progress and errors; the App Info settings card renders an
update-status chip from those writes.

## Update Dialog

`UI/custom/update_dialog.py` is a full custom control (not a small
component): it mounts a scrim + centered surface into `page.overlay`,
owns the download/install state machine (Windows: elevated Inno Setup
hand-off; Android: system installer via `ACTION_VIEW`), and renders the
release notes itself — sanitizing GitHub bodies (images/HTML/code blocks
stripped) and building a themed `ft.Markdown`. It is reused by the
startup snackbar and the manual check flow in App Info.

Sizing is content-aware and layout-driven: `DialogMetrics` from
`resolve_dialog_metrics` supply the window-derived bounds; the dialog
estimates the notes height (line-wrap based) and composes
`height = clamp(chrome + notes_estimate, min, max)`; a `KEY_LAYOUT`
observer re-sizes the open surface on window changes.

## Key Design Decisions

1. **Layout-driven metrics** — controls never compute window math inline;
   `AppLayout` is the single source of truth, re-resolved on every
   resize/media change.
2. **ThemeMode.SYSTEM** — follow OS dark/light preference (accent seeded
   from config; `UI/theme.py`).
3. **Breakpoints**: compact <600dp, medium 600-839dp, expanded 840dp+.
4. **Singleton state** — property + callback pattern, not reactive
   (avoids complexity).
5. **Headless-safe construction** for stateful custom controls — required
   by the test suite, which never mounts flet.
6. **Auto-update** — Windows: Inno Setup silent installer reusing AppId;
   Android: `ACTION_VIEW` install intent.

## See Also

- ADR-0001: Collection-parity-and-foreground-dual-track
- ADR-0002: Event-sourced-collection-architecture
