# ADR-0002: Event-Sourced Collection Architecture

**Status:** Accepted (v0.4.1); storage layout updated by schema v7 (v0.4.10-dev) — see "Schema v7 update" below.

## Context

The exported Android datasets revealed architectural problems that incremental fixes cannot resolve:

- `duration=0` alongside `data.durations.duration_s=58` — two conflicting sources of truth in one row
- Multi-entity embedding — a single observation simultaneously represents current foreground app, per-package cumulative durations, and a provenance label
- The 58-second duration ceiling — `UsageStatsManager.queryUsageStats()` is a batch API that flushes approximately every 60s; reducing the poll interval does not change this
- AFK timeline contradictions — Android has no equivalent of Windows `GetLastInputInfo`; approximating idle time from app events produces fabricated precision
- Research confirms (Google Issue Tracker, April 2026) that even Digital Wellbeing uses privileged system APIs unavailable to third parties

## Decision

Replace the observation-centric pipeline with an **event-sourced architecture**.

### Core principles

1. **Platform APIs emit discrete events, not rows.** Each collector transforms a native API call into a typed, immutable, append-only event.

2. **Duration is never a first-class column.** Duration is computed from event timestamps during reconstruction, never stored at write time.

3. **Sessions are a derived view.** Reconstructed deterministically from events, not approximated via pulse-merge at write time.

4. **AFK is modeled as device state, not idle time.** On Android, only screen state and app-event gaps are observable. "Idle seconds" is fabricated precision.

5. **One entity per event.** No embedding of aggregated durations alongside point-in-time fields.

6. **Provenance is preserved.** Every event records which platform API produced it.

### Pipeline

```
Platform APIs
    │
    ▼
Raw Events (typed, immutable, append-only)
    │
    ├── foreground_transition    (app switch detected)
    ├── app_usage_interval       (per-package duration in window)
    ├── screen_state_change      (on↔off transition)
    ├── power_change             (battery snapshot)
    ├── idle_transition          (Windows only — precise)
    └── user_presence            (Android only — boolean approximation)
    │
    ▼
Canonical Event Store (raw_events table, SQLite)
    │
    ▼
Session Reconstructor (deterministic, idempotent)
    │
    ▼
Sessions Table (derived, recompute at will)
    │
    ▼
Device State Timeline (derived, contiguous blocks)
```

### Canonical entities

#### RawEvent (write-time entity)

```python
event_type: str       # "foreground_transition" | "app_usage_interval" | etc.
timestamp: float      # when the event occurred (UTC epoch)
collected_at: float   # when we observed it (UTC epoch)
payload: dict         # type-specific data
source: str           # which API produced this
device_id: str        # provenance
platform: str         # "windows" | "android"
```

#### Session (derived)

```python
start_ts: float       # first event timestamp
end_ts: float         # last event timestamp + poll_interval
duration_s: float     # sum of app_usage_interval durations OR end-start
app_key: str          # process name / package name
payload: dict         # merged metadata
session_type: str     # "foreground"
```

#### StateBlock (derived)

```python
start_ts: float
end_ts: float
state_type: str       # "screen_on" | "screen_off" | "charging" | "discharging"
value: any
```

### Migration

The migration happens in phases, maintaining backward compatibility at each step:

- **Phase A:** Add `raw_events` table + dual-write (events go to both old and new tables)
- **Phase B:** Rewrite Android collectors as event-driven
- **Phase C:** Rewrite sessionizer to consume raw_events
- **Phase D:** Remove legacy tables and pulse-merge code

## Consequences

- Positive: No more `duration=0` contradictions; one entity per event; deterministic session reconstruction; provenance tracking
- Positive: Android data becomes event-driven (fewer rows, higher semantic density); ~1440 rows/day → ~200
- Positive: AFK on Android no longer fabricates idle-seconds precision
- Neutral: Dual-write temporarily doubles storage writes during migration
- Negative: Windows precision cannot be matched on Android (fundamental API limitation — documented, not hidden)
- Migration effort: phased approach allows each step to be verified independently

## Schema v7 update (v0.4.10-dev) — de-bloated storage layout

The event-sourced principles are unchanged; the **storage layout** of the
canonical store was redesigned to stay small as the timeline grows:

- **Device identity lives once.** `devices` gains a stable integer `id`;
  event rows reference it via `device_fk` instead of repeating the 36-char
  UUID on every row. `platform` is no longer repeated either (derivable
  via the `devices` join). RawEvent's `device_id`/`platform` are still
  returned by the Storage API — the API shape is preserved.
