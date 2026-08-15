"""Explore the Unscreen SQLite database from the command line.

Prints the same first-look information as the sqlite3 shell walkthrough in
docs/architecture/db-query-guide.md section 8: database location, schema
version, integrity, tables with row counts, device registry, event catalog
summary, today's event count and the most recent events in local time.

Read-only (mode=ro) - safe to run while the app is collecting.

Usage:
    uv run python scripts/db_explore.py [db_path] [--schema] [--limit N]
"""

import argparse
import json
import os
import platform
import sqlite3
import sys
from datetime import datetime

_ANDROID_PACKAGE = "com.mycompany.unscreen"


def resolve_db_path() -> str:
    override = os.environ.get("UNSCREEN_DATA_DIR")
    if override:
        return os.path.join(override, "data.db")
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "Unscreen", "data.db")
    home = os.environ.get("HOME")
    if home:
        return os.path.join(home, "Unscreen", "data.db")
    return os.path.join(f"/data/data/{_ANDROID_PACKAGE}/files", "Unscreen", "data.db")


def fmt_local(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def event_summary(conn: sqlite3.Connection) -> None:
    print("\nEvent catalog (raw_events)")
    print("-------------------------")
    print("Per event_type:")
    for r in conn.execute(
        "SELECT event_type, COUNT(*) FROM raw_events GROUP BY event_type ORDER BY 2 DESC"
    ):
        print(f"  {r[0]:<24} {r[1]:>8}")
    print("Per source:")
    for r in conn.execute(
        "SELECT source, COUNT(*) FROM raw_events GROUP BY source ORDER BY 2 DESC"
    ):
        print(f"  {r[0]:<24} {r[1]:>8}")
    print("Per platform:")
    for r in conn.execute(
        "SELECT platform, COUNT(*) FROM raw_events GROUP BY platform"
    ):
        print(f"  {r[0]:<24} {r[1]:>8}")
    local_start = (
        datetime.now()
        .astimezone()
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    today = conn.execute(
        "SELECT COUNT(*) FROM raw_events WHERE timestamp >= ?", (local_start,)
    ).fetchone()[0]
    first = conn.execute("SELECT MIN(timestamp) FROM raw_events").fetchone()[0]
    last = conn.execute("SELECT MAX(timestamp) FROM raw_events").fetchone()[0]
    print(f"  {'events today (local day)':<24} {today:>8}")
    print(f"  {'first event (local)':<24} {fmt_local(first):>8}")
    print(f"  {'last event (local)':<24} {fmt_local(last):>8}")


def recent_events(conn: sqlite3.Connection, limit: int) -> None:
    print(f"\nMost recent {limit} events (local time)")
    print("------------------------------------")
    rows = conn.execute(
        "SELECT timestamp, event_type, source, payload FROM raw_events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    for ts, event_type, source, payload in rows:
        preview = json.dumps(payload)[:90]
        print(f"  {fmt_local(ts)}  {event_type:<24} {source:<18} {preview}")


def devices(conn: sqlite3.Connection) -> None:
    print("\nDevices")
    print("-------")
    try:
        rows = conn.execute(
            "SELECT device_id, hostname, platform, first_seen, last_seen, is_current FROM devices ORDER BY first_seen"
        ).fetchall()
    except sqlite3.OperationalError:
        print("  (no devices table)")
        return
    for device_id, hostname, plat, first, last, current in rows:
        print(
            f"  {device_id:<24} {hostname or '-':<16} {plat:<8} "
            f"first={fmt_local(datetime.fromisoformat(first).timestamp()) if first else '-'} "
            f"last={fmt_local(datetime.fromisoformat(last).timestamp()) if last else '-'} "
            f"{'<-- current' if current else ''}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Explore the Unscreen SQLite database")
    parser.add_argument(
        "db_path", nargs="?", help="path to data.db (default: auto-resolved)"
    )
    parser.add_argument("--schema", action="store_true", help="print full table DDL")
    parser.add_argument("--limit", type=int, default=10, help="recent events to show")
    args = parser.parse_args()

    path = args.db_path or resolve_db_path()
    if not os.path.exists(path):
        print(f"Database not found at: {path}")
        print("Run the app once so it creates the file, or pass an explicit path.")
        sys.exit(1)

    size = os.path.getsize(path)
    sidecars = [f for f in os.listdir(os.path.dirname(path)) if f.startswith("data.db")]
    print(f"Database: {path}")
    print(f"Size: {size / 1024:.1f} KiB")
    print(f"Files in dir: {', '.join(sidecars)}")
    print()

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        print("Pragmas")
        print("-------")
        print(f"  user_version    {conn.execute('PRAGMA user_version').fetchone()[0]}")
        print(f"  journal_mode    {conn.execute('PRAGMA journal_mode').fetchone()[0]}")
        print(
            f"  integrity_check {conn.execute('PRAGMA integrity_check').fetchone()[0]}"
        )

        print("\nTables")
        print("------")
        for name in table_names(conn):
            print(f"  {name:<24} {row_count(conn, name):>8} rows")

        devices(conn)

        if "raw_events" in table_names(conn):
            event_summary(conn)
            recent_events(conn, args.limit)

        if args.schema:
            print("\nSchema (DDL)")
            print("-------------")
            for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL ORDER BY name"
            ):
                print(f"  -- {r[0]}")
                for line in r[1].splitlines():
                    print(f"  {line}")
                print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
