# Unscreen

Cross-device app usage timeline tracker with idle detection. Privacy-first, local-only, no cloud.

**Tracks:** what app you're using, on which device, for how long, with browser page inference.

**Does NOT track:** CPU, RAM, disk, network, audio, screenshots, clipboard, keystrokes, microphone, camera.

---

## Architecture

```
                   2s                         5s                         60s
            ForegroundWatcher           AfkWatcher              PowerWatcher
            ┌─────────────────┐    ┌───────────────┐      ┌──────────────────┐
            │ WindowAnalyzer  │    │ GetLastInput  │      │ sensors_battery  │
            │  + BrowserAnaly │    │  → idle_secs  │      │  → pct, charging │
            └────────┬────────┘    └───────┬───────┘      └───────┬──────────┘
                     │                     │                       │
                     └─────────────────────┼───────────────────────┘
                                           ▼
                                     TickBus
                                   (async pub/sub)
                                    ┌──┬──┐
                                    │  │  │
                                    ▼  ▼  ▼
                               Storage   UI
                              (APSW)   (Flet)
```

## Data Model

### Tick (unit of collection)

Every watcher emits a `Tick` on each poll cycle. Adjacent ticks with identical data are merged into sessions at write time (pulse-merge, like ActivityWatch).

```python
@dataclass
class Tick:
    id: UUID
    watcher: str        # "foreground" | "afk" | "power"
    timestamp: datetime  # UTC
    data: dict           # watcher-specific payload
```

### Watcher Schemas

| Watcher | Interval | Data | Merge Key |
|---|---|---|---|
| `foreground` | 2s | `{app, title, [browser], [page_title], [inferred_domain]}` | all fields |
| `afk` | 5s | `{status: "active"\|"idle"\|"away", idle_seconds}` | `status` only |
| `power` | 60s | `{battery_pct, charging}` | all fields |

