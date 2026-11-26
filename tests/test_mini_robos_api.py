import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


ROOT = "/api/mini-robos"
AGENT = ROOT + "/agents/managed"


class MiniRobosApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.addClassCleanup(temp.cleanup)
        database_path = Path(temp.name) / "mini_robos.sqlite3"
        env = patch.dict(os.environ, {"DATABASE_URL": "", "ADOPTIMA_DB_PATH": str(database_path), "ENABLE_API_DOCS": "false"})
        cls.enterClassContext(env)

        from backend.app import app
        from backend.db import database
        from backend.db.mini_robos_models import RoboAgent, RoboAgentVersion, RoboAgentKey, RoboAudit, RoboRun
        from backend.db.models import Account, AccountType, User, UserAccountAssignment
        from backend.routes.auth import create_access_token

        if Path(database.DB_PATH) != database_path or database.get_active_db() != "sqlite":
            raise RuntimeError("Run the Mini Robos API module in a separate test process")
        cls.addClassCleanup(database.engine.dispose)
        database.Base.metadata.create_all(database.engine)
        cls.sessions = database.SessionLocal
        cls.Agent, cls.Version, cls.Key, cls.Audit, cls.Run = RoboAgent, RoboAgentVersion, RoboAgentKey, RoboAudit, RoboRun
        cls.client = TestClient(app, follow_redirects=False)
        cls.addClassCleanup(cls.client.close)
        cls.create_token = staticmethod(create_access_token)

        with cls.sessions() as db:
            account = Account(name="Managed client", account_type=AccountType.META, external_id="local-fixture")
            db.add(account)
            db.flush()
            cls.account_id = account.id
            roles = (("admin", "admin", False, False, False), ("view", "user", True, False, True),
                     ("edit", "newuser", True, True, True), ("outside", "user", True, True, False),
                     ("edit_only", "user", False, True, True))
            for name, role, access, edit, linked in roles:
                row = User(email=f"{name}@example.test", hashed_password="unused", role=role, onboarding_completed=True,
                           access_mini_robos=access, edit_mini_robos=edit)
                if linked:
                    row.assigned_accounts.append(UserAccountAssignment(account_id=account.id))
                db.add(row)
            db.commit()

    def setUp(self):
        with self.sessions() as db:
            for table in (self.Run, self.Key, self.Audit, self.Version, self.Agent):
                db.query(table).delete()
            for slug, client in (("managed", self.account_id), ("internal", None)):
                agent = self.Agent(slug=slug, name=slug, account_id=client, type="other", runs_on="apps_script")
                db.add(agent)
                db.flush()
                db.add(self.Version(agent_id=agent.id, version=1, status="published", author="system", rules=["Observe the slot."]))
            db.commit()

    def auth(self, user="admin"):
        return {"Authorization": "Bearer " + self.create_token({"sub": f"{user}@example.test"})}

    def key(self, path=AGENT):
        response = self.client.post(path + "/keys", headers=self.auth("edit" if path == AGENT else "admin"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        row = response.json()
        return row["id"], {"X-Robo-Key": row["key"]}

    def test_access_flags_account_scope_and_write_permissions(self):
        self.assertEqual(self.client.get(ROOT + "/agents").status_code, 401)
        self.assertEqual(self.client.get(ROOT + "/agents", headers=self.auth("edit_only")).status_code, 403)
        self.assertEqual(self.client.get(AGENT, headers=self.auth("outside")).status_code, 404)
        self.assertEqual(self.client.get(ROOT + "/agents/internal", headers=self.auth("view")).status_code, 404)
        self.assertEqual(self.client.get(AGENT, headers=self.auth("view")).status_code, 200)
        self.assertEqual(self.client.put(AGENT + "/draft", json={}, headers=self.auth("view")).status_code, 403)
        self.assertEqual(self.client.put(AGENT + "/draft", json={}, headers=self.auth("edit")).status_code, 200)
        rows = self.client.get(ROOT + "/agents", headers=self.auth()).json()["agents"]
        self.assertEqual({row["slug"] for row in rows}, {"managed", "internal"})
        rows = self.client.get(ROOT + "/agents", headers=self.auth("view")).json()["agents"]
        self.assertEqual([row["slug"] for row in rows], ["managed"])

    def test_heartbeat_key_revocation_size_and_idempotent_finish(self):
        endpoint = ROOT + "/heartbeat"
        finish = {"event": "finish", "status": "succeeded", "output_count": 1}
        self.assertEqual(self.client.post(endpoint, json=finish, headers={"X-Robo-Key": "a" * 43}).status_code, 401)
        key_id, headers = self.key()
        self.assertEqual(self.client.post(endpoint, content=b"x" * 8193, headers=headers).status_code, 413)
        self.assertEqual(self.client.post(endpoint, content=iter([b"x" * 4096, b"x" * 4097]), headers=headers).status_code, 413)
        start = self.client.post(endpoint, json={"event": "start"}, headers=headers)
        self.assertEqual(start.status_code, 200)
        run_id = start.json()["run_id"]
        for _ in range(2):
            response = self.client.post(endpoint, json=finish | {"run_id": run_id, "message": headers["X-Robo-Key"] + "x" * 2100},
                                        headers=headers)
            self.assertEqual(response.status_code, 200)
        with self.sessions() as db:
            [run] = db.query(self.Run).all()
            self.assertEqual(run.output_count, 1)
            self.assertEqual(len(run.message), 2000)
            self.assertTrue(run.message.startswith("[redacted]"))
            self.assertFalse(db.get(self.Key, key_id).key_hash == headers["X-Robo-Key"])
        self.assertNotIn("key", self.client.get(AGENT + "/keys", headers=self.auth("edit")).json()["keys"][0])
        self.assertEqual(self.client.delete(AGENT + f"/keys/{key_id}", headers=self.auth("edit")).status_code, 200)
        self.assertEqual(self.client.post(endpoint, json=finish, headers=headers).status_code, 401)

    def test_config_requires_a_matching_reporter_key_and_live_sync(self):
        _, headers = self.key()
        self.assertEqual(self.client.get(AGENT + "/config", headers=self.auth()).status_code, 401)
        self.assertEqual(self.client.get(AGENT + "/config", headers=headers).status_code, 404)
        self.assertEqual(self.client.get(ROOT + "/agents/internal/config", headers=headers).status_code, 401)
        update = self.client.put(AGENT + "/profile", headers=self.auth("edit"), json={"name": "Managed", "live_sync": True})
        self.assertEqual(update.status_code, 200)
        draft = self.client.put(AGENT + "/draft", headers=self.auth("edit"), json={"rules": ["New draft"]})
        self.assertEqual(draft.status_code, 200)
        response = self.client.get(AGENT + "/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rules"], ["Observe the slot."])
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("backend.routes.mini_robos.REPORTS_PER_HOUR", 1):
            self.assertEqual(self.client.get(AGENT + "/config", headers=headers).status_code, 429)

    def test_recipient_change_confirmation_and_rollback_create_new_version(self):
        headers = self.auth("edit")
        draft = {"prompt": "Updated prompt", "recipients": ["Team A"]}
        self.assertEqual(self.client.put(AGENT + "/draft", headers=headers, json=draft).status_code, 200)
        self.assertEqual(self.client.put(AGENT + "/draft", headers=headers, json=draft).status_code, 200)
        response = self.client.post(AGENT + "/publish", headers=headers, json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["to"], ["Team A"])
        stale = {"confirm_recipients": True, "recipient_diff": {"from": [], "to": ["Team B"]}}
        self.assertEqual(self.client.post(AGENT + "/publish", headers=headers, json=stale).status_code, 409)
        response = self.client.post(AGENT + "/publish", headers=headers, json={"confirm_recipients": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 2)
        route = AGENT + "/versions/1/rollback"
        self.assertEqual(self.client.post(route, headers=headers, json={}).status_code, 409)
        response = self.client.post(route, headers=headers, json={"confirm_recipients": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 3)
        versions = self.client.get(AGENT + "/versions", headers=headers).json()["versions"]
        self.assertEqual([row["version"] for row in versions], [3, 2, 1])
        self.assertEqual(versions[0]["rules"], versions[2]["rules"])
        self.assertEqual(versions[1]["recipients"], ["Team A"])

    def test_pause_resume_and_run_now_only_dispatch_registered_job(self):
        headers = self.auth("edit")
        self.assertEqual(self.client.post(AGENT + "/pause", headers=headers, json={"reason": " "}).status_code, 422)
        self.assertEqual(self.client.post(AGENT + "/run-now", headers=headers).status_code, 400)
        self.assertEqual(self.client.post(AGENT + "/pause", headers=headers, json={"reason": "Maintenance"}).status_code, 200)
        self.assertEqual(self.client.get(AGENT, headers=headers).json()["status"], "paused")
        self.assertEqual(self.client.post(AGENT + "/resume", headers=headers, json={"reason": "Done"}).status_code, 200)
        with self.sessions() as db:
            agent = db.query(self.Agent).filter_by(slug="managed").one()
            agent.runs_on = "ops_app_job"
            agent.job_ref = "synthetic"
            db.commit()
        runner = Mock(running=True)
        with patch("backend.services.mini_robos.get_job", return_value=(runner, SimpleNamespace(id="synthetic"))):
            self.assertEqual(self.client.post(AGENT + "/run-now", headers=headers).status_code, 200)
        runner.modify_job.assert_called_once()
        self.assertEqual(runner.modify_job.call_args.args, ("synthetic",))
        audit = self.client.get(AGENT + "/audit", headers=self.auth("view")).json()["audit"]
        self.assertEqual([row["action"] for row in audit], ["run_now", "resume", "pause"])
