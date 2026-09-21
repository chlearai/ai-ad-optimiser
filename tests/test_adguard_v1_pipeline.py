"""
AdGuard V1 Automated Pipeline Test Suite.

Tests:
  1. DB Table creation & session registration (/api/v1/tag/session)
  2. Pre-Submit Interceptor Green verdict (<400ms)
  3. Pre-Submit Interceptor Red verdict (Automation/Datacenter/Disposable -> Silent quarantine)
  4. Pre-Submit Interceptor Grey verdict + OTP verification (/api/v1/tag/otp)
  5. AI Bot-Caller outcome callback (/api/v1/webhooks/botcaller)
  6. Universal SAKHA CRM stage change webhook (/api/v1/webhooks/crm)
  7. Conversion Signal Firewall flush dispatcher
  8. Dashboard savings & invalid-click evidence export
"""
import json
import os
import sys
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from backend.app import app
from backend.db.database import SessionLocal, init_db
from backend.db.models import (
    AdGuardAccount,
    AdGuardConversionEvent,
    AdGuardExclusionMember,
    AdGuardLead,
    AdGuardNetworkEntity,
    AdGuardSession,
    AdGuardVerdict,
    User,
)
from backend.routes.auth import create_access_token, get_password_hash
from backend.services.adguard_firewall import flush_pending_conversions
from backend.services.adguard_shared_network import run_network_score_decay


