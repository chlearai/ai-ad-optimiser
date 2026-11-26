from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from queue import Queue
from threading import Event
import unittest
from unittest.mock import patch

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED, JobExecutionEvent, JobSubmissionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from backend.db import database, models
from backend.db.mini_robos_models import RoboAgent, RoboAgentVersion, RoboIncident, RoboRun
from backend.services import mini_robos


NOW = datetime(2026, 11, 26, 10, tzinfo=timezone.utc)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd()))
        self.engine = create_engine("sqlite:///" + (Path(root) / "ops.sqlite3").as_posix())
        self.addCleanup(self.engine.dispose)
        database.Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.enterContext(patch.object(mini_robos, "SessionLocal", self.sessions))
        self.enterContext(patch.object(mini_robos, "utcnow", return_value=NOW))
        self.scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        self.scheduler.add_job(lambda: None, "interval", id="tlg_lead_routing", minutes=12)
        self.scheduler.add_job(lambda: None, "cron", id="daily_mantri_mis_refresh", hour=6, minute=30)
        with self.sessions() as db:
            db.add(models.Account(name="The Little Gym (TLG)", account_type=models.AccountType.META, external_id="fixture"))
            db.commit()
            mini_robos.seed_agents(db, self.scheduler)

    def test_seed_is_idempotent_and_uses_actual_triggers(self):
        with self.sessions() as db:
            mini_robos.seed_agents(db, self.scheduler)
            self.assertEqual(db.query(RoboAgent).count(), 12)
            self.assertEqual(db.query(RoboAgentVersion).count(), 12)
            self.assertEqual(db.query(RoboAgent).filter_by(slug="tlg-lead-routing").one().schedule,
                             {"kind": "interval", "minutes": 12})
            self.assertEqual(db.query(RoboAgent).filter_by(slug="mantri-mis-refresh").one().schedule["times"], ["06:30"])
            branches = db.query(RoboAgent).filter_by(runs_on="apps_script").all()
            self.assertEqual(len(branches), 4)
            self.assertTrue(all(row.schedule is None and row.account_id is not None for row in branches))

    def test_listener_never_calls_a_slow_or_failing_database(self):
        blocked = Event()

        def slow_database():
            blocked.wait(2)
            raise RuntimeError

        with patch.object(mini_robos, "_events", Queue(1)) as events:
            for failure in (RuntimeError, slow_database):
                with self.subTest(failure=failure), patch.object(mini_robos, "SessionLocal", side_effect=failure) as session:
                    mini_robos.listen(JobSubmissionEvent(EVENT_JOB_SUBMITTED, "tlg_lead_routing", "default", [NOW]))
                    session.assert_not_called()
                    session.begin.assert_not_called()
                    self.assertEqual(events.get_nowait(), ("tlg_lead_routing", EVENT_JOB_SUBMITTED, (NOW,), None, NOW))

    def test_full_queue_drops_events_without_logging_on_the_scheduler_thread(self):
        with (
            patch.object(mini_robos, "_events", Queue(1)) as events,
            patch.object(mini_robos, "_queue_full", Event()) as full,
            patch.object(mini_robos.logger, "warning") as warning,
        ):
            submission = JobSubmissionEvent(EVENT_JOB_SUBMITTED, "tlg_lead_routing", "default", [NOW])
            mini_robos.listen(submission)
            mini_robos.listen(submission)
            mini_robos.listen(submission)
            self.assertEqual(events.qsize(), 1)
            self.assertTrue(full.is_set())
            warning.assert_not_called()

    def test_worker_records_events_and_continues_after_a_database_error(self):
        job = "tlg_lead_routing"
        entered = Event()
        release = Event()
        recorded = Event()
        completed = NOW - timedelta(minutes=24)
        record_event = mini_robos.record_event
        begin = self.sessions.begin

        def record(payload):
            record_event(payload)
            if payload[1:3] == (EVENT_JOB_MISSED, (completed,)):
                recorded.set()

        def slow_then_recover():
            if not entered.is_set():
                entered.set()
                release.wait(5)
                raise RuntimeError
            return begin()

        with (
            patch.object(mini_robos, "_events", Queue(10)) as events,
            patch.object(mini_robos, "_queue_full", Event()) as full,
            patch.object(mini_robos, "_worker", None),
            patch.object(self.sessions, "begin", side_effect=slow_then_recover),
            patch.object(mini_robos, "record_event", side_effect=record),
        ):
            mini_robos.listen(JobSubmissionEvent(EVENT_JOB_SUBMITTED, job, "default", [NOW]))
            full.set()
            with self.assertLogs("AdOptima", "WARNING") as logs:
                mini_robos.start_event_worker()
                worker = mini_robos._worker
                try:
                    self.assertTrue(entered.wait(5))
                    mini_robos.start_event_worker()
                    self.assertIs(worker, mini_robos._worker)
                    self.assertTrue(worker.daemon)
                    mini_robos.listen(JobExecutionEvent(EVENT_JOB_EXECUTED, job, "default", NOW, retval=2))
                    mini_robos.listen(JobSubmissionEvent(EVENT_JOB_SUBMITTED, job, "default", [NOW]))
                    mini_robos.listen(JobSubmissionEvent(EVENT_JOB_SUBMITTED, "schedule_refresher", "default", [NOW]))
                    mini_robos.listen(JobExecutionEvent(EVENT_JOB_ERROR, job, "default", NOW - timedelta(minutes=12),
                                                       exception=ValueError()))
                    mini_robos.listen(JobExecutionEvent(EVENT_JOB_MISSED, job, "default", completed))
                    self.assertEqual(events.qsize(), 5)
                finally:
                    release.set()
                self.assertTrue(recorded.wait(5))
            self.assertEqual(sum("event queue is full" in line for line in logs.output), 1)
            self.assertEqual(sum("could not record" in line for line in logs.output), 1)

        with self.sessions() as db:
            rows = db.query(RoboRun).order_by(RoboRun.started_at.desc()).all()
            self.assertEqual([row.status for row in rows], ["succeeded", "failed", "failed"])
            self.assertEqual(rows[0].output_count, 2)
            self.assertIsNone(rows[0].slot)
            self.assertEqual(rows[2].error, mini_robos.MISSED_ERROR)

    def test_watchdog_prunes_old_runs_and_resolves_incidents(self):
        with self.sessions() as db:
            agent = db.query(RoboAgent).filter_by(slug="tlg-lead-routing").one()
            db.add_all([
                RoboRun(id="old", agent_id=agent.id, status="succeeded", started_at=NOW - timedelta(days=91)),
                RoboRun(id="recent", agent_id=agent.id, status="succeeded", started_at=NOW - timedelta(minutes=40)),
            ])
            db.commit()
        mini_robos.watchdog()
        with self.sessions() as db:
            self.assertIsNone(db.get(RoboRun, "old"))
            incident = db.query(RoboIncident).filter_by(kind="stale").one()
            self.assertIsNone(incident.closed_at)
            db.add(RoboRun(id="recovered", agent_id=incident.agent_id, status="succeeded", started_at=NOW, finished_at=NOW))
            db.commit()
        mini_robos.watchdog()
        with self.sessions() as db:
            self.assertIsNotNone(db.query(RoboIncident).filter_by(kind="stale").one().closed_at)

    def test_reporting_migration_failure_preserves_user_queries(self):
        def begin(connection):
            connection.exec_driver_sql("BEGIN")

        event.listen(self.engine, "begin", begin)
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE users DROP COLUMN access_mini_robos"))
            connection.execute(text("ALTER TABLE users DROP COLUMN edit_mini_robos"))
            connection.execute(text("INSERT INTO users (email, hashed_password) VALUES ('login@example.test', 'unused')"))
        metadata = database.Base.metadata
        create_all = metadata.create_all

        def create_or_fail(bind=None, tables=None, **kwargs):
            if tables and all(table.name.startswith("robo_") for table in tables):
                raise RuntimeError("Reporting schema unavailable")
            return create_all(bind=bind, tables=tables, **kwargs)

        with (
            patch.object(database, "engine", self.engine),
            patch.object(metadata, "create_all", side_effect=create_or_fail),
            patch("backend.migrations.seed_default_admin.run_migration"),
            patch("backend.migrations.seed_initial_accounts.run_migration"),
            patch("backend.migrations.fix_serial_sequences.run_migration") as later,
            self.assertLogs("AdOptima", "WARNING") as logs,
        ):
            database.init_db()
        later.assert_called_once_with()
        self.assertTrue(any("Additive Mini Robos migration skipped/failed" in row for row in logs.output))
        with self.sessions() as db:
            user = db.query(models.User).filter_by(email="login@example.test").one()
            self.assertFalse(user.access_mini_robos)
            self.assertFalse(user.edit_mini_robos)
