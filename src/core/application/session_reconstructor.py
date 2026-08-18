"""Deterministic session derivation from the canonical event store.

Sessions are a *derived view* over ``raw_events`` (ADR-0002): this module
reconstructs them by walking the event stream instead of trusting
write-time bookkeeping. It powers Android session production — the bridge
stays write-time for Windows, Android sessions are rebuilt from events.

Design:

* ``derive_app_sessions`` — one row per ``foreground_transition`` plus one
  per screen-on resume of the last-known app. A session closes at the next
  transition or at screen-off (``screen_state_change`` with
  ``screen_on=false``); screen-on reopens it for the last-known app, so
  screen-off time never inflates Android durations.
* ``derive_status_sessions`` — statuses come from screen state only
  (Android has no ``GetLastInputInfo`` equivalent): ``active`` while the
  screen is on, ``away`` while it is off. A head block opens at the first
  event when the device was awake before the first screen event; blocks
  are write-on-change (runs of equal status merge) and the final block
  stays open (``end_ts IS NULL``), matching Windows semantics.

``rebuild_sessions`` replaces every derived row for one device — delete
and re-insert in one transaction — so re-running is idempotent and repairs
crash leftovers, imported rows, and pre-derivation history alike.
"""

import logging

logger = logging.getLogger(__name__)

_FG_EVENT = "foreground_transition"
_SCREEN_EVENT = "screen_state_change"

_OPEN = None  # marker: end_ts/duration_s stay NULL while a session is open


def _close_open(open_row: dict, at_ts: int) -> None:
    """Close ``open_row`` at ``at_ts`` in place (half-open interval)."""
    open_row["end_ts"] = at_ts
    open_row["duration_s"] = (at_ts - open_row["start_ts"]) / 1000.0


def derive_app_sessions(events: list[dict]) -> list[dict]:
    """Derive ``app_sessions`` rows from a device's event stream.

    ``events`` must be ordered by ``(timestamp, id)`` ascending and carry
    ``id``, ``event_type``, ``timestamp`` and ``payload`` (parsed dict)
    keys. Only ``foreground_transition`` and ``screen_state_change`` rows
    are considered; everything else is ignored.

    Returns rows shaped like the ``app_sessions`` table columns
    (``event_id`` references the row that opened the session) with
    ``end_ts``/``duration_s`` left ``None`` while open.
    """
    sessions: list[dict] = []
    open_session = None
    last_app: tuple[str, dict] | None = None

    for ev in events:
        ts = ev["timestamp"]
        etype = ev["event_type"]

        if etype == _FG_EVENT:
            app_key = ev["payload"]["package"]
            if open_session is not None:
                _close_open(open_session, ts)
            last_app = (app_key, ev["payload"])
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
                if open_session is not None:
                    _close_open(open_session, ts)
                    open_session = None
            elif open_session is None and last_app is not None:
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

    return sessions


def derive_status_sessions(events: list[dict]) -> list[dict]:
    """Derive ``status_sessions`` rows from a device's event stream.

    Status is a pure function of screen state: ``active`` while the
    screen is on, ``away`` while it is off. When no screen event exists
    yet but other events prove the device was awake, an ``active`` head
    block opens at the first event. Rows are emitted on status change
    only; the final block stays open.
    """
    blocks: list[dict] = []
    open_block = None

    for ev in events:
        ts = ev["timestamp"]
        etype = ev["event_type"]

        if etype == _SCREEN_EVENT:
            status = "active" if ev["payload"]["screen_on"] else "away"
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
        elif open_block is None:
            open_block = {
                "event_id": ev["id"],
                "start_ts": ts,
                "end_ts": _OPEN,
                "duration_s": _OPEN,
                "status": "active",
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
