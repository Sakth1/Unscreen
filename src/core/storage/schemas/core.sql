-- Schema v8 — de-bloated canonical event store + write-time session production
--
-- Design decisions (see docs/architecture/db-query-guide.md §2):
-- * Device identity lives ONCE in `devices` (the per-install unique UUID).
--   Event rows reference it by a small integer FK instead of repeating a
--   36-char string on every row.
-- * Timestamps are INTEGER milliseconds (Unix epoch UTC) — exact
--   comparisons, deterministic dedup keys, no float artifacts.
-- * `platform` is derivable from `devices.platform` via device_fk, so it is
--   not repeated on event rows.
-- * `event_type` / `source` are dictionary-encoded (a handful of distinct
--   values each) into small integer FKs.
-- * `payload_hash` (8-byte blake2b of canonical payload JSON, stored as
--   INTEGER) makes sync re-imports idempotent: UNIQUE(device_fk,
--   event_type_fk, timestamp, payload_hash) admits Android's
--   same-timestamp interval fan-out (distinct payloads => distinct hashes)
--   while rejecting identical re-imports. 8 bytes halves the v7 cost (16B
--   BLOB) with negligible collision risk for dedup at personal scale.
-- * `sync_cursors` holds per-remote-device high-water marks for the planned
--   sync engine.

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT, -- stable integer key for FK references
    device_id   TEXT NOT NULL UNIQUE,              -- per-install unique UUID, generated once
    hostname    TEXT,
    platform    TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT,
    is_current  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_types (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS sources (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

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

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_identity
    ON raw_events(device_fk, event_type_fk, timestamp, payload_hash);

CREATE INDEX IF NOT EXISTS idx_raw_events_device_ts
    ON raw_events(device_fk, timestamp);

CREATE INDEX IF NOT EXISTS idx_raw_events_type_ts
    ON raw_events(event_type_fk, timestamp);

-- Schema v8 — Derived app sessions, produced at write time (Windows)
-- The event bridge inserts one row per foreground_transition (the row that
-- STARTED the block, referenced by event_id) and closes it when the next
-- transition arrives or collection stops: end_ts = next transition start,
-- duration_s = (end_ts - start_ts) / 1000. Half-open [start_ts, end_ts).

CREATE TABLE IF NOT EXISTS app_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk  INTEGER NOT NULL REFERENCES devices(id),
    event_id   INTEGER NOT NULL REFERENCES raw_events(id),
    start_ts   INTEGER NOT NULL,             -- Unix epoch ms (UTC) — the owning transition's timestamp
    end_ts     INTEGER,                      -- Unix epoch ms (UTC) — NULL while the session is open
    duration_s REAL,                         -- (end_ts - start_ts) / 1000, NULL while open
    app_key    TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_sessions_identity
    ON app_sessions(device_fk, event_id);

CREATE INDEX IF NOT EXISTS idx_app_sessions_device_app
    ON app_sessions(device_fk, app_key, start_ts);

CREATE INDEX IF NOT EXISTS idx_app_sessions_ts
    ON app_sessions(device_fk, start_ts);

-- Schema v8 — Status blocks (Windows idle state machine)
-- One row per idle_transition event (event_id = the owning event). The
-- bridge inserts the row when a status entry is written and closes it on
-- the next entry or on collection stop: end_ts = next entry's start,
-- duration_s = (end_ts - start_ts) / 1000. Every status is recorded,
-- including 'active' — absence of a row never means anything.

CREATE TABLE IF NOT EXISTS status_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk  INTEGER NOT NULL REFERENCES devices(id),
    event_id   INTEGER NOT NULL REFERENCES raw_events(id),
    start_ts   INTEGER NOT NULL,             -- Unix epoch ms (UTC) — the owning event's timestamp
    end_ts     INTEGER,                      -- Unix epoch ms (UTC) — NULL while the block is open
    duration_s REAL,                         -- (end_ts - start_ts) / 1000, NULL while open
    status     TEXT NOT NULL,                -- 'active' | 'idle' | 'away'
    payload    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_status_sessions_identity
    ON status_sessions(device_fk, event_id);

CREATE INDEX IF NOT EXISTS idx_status_sessions_ts
    ON status_sessions(device_fk, start_ts);

CREATE INDEX IF NOT EXISTS idx_status_sessions_status
    ON status_sessions(device_fk, status, start_ts);

-- Schema v7 — URL visit log (browser navigation events, Windows only)
-- event_id is populated at write time by the event bridge: the
-- foreground_transition that started the browser session owns every
-- url_visit row written until the next app transition. session_id is
-- backfilled by the bridge when the owning app session closes.

CREATE TABLE IF NOT EXISTS url_visits (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk        INTEGER NOT NULL REFERENCES devices(id),
    event_id         INTEGER NOT NULL REFERENCES raw_events(id),
    session_id       INTEGER REFERENCES app_sessions(id),

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
    collected_at     INTEGER NOT NULL        -- Unix epoch ms (UTC)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_url_visits_identity
    ON url_visits(device_fk, event_id, url);

CREATE INDEX IF NOT EXISTS idx_url_visits_device_seen
    ON url_visits(device_fk, seen_at);

CREATE INDEX IF NOT EXISTS idx_url_visits_device_domain
    ON url_visits(device_fk, domain, seen_at);

CREATE INDEX IF NOT EXISTS idx_url_visits_event
    ON url_visits(event_id);

CREATE INDEX IF NOT EXISTS idx_url_visits_session
    ON url_visits(session_id);

-- Schema v7 — sync high-water marks (planned sync engine)
-- One row per remote device this device has exchanged data with.

CREATE TABLE IF NOT EXISTS sync_cursors (
    remote_device_id TEXT PRIMARY KEY,
    last_synced_at   INTEGER NOT NULL        -- Unix epoch ms (UTC)
);

-- Schema v9 — App icon cache
-- Resolved icons are cached to avoid re-extraction on every dashboard load.
-- Entries older than 30 days are evicted on each cache pass. Fingerprints
-- track staleness so an app update/move triggers re-extraction.
CREATE TABLE IF NOT EXISTS app_icons (
    app_key     TEXT PRIMARY KEY,
    source      TEXT NOT NULL,          -- 'android_package' | 'windows_exe' | 'site_favicon'
    fingerprint TEXT NOT NULL,
    png         BLOB NOT NULL,
    width       INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL       -- Unix epoch ms (UTC)
);