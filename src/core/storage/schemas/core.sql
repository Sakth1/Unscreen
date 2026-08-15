-- Schema v7 — de-bloated canonical event store
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
-- * `payload_hash` (16-byte blake2b of canonical JSON payload) makes sync
--   re-imports idempotent: UNIQUE(device_fk, event_type_fk, timestamp,
--   payload_hash) admits Android's same-timestamp interval fan-out (distinct
--   payloads => distinct hashes) while rejecting identical re-imports.
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
    payload_hash  BLOB NOT NULL              -- 16-byte blake2b of canonical payload JSON
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_identity
    ON raw_events(device_fk, event_type_fk, timestamp, payload_hash);

CREATE INDEX IF NOT EXISTS idx_raw_events_device_ts
    ON raw_events(device_fk, timestamp);

CREATE INDEX IF NOT EXISTS idx_raw_events_type_ts
    ON raw_events(event_type_fk, timestamp);

-- Schema v7 — Derived sessions (shared, not per-device)
-- Sessions are reconstructed from raw_events deterministically.

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_fk     INTEGER NOT NULL REFERENCES devices(id),
    start_ts      INTEGER NOT NULL,          -- Unix epoch ms (UTC)
    end_ts        INTEGER,                   -- Unix epoch ms (UTC)
    duration_s    REAL,
    app_key       TEXT NOT NULL,
    payload       TEXT NOT NULL,
    session_type  TEXT DEFAULT 'foreground'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_identity
    ON sessions(device_fk, app_key, start_ts);

CREATE INDEX IF NOT EXISTS idx_sessions_device_app
    ON sessions(device_fk, app_key, start_ts);

CREATE INDEX IF NOT EXISTS idx_sessions_ts
    ON sessions(device_fk, start_ts);

-- Schema v7 — URL visit log (browser navigation events, Windows only)
-- event_id is populated at write time by the event bridge: the
-- foreground_transition that started the browser session owns every
-- url_visit row written until the next app transition. session_id is
-- filled by the session reconstructor (derived data).

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