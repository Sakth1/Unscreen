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
                               (sqlite3) (Flet)
```

## Data Model

### Tick (unit of collection)

Every watcher emits a `Tick` on each poll cycle. The event bridge (`_EventBridge` in `core/application/collection_manager.py`) turns every distinct tick into one row in `raw_events` — no write-time merging.

```python
@dataclass
class Tick:
    watcher: str        # "foreground" | "afk" | "power" | "android_*"
    timestamp: datetime  # UTC
    data: dict           # watcher-specific payload
```

### Watcher Schemas

| Watcher | Interval | Payload |
|---|---|---|
| `foreground` (Windows) | 2s | `{app, title}` + optional `url_visit` attachment |
| `afk` | 5s | `{status: "active"\|"idle"\|"away", idle_seconds}` |
| `power` | 60s | `{battery_pct, charging}` |
| `android_foreground` | 5s | `{package}` |
| `android_app_usage` | 60s | `{intervals: [{package, start, end, ...}]}` (fan-out into one event per interval) |
| `android_afk` | 30s | `{present}` → `user_presence` |
| `android_power` | 60s | `{battery_pct, charging}` |

Browser info never lands in the `foreground_transition` payload. When the foreground window is a known browser (Chrome, Firefox, Edge, Brave, Opera, Vivaldi) and URL extraction is enabled (default: on), the collector attaches a `url_visit` dict — `{url, browser, scheme, host, domain, path, extraction_method, confidence, is_trackable}` — to the tick on URL changes only. The event bridge persists it into the `url_visits` table, owned by the `foreground_transition` event that started the browser session (a fresh event on app change, the cached owning event on tab change). When extraction fails, the title-inference fallback is recorded as a `url_visit` with `confidence="low"` instead of polluting the event payload. Non-trackable URLs (`about:blank`, `chrome://newtab`, …) are filtered out.

## Database Schema

**Location:** `%APPDATA%\Unscreen\data.db`

**Engine:** SQLite (stdlib `sqlite3`), WAL journal mode. Schema v7 (`PRAGMA user_version = 7`).

All devices share one set of tables — no per-device tables. Device identity lives **once** in `devices`; event rows reference it by a small integer FK. Timestamps are **integer milliseconds** (Unix epoch UTC).

### `devices` — device registry (shared across platforms)

```sql
CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT, -- stable integer key for FK references
    device_id   TEXT NOT NULL UNIQUE,              -- UUID from MachineGuid or generated
    hostname    TEXT,
    platform    TEXT,               -- "windows" | "android"
    first_seen  TEXT NOT NULL,      -- ISO 8601 UTC
    last_seen   TEXT,               -- updated on each startup
    is_current  INTEGER DEFAULT 0   -- 1 = this machine
);
```

### `event_types` / `sources` — dictionary tables

A handful of distinct values each, dictionary-encoded into small integer FKs:

```sql
CREATE TABLE IF NOT EXISTS event_types (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS sources      (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
```

### `raw_events` — canonical event log (shared across devices)

```sql
CREATE TABLE IF NOT EXISTS raw_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk     INTEGER NOT NULL REFERENCES devices(id),
    event_type_fk INTEGER NOT NULL REFERENCES event_types(id),
    source_fk     INTEGER NOT NULL REFERENCES sources(id),
    timestamp     INTEGER NOT NULL,          -- Unix epoch ms (UTC) — when the event occurred
    collected_at  INTEGER NOT NULL,          -- Unix epoch ms (UTC) — when we observed it
    payload       TEXT NOT NULL,             -- JSON payload, type-specific
    payload_hash  INTEGER NOT NULL           -- 8-byte blake2b of canonical payload JSON
);
```

`UNIQUE(device_fk, event_type_fk, timestamp, payload_hash)` makes sync re-imports idempotent: Android's same-millisecond `app_usage_interval` fan-out is admitted (distinct payloads → distinct hashes), identical re-imports are rejected.

### `app_sessions` — derived app sessions (Windows, produced at write time)

One row per `foreground_transition` (referenced by `event_id`). The event bridge opens the row at write time and closes it at the next transition's start (`end_ts`, `duration_s`); collection stop closes the last one. Android has no producer yet.

```sql
CREATE TABLE IF NOT EXISTS app_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk  INTEGER NOT NULL REFERENCES devices(id),
    event_id   INTEGER NOT NULL REFERENCES raw_events(id),
    start_ts   INTEGER NOT NULL,          -- Unix epoch ms (UTC)
    end_ts     INTEGER,                   -- NULL while open
    duration_s REAL,                      -- (end_ts - start_ts) / 1000
    app_key    TEXT NOT NULL,
    payload    TEXT NOT NULL
);
```

### `status_sessions` — Windows idle status blocks (produced at write time)

One row per `idle_transition` entry (every status, including `active`), opened at write time and closed at the next entry or on collection stop.

