# Querying the Unscreen Database (post-cleanup reference)

Status note: written at v0.4.8 after the timeline branch was reverted. The
`raw_events` store and the storage API are unchanged by that revert; the
`sessions` table has **no producer** at this version (see §6) — you will
re-implement session reconstruction ("cleaning") yourself. This document
tells you exactly what data is on disk, in what shape, and how to get it
out.

---

## 1. Where the database lives

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Unscreen\data.db` |
| Android | `$HOME/Unscreen/data.db` or `/data/data/com.mycompany.unscreen/files/Unscreen/data.db` |
| Override | `FLET_APP_STORAGE_DATA` env var replaces the whole data dir |

`utils/paths.py:get_data_dir()` is the single source of truth. SQLite runs
in **WAL mode** (`journal_mode=WAL`, `synchronous=NORMAL`) — you will see
`data.db-wal` and `data.db-shm` sidecars while the app is running; querying
the main file while the app is live is safe (WAL readers are non-blocking).
Schema is versioned via `PRAGMA user_version` (`SCHEMA_VERSION = 6`).

## 2. Schema (3 live tables + registry)

### `devices` — device registry
```sql
device_id TEXT PRIMARY KEY, hostname TEXT, platform TEXT,
first_seen TEXT, last_seen TEXT, is_current INTEGER
```
One row per physical device (registered with `INSERT OR IGNORE` on every
startup). `platform` is `"windows"` or `"android"` (lowercase).

### `raw_events` — the canonical event store (append-only)
```sql
id           INTEGER PRIMARY KEY AUTOINCREMENT,
device_id    TEXT NOT NULL,
platform     TEXT NOT NULL,
event_type   TEXT NOT NULL,   -- see §3
timestamp    REAL NOT NULL,   -- Unix epoch SECONDS (UTC), when the event occurred
collected_at REAL NOT NULL,   -- Unix epoch seconds (UTC), when we observed it
payload      TEXT NOT NULL,   -- JSON object, type-specific
source       TEXT NOT NULL    -- which watcher/API produced it
```
Indexes: `(event_type, timestamp)`, `(device_id, timestamp)`.

> **The single most important rule (ADR-0002):** `timestamp` is the event's
> truth. **Duration is never stored on events** — it is always derived from
> timestamps or from payload deltas. Never write a query that assumes a
> duration column exists.

### `sessions` — derived sessions (currently an empty API surface)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
device_id TEXT NOT NULL, platform TEXT NOT NULL,
start_ts REAL NOT NULL, end_ts REAL, duration_s REAL,
app_key TEXT NOT NULL, payload TEXT NOT NULL,
session_type TEXT DEFAULT 'foreground'
```
Indexes: `(device_id, app_key, start_ts)`, `(device_id, start_ts)`.
`Storage.write_canonical_session()` / `get_canonical_sessions()` exist but
**nothing calls them at v0.4.8** — the reconstructor was part of the
reverted branch. Rebuilding this table is your job (§6).