- **Timestamps are integer milliseconds** (UTC epoch) everywhere —
  exact comparisons, deterministic dedup keys, no float artifacts.
- **`event_type` / `source` are dictionary-encoded** into small integer
  FKs (`event_types`, `sources` tables).
- **`payload_hash` (16-byte blake2b of canonical payload JSON) makes
  re-imports idempotent**: `UNIQUE(device_fk, event_type_fk, timestamp,
  payload_hash)` rejects exact duplicates (sync-safe) while admitting
  Android's same-millisecond `app_usage_interval` fan-out (distinct
  payloads → distinct hashes).
- **`sync_cursors`** records per-remote-device high-water marks for the
  planned sync engine.
- **`url_visits.event_id` is populated at write time** by the event
  bridge (fresh event on app change, cached `_last_event_id[watcher]` on
  tab change) — the bridge owns URL-visit persistence, the collector only
  attaches a `url_visit` dict to its tick. `foreground_transition`
  payloads stay lean (no browser/url/page_title/inferred_domain).
- **Pre-v7 databases are wiped and recreated fresh — no data migration**
  (early-stage policy; the Android durable backup is deleted during the
  wipe). This replaces the phased migration plan above, which is obsolete.

## Schema v8 update (v0.4.10-dev) — write-time session production

Principles 2 and 3 of this ADR are **amended** for Windows:

- **Duration is computed from event timestamps at write time, not stored
  at collection time.** The event bridge still never fabricates duration
  — it computes it from the *next* event's timestamp. The invariant
  "duration is derived from timestamps, never authored" is unchanged; only
  the moment of derivation moved from a batch reconstructor to the write
  path.
- **Sessions are produced, not reconstructed.** `sessions` is renamed
  `app_sessions`; on every `foreground_transition` write the bridge opens
  a session for the new event (`event_id` FK, NOT NULL) and closes the
  previous one at the new event's timestamp (`end_ts`,
  `duration_s = (end_ts - start_ts) / 1000`). The open session is closed
  on collection stop. Sessions remain disposable and idempotent per event
  (`UNIQUE(device_fk, event_id)`), and `url_visits.session_id` is
  backfilled at close time.
- **Status blocks are a second derived table.** `status_sessions` records
  one row per `idle_transition` entry (every status, including `active`),
  closed the same way at the next entry or on stop. This replaces the
  planned StateBlock reconstructor for Windows idle; Android
  (`user_presence`) stays event-only for now.
- **`payload_hash` is an 8-byte blake2b stored as INTEGER** (was a 16-byte
  BLOB). Same dedup semantics, half the storage.
- **Pre-v8 databases are wiped and recreated fresh** — same early-stage
  policy, extended to v8.

## Android session derivation (v0.5.x) — the write-time amendment does not extend to Android

Android sessions are produced by a **derivation pass over raw events**
(`core/application/session_reconstructor.py`), not by the write-time
bridge, because:

- **Screen-off is the only reliable session boundary.** On Android, apps
  keep running and `foreground_transition` only fires when the foreground
  package actually changes (10s poll, plus two-stage confirm). A
  write-time producer would have to guess session ends; the reconstructor
  splits app sessions at `screen_state_change` off and reopens the
  last-known app on screen-on.
- **`app_usage_interval` is never a session source.** QueryEvents
  intervals overstate durations (observed 148s of "usage" inside a 60s
  poll window) and are unreliable on Android 14+ (queryEvents staleness,
  Google issue 309104474). It remains an event type only.
- **Statuses follow screen state, not input idleness.** Android has no
  `GetLastInputInfo` equivalent; the AccessibilityService route was
  rejected (policy, battery, and a Python app's runtime costs). Status
  blocks derive `active`/`away` from `screen_state_change`; `user_presence`
  stays event-only. "AFK" on Android *is* the device being away.
- **Idempotence is delete-and-rebuild.** `replace_device_sessions` wipes
  and re-inserts a device's rows in one transaction, preserving
  `UNIQUE(device_fk, event_id)`. Runs at collection start and stop, and
  via `scripts/backfill_sessions.py` for imported databases. Re-runs
  produce identical rows (verified: 28 app sessions summing 41.8 min
  against 42 min of measured screen-on on a real Samsung dataset).
- **Derivation must not read the future.** Head blocks open at the first
  event when awake before any screen event; a trailing block stays open
  (`end_ts IS NULL`, duration NULL) until the next run closes it. Windows
  semantics unchanged — its bridge remains the only write-time producer.
