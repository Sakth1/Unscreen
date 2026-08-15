# Querying the Unscreen Database (post-cleanup reference)

Status note: written at v0.4.8 after the timeline branch was reverted. The
`raw_events` store and the storage API are unchanged by that revert; the
`sessions` table has **no producer** at this version (see §6) — you will
re-implement session reconstruction ("cleaning") yourself. This document
tells you exactly what data is on disk, in what shape, and how to get it
out. Updated at v0.4.9: `Storage.count_events()` added (§5), CLI
exploration added (§8). Updated for **schema v7** (v0.4.10-dev): de-bloated
event store — integer FK references, integer-millisecond timestamps,
dictionary-encoded event types/sources, `payload_hash` dedup identity,
`sync_cursors`. Pre-v7 databases are **wiped, not migrated** (see §9).

---

## 1. Where the database lives

| Platform | Path |
|---|---|
| Windows (installed) | `%APPDATA%\Unscreen\data.db` |
| Android | `$HOME/Unscreen/data.db` or `/data/data/com.mycompany.unscreen/files/Unscreen/data.db` |
| Dev (`flet run`) | `<project>/.flet/storage/data/data.db` (git-ignored) |
| Override | `UNSCREEN_DATA_DIR` env var replaces the whole data dir (development/testing only) |

Note (v0.4.10-dev3): flet's own `FLET_APP_STORAGE_DATA` env var is honored
**only** in CLI dev mode (`flet run`), where it points at the project-local,
git-ignored `.flet/storage/data` dir — dev runs never touch `%APPDATA%`.
Installed builds set the same variable to the OS app-support dir, which is
deliberately **ignored** so they always use the canonical paths above and
data never lands in flet's `%APPDATA%\Flet\unscreen\data` folder.
`UNSCREEN_DATA_DIR` is the only app-specific override and wins over all of
the above.

Android durability (v0.4.10-dev3): the app keeps a consistent copy of
`data.db` in the user-visible MediaStore Downloads collection at
`Download/Unscreen-data-backup/unscreen.db` (API 29+, no permissions).
That copy survives an app uninstall and is user-deletable, mirroring the
Windows contract; Auto Backup is also enabled explicitly
(`allowBackup="true"` in the manifest) for silent cloud restore after
reinstall. The copy is synced on collection stop and hourly, and is
restored into a fresh data dir when present (same-install only). A schema
wipe also **deletes** the backup so it cannot resurrect wiped data. See
`docs/research/android-data-persistence.md` and ADR-0003.

`utils/paths.py:get_data_dir()` is the single source of truth. SQLite runs
in **WAL mode** (`journal_mode=WAL`, `synchronous=NORMAL`) — you will see
`data.db-wal` and `data.db-shm` sidecars while the app is running; querying
the main file while the app is live is safe (WAL readers are non-blocking).
Schema is versioned via `PRAGMA user_version` (`SCHEMA_VERSION = 7`).

## 2. Schema (7 live tables + registry)

