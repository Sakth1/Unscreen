# UI Architecture — v0.5.0 Material Design 3

## Overview

Complete redesign of the application UI from a set of functional screens into a polished, cross-platform Material Design 3 application with a consistent design language, navigation system, and auto-update support.

## Directory Layout

```
src/UI/
├── __init__.py
├── app.py                    # New entry point — wires theme, routing, state
├── theme/
│   ├── __init__.py
│   ├── tokens.py             # Design constants (seed color, spacing, shape, elevation)
│   └── app_theme.py          # build_theme() / build_dark_theme()
├── layouts/
│   ├── __init__.py
│   └── responsive_scaffold.py  # Adaptive shell: NavRail or NavBar based on width
├── screens/
│   ├── __init__.py
│   ├── dashboard_screen.py
│   ├── timeline_screen.py
│   ├── export_screen.py
│   └── settings_screen.py
├── components/
│   ├── __init__.py
│   ├── status_card.py        # Reusable M3 card with icon/title/value
│   ├── empty_state.py        # Icon + message + optional action button
│   ├── error_boundary.py     # try/except wrapper with fallback UI
│   ├── settings_tile.py      # Label + control row for settings
│   ├── filter_chips.py       # Date picker + watcher filter chips
│   └── update_dialog.py      # Update available dialog with progress
├── markdown_notes.py         # Release-notes sanitize + ft.Markdown builder
├── services/
│   ├── __init__.py
│   ├── router.py             # page.views routing definitions
│   ├── update_checker.py     # GitHub Releases API client
│   ├── android_updater.py    # APK download + install intent
│   └── windows_updater.py    # Inno Setup silent download + install
```

Shared runtime state lives in the core layer, not under `UI/`: `src/core/state/app_state.py` — it holds platform/device facts, collection health, UI layout/route, and update status, and is consumed by both core and UI code.

## Design System

### Seed Color
- **GREEN** (`ft.Colors.GREEN`) — maps to nature/app name theme
- Flet auto-generates full M3 tonal palette via `color_scheme_seed`
- Accessible contrast ratios for all text on surface variants

### Typography
M3 typography scale via `ft.TextTheme`:
- **Display** — large headline (first-run onboarding)
- **Headline** — section titles, cards
- **Title** — dialog titles, nav destinations
- **Body** — content text, descriptions
- **Label** — button text, chips, metadata

### Spacing
4dp grid: 4, 8, 12, 16, 24, 32, 48, 64

### Shape
Filled cards: `RoundedRectangleBorder(radius=12)` for dashboard status cards

### Theme Mode
- `ft.ThemeMode.SYSTEM` — follows OS dark/light preference
- `page.theme` = light, `page.dark_theme` = dark (both built from same seed)

## Layout Architecture

### Responsive Breakpoints — Material 3 Window Size Classes
Width and height are classified separately (per M3 adaptive guidance); the
width class drives layout, the height class matters for phone-landscape and
two-pane feasibility. The custom navigation chrome (drawer/rail/nav bar)
re-derives its widths, paddings, and margins from the resolved
`AppLayout` (`src/utils/layout.py` → `src/UI/layout/metrics.py`) on every
resize and orientation change (`page.on_resize` + `page.on_media_change`).

| Width class | Range | Height class | Range | Form factor | Navigation |
|-------------|-------|--------------|-------|-------------|------------|
| Compact | <600dp | Compact | <480dp | MOBILE | NavigationBar (bottom pill) |
| Medium | 600–839dp | Compact | <480dp | MOBILE (phone landscape) | NavigationBar (bottom pill, lower/wider) |
| Medium | 600–839dp | Medium+ | ≥480dp | TABLET_PORTRAIT | Mini rail (icon-only, 80dp) |
| Expanded | 840–1199dp | any | — | TABLET_LANDSCAPE | Extended drawer (scales 200–300dp) |
| Large | 1200–1599dp | any | — | DESKTOP | Extended drawer |
| Extra-large | ≥1600dp | any | — | DESKTOP | Extended drawer |

