import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class SakhaAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.addClassCleanup(temp.cleanup)
        db_path = Path(temp.name) / "sakha.sqlite3"
        environment = patch.dict(os.environ, {
            "DATABASE_URL": "", "ADOPTIMA_DB_PATH": str(db_path), "ENABLE_API_DOCS": "false",
            "SAKHA_DATA_MODE": "sample", "SAKHAA_MANTRI_WEBCITY_KEY": "", "SAKHAA_MANTRI_WEBCITY_SECRET": "",
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        from backend.db import database
        if database.get_active_db() != "sqlite" or Path(database.DB_PATH) != db_path:
            raise RuntimeError("Run this module alone with its disposable database")
        cls.addClassCleanup(database.engine.dispose)

        from backend.app import app
        from backend.db.models import Account, AccountType, User, UserAccountAssignment
        from backend.routes.auth import create_access_token
        from backend.services.sakha.sync import ensure_mantri

        database.Base.metadata.create_all(database.engine)
        cls.client = TestClient(app, follow_redirects=False)
        cls.addClassCleanup(cls.client.close)
        cls.create_token = staticmethod(create_access_token)
        with database.SessionLocal() as db:
            account = Account(name="Mantri Webcity", account_type=AccountType.META, external_id="fixture")
            db.add(account)
            db.flush()
            identities = {
                "admin": ("admin", False, False), "super": ("superadmin", False, False),
                "no_flag": ("user", False, True), "unassigned": ("user", True, False),
                "assigned": ("user", True, True), "new": ("newuser", True, True),
            }
            for name, (role, flag, assigned) in identities.items():
                user = User(email=f"{name}@example.test", hashed_password="unused",
                            role=role, access_sakha=flag, onboarding_completed=True)
                if assigned:
                    user.assigned_accounts.append(UserAccountAssignment(account_id=account.id))
                db.add(user)
            db.commit()
            ensure_mantri(db)

    def headers(self, identity):
        return {"Authorization": "Bearer " + self.create_token({"sub": f"{identity}@example.test"})}

    def test_auth_module_flag_and_assignment_on_all_read_routes(self):
        for action in ("dashboard", "issues", "sync-status"):
            path = f"/api/sakha/clients/mantri-webcity/{action}"
            self.assertEqual(self.client.get(path).status_code, 401)
            for identity in ("unassigned", "no_flag", "assigned", "new", "admin", "super"):
                with self.subTest(action=action, identity=identity):
                    expected = 403 if identity in {"unassigned", "no_flag"} else 200
                    response = self.client.get(path, headers=self.headers(identity))
                    self.assertEqual(response.status_code, expected)
                    if expected == 200:
                        self.assertEqual(response.json()["data_mode"], "sample")
        listing = self.client.get("/api/sakha/clients", headers=self.headers("unassigned"))
        self.assertEqual(listing.status_code, 200)
        self.assertFalse(listing.json()["clients"])
        self.assertEqual(self.client.get("/api/sakha/clients", headers=self.headers("no_flag")).status_code, 403)

    def test_admin_sync_config_and_dashboard_serve_sample_data(self):
        prefix = "/api/sakha/clients/mantri-webcity"
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No external calls")) as request:
            for method, action, body in (("POST", "sync", None), ("PUT", "config", {"targets": {"first_contact_minutes": 45}})):
                path = f"{prefix}/{action}"
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)
                self.assertEqual(self.client.request(method, path, json=body, headers=self.headers("assigned")).status_code, 403)
                response = self.client.request(method, path, json=body, headers=self.headers("admin"))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["last_sync_ok"])
                self.assertFalse(response.json()["configured"])
                self.assertEqual(response.json()["data_mode"], "sample")
            request.assert_not_called()
        dashboard = self.client.get(f"{prefix}/dashboard", headers=self.headers("assigned")).json()
        self.assertIsNotNone(dashboard["last_sync_at"])
        self.assertEqual(dashboard["speed"]["target_minutes"], 45)
        self.assertIn("source:lc_1", {row["code"] for row in dashboard["issues"]})
        self.assertEqual(self.client.get(f"{prefix}/dashboard?from=2026-11-26&to=2026-11-25",
                                         headers=self.headers("assigned")).status_code, 400)
        clients = self.client.get("/api/sakha/clients", headers=self.headers("assigned")).json()["clients"]
        self.assertEqual([row["slug"] for row in clients], ["mantri-webcity"])
        for page in ("/sakha", "/sakha/mantri-webcity"):
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Sample data", response.text)


if __name__ == "__main__":
    unittest.main()