### `devices` — device registry
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL UNIQUE,
hostname TEXT, platform TEXT, first_seen TEXT, last_seen TEXT,
is_current INTEGER
```
One row per physical device (registered with `INSERT OR IGNORE` on every
startup). `platform` is `"windows"` or `"android"` (lowercase). `id` is the
stable integer key every other table references; `device_id` (the
per-install UUID) appears **only here** — event rows never repeat it.

### `event_types` / `sources` — dictionary tables
```sql
event_types: id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
sources:     id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
```
A handful of distinct values each. `raw_events` references them by
`event_type_fk` / `source_fk`; join to read the names (the Storage API does
this for you).

### `raw_events` — the canonical event store (append-only)
```sql
id            INTEGER PRIMARY KEY AUTOINCREMENT,
device_fk     INTEGER NOT NULL REFERENCES devices(id),
event_type_fk INTEGER NOT NULL REFERENCES event_types(id),
source_fk     INTEGER NOT NULL REFERENCES sources(id),
timestamp     INTEGER NOT NULL,   -- Unix epoch MILLISECONDS (UTC), when the event occurred
collected_at  INTEGER NOT NULL,   -- Unix epoch ms (UTC), when we observed it
payload       TEXT NOT NULL,      -- JSON object, type-specific
payload_hash  BLOB NOT NULL       -- 16-byte blake2b of canonical payload JSON
```
`UNIQUE(device_fk, event_type_fk, timestamp, payload_hash)` is the dedup
identity: it admits Android's same-millisecond `app_usage_interval`
fan-out (distinct payloads → distinct hashes) while rejecting identical
re-imports (sync idempotence). Indexes: `(device_fk, timestamp)`,
`(event_type_fk, timestamp)`.

> **The single most important rule (ADR-0002):** `timestamp` is the event's
> truth. **Duration is never stored on events** — it is always derived from
> timestamps or from payload deltas. Never write a query that assumes a
> duration column exists.

### `sessions` — derived sessions (currently an empty API surface)
```sql
id           INTEGER PRIMARY KEY AUTOINCREMENT,
device_fk    INTEGER NOT NULL REFERENCES devices(id),
start_ts     INTEGER NOT NULL,   -- Unix epoch ms (UTC)
end_ts       INTEGER,            -- Unix epoch ms (UTC)
duration_s   REAL,
app_key      TEXT NOT NULL,
payload      TEXT NOT NULL,
session_type TEXT DEFAULT 'foreground'
```
Indexes: `(device_fk, app_key, start_ts)`, `(device_fk, start_ts)`.
`Storage.write_canonical_session()` / `get_canonical_sessions()` exist but
**nothing calls them at v0.4.8** — the reconstructor was part of the
reverted branch. Rebuilding this table is your job (§6).

### `url_visits` — browser navigation log (Windows only, optional)
```sql
id                INTEGER PRIMARY KEY AUTOINCREMENT,
device_fk         INTEGER NOT NULL REFERENCES devices(id),
event_id          INTEGER NOT NULL REFERENCES raw_events(id),
session_id        INTEGER REFERENCES sessions(id),
url               TEXT NOT NULL,
browser           TEXT,
scheme TEXT, host TEXT, domain TEXT, path TEXT,
extraction_method TEXT, confidence TEXT DEFAULT 'high',
is_trackable      INTEGER DEFAULT 1,
seen_at           INTEGER NOT NULL,   -- Unix epoch ms (UTC)
collected_at      INTEGER NOT NULL    -- Unix epoch ms (UTC)
```
**`event_id` is populated at write time** by the event bridge (v7): the
`foreground_transition` that started the browser session owns every visit
row written until the next app transition. It is NOT NULL — no backfill
exists (the old `backfill_url_event_id()` was removed). `session_id` is
still filled by the future session reconstructor (derived, nullable;
`backfill_url_session_id()` exists). `UNIQUE(device_fk, event_id, url)`
collapses revisits of one URL within a session; `write_url_visit()`
skips duplicates silently. Indexes on `(device_fk, seen_at)`,
`(device_fk, domain, seen_at)`, `event_id`, `session_id`.

### `sync_cursors` — sync high-water marks (planned sync engine)
```sql
remote_device_id TEXT PRIMARY KEY,
last_synced_at   INTEGER NOT NULL   -- Unix epoch ms (UTC)
```
One row per remote device this device has exchanged data with. Not written
by anything yet.

### Gone forever (cleaned by wipe)
Legacy per-device tables `events_<short_id>`, `observations_<short_id>`,
`sessions_<short_id>` (pre-v0.5) and the v0.5/v0.6 shared tables with
`device_id TEXT` columns and REAL timestamps are all gone. Pre-v7
databases are **deleted and recreated fresh** on first launch (§9) — there
is no migration SQL to reason about.

## 3. Event catalog — every `event_type` and its `payload`

Written through `Storage.write_event()` via the `_EventBridge` in
`core/application/collection_manager.py`. `source` = the watcher name from
`_watcher_to_event_type()`.

### `foreground_transition` — app switch (lean payload, v7)
| source | payload |
|---|---|
| `foreground` (Windows) | `{"app": "chrome.exe", "title": "...", "pid": 1234}` |
| `android_foreground` | `{"package": "com.android.chrome", "app_name": "Chrome"}` |

- Windows polls `GetForegroundWindow` every **2 s**; Android watches
  UsageStats `ACTIVITY_RESUMED` events every **10 s**.
- The payload is pure app-transition context. Browser/URL data is **not**
  in the payload — it travels as a `url_visit` attachment and lands in the
  `url_visits` table (§9).
- The bridge **dedups consecutive identical apps in memory**
  (`_last_app[watcher]`): no two adjacent rows share the same app key, so
  the start of the next transition is implicitly the end of the previous.
  Dedup is *not* persisted anywhere else — do not re-dedup in SQL.
- The very first transition after app start is written too (bridge starts
  with `_last_app` empty).
- Every transition row writes its rowid into the bridge's
  `_last_event_id[watcher]` cache; `url_visits` written while the same app
  stays foreground reuse that event id.

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
2. **Dedup happened at the bridge** (consecutive duplicate foreground
   transitions are dropped in memory) **and at the identity level**
   (`UNIQUE(device_fk, event_type_fk, timestamp, payload_hash)` rejects
   exact re-imports — the sync idempotence guarantee).
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
s.count_events(since=..., until=..., event_type=...)  # cheap COUNT(*) (0.4.9)
s.write_canonical_session({...})      # insert one derived session (no producer yet)
s.get_canonical_sessions(app_key=..., device_id=..., since=..., until=...,
                         platform=..., limit=...)     # derived sessions as dicts
s.close()
```
All methods return dicts with `payload` parsed back to a `dict`, and
`device_id` / `platform` / `event_type` / `source` re-joined from the
dictionary tables (the API shape is unchanged from v6 — only the storage
layout changed). **Timestamps are integer milliseconds everywhere.**

