"""Isolated regression tests for subscriber serialization; no external database access."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()
class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime)
class Lead(Base):
    __tablename__ = 'leads'
    id = Column(Integer, primary_key=True)
    adguard_account_id = Column(Integer)
    verdict = Column(String)
    raw_payload = Column(String)
    received_at = Column(DateTime)
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String)

source = Path(__file__).resolve().parents[1] / 'backend/routes/adguard.py'
node = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef) and n.name == 'admin_subscribers')
node.decorator_list = []
node.args.defaults = []
for arg in node.args.args:
    arg.annotation = None
namespace = dict(AdGuardAccount=Account, AdGuardLead=Lead, User=User, func=func, os=os, HTTPException=HTTPException, _webhook_hits=[], allowance=lambda db, ws: {'quota_pct': None if ws.lead_quota <= 0 else round(100 * ws.leads_this_month / ws.lead_quota, 1)})
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)

class SubscriberTests(unittest.TestCase):
    def run_endpoint(self, quota=300, owner=True):
        fields = ['phone','company_name','industry','plan','plan_expires_at','overage_policy','payment_mode','payment_ref','amount_paid','gst_invoice_no','payment_status','account_status','google_is_live','meta_is_live','is_archived','signup_source','is_beta','created_at','call_credits_remaining','call_credits_granted_total','call_credits_used','crm_preference','google_last_sync_at','meta_last_sync_at']
        ws = SimpleNamespace(**dict.fromkeys(fields), id=1, owner_email='owner@example.test', display_name='Test', lead_quota=quota, leads_this_month=30)
        db = MagicMock()
        def query(entity):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.all.return_value = [ws]
            q.scalar.return_value = 90
            q.first.return_value = (SimpleNamespace(tos_accepted_version='2.0',is_active=True,onboarding_completed=True) if owner else None) if entity is User else None
            return q
        db.query.side_effect = query
        return namespace['admin_subscribers'](db, SimpleNamespace(role='admin'))['subscribers'][0]
    def test_owner_terms_and_monthly_quota(self):
        row = self.run_endpoint()
        self.assertEqual(row['tos_accepted_version'], '2.0')
        self.assertEqual(row['quota_pct'], 10)
    def test_missing_owner_and_zero_quota(self):
        row = self.run_endpoint(quota=0, owner=False)
        self.assertIsNone(row['tos_accepted_version'])
        self.assertIsNone(row['quota_pct'])
    def test_unlimited_quota(self):
        self.assertIsNone(self.run_endpoint(quota=-1)['quota_pct'])
    def test_customer_forbidden(self):
        with self.assertRaises(HTTPException) as error:
            namespace['admin_subscribers'](MagicMock(), SimpleNamespace(role='user'))
        self.assertEqual(error.exception.status_code, 403)

if __name__ == '__main__':
    unittest.main()