```sql
CREATE TABLE IF NOT EXISTS status_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk  INTEGER NOT NULL REFERENCES devices(id),
    event_id   INTEGER NOT NULL REFERENCES raw_events(id),
    start_ts   INTEGER NOT NULL,          -- Unix epoch ms (UTC)
    end_ts     INTEGER,                   -- NULL while open
    duration_s REAL,                      -- (end_ts - start_ts) / 1000
    status     TEXT NOT NULL,             -- 'active' | 'idle' | 'away'
    payload    TEXT NOT NULL
);
```

### `url_visits` — URL visit log (Windows only)

`event_id` is populated at write time by the event bridge and is NOT NULL — it always points at the owning `foreground_transition`. `session_id` is backfilled when the owning app session closes (derived data, nullable).

```sql
CREATE TABLE IF NOT EXISTS url_visits (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk        INTEGER NOT NULL REFERENCES devices(id),
    event_id         INTEGER NOT NULL REFERENCES raw_events(id),
    session_id       INTEGER REFERENCES sessions(id),
    url              TEXT    NOT NULL,
    browser          TEXT,
    scheme           TEXT,
    host             TEXT,
    domain           TEXT,
    path             TEXT,
    extraction_method TEXT,
    confidence        TEXT DEFAULT 'high',
    is_trackable      INTEGER DEFAULT 1,
    seen_at          INTEGER NOT NULL,       -- Unix epoch ms (UTC)
    collected_at     INTEGER NOT NULL
);
```

`UNIQUE(device_fk, event_id, url)` collapses revisits of the same URL within one browser session into a single row (`write_url_visit` skips duplicates silently).

### `sync_cursors` — sync high-water marks

```sql
CREATE TABLE IF NOT EXISTS sync_cursors (
    remote_device_id TEXT PRIMARY KEY,
    last_synced_at   INTEGER NOT NULL        -- Unix epoch ms (UTC)
);
```

### Data Cleaning / Schema Reset

The app is early-stage: schemas still evolve, and **pre-v7 databases are not migrated — they are wiped and recreated fresh** on first launch after the upgrade. This is a deliberate policy: no data-migration code, no half-broken legacy data. The wipe also deletes the Android durable backup (`Download/Unscreen-data-backup/unscreen.db`) so a stale copy cannot resurrect old data on the next install. Data accumulated on v7+ survives updates. "Clear all data" in Settings wipes events, sessions, url visits, and sync cursors.

### Storage growth

~110 raw events/day ≈ ~40 KB/day ≈ ~14 MB per year (per device, before the payload_hash column is accounted for).

## Watcher Implementation Details

### ForegroundWatcher (composite)

Polls `GetForegroundWindow` + `GetWindowText` + `GetWindowThreadProcessId` + `psutil.Process().name()` every 2s. If process is a known browser, passes result through `BrowserAnalyzer` to extract page title, infer domain, and (when extraction is enabled) the active tab URL via UIA (Windows accessibility API), falling back to browser session files. URL data is attached to the tick as `url_visit` — see Watcher Schemas.

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
| Unit tests | `pytest tests/` (660+ tests) | Yes | Component logic (storage, scheduler, collectors, etc.) |
| Cloud CI replication | `python scripts/ci/local_ci.py` | Yes | Environment-only failures masked by local `.pyc` caches (fresh checkout copy + fresh venv) |

Run locally:
```bash
uv run ruff check src/ tests/
uv run pyright src/
uv run pytest tests/ -q
uv run python scripts/validate_wiring.py
uv run python scripts/ci/local_ci.py   # full cloud-CI replication (also runs on pre-push)
```

The pre-push hook is managed by [lefthook](https://lefthook.dev) (`lefthook.yml`; install with `uv tool install lefthook && lefthook install`) and runs the cloud-CI replication on every push. Manual run: `lefthook run pre-push --force`. Skip once: `LEFTHOOK=0 git push ...`.

## Dependencies

flet, orjson, psutil, rich

(Removed from initial scope: bleak, pycaw, pydantic, screeninfo, websockets, wmi)

## Sync (Planned)

All devices write into the single shared `raw_events` table, namespaced by `device_fk`. Sync exports each device's rows after its high-water mark (tracked in `sync_cursors`); the receiving device imports them as-is. Re-imports are idempotent via `UNIQUE(device_fk, event_type_fk, timestamp, payload_hash)`. Cross-device timeline queries are plain `WHERE device_fk IN (...)` — no table gymnastics.

## Browser URL Extraction (prototype)

`prototypes/browser_url_extractor/` — experimental pure-Python browser URL extractor. **The core logic has been integrated into the app** (`src/core/collectors/windows/url_extractor.py`). The prototype directory holds the standalone CLI for testing.

| Platform | Status | Method |
|---|---|---|
| Windows | Tested | UIA (pywinauto) + SNSS session files |
| macOS | Stub only, untested | AppleScript + JSONLZ4 session files |
| Linux | Stub only, untested | AT-SPI2 / xdotool + SNSS/JSONLZ4 session files |

**Prototype deps:** `pywinauto` (Windows UIA, auto-installed on Windows), `lz4` (Firefox JSONLZ4).

Usage: `python prototypes/browser_url_extractor/cli.py --help`