from datetime import datetime, timedelta, timezone
import os
import re
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
from backend.db.mini_robos_models import RoboAgent, RoboAgentKey, RoboAgentVersion, RoboAudit, RoboIncident, RoboRun
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
            db.add_all([
                models.Account(name="The Little Gym (TLG)", account_type=models.AccountType.META, external_id="fixture"),
                models.Account(name="Bhago Mobility", account_type=models.AccountType.META, external_id="bhago-fixture"),
            ])
            db.commit()
            mini_robos.seed_agents(db, self.scheduler)

    def test_seed_is_idempotent_and_uses_actual_triggers(self):
        with self.sessions() as db:
            mini_robos.seed_agents(db, self.scheduler)
            self.assertEqual(db.query(RoboAgent).count(), 16)
            self.assertEqual(db.query(RoboAgentVersion).count(), 16)
            self.assertEqual(db.query(RoboAgent).filter_by(slug="tlg-lead-routing").one().schedule,
                             {"kind": "interval", "minutes": 12})
            self.assertEqual(db.query(RoboAgent).filter_by(slug="mantri-mis-refresh").one().schedule["times"], ["06:30"])
            branches = db.query(RoboAgent).filter_by(runs_on="apps_script").all()
            self.assertEqual(len(branches), 4)
            self.assertTrue(all(row.schedule is None and row.account_id is not None for row in branches))

    def test_eduinfra_seeds_use_only_a_unique_existing_account(self):
        names = {
            "eduinfra-specifiers": "EduInfra Nisha \u2014 Specifiers",
            "eduinfra-demand-creators": "EduInfra Nisha \u2014 Demand Creators",
            "eduinfra-commercial": "EduInfra Nisha \u2014 Commercial Participants",
            "eduinfra-inbound": "EduInfra inbound agent",
        }
        with self.sessions() as db:
            agents = db.query(RoboAgent).filter(RoboAgent.slug.in_(names))
            self.assertEqual({agent.slug: agent.name for agent in agents}, names)
            self.assertTrue(all(agent.account_id is None for agent in agents))
            self.assertEqual(db.query(models.Account).count(), 2)
            account = models.Account(name="CHLEAR EDU Infra Expand", account_type=models.AccountType.META, external_id="edu-fixture")
            db.add(account)
            db.commit()
            mini_robos.seed_agents(db, self.scheduler)
            mini_robos.seed_agents(db, self.scheduler)
            self.assertEqual(agents.count(), 4)
            for agent in agents:
                self.assertEqual(agent.account_id, account.id)
                self.assertEqual((agent.type, agent.runs_on, agent.status), ("calling", "samvaad", "no_signal"))
                self.assertEqual(agent.source_url, "https://samvaad.sakhaa.ai/dashboard")
                self.assertIsNone(agent.job_ref)
                self.assertIsNone(agent.schedule)
                version = db.query(RoboAgentVersion).filter_by(agent_id=agent.id).one()
                self.assertEqual((version.version, version.status, version.author, version.prompt), (1, "published", "system", ""))
                if agent.slug == "eduinfra-inbound":
                    self.assertEqual(version.rules[-1], "Handles incoming calls; script not yet recorded")
                    self.assertEqual(len(version.rules), 2)
                    self.assertFalse(re.search(r"\d{4,}", " ".join(version.rules)))
                else:
                    self.assertEqual(len(version.rules), 6)
                    self.assertEqual(re.findall(r"\d{4,}", " ".join(version.rules)), ["0800"])

            db.add(models.Account(name="Second eduinfra account", account_type=models.AccountType.META, external_id="ambiguous"))
            db.commit()
            mini_robos.seed_agents(db, self.scheduler)
            self.assertTrue(all(agent.account_id is None for agent in agents))
            self.assertEqual(db.query(models.Account).count(), 4)

    def test_bhago_calling_seeds_are_idempotent(self):
        expected = {
            "bhago-calling": ("Bhago payment-reminder calls", "Run payment-reminder calls for the Bhago calling campaign."),
            "bhago-lead-calls": ("Bhago Lead-Calls", "Run AI calls to new Bhago leads through the Samvaad Lead-Calls campaign."),
            "bhago-i-ride-calls": ("Bhago I-Ride-Calls", "Run AI calls for the Bhago I-Ride campaign through Samvaad."),
        }
        with self.sessions() as db:
            mini_robos.seed_agents(db, self.scheduler)
            account = db.query(models.Account).filter_by(name="Bhago Mobility").one()
            agents = db.query(RoboAgent).filter_by(account_id=account.id).all()
            self.assertCountEqual([agent.slug for agent in agents], expected.keys())
            for agent in agents:
                name, rule = expected[agent.slug]
                self.assertEqual((agent.name, agent.type, agent.runs_on), (name, "calling", "samvaad"))
                self.assertIsNone(agent.schedule)
                self.assertIsNone(agent.job_ref)
                version = db.query(RoboAgentVersion).filter_by(agent_id=agent.id).one()
                self.assertEqual((version.version, version.status, version.rules), (1, "published", [rule]))

    def test_seed_removes_adguard_agents_and_history_but_leaves_jobs_running(self):
        removed = {
            "adguard-meta-poll": "adguard_meta_leads_poll",
            "adguard-conversion-dispatcher": "adguard_conversion_dispatcher",
        }
        jobs = {ref: self.scheduler.add_job(lambda: None, "interval", id=ref, minutes=1)
                for ref in removed.values()}
        children = (RoboAgentVersion, RoboRun, RoboIncident, RoboAgentKey, RoboAudit)
        with self.sessions() as db:
            self.assertEqual(db.scalar(text("PRAGMA foreign_keys")), 0)
            for index, (slug, job_ref) in enumerate(removed.items()):
                agent = RoboAgent(slug=slug, name=slug, type="data_sync", runs_on="ops_app_job", job_ref=job_ref)
                db.add(agent)
                db.flush()
                agent_id = agent.id
                db.add_all([
                    RoboAgentVersion(agent_id=agent_id, version=1, status="published", author="system"),
                    RoboRun(id=slug, agent_id=agent_id, started_at=NOW, status="failed"),
                    RoboIncident(agent_id=agent_id, kind="failed"),
                    RoboAgentKey(agent_id=agent_id, key_hash=str(index) * 64),
                    RoboAudit(agent_id=agent_id, who="system", action="run_now"),
                ])
            db.commit()
            agent_ids = db.query(RoboAgent.id).filter(RoboAgent.slug.in_(removed)).all()
            mini_robos.seed_agents(db, self.scheduler)
            mini_robos.seed_agents(db, self.scheduler)
            self.assertEqual(db.query(RoboAgent).filter(RoboAgent.slug.in_(removed)).count(), 0)
            self.assertEqual(db.query(RoboAgentVersion).count(), 16)
            for model in children:
                self.assertEqual(db.query(model).filter(model.agent_id.in_([row.id for row in agent_ids])).count(), 0)

        for job_ref, job in jobs.items():
            self.assertIs(self.scheduler.get_job(job_ref), job)
            mini_robos.record_event((job_ref, EVENT_JOB_EXECUTED, (NOW,), 2, NOW))
        with self.sessions() as db:
            self.assertEqual(db.query(RoboRun).count(), 0)

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

    def test_watchdog_alerts_once_and_summary_requires_recipients(self):
        from backend.db.mini_robos_models import RoboAlert

        with self.sessions() as db:
            agent = db.query(RoboAgent).filter_by(slug="tlg-lead-routing").one()
            agent.alert_recipients = ["15550100000"]
            db.add(RoboRun(id="stale-alert", agent_id=agent.id, status="succeeded",
                           started_at=NOW - timedelta(minutes=40), finished_at=NOW - timedelta(minutes=39)))
            db.commit()
            agent_id = agent.id
        mini_robos.watchdog()
        mini_robos.watchdog()
        with self.sessions() as db:
            rows = db.query(RoboAlert).all()
            self.assertEqual([row.kind for row in rows], ["opened"])
            self.assertIn("TLG lead routing", rows[0].text)
            db.add(RoboRun(id="good-alert", agent_id=agent_id, status="succeeded", started_at=NOW, finished_at=NOW))
            db.commit()
        mini_robos.watchdog()
        mini_robos.watchdog()
        with patch.dict(os.environ, {"MINI_ROBOS_SUMMARY_TO": ""}):
            mini_robos.queue_daily_summary()
        with self.sessions() as db:
            self.assertEqual([row.kind for row in db.query(RoboAlert).order_by(RoboAlert.id)], ["opened", "recovered"])
        with patch.dict(os.environ, {"MINI_ROBOS_SUMMARY_TO": "15550100000"}):
            mini_robos.queue_daily_summary()
            mini_robos.queue_daily_summary()
        with self.sessions() as db:
            self.assertEqual(db.query(RoboAlert).filter_by(kind="summary").count(), 1)
            agent = db.get(RoboAgent, agent_id)
            agent.alert_recipients = []
            db.query(RoboRun).filter_by(id="good-alert").delete()
            db.commit()
        mini_robos.watchdog()
        with self.sessions() as db:
            self.assertEqual(db.query(RoboAlert).count(), 3)

    def test_alert_migration_upgrades_existing_robo_agents(self):
        from sqlalchemy import inspect
        from backend.db.mini_robos_models import RoboAlert
        from backend.migrations.add_mini_robos_schema import run_migration

        RoboAlert.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE robo_agents DROP COLUMN alert_recipients"))
        run_migration(self.engine)
        run_migration(self.engine)
        self.assertTrue(inspect(self.engine).has_table("robo_alerts"))
        with self.sessions() as db:
            self.assertIsNone(db.query(RoboAgent).filter_by(slug="tlg-lead-routing").one().alert_recipients)

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
