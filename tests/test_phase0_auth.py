import logging
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class PhaseZeroSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        http_logger = logging.getLogger("httpx")
        old_level = http_logger.level
        http_logger.setLevel(logging.WARNING)
        cls.addClassCleanup(http_logger.setLevel, old_level)
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.config_file = Path(cls.temp.name) / "config.json"
        db_path = Path(cls.temp.name) / "security.sqlite3"
        environment = patch.dict(os.environ, {
            "DATABASE_URL": "",
            "ADOPTIMA_DB_PATH": str(db_path),
            "ENABLE_API_DOCS": "false",
        })
        environment.start()
        cls.addClassCleanup(environment.stop)

        from backend.db import database
        if database.get_active_db() != "sqlite" or Path(database.DB_PATH) != db_path:
            raise RuntimeError("Run this module alone with its disposable database")
        cls.addClassCleanup(database.engine.dispose)
        from backend.app import app
        from backend.db.models import User
        from backend.routes.auth import create_access_token, get_password_hash
        from backend.services import config

        for name, path in (("CONFIG_PATH", cls.config_file), ("ENV_PATH", Path(cls.temp.name) / ".env")):
            override = patch.object(config, name, str(path))
            override.start()
            cls.addClassCleanup(override.stop)
        database.Base.metadata.create_all(database.engine)
        cls.db = database.SessionLocal
        cls.User = User
        cls.create_token = staticmethod(create_access_token)
        cls.password_hash = staticmethod(get_password_hash)
        cls.app = app
        cls.client = TestClient(app, follow_redirects=False)
        cls.addClassCleanup(cls.client.close)

    def setUp(self):
        if self.config_file.exists():
            self.config_file.unlink()
        with self.db() as db:
            db.query(self.User).delete()
            db.add_all(self.User(
                email=f"{role}@example.test", role=role, full_name=role,
                hashed_password="unused", is_active=True, onboarding_completed=True,
            ) for role in ("admin", "user", "superadmin"))
            db.commit()

    def headers(self, role):
        return {"Authorization": "Bearer " + self.create_token({"sub": f"{role}@example.test"})}

    def check_route(self, method, path, body=None, *, admin=True, status=200):
        request = self.client.request
        self.assertEqual(request(method, path, json=body).status_code, 401, path)
        self.assertEqual(
            request(method, path, headers=self.headers("user"), json=body).status_code,
            403 if admin else status, path,
        )
        return self.assert_status(request(method, path, headers=self.headers("admin"), json=body), status, path)

    def assert_status(self, response, status, label):
        self.assertEqual(response.status_code, status, label)
        return response

    def test_admin_config_and_audit_routes(self):
        for method, path, body in [
            ("GET", "/api/config", None),
            ("POST", "/api/config", {"safe_mode": True}),
            ("GET", "/api/config/meta-status", None),
            ("GET", "/api/logs", None),
            ("GET", "/api/audit-settings", None),
            ("PUT", "/api/audit-settings/smart_audit_lookback_days", {"value": "30"}),
            ("POST", "/api/seed-demo-accounts", None),
            ("POST", "/api/revenueops/seed-demo", None),
        ]:
            with self.subTest(method=method, path=path):
                self.check_route(method, path, body)

    def test_admin_google_mutations(self):
        with patch("backend.routes.optimizations.GoogleAdsApiClient") as ads:
            ads.return_value.is_valid = True
            ads.return_value.mutate_campaign_budget.return_value = {"ok": True}
            ads.return_value.mutate_ad_group_criterion_bid.return_value = {"ok": True}
            self.check_route("POST", "/api/campaign/12/budget", {"campaign_id": "12", "new_budget": 20})
            self.check_route("POST", "/api/keyword/bid", {
                "campaign_id": "12", "ad_group_id": "34", "criterion_id": "56", "new_bid": 2,
            })
            ads.return_value.mutate_campaign_budget.assert_called_once()
            ads.return_value.mutate_ad_group_criterion_bid.assert_called_once()

    def test_admin_notification_routes(self):
        self.check_route("GET", "/api/notification-settings")
        response = self.check_route("PUT", "/api/notification-settings/email", {
            "enabled": True, "config": {"smtp_pass": "synthetic-notification-credential"},
        })
        self.assertTrue(response.json()["config_configured"])
        self.assertNotIn("config", response.json())
        with patch("backend.routes.notifications.send_email", return_value={"status": "sent"}) as send:
            self.check_route("POST", "/api/notification-settings/email/test")
            send.assert_called_once()

    def test_admin_account_writes(self):
        group_id = self.check_route("POST", "/api/account-groups", {"name": "Phase Zero"}).json()["id"]
        account_id = self.check_route("POST", "/api/accounts", {"name": "Phase Zero", "group_id": group_id}).json()["id"]
        path = f"/api/accounts/{account_id}"
        from backend.db.models import Account
        with self.db() as db:
            account = db.get(Account, account_id)
            account.google_is_live = True
            account.google_credentials = "synthetic-opaque-credential"
            account.google_developer_token = "synthetic-account-credential"
            db.commit()
        response = self.check_route("PUT", path, {"name": "Renamed"})
        self.assertTrue(response.json()["google_developer_token_configured"])
        self.assertNotIn("google_developer_token", response.json())
        self.check_route("PUT", path + "/campaign-type-tags/12", {"campaign_type": "brand"})
        with patch("backend.routes.accounts.get_connector") as connect:
            connect.return_value.is_valid = True
            connect.return_value.fetch_account_metrics.return_value = {}
            connect.return_value.fetch_campaigns.return_value = []
            self.check_route("POST", path + "/test-pull/google")
            self.check_route("POST", path + "/refresh")
        with (
            patch("backend.routes.audits.run_manual_smart_audit", return_value={}),
            patch("backend.routes.audits.fetch_campaign_landing_pages", return_value={}),
            patch("backend.routes.audits.crawl_stale_landing_pages", return_value={}),
            patch("backend.routes.audits.audit_account", return_value={}),
        ):
            for action in ("smart-audit", "refresh-landing-pages", "audit"):
                self.check_route("POST", f"{path}/{action}")
        self.check_route("DELETE", path)

    def test_admin_user_routes(self):
        self.check_route("GET", "/api/auth/users")
        with patch("backend.routes.auth.send_onboarding_email", return_value={"sent": True}):
            user_id = self.check_route("POST", "/api/auth/users", {
                "email": "invite@example.test", "full_name": "Invite", "role": "user",
            }).json()["user"]["id"]
            path = f"/api/auth/users/{user_id}"
            self.check_route("PUT", path, {"full_name": "Updated"})
            self.check_route("POST", path + "/reset-password")
            self.check_route("DELETE", path)
        for headers in ({}, self.headers("user"), self.headers("admin"), self.headers("superadmin")):
            self.assertEqual(self.client.post("/api/auth/reset-superadmin", headers=headers).status_code, 404)

    def test_authenticated_read_routes(self):
        with patch("backend.routes.optimizations.build_audit_summary", return_value={}), \
             patch("backend.routes.reports.get_full_report", return_value={}), \
             patch("urllib.request.urlopen", side_effect=OSError):
            for path in ("/api/audit", "/api/audit-runs", "/api/report/full", "/api/crashclub/status"):
                with self.subTest(path=path):
                    self.check_route("GET", path, admin=False)
        response = self.client.get("/api/audit-runs", headers={"Authorization": "Bearer invalid"})
        self.assertEqual(response.status_code, 401)

    def test_config_only_returns_status_and_allowlisted_fields(self):
        from backend.services.config import CONFIG_SECRET_FIELDS

        secrets = {key: f"synthetic-{key}-credential" for key in CONFIG_SECRET_FIELDS}
        secrets["unlisted_integration_key"] = "synthetic-extra-credential"
        self.config_file.write_text(json.dumps({
            **secrets, "salesforce_client_id": "test-client",
            "salesforce_url": "https://user:synthetic-url-password@crm.example.test/api?key=synthetic-url-key",
        }), encoding="utf-8")
        response = self.check_route("GET", "/api/config")
        data = response.json()
        self.assertEqual(data["configured"], dict.fromkeys(CONFIG_SECRET_FIELDS, True))
        self.assertEqual(data["salesforce_client_id"], "test-client")
        self.assertEqual(data["salesforce_url"], "https://crm.example.test/api")
        self.assertFalse(secrets.keys() & data.keys())
        self.assertFalse(any(value in response.text for value in secrets.values()))
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": "synthetic-env-credential"}):
            self.check_route("POST", "/api/config", {"leadsquared_base_url": "https://crm.example.test"})
        self.assertNotIn("synthetic-env-credential", self.config_file.read_text(encoding="utf-8"))
        self.assertTrue(all(self.client.get("/api/config", headers=self.headers("admin")).json()["configured"].values()))

    def test_docs_and_health(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.get("/health").status_code, 200)
        for headers in ({}, self.headers("user")):
            self.assertEqual(self.client.get("/health/db", headers=headers).json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/db", headers=self.headers("admin")).json()["active_db"], "sqlite")

    def test_first_user_bootstrap_ends_after_first_user(self):
        body = {"email": "first@example.test", "full_name": "First User", "mobile": "", "password": "synthetic-test-password"}
        self.assertEqual(self.client.post("/api/auth/onboard", json=body).status_code, 400)
        with self.db() as db:
            db.query(self.User).delete()
            db.commit()
        self.assertEqual(self.client.get("/api/auth/onboarding-required").json(), {"onboarding_required": True})
        self.assertEqual(self.client.post("/api/auth/onboard", json=body).status_code, 200)
        login = self.client.post("/api/auth/login", data={"username": body["email"], "password": body["password"]})
        self.assertEqual(login.status_code, 200)
        session = {"Authorization": "Bearer " + login.json()["access_token"]}
        self.assertEqual(self.client.get("/api/auth/me", headers=session).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/onboard", json=body).status_code, 400)
        self.assertEqual(self.client.get("/api/auth/onboarding-required").json(), {"onboarding_required": False})

    def test_oauth_callbacks_validate_state_before_exchange(self):
        from backend.services.oauth import build_state

        params = {"code": "synthetic-code", "state": "invalid"}
        with patch("backend.routes.auth.exchange_code_for_token", return_value={
            "refresh_token": "synthetic-gmail-refresh",
        }) as exchange:
            self.assertEqual(self.client.get("/api/auth/gmail/callback", params=params).status_code, 400)
            exchange.assert_not_called()
            params["state"] = build_state("gmail", redirect_uri="https://app.example.test/callback")
            self.assertEqual(self.client.get("/api/auth/gmail/callback", params=params).status_code, 200)
            self.assertEqual(self.client.get("/api/auth/me", headers={
                "Authorization": "Bearer " + params["state"],
            }).status_code, 401)
            exchange.assert_called_once()

    def test_meta_webhook_machine_signature(self):
        key = "synthetic-webhook-key"
        body = b'{"entry":[]}'
        signature = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
        header = {"X-Hub-Signature-256": signature}
        with (
            patch.dict(os.environ, {"ADGUARD_META_APP_SECRET": key}),
            patch("backend.routes.crashclub.WEBHOOK_APP_SECRET", key),
        ):
            for path in ("/api/adguard/meta/webhook", "/api/crashclub/webhook"):
                self.assertEqual(self.client.post(path, content=body).status_code, 403)
                self.assertEqual(self.client.post(path, content=body, headers=header).status_code, 200)

    def test_workspace_webhook_secret_is_write_only(self):
        from backend.db.models import AdGuardAccount

        with self.db() as db:
            ws = AdGuardAccount(owner_email="admin@example.test", display_name="Security Test")
            db.add(ws)
            db.commit()
            path = f"/api/adguard/workspaces/{ws.id}/installation"
        headers = self.headers("admin")
        self.assertEqual(self.client.get(path).status_code, 401)
        secret = "synthetic-workspace-hook-credential"
        response = self.client.put(path, headers=headers, json={
            "allowed_origins": ["https://site.example.test"],
            "webhook_secret": secret,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.text)
        response = self.client.get(path, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["webhook_secret_configured"])
        self.assertNotIn("webhook_secret", response.json())

    def test_tlg_script_does_not_return_reporting_key(self):
        from backend.db.models import Account, AccountType, TlgCentre

        with self.db() as db:
            account = Account(name="The Little Gym Security Test", account_type=AccountType.GOOGLE, external_id="")
            db.add(account)
            db.flush()
            centre = TlgCentre(account_id=account.id, name="Branch",
                               source_url="https://docs.google.com/spreadsheets/d/example_sheet/edit#gid=0")
            db.add(centre)
            db.commit()
            base = f"/api/tlg/{account.id}/centres/{centre.id}"
        key = "synthetic-tlg-reporting-key"
        digest = hashlib.sha256(key.encode()).hexdigest()
        response = self.client.post(base + "/reporting-script", headers=self.headers("admin"),
                                    json={"token_hash": digest})
        self.assertEqual(response.status_code, 200)
        self.assertIn("__TLG_REPORTING_TOKEN__", response.json()["script"])
        self.assertNotIn(key, response.text)
        self.assertNotIn(digest, response.text)
        payload = {"filter_version": 1, "daily_counts": {"2026-10-01": 4}}
        self.assertEqual(self.client.post(base + "/sheet-counts", json=payload).status_code, 401)
        self.assertEqual(self.client.post(base + "/sheet-counts", json=payload,
                                          headers={"Authorization": "Bearer " + key}).status_code, 200)

    def test_every_public_exception_reaches_its_handler_without_login(self):
        from backend.api_access import PUBLIC_ROUTES

        # Empty or invalid provider inputs must fail at the provider/handler
        # boundary, rather than at the dashboard JWT policy.
        for method, template in PUBLIC_ROUTES:
            path = (template.replace("{token}", "invalid")
                    .replace("{account_id}", "1").replace("{centre_id}", "1").replace("{id}", "1"))
            with self.subTest(method=method, path=path):
                with patch.dict(os.environ, {"MINI_ROBOS_SENDER_KEY_HASH": "0" * 64}):
                    response = self.client.request(method, path)
                self.assertLess(response.status_code, 500, path)
                if response.status_code == 401:
                    self.assertNotEqual(response.headers.get("www-authenticate"), "Bearer", path)
                self.assertNotEqual(response.status_code, 405, path)
        self.assertEqual(self.client.get("/api/v1/tag/adguard.js").status_code, 200)
        self.assertEqual(self.client.post("/api/adguard/meta/webhook", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/crashclub/webhook", json={}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