### Raw SQL (the workhorse queries)

`raw_events` no longer stores `device_id`/`event_type`/`source` as text —
join the registry tables:

```sql
-- A day of foreground activity, Windows
SELECT e.timestamp, e.payload, d.platform
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
JOIN devices d      ON d.id   = e.device_fk
WHERE et.name = 'foreground_transition' AND d.platform = 'windows'
  AND e.timestamp >= ? AND e.timestamp <= ?      -- day start/end (UTC epoch ms)
ORDER BY e.timestamp ASC;

-- Consecutive pairs → sessions: each row's end is the NEXT row's timestamp.
-- (SQLite window functions work well here: LEAD(timestamp) OVER (ORDER BY timestamp))

-- Idle/away timeline (Windows): statuses between two foreground transitions
SELECT e.timestamp, json_extract(e.payload, '$.status') AS status
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
WHERE et.name = 'idle_transition' ORDER BY e.timestamp;

-- Screen-off blocks (Android): pair screen_on=false with the next screen_on=true
SELECT e.timestamp AS off_at,
       LEAD(e.timestamp) OVER (ORDER BY e.timestamp) AS on_at
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
WHERE et.name = 'screen_state_change'
  AND json_extract(e.payload, '$.screen_on') = 0;

-- Battery state blocks
SELECT e.timestamp, json_extract(e.payload, '$.charging') AS charging
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
WHERE et.name = 'power_change' ORDER BY e.timestamp;

-- Android per-package duration deltas in a window
SELECT json_extract(e.payload, '$.package') AS pkg,
       SUM(json_extract(e.payload, '$.duration_s')) AS total_s
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
WHERE et.name = 'app_usage_interval'
GROUP BY pkg ORDER BY total_s DESC;

-- URL visits joined to the foreground event that produced them
-- (event_id is populated at write time since v7)
SELECT u.url, u.domain, u.seen_at, e.timestamp
FROM url_visits u LEFT JOIN raw_events e ON e.id = u.event_id
WHERE u.is_trackable = 1 ORDER BY u.seen_at;
```