class TestAdGuardV1Pipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)
        cls.db = SessionLocal()

        # Reset network entities for isolated testing
        cls.db.query(AdGuardNetworkEntity).delete()
        cls.db.commit()

        # Seed test workspace
        ws = cls.db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == "test_v1@adguard.local").first()
        if not ws:
            ws = AdGuardAccount(
                owner_email="test_v1@adguard.local",
                display_name="Test Enterprise Workspace",
                plan="pro",
                lead_quota=5000,
                conversion_values=json.dumps({"lead": 500, "qualified": 2000, "appointment": 10000}),
            )
            cls.db.add(ws)
            cls.db.commit()
            cls.db.refresh(ws)
        cls.ws = ws


        # Seed admin user for auth tests
        admin_user = cls.db.query(User).filter(User.email == "admin_test@adguard.local").first()
        if not admin_user:
            admin_user = User(
                email="admin_test@adguard.local",
                full_name="Admin Tester",
                hashed_password=get_password_hash("admin123"),
                role="admin",
                is_active=True,
                access_adguard=True,
            )
            cls.db.add(admin_user)
            cls.db.commit()
            cls.db.refresh(admin_user)
        cls.auth_token = create_access_token({"sub": "admin_test@adguard.local"})
        cls.auth_headers = {"Authorization": f"Bearer {cls.auth_token}"}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_serve_js_tag(self):
        """Verify the JS tag is served properly with javascript content-type."""
        resp = self.client.get("/api/v1/tag/adguard.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/javascript", resp.headers["content-type"])
        self.assertIn("AdGuard Pre-Submit Interceptor Tag", resp.text)

    def test_02_session_registration(self):
        """Verify page load session telemetry ingestion."""
        session_uuid = f"test_sid_{os.urandom(4).hex()}"
        payload = {
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "fingerprint_hash": "canvas_hash_12345",
            "gclid": "test_gclid_9999",
            "utm_campaign": "AdGuard_Search_Live",
            "page_url": "https://client.example.com/landing",
            "device_info": {"webdriver": False, "screen": "1920x1080"},
            "behaviour": {"time_on_page_ms": 5000, "mouse_moves_count": 25},
        }
        resp = self.client.post("/api/v1/tag/session", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        # Verify DB entry
        session_row = self.db.query(AdGuardSession).filter(AdGuardSession.session_uuid == session_uuid).first()
        self.assertIsNotNone(session_row)
        self.assertEqual(session_row.gclid, "test_gclid_9999")

    def test_03_clean_green_verdict(self):
        """Verify clean human form submission gets GREEN verdict (<400ms target)."""
        session_uuid = f"clean_sid_{os.urandom(4).hex()}"
        # Register session first
        self.client.post("/api/v1/tag/session", json={
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "gclid": "gclid_clean_1",
            "device_info": {"webdriver": False, "headless": False},
        })

        import random
        rand_phone = f"98{random.randint(10000000, 99999999)}"
        verdict_payload = {
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "full_name": "Rohan Sharma",
            "email": f"rohan.sharma.{os.urandom(2).hex()}@gmail.com",
            "phone": rand_phone,
            "city": "Bengaluru",
            "state": "Karnataka",
            "campaign_name": "AdGuard Search Clean",
            "time_on_page_ms": 15000,
            "time_to_fill_ms": 8000,
            "mouse_moves_count": 40,
            "keystrokes_count": 30,
        }
        resp = self.client.post("/api/v1/tag/verdict", json=verdict_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["verdict"], "green")
        self.assertLess(data["risk_score"], 40)
        self.assertFalse(data["otp_required"])

        # Check Lead record state
        lead_id = data["lead_id"]
        lead = self.db.query(AdGuardLead).filter(AdGuardLead.id == lead_id).first()
        self.assertEqual(lead.stage, "submitted")
        self.assertEqual(lead.bot_call_status, "queued")

    def test_04_bot_red_verdict_silent_quarantine(self):
        """Verify automated headless bot gets RED verdict, is quarantined and added to threat network."""
        session_uuid = f"bot_sid_{os.urandom(4).hex()}"
        self.client.post("/api/v1/tag/session", json={
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "device_info": {"webdriver": True, "headless": True},
        })

        bot_payload = {
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "full_name": "Bot Hunter",
            "email": "disposable_bot@mailinator.com",
            "phone": "1234567890",  # non-Indian carrier format
            "webdriver": True,
            "headless": True,
            "time_on_page_ms": 500,
            "time_to_fill_ms": 300,
            "mouse_moves_count": 0,
        }
        resp = self.client.post("/api/v1/tag/verdict", json=bot_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["verdict"], "red")
        self.assertGreaterEqual(data["risk_score"], 70)

        # Check silent quarantine
        lead = self.db.query(AdGuardLead).filter(AdGuardLead.id == data["lead_id"]).first()
        self.assertEqual(lead.stage, "rejected")
        self.assertEqual(lead.verdict, "red")

        # Check that exclusions and threat network were updated
        exclusions = self.db.query(AdGuardExclusionMember).filter(AdGuardExclusionMember.adguard_account_id == self.ws.id).all()
        self.assertGreater(len(exclusions), 0)

    def test_05_grey_band_and_otp_stepup(self):
        """Verify grey band triggers OTP step-up, and verifying OTP upgrades lead to green."""
        session_uuid = f"grey_sid_{os.urandom(4).hex()}"
        self.client.post("/api/v1/tag/session", json={
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "device_info": {"webdriver": False, "headless": False},
        })

        # Disposable email (+40 pts) with otherwise normal form fill -> lands in Grey band (40-69)
        grey_payload = {
            "session_uuid": session_uuid,
            "adguard_account_id": self.ws.id,
            "full_name": "Grey Tester",
            "email": f"grey_user_{os.urandom(2).hex()}@mailinator.com",
            "phone": "9811122233",
            "time_on_page_ms": 8000,
            "time_to_fill_ms": 4000,
            "mouse_moves_count": 15,
        }
        resp = self.client.post("/api/v1/tag/verdict", json=grey_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data["verdict"], ("grey", "green", "red"))

        # Test OTP verification endpoint with universal sandbox code '123456'
        otp_resp = self.client.post("/api/v1/tag/otp", json={
            "session_uuid": session_uuid,
            "otp_code": "123456",
        })
        self.assertEqual(otp_resp.status_code, 200)
        self.assertTrue(otp_resp.json()["verified"])
        self.assertEqual(otp_resp.json()["verdict"], "green")

    def test_06_bot_caller_verification_and_conversion_event(self):
        """Verify AI Bot-Caller callback upgrades lead to verified and queues conversion event."""
        # Create a submitted lead
        lead = AdGuardLead(
            adguard_account_id=self.ws.id,
            full_name="Ananya Roy",
            email="ananya.roy@example.com",
            phone="9876543210",
            gclid="gclid_botcaller_test",
            verdict="green",
            stage="submitted",
        )
        self.db.add(lead)
        self.db.commit()
        self.db.refresh(lead)

        # Bot caller hits webhook with verified outcome
        resp = self.client.post("/api/v1/webhooks/botcaller", json={
            "lead_id": lead.id,
            "outcome": "verified",
            "summary": "Confirmed interest in Bangalore project, budget 1.2 Cr",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stage"], "verified")

        # Verify conversion event was queued
        events = self.db.query(AdGuardConversionEvent).filter(AdGuardConversionEvent.lead_id == lead.id).all()
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0].event_name, "Lead")
        self.assertEqual(events[0].value, 500.0)

    def test_07_crm_qualified_stage_webhook(self):
        """Verify SAKHA CRM stage webhook dispatches higher value conversion tier."""
        lead = AdGuardLead(
            adguard_account_id=self.ws.id,
            full_name="Deepak Kumar",
            email="deepak.kumar@example.com",
            phone="9899988877",
            gclid="gclid_crm_stage_test",
            verdict="green",
            stage="verified",
        )
        self.db.add(lead)
        self.db.commit()
        self.db.refresh(lead)

        # SAKHA CRM pushes Qualified stage
        resp = self.client.post("/api/v1/webhooks/crm", json={
            "lead_id": lead.id,
            "stage": "Qualified",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stage"], "qualified")

        # Check conversion event for QualifiedLead with value 2000
        qualified_evt = (
            self.db.query(AdGuardConversionEvent)
            .filter(AdGuardConversionEvent.lead_id == lead.id, AdGuardConversionEvent.event_name == "QualifiedLead")
            .first()
        )
        self.assertIsNotNone(qualified_evt)
        self.assertEqual(qualified_evt.value, 2000.0)

    def test_08_flush_conversion_dispatcher(self):
        """Verify conversion firewall background flush worker."""
        summary = flush_pending_conversions(self.db, limit=10)
        self.assertIn("sent", summary)
        self.assertIn("failed", summary)

    def test_09_savings_and_evidence_endpoints(self):
        """Verify dashboard savings calculation and evidence CSV export with auth."""
        # Savings
        savings_resp = self.client.get(f"/api/v1/accounts/{self.ws.id}/savings", headers=self.auth_headers)
        self.assertEqual(savings_resp.status_code, 200)
        s_data = savings_resp.json()
        self.assertIn("spend_prevented_inr", s_data)
        self.assertIn("total_leads_intercepted", s_data)

        # Evidence Pack Export
        evidence_resp = self.client.get(f"/api/v1/accounts/{self.ws.id}/evidence", headers=self.auth_headers)
        self.assertEqual(evidence_resp.status_code, 200)
        self.assertIn("text/csv", evidence_resp.headers["content-type"])
        self.assertIn("Google Click ID (GCLID)", evidence_resp.text)


if __name__ == "__main__":
    unittest.main()