### `url_visits` — browser navigation log (Windows only, optional)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
device_id TEXT NOT NULL,
event_id   INTEGER REFERENCES raw_events(id),
session_id INTEGER REFERENCES sessions(id),
url TEXT NOT NULL, scheme TEXT, host TEXT, domain TEXT, path TEXT,
extraction_method TEXT, confidence TEXT DEFAULT 'high',
is_trackable INTEGER DEFAULT 1,
seen_at REAL NOT NULL, collected_at REAL NOT NULL
```
`event_id` / `session_id` are backfilled by
`Storage.backfill_url_event_id()` / `backfill_url_session_id()` (not
populated at v0.4.8 either). Indexes on `(device_id, seen_at)`,
`(device_id, domain, seen_at)`, `event_id`, `session_id`.

### Gone forever (cleaned by migration)
Legacy per-device tables `events_<short_id>`, `observations_<short_id>`,
`sessions_<short_id>` are dropped by the v0.5 migration (schemas
`windows.sql` / `android.sql`). A stray `tick_uuid` column on `raw_events`
was also dropped. If a DB predates v0.5, migration handles it; there is
nothing left to clean by hand.

## 3. Event catalog — every `event_type` and its `payload`

Written through `Storage.write_event()` via the `_EventBridge` in
`core/application/collection_manager.py`. `source` = the watcher name from
`_watcher_to_event_type()`.

### `foreground_transition` — app switch
| source | payload |
|---|---|
| `foreground` (Windows) | `{"app": "chrome.exe", "title": "...", "pid": 1234}` plus, when browser + URL extraction enabled: `"browser"`, `"url"`, or `"page_title"`/`"inferred_domain"` |
| `android_foreground` | `{"package": "com.android.chrome", "app_name": "Chrome"}` |

- Windows polls `GetForegroundWindow` every **2 s**; Android watches
  UsageStats `ACTIVITY_RESUMED` events every **10 s**.
- The bridge **dedups consecutive identical apps in memory**
  (`_last_app[watcher]`): no two adjacent rows share the same app key, so
  the start of the next transition is implicitly the end of the previous.
  Dedup is *not* persisted anywhere else — do not re-dedup in SQL.
- The very first transition after app start is written too (bridge starts
  with `_last_app` empty).

### `idle_transition` — Windows user activity (precise)
```json
{"status": "active" | "idle" | "away", "idle_seconds": 123.4}
```
Every **5 s** (always written, even when nothing changes). `status` is
computed from `GetLastInputInfo` vs config thresholds
(`afk_idle_threshold_s`, default 60 s → `idle`; `afk_away_threshold_s`,
default 300 s → `away`). This is the ONLY idle source on Windows.

### `user_presence` — Android presence approximation (boolean)
```json
{"present": true|false, "screen_on": true, "seconds_since_last_event": 42.0|null}
```
Every **5 s**. `present=false` means "screen on but no app RESUMED event in
the lookback" — do NOT interpret it as precise idle seconds; it degrades to
`present=true` if the UsageStats permission is lost.

### `screen_state_change` — Android only
```json
{"screen_on": true|false}
```
Written by `CollectionManager._monitor_screen_state()` (source
`screen_monitor`), only on actual on↔off transitions. Screen off also
auto-pauses collection, so **no other events are emitted while the screen
is off** — a gap in `foreground_transition`/`user_presence` between a
`screen_on:false` and `screen_on:true` pair means the screen was off.

### `power_change` — battery snapshot (both platforms)
```json
{"battery_pct": 87, "charging": true|false}
```
Every **60 s**. Windows `psutil.sensors_battery()`; Android
`get_battery_info()`. Note: on Windows a desktop without a battery produces
**no** events (watcher returns `None`).

### `app_usage_interval` — Android cumulative-time deltas
Each row's payload is one interval:
```json
{"package": "com.android.chrome", "duration_ms": 58000,
 "duration_s": 58.0, "app_name": "Chrome"}
```
Emitted every **60 s** by `android_app_usage`; one row per package with a
positive delta since the previous poll (the tick itself carries a list,
the bridge flattens it into one event per interval). Because
`queryUsageStats()` batches ~every 60 s, `duration_s` is the *only* place
in the whole DB where a duration is stored on an event — treat it as a
sampling artifact, and prefer timestamp deltas for reconstruction.

## 4. "After cleaning" — what the cleanup actually consists of

At v0.4.8 there is no row-level cleaning step in the codebase. What
"clean" means in practice:

1. **Immutable raw store.** `raw_events` is never UPDATEd or DELETEd by the
   app (only `clear_all_data()` wipes it). If you need a "cleaned" view,
   derive it in a query or rebuild `sessions` — never edit `raw_events`.
2. **Dedup already happened at the bridge** (consecutive duplicate
   foreground transitions are dropped in memory).
3. **Derived data is disposable.** `sessions` can be truncated and rebuilt
   at any time from `raw_events` — that is the design.
4. **DB maintenance** is automatic: `PRAGMA integrity_check` + auto-VACUUM
   (when >20 % waste and >10 MB) on startup and hourly. A corrupt DB file
   is quarantined as `data.db.corrupt-<ts>` and rebuilt.
5. **Sanitize `app_key` yourself.** `app`/`package` are written raw; the
   previous branch normalized e.g. `chrome.exe` → "Chrome" via an app
   palette. Decide your own normalization in the query layer.

## 5. Query recipes

### Via the Storage API (recommended in app code)
```python
from core.storage import Storage

