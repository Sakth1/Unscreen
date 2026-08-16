import contextlib
import csv
import json
import logging
import os
import sqlite3
import tempfile
from io import StringIO
from typing import Any

from utils.files import timestamped_filename
from utils.time_utils import fmt_timestamp

_CHUNK_SIZE = 1024 * 1024

logger = logging.getLogger(__name__)


class ExportService:
    """Platform-agnostic export data preparation.

    Each ``prepare_*`` static method returns ``(filename, utf-8-encoded bytes)``.
    The caller is responsible for persisting the bytes to the desired location.
    """

    @staticmethod
    def prepare_raw_events_csv(rows: list[dict[str, Any]]) -> tuple[str, bytes]:
        filename = timestamped_filename("raw_events", "csv")
        buf = StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["id", "event_type", "timestamp", "collected_at", "source", "payload"]
        )
        for r in rows:
            w.writerow(
                [
                    r["id"],
                    r["event_type"],
                    fmt_timestamp(r["timestamp"]),
                    fmt_timestamp(r["collected_at"]),
                    r["source"],
                    json.dumps(r["payload"], ensure_ascii=False),
                ]
            )
        return filename, buf.getvalue().encode("utf-8")

    @staticmethod
    def prepare_raw_events(rows: list[dict[str, Any]]) -> tuple[str, bytes]:
        filename = timestamped_filename("raw_events", "json")
        out = [
            {
                "id": r["id"],
                "device_id": r["device_id"],
                "platform": r["platform"],
                "event_type": r["event_type"],
                "timestamp": fmt_timestamp(r["timestamp"]),
                "collected_at": fmt_timestamp(r["collected_at"]),
                "payload": r["payload"],
                "source": r["source"],
            }
            for r in rows
        ]
        data = json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8")
        return filename, data

    @staticmethod
    def prepare_db_snapshot(db_path: str) -> tuple[str, bytes] | None:
        """Return a consistent copy of the sqlite database as ``(filename, bytes)``.

        The snapshot is taken with ``VACUUM INTO`` (falling back to a chunked
        raw copy) so the export stays valid even while the app is writing.
        Returns ``None`` when the database does not exist or is empty.
        """
        if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
            return None
        fd, tmp = tempfile.mkstemp(prefix="unscreen-export-", suffix=".db")
        os.close(fd)
        os.unlink(tmp)
        try:
            try:
                src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    src.execute(f"VACUUM INTO '{tmp}'")
                finally:
                    src.close()
            except Exception:
                logger.warning(
                    "VACUUM INTO failed for db export — falling back to raw copy",
                    exc_info=True,
                )
                with open(db_path, "rb") as src_fp, open(tmp, "wb") as dst_fp:
                    while True:
                        chunk = src_fp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst_fp.write(chunk)
            with open(tmp, "rb") as fp:
                data = fp.read()
        except Exception:
            logger.exception("Failed to snapshot database for export")
            return None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return timestamped_filename("unscreen_data", "db"), data
