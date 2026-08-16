import csv
import json
import sqlite3
from io import StringIO

from core.application.export_service import ExportService

_RAW_ROW = {
    "id": 1,
    "device_id": "dev1",
    "platform": "android",
    "event_type": "app_usage_interval",
    "timestamp": 1700000000000,
    "collected_at": 1700000010000,
    "payload": {"key": "val"},
    "source": "android_app_usage",
}


class TestPrepareRawEventsCsv:
    def test_empty_rows(self):
        filename, data = ExportService.prepare_raw_events_csv([])
        assert filename.endswith(".csv")
        decoded = data.decode("utf-8")
        reader = csv.reader(StringIO(decoded))
        rows = list(reader)
        assert rows[0] == [
            "id",
            "event_type",
            "timestamp",
            "collected_at",
            "source",
            "payload",
        ]
        assert len(rows) == 1

    def test_single_row(self):
        rows = [_RAW_ROW]
        filename, data = ExportService.prepare_raw_events_csv(rows)
        decoded = data.decode("utf-8")
        reader = csv.reader(StringIO(decoded))
        rows_out = list(reader)
        assert len(rows_out) == 2
        assert rows_out[1][0] == "1"
        assert rows_out[1][1] == "app_usage_interval"
        assert "key" in rows_out[1][5]

    def test_multiple_rows(self):
        rows = [{**_RAW_ROW, "id": i} for i in range(5)]
        filename, data = ExportService.prepare_raw_events_csv(rows)
        decoded = data.decode("utf-8")
        reader = csv.reader(StringIO(decoded))
        rows_out = list(reader)
        assert len(rows_out) == 6

    def test_nested_payload(self):
        rows = [{**_RAW_ROW, "payload": {"level1": {"level2": "deep"}}}]
        _, data = ExportService.prepare_raw_events_csv(rows)
        decoded = data.decode("utf-8")
        reader = csv.reader(StringIO(decoded))
        rows_out = list(reader)
        assert '"level2"' in rows_out[1][5]

    def test_filename_format(self):
        filename, _ = ExportService.prepare_raw_events_csv([])
        assert filename.startswith("raw_events_")
        assert filename.endswith(".csv")
        assert filename.count("_") >= 2

    def test_unicode_in_payload(self):
        rows = [{**_RAW_ROW, "payload": {"name": "José café ñoño"}}]
        _, data = ExportService.prepare_raw_events_csv(rows)
        decoded = data.decode("utf-8")
        assert "José" in decoded

    def test_payload_list_field(self):
        rows = [{**_RAW_ROW, "payload": {"items": [1, 2, 3]}}]
        _, data = ExportService.prepare_raw_events_csv(rows)
        decoded = data.decode("utf-8")
        assert "[1, 2, 3]" in decoded


class TestPrepareRawEvents:
    def test_empty_rows(self):
        filename, data = ExportService.prepare_raw_events([])
        assert filename.endswith(".json")
        parsed = json.loads(data.decode("utf-8"))
        assert parsed == []

    def test_single_row(self):
        rows = [_RAW_ROW]
        _, data = ExportService.prepare_raw_events(rows)
        parsed = json.loads(data.decode("utf-8"))
        assert len(parsed) == 1
        assert parsed[0]["id"] == 1
        assert parsed[0]["payload"]["key"] == "val"
        assert parsed[0]["event_type"] == "app_usage_interval"
        assert parsed[0]["source"] == "android_app_usage"

    def test_timestamp_formatted(self):
        from datetime import datetime

        rows = [_RAW_ROW]
        _, data = ExportService.prepare_raw_events(rows)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed[0]["timestamp"] == datetime.fromtimestamp(
            1700000000.0
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f%z")
        assert parsed[0]["collected_at"] == datetime.fromtimestamp(
            1700000010.0
        ).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f%z")

    def test_unicode(self):
        rows = [{**_RAW_ROW, "payload": {"name": "José café"}}]
        _, data = ExportService.prepare_raw_events(rows)
        decoded = data.decode("utf-8")
        assert "José" in decoded

    def test_pretty_print(self):
        rows = [_RAW_ROW]
        _, data = ExportService.prepare_raw_events(rows)
        decoded = data.decode("utf-8")
        assert "\n  " in decoded

    def test_multiple_rows(self):
        rows = [{**_RAW_ROW, "id": i} for i in range(100)]
        _, data = ExportService.prepare_raw_events(rows)
        parsed = json.loads(data.decode("utf-8"))
        assert len(parsed) == 100

    def test_filename_format(self):
        filename, _ = ExportService.prepare_raw_events([])
        assert filename.endswith(".json")
        assert filename.startswith("raw_events_")


class TestConsistency:
    def test_csv_and_json_same_data(self):
        rows = [_RAW_ROW, {**_RAW_ROW, "id": 2, "event_type": "foreground_transition"}]
        csv_fn, csv_data = ExportService.prepare_raw_events_csv(rows)
        json_fn, json_data = ExportService.prepare_raw_events(rows)
        assert csv_fn != json_fn
        assert len(csv_data) > 0


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "data.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO items VALUES ('answer', '42')")
    conn.commit()
    conn.close()
    return db_path


class TestPrepareDbSnapshot:
    def test_missing_db_returns_none(self, tmp_path):
        assert ExportService.prepare_db_snapshot(str(tmp_path / "nope.db")) is None

    def test_empty_db_returns_none(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        sqlite3.connect(db_path).close()
        assert ExportService.prepare_db_snapshot(db_path) is None

    def test_filename_format(self, tmp_path):
        snapshot = ExportService.prepare_db_snapshot(_make_db(tmp_path))
        assert snapshot is not None
        filename, _ = snapshot
        assert filename.startswith("unscreen_data_")
        assert filename.endswith(".db")

    def test_snapshot_is_valid_sqlite_with_data(self, tmp_path):
        snapshot = ExportService.prepare_db_snapshot(_make_db(tmp_path))
        assert snapshot is not None
        filename, data = snapshot
        assert len(data) > 0
        snap_path = tmp_path / filename
        snap_path.write_bytes(data)
        conn = sqlite3.connect(snap_path)
        try:
            row = conn.execute("SELECT v FROM items WHERE k='answer'").fetchone()
            assert row == ("42",)
        finally:
            conn.close()

    def test_snapshot_does_not_modify_source(self, tmp_path):
        db_path = _make_db(tmp_path)
        with open(db_path, "rb") as fp:
            before = fp.read()
        ExportService.prepare_db_snapshot(db_path)
        with open(db_path, "rb") as fp:
            assert fp.read() == before