## 6. The concept and philosophy of sessions

### What a session is

A **session** is one continuous foreground block: one app on one device,
from the moment it became foreground to the moment another app replaced
it. `session_type` is `'foreground'` and only that — AFK, screen and
battery states are **not** sessions, they are state blocks (see
philosophy #5).

### The philosophy (ADR-0002)

1. **Sessions are derived, never collected.** Collectors write events to
   `raw_events`; nothing writes `sessions` at collection time. A session
   is the *output* of a deterministic reconstruction step.
2. **Duration is never stored at write time.** It is computed during
   reconstruction from event timestamps. The sole exception is Android's
   `app_usage_interval.duration_s` — a sampling artifact of the ~60 s
   UsageStats batch, reference only.
3. **Sessions are disposable and idempotent.** Reconstructing the same
   window twice yields the same rows. `TRUNCATE sessions` and rebuild at
   any time — derived data is never precious. This is why `sessions` has
   write APIs (`write_canonical_session`) but no producer: the producer is
   the reconstructor you re-implement.
4. **Boundaries are half-open intervals.** A session is
   `[start_ts, next_start_ts)`: its end is the *next* transition's start.
   This works because the bridge dedups consecutive identical apps in
   memory — transitions always alternate, no two adjacent rows share an
   app key (§3).
5. **Sessions carry no device state.** Idle/away, screen on/off,
   charging/discharging are orthogonal time-series: during one session
   the user can idle, the screen can go off (Android auto-pauses, so
   events stop), or the battery can drain. Overlaying those blocks on a
   session is a query-time concern (recipe step 2), not a session
   property.
6. **Precision is platform-honest (ADR-0001).** Windows: exact — 2 s
   poll, precise idle via `GetLastInputInfo`. Android: coarse — 10 s event
   granularity, 60 s duration batches, idle only *approximated* from
   screen state + app-event gaps (`user_presence.present`). Never pretend
   Android knows idle seconds.

### Session shape & invariants (the contract)

```python
start_ts: int         # first event timestamp of the block (epoch ms)
end_ts: int           # next transition start (or last event + poll interval)
duration_s: float     # (end - start) / 1000 (Windows); UsageStats sums (Android)
app_key: str          # process name (Windows) / package name (Android)
payload: dict         # merged metadata (title, app_name, ...)
session_type: str     # "foreground" — the only type today
```

Invariants to preserve when re-implementing:

- One session per contiguous foreground block per app; no overlaps, no
  gaps for the same app.
- `end_ts >= start_ts`; `duration_s = (end_ts - start_ts) / 1000` unless
  Android UsageStats is authoritative.
- `device_fk` + platform carried from the events the session was built
  from (resolve `device_id` → `device_fk` via `devices` when writing
  sessions for foreign devices).

### Reconstruction recipe (the "cleaning" you will re-implement)

Per ADR-0002, sessions are derived and idempotent. Reference recipe used by
the reverted branch (re-implement freely, this is the shape):

1. **Per platform**, pull `foreground_transition` rows for the window
   (join `event_types`/`devices` to filter).
2. **Windows:** pair consecutive transitions; each pair
   `[start_ts, next_start_ts)` is a session of `app_key = payload.app`
   (dedup already guarantees alternation). Merge in `idle_transition`
   spans (`status != 'active'`) clipped to the session, and `power_change`
   for charging states. Sleep = a long `away` run or a gap with no events.
   Fill `url_visits.session_id` for visits whose `event_id` falls inside
   the session via `backfill_url_session_id()`.
3. **Android:** transitions give coarse start/end; refine `duration_s`
   with the sum of `app_usage_interval.duration_s` for that package in the
   window (UsageStats is authoritative for time-in-app). Use
   `screen_state_change` to bound sessions and `user_presence.present`
   for the idle-overlay approximation.
4. Write results via `Storage.write_canonical_session()`, or keep
   `sessions` empty and serve everything from `raw_events` queries.

## 7. Gotchas

- All timestamps are **UTC epoch milliseconds** (INTEGER). Convert with
  `datetime.fromtimestamp(ts / 1000, tz=utc)`; "a day" means a local-day
  range computed in the app, not a fixed 86 400 s slice.
- `payload` is JSON text — `json_extract` in SQL, or load in Python
  (`Storage` methods already parse it for you).
- `raw_events` holds FKs, not names: filter/group by joining
  `event_types`, `sources`, `devices` — or use the Storage API, which
  re-joins for you.
- No events are emitted while Android's screen is off (auto-pause) or on
  Windows while the app is closed — the timeline you build is
  "collection-lived", gaps are data, not corruption.
- Desktop Windows with no battery → no `power_change` rows at all.
- `sessions` has no producer yet, and `url_visits.session_id` is unfilled
  until the reconstructor runs; don't query them expecting data at
  v0.4.10. `url_visits.event_id` **is** populated at write time since v7.
- Duplicate writes are **not** silent failures: an exact `raw_events`
  re-import raises `IntegrityError` (by design, for sync idempotence);
  duplicate `url_visits` are skipped silently (same URL revisited in one
  session).
- Export (Settings → export) reads `raw_events` via `ExportService` — a
  ready-made example of the read path.

## 8. Exploring the DB from the CLI (official `sqlite3` shell)

You don't need any SQL GUI. SQLite ships an official command-line shell —
`sqlite3.exe` — from the SQLite project itself. Everything below is that
one tool, no custom scripts.

### Install on Windows (the official tool)

1. Go to <https://www.sqlite.org/download.html> → "Precompiled binaries
   for Windows" → download the **tools** bundle:
   `sqlite-tools-win-x64-<version>.zip` (contains `sqlite3.exe`; you do NOT
   need the DLL or shell bundles).
2. Extract the zip anywhere, e.g. `C:\sqlite\` (no installer, no admin
   rights needed).
3. (Optional) put it on PATH so `sqlite3` works from any terminal:
   Settings → System → About → Advanced system settings → Environment
   Variables → under *User variables* edit `Path` → New →
   `C:\sqlite` → OK (reopen the terminal afterwards).
4. Verify: `sqlite3 --version`.

### Open the database

The app may be running — that is fine (WAL readers don't block writers).
Use `-readonly` so you can never accidentally modify data:

```powershell
sqlite3 -readonly "$env:APPDATA\Unscreen\data.db"
```

(or, without PATH: `& "C:\sqlite\sqlite3.exe" -readonly "$env:APPDATA\Unscreen\data.db"`)

You are now at the `sqlite>` prompt. Exit anytime with `.quit`.

### First look around

```sql
.tables                                  -- devices, event_types, sources, raw_events, sessions, url_visits, sync_cursors
.schema raw_events                       -- DDL for one table (or .schema for all)
.headers on                              -- show column names
.mode column                             -- aligned table output (.mode box is prettier)
PRAGMA user_version;                     -- schema version, expect 7
PRAGMA journal_mode;                     -- expect 'wal'
PRAGMA integrity_check;                  -- 'ok' = file is healthy
```

### Reading the data as the guide's queries intend

Timestamps are UTC epoch **milliseconds**; render them human-readable with
a ms → s division plus the `unixepoch` modifier, `localtime` for your
timezone:

```sql
-- 10 most recent events, local time
SELECT datetime(e.timestamp / 1000, 'unixepoch', 'localtime') AS local_time,
       et.name AS event_type, s.name AS source,
       json_extract(e.payload, '$.app') AS app
FROM raw_events e
JOIN event_types et ON et.id = e.event_type_fk
JOIN sources s      ON s.id  = e.source_fk
ORDER BY e.id DESC LIMIT 10;

-- Event counts per type (first sanity check that collection works)
SELECT et.name AS event_type, COUNT(*) AS n
FROM raw_events e JOIN event_types et ON et.id = e.event_type_fk
GROUP BY et.name;

-- "Events today" in YOUR local day (same definition as the status bar)
SELECT COUNT(*) FROM raw_events e JOIN event_types et ON et.id = e.event_type_fk
WHERE e.timestamp >= strftime('%s', 'now', 'localtime', 'start of day') * 1000;

-- A day's foreground activity (any of the §5 recipes work verbatim here)
SELECT datetime(e.timestamp / 1000, 'unixepoch', 'localtime') AS t,
       json_extract(e.payload, '$.app') AS app
FROM raw_events e JOIN event_types et ON et.id = e.event_type_fk
WHERE et.name = 'foreground_transition'
  AND e.timestamp >= strftime('%s', 'now', 'localtime', 'start of day') * 1000
ORDER BY e.timestamp;
```

Notes:
- Modifiers are applied left-to-right: `'now','localtime','start of day'`
  = local now → midnight local → epoch seconds. Multiply by 1000 to get
  ms for comparison against `e.timestamp`.
- `json_extract(payload, '$.app')` is the SQL twin of the Python
  `payload["app"]` the guide uses; quoted keys work the same
  (`'$.duration_s'`, `'$.screen_on'`).
- Paste the longer §5 queries straight into the prompt; they only need
  `?` placeholders replaced with concrete epoch-ms numbers
  (e.g. `WHERE e.timestamp >= 1755000000000 AND e.timestamp <= 1755086400000`).

### Exporting results to a file

```sql
.mode csv
.output C:\temp\foreground_day.csv
SELECT ...;            -- query as usual
.output stdout         -- switch output back to the terminal
```

`.mode json` does the same for JSON.

### Android (optional)

The DB lives inside the app sandbox, so the only official path is the
Android SDK's `adb` (from Android Studio's "Command line tools" or
standalone platform-tools):

```powershell
adb exec-out run-as com.mycompany.unscreen cat files/Unscreen/data.db > data.db
sqlite3 -readonly data.db
```

(`run-as` works on debug builds; on a production build the file is not
readable this way.)

### Zero-install fallback (you already have it)

If you cannot download anything, `uv run python` has the official Python
`sqlite3` module built in — a one-liner, not a tool:

```powershell
uv run python -c "import sqlite3; c=sqlite3.connect(r'$env:APPDATA\Unscreen\data.db'); print(c.execute(\"SELECT et.name, COUNT(*) FROM raw_events e JOIN event_types et ON et.id=e.event_type_fk GROUP BY et.name\").fetchall())"
```

## 9. Other features worth knowing about

### Event-sourced pipeline (the architecture in one paragraph)

Everything in this DB is the middle of a pipeline (ADR-0002): platform
APIs → typed, immutable, append-only events → `raw_events` (canonical
store) → derived views (`sessions`, state blocks). Two consequences you
will feel: **nothing in `raw_events` is ever edited** (if a row is wrong,
the fix is a new event or a derived view, never an UPDATE), and **every
row remembers its origin** (`source_fk` → `sources`, `device_fk` →
`devices`). When in doubt about a number, go back to the events that
produced it — not the other way around.

### Platform parity — what is precise vs. approximated (ADR-0001)

| Concern | Windows | Android |
|---|---|---|
| Foreground app | Exact (2 s `GetForegroundWindow` poll) | Coarse (10 s usage events) |
| Time-in-app | Derived from transition timestamps | UsageStats batches (~60 s), authoritative for durations |
| Idle/away | Precise (`idle_transition` from `GetLastInputInfo`) | Approximated (`user_presence` boolean from screen state + event gaps) |
| Screen state | Not observable (no events) | Exact (`screen_state_change` on↔off) |
| Battery | `power_change` every 60 s (batteryless desktops → no rows) | `power_change` every 60 s |

Windows and Android foreground schemas deliberately diverge (process/title
vs. package/app_name) because they measure different things; AFK and power
schemas are shared. This asymmetry is a design decision, not drift.

### Device registry and multi-device

`devices` is an append-only registry (`INSERT OR IGNORE` at startup): one
row per physical device that ever ran the app, `is_current` marks the one
you are on now. Every event and session carries `device_fk`, so the schema
supports multi-device timelines; nothing in the app aggregates across
devices yet — query with
`WHERE e.device_fk = (SELECT id FROM devices WHERE is_current = 1)` when
you only want this machine.

### Sync design (planned)

All devices write into the single shared `raw_events` table, namespaced by
`device_fk`. The future sync engine exports each device's rows after its
high-water mark (`sync_cursors.last_synced_at`) and imports remote rows
as-is; re-imports are rejected by `UNIQUE(device_fk, event_type_fk,
timestamp, payload_hash)`. Cross-device timeline queries are plain
`WHERE device_fk IN (...)`.

### Schema versioning, wipe policy and durability

`PRAGMA user_version` (= `SCHEMA_VERSION = 7`) gates schema handling:
up-to-date DBs are opened as-is; a DB at version 0 (fresh) gets the schema
created; **any DB at a version below 7 is deleted and recreated fresh —
there is no data migration**. This is a deliberate early-stage policy:
schemas are still settling, so old data is dropped rather than half-
converted. On Android the durable backup
(`Download/Unscreen-data-backup/unscreen.db`) is **deleted during the
wipe** so a stale copy cannot resurrect pre-v7 data on the next install.
"Clear all data" in Settings wipes events, sessions, url visits, and
sync cursors (the device registry row stays).

### Automatic DB self-maintenance

On startup and hourly the app runs `PRAGMA integrity_check` + auto-VACUUM
(>20 % waste and >10 MB), and a corrupt file is quarantined as
`data.db.corrupt-<ts>` and rebuilt from a fresh schema (old rows are not
recovered — `raw_events` is the only source, and it is gone with the file).
There is nothing to do by hand; if you ever see `data.db.corrupt-*` files,
that is the app telling you it survived, not that you need to repair.

### URL visits — optional enrichment, Windows only (v7 wiring)

`url_visits` is an *optional* side-channel fed by the event bridge, not
the collector: the Windows foreground watcher attaches a `url_visit` dict
(`url`, `browser`, `scheme`, `host`, `domain`, `path`,
`extraction_method`, `confidence`, `is_trackable`) to its tick on URL
changes, and `_EventBridge` persists it against the owning
`foreground_transition` event — a **fresh** event on app change, the
**cached** `_last_event_id[watcher]` on tab change. The title-inference
fallback is recorded with `confidence="low"` and
`extraction_method=NULL`. The `foreground_transition` payload itself stays
lean. `event_id` is always set (NOT NULL, no backfill); `session_id` gets
filled by the future reconstructor via `backfill_url_session_id()`.

### Auto-pause and "collection-lived" timelines

Android pauses collection while the screen is off; Windows collects only
while the app runs. A gap in events is *data* (screen off / app closed),
never corruption — the timeline you can build is "collection-lived" by
design. The status bar makes this visible: `Auto-paused · screen off`
state, and a watcher chip without a fresh tick time means that watcher
stopped (or failed — it shows a `✗N` badge).

### The status bar — your live verification feature (v0.4.9)

`CollectionStatusBar` (bottom of the app shell) is the collection-health
readout: state dot/label (collecting / paused / auto-paused / stopped),
per-watcher chips (last tick in local time + failure badges), "N events
today" (a `count_events(since=local midnight in ms)` call) and the app
version. Cross-check it with the CLI (§8): the state should match
`collection_running`/`collection_paused` flags, chips should match recent
`last_tick_at` values, and the counter should match
`SELECT COUNT(*) ... strftime('%s','now','localtime','start of day') * 1000`.

### The export path as a reference read

Settings → export serializes `raw_events` via `ExportService` (CSV/JSON,
local-time timestamps with UTC offset) and is a working example of the
read path — start there before writing your own export.