Browser info (`browser`, `page_title`, `inferred_domain`, `url`) is populated when the foreground window is a known browser (Chrome, Firefox, Edge, Brave, Opera, Vivaldi). When `url_extraction_enabled` (default: on), the actual active tab URL is captured via UIA (Windows accessibility API). Falls back to browser session files if UIA is unavailable. Non-trackable URLs (about:blank, chrome://newtab, etc.) are filtered out. When a real URL is present, `inferred_domain` is omitted as redundant.

## Database Schema

**Location:** `%APPDATA%\Unscreen\data.db`

**Engine:** SQLite via APSW, WAL journal mode.

### `devices` — device registry (shared across platforms)

```sql
CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,   -- UUID from MachineGuid or generated
    hostname    TEXT,               -- "DESKTOP-A1VV4AH"
    platform    TEXT,               -- "windows" | "android"
    first_seen  TEXT NOT NULL,      -- ISO 8601 UTC
    last_seen   TEXT,               -- updated on each startup
    is_current  INTEGER DEFAULT 0   -- 1 = this machine
);
```

### `events_{short_id}` — per-device event storage

One table per device (8-char prefix of device UUID). Future sync: each device writes to its own table, no ID conflicts.

```sql
CREATE TABLE IF NOT EXISTS events_ea56c63f (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher    TEXT NOT NULL,       -- "foreground" | "afk" | "power"
    timestamp  REAL NOT NULL,       -- Unix epoch seconds (UTC)
    duration   REAL DEFAULT 0,     -- seconds, rounded to 2 decimals
    data       TEXT NOT NULL        -- JSON payload (watcher-specific)
);

CREATE INDEX IF NOT EXISTS idx_ea56c63f_watcher_ts
    ON events_ea56c63f(watcher, timestamp);
```

### Merge Algorithm

On each `on_tick()` call:

```
1. Get last event for this watcher (ORDER BY timestamp DESC LIMIT 1)
2. If no last event → INSERT with duration = 0
3. Else:
   a. Compare data by merge key (full dict or specific keys)
   b. If data matches AND tick timestamp <= last.timestamp + last.duration + pulsetime:
        UPDATE last event's duration = max(last.duration, tick.ts - last.ts)
   c. Else:
        INSERT new event with duration = 0
```

This produces timeline sessions without storing redundant ticks.

| Watcher | PulseTime |
|---|---|
| `foreground` | 3s |
| `afk` | 10s |
| `power` | 120s |

### Storage growth

~110 events/day = ~40 KB/day = ~14 MB per year.

## Watcher Implementation Details

### ForegroundWatcher (composite)

Polls `GetForegroundWindow` + `GetWindowText` + `GetWindowThreadProcessId` + `psutil.Process().name()` every 2s. If process is a known browser, passes result through `BrowserAnalyzer` to extract page title and infer domain.

### AfkWatcher

Uses `GetLastInputInfo` with `GetTickCount64` wraparound-safe calculation every 5s. Status thresholds: `< 60s` = active, `60-300s` = idle, `> 300s` = away.

### PowerWatcher

Uses `psutil.sensors_battery()` every 60s. Returns `null` values on desktops without battery.

## Device Identity

- **Primary:** Machine GUID from `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`
- **Fallback:** Generated UUID4 stored in `%APPDATA%\Unscreen\device.json`

## Setup

```bash
uv run flet run           # desktop app
uv run flet run --web     # web app
```

## App Icon & Assets

The canonical app artwork (no text) lives in `src/assets/web/`:

| File | Used for |
|---|---|
| `icon-512.png` | Rounded-boundary app icon — source for Windows, default, splash, legacy launchers |
| `icon-512-maskable.png` | Square full-bleed icon — source for Android adaptive (masked) launcher layers |

From these, `flet build` picks up the platform icons (launcher, favicon, taskbar, splash) automatically:

| File | Used for | Recommended size |
|---|---|---|
| `icon.png` | Default icon (all platforms, splash fallback) | ≥ 1024×1024 |
| `icon_windows.png` | Windows `.exe` icon (auto-converted to `.ico`) | 256×256 |
| `icon_windows.ico` | Runtime window/taskbar icon (`page.window.icon`) | 256×256 |
| `src/assets/android/res/` mipmaps | Android launcher (adaptive icon from the maskable artwork) | per density |
| `icon_ios.png` | iOS/macOS app icon | ≥ 1024×1024 |
| `icon_macos.png` | macOS app icon | ≥ 1024×1024 |

So replacing the app icon is a single edit:

1. Replace `src/assets/web/icon-512.png` (rounded) and `src/assets/web/icon-512-maskable.png` (square) with your new artwork (512×512 PNG).
2. Regenerate the platform icons (sizes, Android densities, `.ico`/favicon) from the two sources.
3. Rebuild: `uv run flet build windows` (or `flet build apk` for Android).

`src/assets/android/` and `src/assets/web/` hold the platform-resized icons generated from the sources above; they are regenerated on every build.

At runtime the desktop window icon is set from `src/assets/icon_windows.ico` in `App._set_window_icon()` (`src/app.py`).

## Validation Pipeline

The project uses a layered validation architecture to catch failures before runtime:

| Layer | Tool | CI? | Catches |
|---|---|---|---|
| Lint | `ruff` (F, E, W, I, B, SIM) | Yes | Syntax, undefined names, imports, common bugs |
| Type checking | `pyright` | Yes | Type mismatches, missing attributes |
| Flet API compat | `pytest tests/test_flet_api_compat.py` | Yes | Removed/renamed APIs, invalid kwargs, deprecation |
| Wiring validation | `python scripts/validate_wiring.py` | Yes | Missing callback methods (`_on_dismiss`-class bugs) |
| Startup smoke | `pytest tests/test_startup.py` | Yes | Construction-time exceptions in any component |
| Unit tests | `pytest tests/` (285+ tests) | Yes | Component logic (storage, scheduler, collectors, etc.) |
| Cloud CI replication | `python scripts/ci/local_ci.py` | Yes | Environment-only failures masked by local `.pyc` caches (fresh checkout + fresh venv) |

Run locally:
```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest tests/ -q
uv run python scripts/validate_wiring.py
uv run python scripts/ci/local_ci.py   # full cloud-CI replication (also runs on pre-push)
```

## Dependencies

flet, orjson, psutil, rich

(Removed from initial scope: bleak, pycaw, pydantic, screeninfo, websockets, wmi)

## Sync (Planned)

Each device writes to its own `events_{id}` table. Sync copies remote tables as read-only replicas. No ID conflict — UUIDs and per-device table names are the namespace. `UNION ALL` across tables for cross-device timeline queries.

## Browser URL Extraction (prototype)

`prototypes/browser_url_extractor/` — experimental pure-Python browser URL extractor. **The core logic has been integrated into the app** (`src/core/collectors/windows/url_extractor.py`). The prototype directory holds the standalone CLI for testing.

| Platform | Status | Method |
|---|---|---|
| Windows | Tested | UIA (pywinauto) + SNSS session files |
| macOS | Stub only, untested | AppleScript + JSONLZ4 session files |
| Linux | Stub only, untested | AT-SPI2 / xdotool + SNSS/JSONLZ4 session files |

**Prototype deps:** `pywinauto` (Windows UIA, auto-installed on Windows), `lz4` (Firefox JSONLZ4).

Usage: `python prototypes/browser_url_extractor/cli.py --help`