s = Storage()
s.get_raw_events(event_type="foreground_transition", since=..., until=...,
                 limit=500, desc=False)   # list[dict] with payload already json-loaded
s.get_raw_events(source="android_afk")
s.get_raw_events()                        # everything, ascending
s.get_latest_battery()                    # newest power_change payload or None
s.get_url_visits(since=..., until=...)
s.get_today_seconds()                     # SUM(duration_s) of sessions today
s.get_today_top_apps(5)                   # sessions GROUP BY app_key today
s.close()
```
All methods return dicts with `payload` parsed back to a `dict`.

### Raw SQL (the workhorse queries)

```sql
-- A day of foreground activity, Windows
SELECT timestamp, payload FROM raw_events
WHERE event_type = 'foreground_transition' AND platform = 'windows'
  AND timestamp >= ? AND timestamp <= ?        -- day start/end (UTC epoch)
ORDER BY timestamp ASC;

-- Consecutive pairs → sessions: each row's end is the NEXT row's timestamp.
-- (SQLite window functions work well here: LEAD(timestamp) OVER (ORDER BY timestamp))

-- Idle/away timeline (Windows): statuses between two foreground transitions
SELECT timestamp, json_extract(payload, '$.status') AS status
FROM raw_events WHERE event_type = 'idle_transition' ORDER BY timestamp;

-- Screen-off blocks (Android): pair screen_on=false with the next screen_on=true
SELECT timestamp AS off_at, LEAD(timestamp) OVER (ORDER BY timestamp) AS on_at
FROM raw_events
WHERE event_type = 'screen_state_change' AND json_extract(payload, '$.screen_on') = 0;

-- Battery state blocks
SELECT timestamp, json_extract(payload, '$.charging') AS charging
FROM raw_events WHERE event_type = 'power_change' ORDER BY timestamp;

-- Android per-package duration deltas in a window
SELECT json_extract(payload, '$.package') AS pkg,
       SUM(json_extract(payload, '$.duration_s')) AS total_s
FROM raw_events WHERE event_type = 'app_usage_interval'
GROUP BY pkg ORDER BY total_s DESC;

-- URL visits joined to the foreground event that produced them
SELECT u.url, u.domain, u.seen_at, e.timestamp
FROM url_visits u LEFT JOIN raw_events e ON e.id = u.event_id
WHERE u.is_trackable = 1 ORDER BY u.seen_at;
```

## 6. Reconstructing sessions (the "cleaning" you will re-implement)

Per ADR-0002, sessions are derived and idempotent. Reference recipe used by
the reverted branch (re-implement freely, this is the shape):

1. **Per platform**, pull `foreground_transition` rows for the window.
2. **Windows:** pair consecutive transitions; each pair
   `[start_ts, next_start_ts)` is a session of `app_key = payload.app`
   (dedup already guarantees alternation). Merge in `idle_transition`
   spans (`status != 'active'`) clipped to the session, and `power_change`
   for charging states. Sleep = a long `away` run or a gap with no events.
3. **Android:** transitions give coarse start/end; refine `duration_s`
   with the sum of `app_usage_interval.duration_s` for that package in the
   window (UsageStats is authoritative for time-in-app). Use
   `screen_state_change` to bound sessions and `user_presence.present`
   for the idle-overlay approximation.
4. Write results via `Storage.write_canonical_session()`, or keep
   `sessions` empty and serve everything from `raw_events` queries.

## 7. Gotchas

- All timestamps are **UTC epoch seconds** (REAL). Convert with
  `datetime.fromtimestamp(ts, tz=utc)`; "a day" means a local-day range
  computed in the app, not a fixed 86 400 s slice.
- `payload` is JSON text — `json_extract` in SQL, or load in Python
  (`Storage` methods already parse it for you).
- No events are emitted while Android's screen is off (auto-pause) or on
  Windows while the app is closed — the timeline you build is
  "collection-lived", gaps are data, not corruption.
- Desktop Windows with no battery → no `power_change` rows at all.
- `sessions` and `url_visits.event_id/session_id` backfill are write APIs
  with no producers yet; don't query them expecting data at v0.4.8.
- Export (Settings → export) reads `raw_events` via `ExportService` — a
  ready-made example of the read path.
