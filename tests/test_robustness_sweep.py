"""Robustness sweep: hostile conditions must degrade, never crash.

Phase 5 of the QA overhaul. Corrupt databases, missing platform
permissions, registry wiring, failing bus subscribers, concurrent
storage access — each scenario asserts the app keeps working or fails
loudly but gracefully.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from sweep_helpers import mock_page

from core.storage import Storage
from utils.bus import TickBus
from utils.models import OSType, ScreenFormFactor, Tick


class TestStartupUpdateCheck:
    def _app(self, tmp_path, prereleases: bool):
        from core.config_manager import ConfigManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        config.check_prereleases = prereleases
        config.save()
        page = mock_page()
        page.window.width = 400
        page.window.height = 800
        with (
            patch("app.detect_os", return_value=OSType.WINDOWS),
            patch("app.ConfigManager", return_value=config),
        ):
            from app import App

            return App(page)

    def test_startup_check_passes_stable_channel(self, tmp_path):
        checker = MagicMock()
        checker.check_for_update.return_value = None
        app = self._app(tmp_path, prereleases=False)
        with patch("app.UpdateChecker", return_value=checker):
            asyncio.run(app._startup_update_check())
        checker.check_for_update.assert_called_once_with(include_prereleases=False)

    def test_startup_check_passes_prerelease_flag(self, tmp_path):
        checker = MagicMock()
        checker.check_for_update.return_value = None
        app = self._app(tmp_path, prereleases=True)
        with patch("app.UpdateChecker", return_value=checker):
            asyncio.run(app._startup_update_check())
        checker.check_for_update.assert_called_once_with(include_prereleases=True)


class TestCorruptDatabaseRecovery:
    def test_garbage_db_is_quarantined_and_rebuilt(self, tmp_path):
        db = tmp_path / "data.db"
        db.write_bytes(b"\x00\x01garbage, not a sqlite file at all" * 100)

        storage = Storage(db_path=str(db))
        try:
            assert storage.check_integrity()["ok"]
            storage.write_event(
                event_type="idle_transition",
                timestamp=1000.0,
                payload={"status": "active"},
                source="afk",
            )
            assert len(storage.get_raw_events()) == 1
        finally:
            storage._conn.close()

        quarantined = list(tmp_path.glob("data.db.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes().startswith(b"\x00\x01")

    def test_garbage_db_with_wal_journals_is_rebuilt(self, tmp_path):
        db = tmp_path / "data.db"
        db.write_bytes(b"garbage")
        for suffix in ("-wal", "-shm"):
            (tmp_path / f"data.db{suffix}").write_bytes(b"journal garbage")

        storage = Storage(db_path=str(db))
        try:
            assert storage.check_integrity()["ok"]
        finally:
            storage._conn.close()

        assert len(list(tmp_path.glob("data.db.corrupt-*"))) == 1
        assert list(tmp_path.glob("data.db-wal")) == []
        assert list(tmp_path.glob("data.db-shm")) == []

    def test_empty_db_file_is_accepted(self, tmp_path):
        db = tmp_path / "data.db"
        db.write_bytes(b"")

        storage = Storage(db_path=str(db))
        try:
            assert storage.check_integrity()["ok"]
        finally:
            storage._conn.close()

        assert list(tmp_path.glob("data.db.corrupt-*")) == []

    def test_corrupt_db_is_usable_by_next_manager(self, tmp_path):
        db = tmp_path / "data.db"
        db.write_bytes(b"garbage")
        Storage(db_path=str(db))._conn.close()

        storage2 = Storage(db_path=str(db))
        try:
            assert storage2.check_integrity()["ok"]
        finally:
            storage2._conn.close()


class TestStorageConcurrency:
    def test_two_instances_on_same_file(self, tmp_path):
        db = str(tmp_path / "data.db")
        s1 = Storage(db_path=db)
        s2 = Storage(db_path=db)
        try:
            s1.write_event(
                event_type="idle_transition", timestamp=1.0, payload={}, source="afk"
            )
            s2.write_event(
                event_type="idle_transition", timestamp=2.0, payload={}, source="afk"
            )
            assert len(s1.get_raw_events()) == 2
            assert len(s2.get_raw_events()) == 2
        finally:
            s1._conn.close()
            s2._conn.close()


class TestBusSubscriberResilience:
    async def test_failing_subscriber_does_not_break_send(self):
        bus = TickBus()
        received = []
        bus.subscribe(lambda t: received.append(t.watcher))

        def boom(tick):
            raise RuntimeError("subscriber bug")

        bus.subscribe(boom)
        await bus.send(Tick(watcher="afk"))

        assert received == ["afk"]


class TestAppAndroidBoot:
    @staticmethod
    def _android_app(permission_granted: bool):
        page = mock_page()
        page.window.width = 400
        page.window.height = 800
        with (
            patch("app.detect_os", return_value=OSType.ANDROID),
            patch(
                "core.collectors.android.usage_stats.check_usage_stats_permission",
                return_value=permission_granted,
            ),
        ):
            from app import App

            return App(page), page

    def test_missing_permission_shows_dialog(self):
        app, page = self._android_app(permission_granted=False)
        assert page.show_dialog.called
        dialog = page.show_dialog.call_args.args[0]
        assert "Usage Access" in dialog.title.value

    def test_permission_granted_skips_dialog(self):
        app, page = self._android_app(permission_granted=True)
        assert not page.show_dialog.called
        assert app.layout.screen_form_factor is ScreenFormFactor.MOBILE


class TestAppAutoStartWiring:
    def test_initiate_enables_auto_start_when_configured(self, tmp_path):
        from core.config_manager import ConfigManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        config.auto_start_enabled = True
        config.save()

        page = mock_page()
        page.window.width = 400
        page.window.height = 800
        with (
            patch("app.detect_os", return_value=OSType.WINDOWS),
            patch("app.enable_auto_start") as enable,
            patch("app.is_auto_start_enabled", return_value=False),
            patch("app.ConfigManager", return_value=config),
        ):
            from app import App

            App(page)

        enable.assert_called_once()

    def test_initiate_skips_when_already_enabled(self, tmp_path):
        from core.config_manager import ConfigManager

        config = ConfigManager(path=str(tmp_path / "config.json"))
        config.auto_start_enabled = True
        config.save()

        page = mock_page()
        page.window.width = 400
        page.window.height = 800
        with (
            patch("app.detect_os", return_value=OSType.WINDOWS),
            patch("app.enable_auto_start") as enable,
            patch("app.is_auto_start_enabled", return_value=True),
            patch("app.ConfigManager", return_value=config),
        ):
            from app import App

            App(page)

        enable.assert_not_called()


class TestSqliteIntegrityGuard:
    def test_check_integrity_reports_failure_not_crash(self, tmp_path):
        db = tmp_path / "data.db"
        db.write_bytes(b"garbage")
        storage = Storage(db_path=str(db))
        try:
            result = storage.check_integrity()
            assert isinstance(result, dict)
            assert result["ok"] in (True, False)
            assert "message" in result
        finally:
            storage._conn.close()
