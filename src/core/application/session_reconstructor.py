"""Deterministic session derivation from the canonical event store.

Sessions are a *derived view* over ``raw_events`` (ADR-0002): this module
reconstructs them by walking the event stream instead of trusting
write-time bookkeeping. It powers Android session production — the bridge
stays write-time for Windows, Android sessions are rebuilt from events.

Design:

* ``derive_app_sessions`` — one row per ``foreground_transition`` plus one
  per screen-on resume of the last-known app. A session closes at the next
  transition or at screen-off (``screen_state_change`` with
  ``screen_on=false``); screen-on reopens it for the last-known app *only
  when the screen was off briefly* (<= ``_MAX_RESUME_GAP_MS``) — after a
  long gap the last-known app is stale and would inflate durations, so the
  resume waits for a real transition instead. ``app_usage_interval``
  entries (usage-stats deltas naming the active package) fill such gaps
  (F7b): when no session is open and the screen is on, the next interval
  opens a session for its package starting at
  ``max(gap_start, interval_ts - duration_ms)`` — the interval watcher
  keeps recording usage even when the foreground watcher misses
  transitions. Payloads are read from either ``payload["package"]``
  (Android) or ``payload["app"]`` (Windows), so the same derivation serves
  both platforms.
* ``derive_status_sessions`` — statuses come from screen state on Android
  (no ``GetLastInputInfo`` equivalent): ``active`` while the screen is on,
  ``away`` while it is off; on Windows they come from ``idle_transition``
  entries carrying ``status`` (``active``/``idle``/``away``). A head block
  opens at the first event when the device was awake before the first
  status event; blocks are write-on-change (runs of equal status merge)
  and the final block stays open (``end_ts IS NULL``), matching Windows
  semantics.

``rebuild_sessions`` replaces every derived row for one device — delete
and re-insert in one transaction — so re-running is idempotent and repairs
crash leftovers, imported rows, and pre-derivation history alike. The
replace also re-points ``url_visits.session_id`` to the freshly inserted
session rows (row ids change on re-insert), so a rebuild never orphans URL
visits on Windows.
"""

import logging

logger = logging.getLogger(__name__)

_FG_EVENT = "foreground_transition"
_SCREEN_EVENT = "screen_state_change"
_IDLE_EVENT = "idle_transition"
_USAGE_EVENT = "app_usage_interval"

_OPEN = None  # marker: end_ts/duration_s stay NULL while a session is open

# Longest screen-off gap (ms) after which a screen-on still resumes the
# last-known app. Beyond this the device was likely off long enough that
# the foreground app changed underneath us, so reopening the stale app
# would fabricate duration (F4: the bogus overnight "One UI Home" hours).
_MAX_RESUME_GAP_MS = 120_000


def _close_open(open_row: dict, at_ts: int) -> None:
    """Close ``open_row`` at ``at_ts`` in place (half-open interval)."""
    open_row["end_ts"] = at_ts
    open_row["duration_s"] = (at_ts - open_row["start_ts"]) / 1000.0


