import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from backend.db import database, models
from backend.db.sakha_models import SakhaClient, SakhaIssue, SakhaPerson
from backend.migrations.add_sakha_schema import run_migration
from backend.services.sakha import sync
from backend.services.sakha.client import ReportingConfig, SakhaaClient, SyncDeferred, as_utc


NOW = as_utc("2026-11-25T10:00:00Z")
OFFLINE = {"SAKHA_DATA_MODE": "sample", "SAKHAA_MANTRI_WEBCITY_KEY": "", "SAKHAA_MANTRI_WEBCITY_SECRET": ""}


class SakhaSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temp.cleanup)
        engine = create_engine("sqlite:///" + (Path(temp.name) / "sakha.sqlite3").as_posix())
        self.addCleanup(engine.dispose)
        self.engine = engine
        models.Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        self.sessions = sessions
        self.enterContext(patch.object(sync, "SessionLocal", sessions))
        self.enterContext(patch.dict(os.environ, OFFLINE))
        os.environ.pop("SAKHA_HOURLY_CALL_BUDGET", None)
        with sessions() as db:
            db.add(models.Account(name="Mantri Webcity", account_type=models.AccountType.META,
                                  external_id="synthetic-account"))
            db.commit()
            self.client_id = sync.ensure_mantri(db)

    def test_sample_sync_has_no_http_and_issues_auto_resolve(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP in sample mode")) as request:
            sync.sync_all()
            self.assertEqual(sync.sync_client(self.client_id, NOW), "ok")
            with self.sessions() as db:
                issues = {row.code: row for row in db.query(SakhaIssue)}
                self.assertIsNone(issues["source:lc_1"].resolved_at)
                self.assertIsNone(issues["integration:Samvaad AI Voice"].resolved_at)
                self.assertEqual(db.query(SakhaPerson).count(), 50)
            original = sync.make_client
            transport = original(sync.config_for(sync.MANTRI_SLUG), None, now=NOW + timedelta(minutes=15))
            original_page = transport.page

            def resolved_page(endpoint, **kwargs):
                result = original_page(endpoint, **kwargs)
                if endpoint == "health":
                    result["connectors"][0].update(status="active", rejecting_new=False)
                    result["integrations"][0].update(status="active", last_sync_at="2026-11-25T10:00:00Z")
                return result

            transport.page = resolved_page
            with patch.object(sync, "make_client", return_value=transport):
                self.assertEqual(sync.sync_client(self.client_id, NOW + timedelta(minutes=15)), "ok")
            request.assert_not_called()
        with self.sessions() as db:
            for code in ("source:lc_1", "integration:Samvaad AI Voice"):
                issue = db.query(SakhaIssue).filter_by(code=code).one()
                self.assertIsNotNone(issue.resolved_at)
            self.assertEqual(db.query(SakhaPerson).count(), 50)

    def test_live_without_credentials_falls_back_to_sample(self):
        with patch.dict(os.environ, {"SAKHA_DATA_MODE": "live"}), self.assertLogs("AdOptima", "WARNING") as logs, \
                patch("requests.Session") as session:
            self.assertEqual(sync.sync_client(self.client_id, NOW), "ok")
        session.assert_not_called()
        self.assertIn("Using sample data.", " ".join(logs.output))
        with self.sessions() as db:
            self.assertEqual(db.get(SakhaClient, self.client_id).last_sync_mode, "sample")

    def test_live_incremental_resume_tombstones_and_mode_isolation(self):
        self.assertEqual(sync.sync_client(self.client_id, NOW), "ok")
        now_text = "2026-11-25T10:00:00Z"
        person = {"id": "live_person", "display_name": "Synthetic Person", "created_at": now_text,
                  "updated_at": now_text, "deleted_at": None, "phone": "discard-this-field"}
        transport = Mock()
        calls = []

        def page(endpoint, *, since, cursor):
            calls.append((endpoint, since, cursor))
            if endpoint == "health":
                return {"connectors": [], "integrations": []}
            if endpoint == "persons":
                if since:
                    return {"data": [{"id": person["id"], "deleted_at": now_text}], "server_time": now_text}
                if cursor is None:
                    return {"data": [person], "server_time": now_text, "next_cursor": "continue"}
                if calls.count(("persons", None, "continue")) == 1:
                    raise SyncDeferred("test budget")
            return {"data": [], "server_time": now_text}

        transport.page.side_effect = page
        config = ReportingConfig("live", key="test-input", secret="test-input")
        with patch.object(sync, "config_for", return_value=config), patch.object(sync, "make_client", return_value=transport):
            self.assertEqual(sync.sync_client(self.client_id, NOW), "deferred")
            with self.sessions() as db:
                self.assertFalse(sync.read_snapshot(db, self.client_id, "live")["persons"])
            self.assertEqual(sync.sync_client(self.client_id, NOW), "ok")
            with self.sessions() as db:
                live = sync.read_snapshot(db, self.client_id, "live")
                self.assertEqual(live["persons"][0], {k: v for k, v in person.items() if k != "phone"})
            self.assertEqual(sync.sync_client(self.client_id, NOW), "ok")
        self.assertEqual(calls.count(("persons", None, None)), 1)
        self.assertIn(("deal-stage-events", "2026-11-25T09:59:59Z", None), calls)
        with self.sessions() as db:
            self.assertFalse(sync.read_snapshot(db, self.client_id, "live")["persons"])
            self.assertEqual(len(sync.read_snapshot(db, self.client_id, "sample")["persons"]), 50)

    def test_rolling_budget_and_server_cooldown(self):
        with patch.object(sync, "utcnow", return_value=NOW) as clock:
            for _ in range(90):
                sync.update_budget(self.client_id)
            with self.assertRaisesRegex(SyncDeferred, "Hourly"):
                sync.update_budget(self.client_id)
            clock.return_value += timedelta(hours=1, seconds=1)
            sync.update_budget(self.client_id)
            sync.update_budget(self.client_id, delay=60)
            clock.return_value += timedelta(seconds=59)
            with self.assertRaisesRegex(SyncDeferred, "cooldown"):
                sync.update_budget(self.client_id)
            clock.return_value += timedelta(seconds=1)
            sync.update_budget(self.client_id)

    def test_configured_hourly_budget_overrides_default(self):
        with patch.object(sync, "utcnow", return_value=NOW):
            with patch.dict(os.environ, {"SAKHA_HOURLY_CALL_BUDGET": "91"}):
                for _ in range(91):
                    sync.update_budget(self.client_id)
                with self.assertRaisesRegex(SyncDeferred, "Hourly"):
                    sync.update_budget(self.client_id)
            for invalid in ("", "0", "-1", "invalid"):
                with self.subTest(value=invalid), patch.dict(os.environ, {"SAKHA_HOURLY_CALL_BUDGET": invalid}):
                    with self.assertRaisesRegex(SyncDeferred, "Hourly"):
                        sync.update_budget(self.client_id)

    def test_mirror_migration_failure_preserves_access_flag_and_startup(self):
        # Explicit BEGIN makes SQLite DDL transactional, as it is on Postgres.
        def begin(connection):
            connection.exec_driver_sql("BEGIN")

        event.listen(self.engine, "begin", begin)
        self.addCleanup(event.remove, self.engine, "begin", begin)
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO users (email, hashed_password) VALUES ('legacy@example.test', 'unused')"))
            connection.execute(text("ALTER TABLE users DROP COLUMN access_sakha"))

        metadata = models.Base.metadata
        create_all = metadata.create_all
        mirror_attempts = []

        def fail_mirror(bind=None, tables=None, **kwargs):
            if tables and any(table.name.startswith("sakha_") for table in tables):
                mirror_attempts.append(True)
                raise RuntimeError("simulated Sakha mirror migration failure")
            return create_all(bind=bind, tables=tables, **kwargs)

        with (
            patch.object(database, "engine", self.engine),
            patch.object(metadata, "create_all", side_effect=fail_mirror),
            patch("backend.migrations.seed_default_admin.run_migration"),
            patch("backend.migrations.seed_initial_accounts.run_migration"),
            patch("backend.migrations.fix_serial_sequences.run_migration") as later_migration,
            self.assertLogs("AdOptima", "WARNING") as logs,
        ):
            database.init_db()

        self.assertEqual(mirror_attempts, [True])
        later_migration.assert_called_once_with()
        self.assertTrue(any("Additive Sakha migration skipped/failed" in message for message in logs.output))
        with self.sessions() as db:
            legacy_user = db.query(models.User).filter_by(email="legacy@example.test").one()
            self.assertFalse(legacy_user.access_sakha)

    def test_sqlite_migration_backfills_existing_users_and_is_idempotent(self):
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO users (email, hashed_password) VALUES ('migration@example.test', 'unused')"))
            connection.execute(text("ALTER TABLE users DROP COLUMN access_sakha"))
        run_migration(self.engine)
        run_migration(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT access_sakha FROM users")).scalar_one(), 0)
            self.assertIn("sakha_issues", inspect(connection).get_table_names())


class SakhaTransportTests(unittest.TestCase):
    def test_live_http_parameters_read_only_and_retry_after(self):
        session, reserve, defer = Mock(), Mock(), Mock()
        session.get.side_effect = [
            Mock(status_code=429, headers={"Retry-After": "3"}),
            Mock(status_code=200, **{"json.return_value": {"data": [], "next_cursor": None}}),
        ]
        client = SakhaaClient(ReportingConfig("live", "https://example.test/report", "X-Key", "X-Secret",
                                              "test-input", "test-input"), reserve, session, defer)
        with patch("backend.services.sakha.client.time.sleep") as wait:
            client.page("deal-stage-events", since="watermark", cursor="page")
        wait.assert_called_once_with(3)
        defer.assert_called_once_with(3)
        self.assertEqual(reserve.call_count, 2)
        args, kwargs = session.get.call_args
        self.assertEqual(args, ("https://example.test/report/deal-stage-events",))
        self.assertEqual(kwargs["params"], {"since": "watermark", "limit": 200, "cursor": "page"})
        self.assertFalse(kwargs["allow_redirects"])

    def test_reporting_retry_after_can_defer_until_next_run(self):
        session = Mock()
        session.get.return_value = Mock(status_code=429, headers={"Retry-After": "120"})
        client = SakhaaClient(ReportingConfig("live", "https://example.test/report", "X-Key", "X-Secret",
                                              "test-input", "test-input"), Mock(), session)
        with self.assertRaises(SyncDeferred) as error:
            client.page("persons", since="watermark")
        self.assertEqual(error.exception.retry_after, 120)
        self.assertEqual(session.get.call_args.kwargs["params"]["updated_since"], "watermark")


if __name__ == "__main__":
    unittest.main()
