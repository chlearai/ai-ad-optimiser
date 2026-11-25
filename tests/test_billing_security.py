"""AdGuard billing must never upgrade a plan without a verified payment.

Run this module alone: it binds the app to a disposable SQLite database.
"""
import hashlib
import hmac
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class BillingSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        db_path = Path(cls.temp.name) / "billing.sqlite3"
        env = patch.dict(os.environ, {
            "DATABASE_URL": "",
            "ADOPTIMA_DB_PATH": str(db_path),
            "RAZORPAY_KEY_ID": "",
            "RAZORPAY_KEY_SECRET": "",
            "ALLOW_SIMULATED_BILLING": "",
        })
        env.start()
        cls.addClassCleanup(env.stop)

        from backend.db import database
        if database.get_active_db() != "sqlite" or Path(database.DB_PATH) != db_path:
            raise RuntimeError("Run this module alone with its disposable database")
        cls.addClassCleanup(database.engine.dispose)
        from backend.app import app
        from backend.db.models import AdGuardAccount, User
        from backend.routes.auth import create_access_token
        from backend.services import billing

        # Never read a developer's local config.json during tests.
        no_cfg = patch.object(billing.os.path, "exists", side_effect=lambda p: False if str(p).endswith("config.json") else os.path.exists(p))
        no_cfg.start()
        cls.addClassCleanup(no_cfg.stop)

        database.Base.metadata.create_all(database.engine)
        cls.db, cls.User, cls.Account = database.SessionLocal, User, AdGuardAccount
        cls.create_token = staticmethod(create_access_token)
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    def setUp(self):
        with self.db() as db:
            db.query(self.Account).delete()
            db.query(self.User).delete()
            for email, role in (("owner@example.test", "user"), ("other@example.test", "user"), ("admin@example.test", "admin")):
                db.add(self.User(email=email, role=role, full_name=email, hashed_password="x",
                                 is_active=True, onboarding_completed=True))
            db.add(self.Account(id=1, owner_email="owner@example.test", plan="free", lead_quota=50))
            db.add(self.Account(id=2, owner_email="other@example.test", plan="free", lead_quota=50))
            db.commit()

    def h(self, email):
        return {"Authorization": "Bearer " + self.create_token({"sub": email})}

    def plan(self, ws_id):
        with self.db() as db:
            return db.get(self.Account, ws_id).plan

    def verify(self, email, **extra):
        body = {"workspace_id": 1, "plan": "agency", "razorpay_payment_id": "pay_1", **extra}
        return self.client.post("/api/billing/verify-payment", json=body, headers=self.h(email))

    # --- No payment gateway configured (current production default) ---

    def test_subscriber_cannot_self_upgrade_without_gateway(self):
        self.assertEqual(self.client.post("/api/billing/create-order", json={"plan": "agency", "workspace_id": 1},
                                          headers=self.h("owner@example.test")).status_code, 400)
        self.assertEqual(self.verify("owner@example.test").status_code, 400)
        self.assertEqual(self.client.post("/api/billing/sandbox-simulate-success", json={"plan": "agency", "workspace_id": 1},
                                          headers=self.h("owner@example.test")).status_code, 403)
        self.assertEqual(self.plan(1), "free")

    def test_simulation_when_enabled_is_limited_to_own_workspace(self):
        with patch.dict(os.environ, {"ALLOW_SIMULATED_BILLING": "true"}):
            r = self.client.post("/api/billing/sandbox-simulate-success", json={"plan": "pro", "workspace_id": 2},
                                 headers=self.h("owner@example.test"))
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.plan(2), "free")
            r = self.client.post("/api/billing/sandbox-simulate-success", json={"plan": "pro", "workspace_id": 1},
                                 headers=self.h("owner@example.test"))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.plan(1), "pro")

    def test_admin_can_simulate_without_gateway(self):
        r = self.client.post("/api/billing/sandbox-simulate-success", json={"plan": "starter", "workspace_id": 2},
                             headers=self.h("admin@example.test"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.plan(2), "starter")

    # --- Real gateway configured ---

    def _sig(self, order_id, payment_id, secret="s3cret"):
        return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()

    def test_gateway_requires_signature_and_matching_paid_order(self):
        from backend.services import billing
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_live_abc", "RAZORPAY_KEY_SECRET": "s3cret"}):
            # Missing signature, or a simulated order id, is rejected.
            self.assertEqual(self.verify("owner@example.test", razorpay_order_id="order_1").status_code, 400)
            self.assertEqual(self.verify("owner@example.test", razorpay_order_id="order_sbx_agency_1",
                                         razorpay_signature=self._sig("order_sbx_agency_1", "pay_1")).status_code, 400)
            # Wrong signature.
            self.assertEqual(self.verify("owner@example.test", razorpay_order_id="order_1",
                                         razorpay_signature="bad").status_code, 400)
            good = {"razorpay_order_id": "order_1", "razorpay_signature": self._sig("order_1", "pay_1")}
            # Valid signature but the order was for a cheaper plan.
            cheap = {"status": "paid", "amount_paid": 499900, "notes": {"plan": "starter", "workspace_id": "1"}}
            with patch.object(billing, "_fetch_razorpay_order", return_value=cheap):
                self.assertEqual(self.verify("owner@example.test", **good).status_code, 400)
            # Valid signature but unpaid order.
            unpaid = {"status": "created", "amount_paid": 0, "notes": {"plan": "agency", "workspace_id": "1"}}
            with patch.object(billing, "_fetch_razorpay_order", return_value=unpaid):
                self.assertEqual(self.verify("owner@example.test", **good).status_code, 400)
            self.assertEqual(self.plan(1), "free")
            # Matching paid order upgrades.
            paid = {"status": "paid", "amount_paid": 3999900, "notes": {"plan": "agency", "workspace_id": "1"}}
            with patch.object(billing, "_fetch_razorpay_order", return_value=paid):
                self.assertEqual(self.verify("owner@example.test", **good).status_code, 200)
            self.assertEqual(self.plan(1), "agency")

    def test_gateway_disables_simulation_even_for_admin(self):
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": "rzp_live_abc", "RAZORPAY_KEY_SECRET": "s3cret"}):
            r = self.client.post("/api/billing/sandbox-simulate-success", json={"plan": "pro", "workspace_id": 1},
                                 headers=self.h("admin@example.test"))
            self.assertEqual(r.status_code, 403)

    def test_cannot_verify_someone_elses_workspace(self):
        r = self.client.post("/api/billing/verify-payment", headers=self.h("other@example.test"),
                             json={"workspace_id": 1, "plan": "agency", "razorpay_payment_id": "p"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