def derive_app_sessions(events: list[dict]) -> list[dict]:
    """Derive ``app_sessions`` rows from a device's event stream.

    ``events`` must be ordered by ``(timestamp, id)`` ascending and carry
    ``id``, ``event_type``, ``timestamp`` and ``payload`` (parsed dict)
    keys. ``foreground_transition``, ``screen_state_change`` and
    ``app_usage_interval`` rows are considered; everything else is
    ignored. App identity comes from ``payload["package"]`` (Android) or
    ``payload["app"]`` (Windows).

    ``app_usage_interval`` rows only fill *gaps* (F7b): a screen-on after
    a long screen-off leaves no open session, and the next interval names
    the app the device was actually in, so a session opens for it at
    ``max(gap_start, interval_ts - duration_ms)``. Intervals never close
    an open session — usage deltas lag reality and arrive in bursts, so
    handing sessions over on interval data would fabricate switches (e.g.
    a leftover delta for a background app splitting a real session).

    Returns rows shaped like the ``app_sessions`` table columns
    (``event_id`` references the row that opened the session) with
    ``end_ts``/``duration_s`` left ``None`` while open.
    """
    sessions: list[dict] = []
    open_session = None
    last_app: tuple[str, dict] | None = None
    last_screen_off_ts: int | None = None
    screen_on = True  # usage intervals only ever record screen-on time
    gap_since_ts: int | None = None

    for ev in events:
        ts = ev["timestamp"]
        etype = ev["event_type"]

        if etype == _FG_EVENT:
            app_key = ev["payload"].get("package") or ev["payload"].get(
                "app", "unknown"
            )
            if open_session is not None:
                _close_open(open_session, ts)
            last_app = (app_key, ev["payload"])
            gap_since_ts = None
            open_session = {
                "event_id": ev["id"],
                "start_ts": ts,
                "end_ts": _OPEN,
                "duration_s": _OPEN,
                "app_key": app_key,
                "payload": ev["payload"],
            }
            sessions.append(open_session)

        elif etype == _SCREEN_EVENT:
            if not ev["payload"]["screen_on"]:
                last_screen_off_ts = ts
                screen_on = False
                gap_since_ts = None
                if open_session is not None:
                    _close_open(open_session, ts)
                    open_session = None
            else:
                screen_on = True
                if open_session is None:
                    if (
                        last_app is not None
                        and last_screen_off_ts is not None
                        and ts - last_screen_off_ts <= _MAX_RESUME_GAP_MS
                    ):
                        app_key, payload = last_app
                        open_session = {
                            "event_id": ev["id"],
                            "start_ts": ts,
                            "end_ts": _OPEN,
                            "duration_s": _OPEN,
                            "app_key": app_key,
                            "payload": payload,
                        }
                        sessions.append(open_session)
                    else:
                        gap_since_ts = ts

        elif etype == _USAGE_EVENT:
            if open_session is not None or not screen_on:
                continue
            app_key = ev["payload"].get("package") or ev["payload"].get("app")
            if not app_key:
                continue
            duration_ms = max(0, int(ev["payload"].get("duration_ms") or 0))
            # With a screen anchor the session never precedes the screen-on;
            # without one (stream starts mid-usage) the delta covers the
            # time before the interval was observed.
            start_ts = (
                max(gap_since_ts, ts - duration_ms)
                if gap_since_ts is not None
                else ts - duration_ms
            )
            open_session = {
                "event_id": ev["id"],
                "start_ts": start_ts,
                "end_ts": _OPEN,
                "duration_s": _OPEN,
                "app_key": app_key,
                # Normalized to the transition payload shape so totals
                # group both sources of the same app under one key.
                "payload": {
                    "package": app_key,
                    "app_name": ev["payload"].get("app_name") or app_key,
                },
            }
            sessions.append(open_session)
            gap_since_ts = None

    return sessions


def derive_status_sessions(events: list[dict]) -> list[dict]:
    """Derive ``status_sessions`` rows from a device's event stream.

    Status is a pure function of screen state on Android (``active`` while
    the screen is on, ``away`` while it is off) and of ``idle_transition``
    payloads on Windows (``active``/``idle``/``away``). When no status
    event exists yet but other events prove the device was awake, an
    ``active`` head block opens at the first event. Rows are emitted on
    status change only; the final block stays open.
    """
    blocks: list[dict] = []
    open_block = None

    for ev in events:
        ts = ev["timestamp"]
        etype = ev["event_type"]

        if etype == _SCREEN_EVENT:
            status = "active" if ev["payload"]["screen_on"] else "away"
        elif etype == _IDLE_EVENT:
            status = ev["payload"].get("status", "active")
        else:
            if open_block is None:
                open_block = {
                    "event_id": ev["id"],
                    "start_ts": ts,
                    "end_ts": _OPEN,
                    "duration_s": _OPEN,
                    "status": "active",
                    "payload": ev["payload"],
                }
                blocks.append(open_block)
            continue

        if open_block is not None and open_block["status"] == status:
            continue
        if open_block is not None:
            _close_open(open_block, ts)
        open_block = {
            "event_id": ev["id"],
            "start_ts": ts,
            "end_ts": _OPEN,
            "duration_s": _OPEN,
            "status": status,
            "payload": ev["payload"],
        }
        blocks.append(open_block)

    return blocks


def rebuild_sessions(storage, device_id: str) -> dict:
    """Derive and replace every session row for ``device_id``.

    Idempotent: re-running produces the same rows. Returns a summary dict
    with the produced row counts.
    """
    events = storage.get_raw_events(device_id=device_id)
    app_rows = derive_app_sessions(events) if events else []
    status_rows = derive_status_sessions(events) if events else []
    storage.replace_device_sessions(device_id, app_rows, status_rows)
    result = {
        "device_id": device_id,
        "app_sessions": len(app_rows),
        "status_sessions": len(status_rows),
    }
    logger.info(
        "Rebuilt sessions for %s: %d app, %d status",
        device_id,
        len(app_rows),
        len(status_rows),
    )
    return result


def rebuild_all_sessions(storage) -> list[dict]:
    """Rebuild sessions for every device that has raw events."""
    summaries = []
    for device in storage.list_devices():
        count = storage.count_events(device_id=device["device_id"])
        if count == 0:
            continue
        summaries.append(rebuild_sessions(storage, device["device_id"]))
    return summaries