Derived metrics (all layout-driven, never hardcoded in controls):
- **Page padding:** 12 / 16 / 20 / 24 dp per form factor.
- **Safe area:** system insets from `page.media.padding` are merged into
  `AppLayout.safe_padding`; the bottom bar clears the Android gesture bar
  automatically; the content container pads past notches/status bars.
- **Content cap:** screens are centered and capped at 1000dp (tablet
  landscape) / 1200dp (desktop); mobile/tablet portrait use full width.
- **Spacing grid:** 4dp (compact) / 8dp (wide) gaps between nav items.
- **Drawer width:** 80dp mini rail; extended scales with viewport between
  200 and 300dp. The extended/collapsed mode is fully layout-driven (no
  manual hamburger toggle) — mini rail for tablet portrait, extended for
  tablet landscape and desktop.
- **Orientation:** from `page.media.orientation`, falling back to aspect
  ratio (width ≥ height → landscape) for headless runs.

### Navigation Destinations
1. Dashboard — live AFK status, foreground card, top apps, battery
2. Timeline — session history with date picker and filters
3. Export — session import/export
4. Settings — configuration with sub-pages (Appearance, Privacy, About, Diagnostics)

## Routing

### Route Table
| Route | Screen | Notes |
|-------|--------|-------|
| `/` | Dashboard | Default landing |
| `/timeline` | Timeline | Session history |
| `/export` | Export | Import/export |
| `/settings` | Settings | Default tab |
| `/settings/appearance` | Settings > Appearance | Theme, seed color |
| `/settings/privacy` | Settings > Privacy | Data collection |
| `/settings/about` | Settings > About | Version, update check |
| `/settings/diagnostics` | Settings > Diagnostics | Logs, telemetry |

### Implementation
- Uses Flet `page.views` stack-based routing
- `page.on_route_change` handler maps routes to view builders
- `page.on_view_pop` handles Android system Back button
- Deep-linking supported for web target

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
the update dialog (`UI/components/update_dialog.py`) record
AVAILABLE → DOWNLOADING → READY → APPLYING (or FAILED / IDLE on cancel)
plus download progress and errors; the App Info settings card renders an
update-status chip from those writes.

## Auto-Update Architecture

### Windows
1. Update Checker queries GitHub Releases API for latest release
2. If version > current, Update Dialog shows release notes
3. User clicks "Update Now" → downloads `*-setup.exe` to `%TEMP%`
4. Launches `setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`
5. Inno Setup detects existing install via AppId → updates in place
6. After installer exits, updated app launches
7. User data in `%APPDATA%` preserved

### Android
1. Same Update Checker, selects `*.apk` asset
2. Download to app-specific external storage with progress
3. Trigger `ACTION_VIEW` install intent
4. **BLOCKER**: FileProvider XML injection into Flet build unverified
5. Fallback: `file://` URI (deprecated on API 24+, still works on most devices) or direct user to GitHub Releases page

## Auto-Update CI Changes

Inno Setup script must include:
- `DisableDirPage=auto`
- `DisableProgramGroupPage=auto`
- `UsePreviousAppDir=yes` (default, verify present)

## Key Design Decisions

1. **Seed color GREEN** — maps to nature/app name theme, Flet auto-generates M3 tonal palette
2. **ThemeMode.SYSTEM** — follow OS dark/light preference
3. **Breakpoints**: compact <600dp, medium 600-839dp, expanded 840dp+
4. **Singleton state** — property + callback pattern, not reactive (avoids complexity)
5. **Page-based routing** via `page.views` — enables system back button on Android
6. **Auto-update** — Windows: Inno Setup silent installer reusing AppId; Android: ACTION_VIEW install intent (FileProvider preferred, file:// fallback)

## See Also

- Parent epic: #51
- ADR-0001: Collection-parity-and-foreground-dual-track
- ADR-0002: Event-sourced-collection-architecture
