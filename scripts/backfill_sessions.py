"""Rebuild derived app_sessions / status_sessions from raw_events.

Derived sessions are a view over the canonical event store — wiping and
re-inserting them per device is idempotent (see
``core.application.session_reconstructor``). Use this to backfill
databases that predate Android session derivation (e.g. exported Android
DBs), or after manual raw-event imports.

Note: opening a database through ``Storage`` registers the current machine
as a device row if it is not present yet — harmless for copies, be aware
when pointing this at a live database.

Usage:
    uv run python scripts/backfill_sessions.py [db_path] [--device UUID]
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path", nargs="?", help="path to data.db (default: auto-resolved)"
    )
    parser.add_argument(
        "--device",
        help="rebuild only this device_id (default: every device with events)",
    )
    args = parser.parse_args()

    from core.application.session_reconstructor import (
        rebuild_all_sessions,
        rebuild_sessions,
    )
    from core.storage import Storage

    storage = Storage(args.db_path)
    try:
        if args.device:
            summaries = [rebuild_sessions(storage, args.device)]
        else:
            summaries = rebuild_all_sessions(storage)
    finally:
        storage.close()

    if not summaries:
        print("No devices with raw events found — nothing to rebuild.")
        return 0

    total_app = sum(s["app_sessions"] for s in summaries)
    total_status = sum(s["status_sessions"] for s in summaries)
    for s in summaries:
        print(
            f"  {s['device_id']}: {s['app_sessions']} app sessions, "
            f"{s['status_sessions']} status blocks"
        )
    print(f"Rebuilt {len(summaries)} device(s): {total_app} app, {total_status} status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
