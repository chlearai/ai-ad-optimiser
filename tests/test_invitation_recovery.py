"""Invitation recovery tests with an isolated SQLite database and mocked delivery."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock
import logging
import os
import secrets
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    email = Column(String)
    full_name = Column(String)
    mobile = Column(String)
    access_adpulse = Column(Boolean)
    access_insightdesk = Column(Boolean)
    access_revenueops = Column(Boolean)
    access_audit_review = Column(Boolean)
    access_adguard = Column(Boolean)
    role = Column(String)
    is_active = Column(Boolean)
    onboarding_completed = Column(Boolean)
    onboarding_token = Column(String)
    onboarding_token_expires_at = Column(DateTime)
    hashed_password = Column(String)
    def to_dict(self): return {'id': self.id, 'email': self.email}

class AppSetting(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    key = Column(String)
    value = Column(String)

source = Path(__file__).resolve().parents[1] / 'backend/routes/auth.py'
namespace = dict(User=User, AppSetting=AppSetting, datetime=datetime, timedelta=timedelta,
                 os=os, secrets=secrets, HTTPException=HTTPException, logger=logging.getLogger('test'),
                 ONBOARDING_TOKEN_EXPIRE_HOURS=72)
for node in ast.parse(source.read_text(encoding='utf-8')).body:
    if isinstance(node, ast.FunctionDef) and node.name in ('create_user', 'reset_user_password'):
        node.decorator_list = []
        node.args.defaults = []
        for arg in node.args.args: arg.annotation = None
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)

class InvitationRecoveryTests(TestCase):
    def setUp(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(id=2, email='pending@example.test', full_name='Pending', role='user',
                         is_active=False, onboarding_completed=False, hashed_password='unchanged',
                         onboarding_token='old')
        self.db.add(self.user)
        self.db.commit()
        namespace['send_onboarding_email'] = Mock(return_value={'sent': False, 'error': 'Mail unavailable'})
        namespace['log_activity'] = Mock()
        self.admin = SimpleNamespace(id=1, full_name='Admin', email='admin@example.test', role='admin')
        self.request = SimpleNamespace(base_url='https://app.example.test/')
    def tearDown(self): self.db.close()
    def reset(self): return namespace['reset_user_password'](2, self.request, self.db, self.admin)
    def test_create_reports_failure_and_commits_even_if_activity_log_rolls_back(self):
        namespace['get_password_hash'] = Mock(return_value='temporary-hash')
        namespace['log_activity'].side_effect = lambda **kw: kw['db'].rollback()
        req = SimpleNamespace(email='new@example.test', full_name='New', mobile='', role='user',
                              access_adpulse=True, access_insightdesk=False, access_revenueops=False,
                              access_audit_review=False, access_adguard=False, assigned_account_ids=[])
        result = namespace['create_user'](req, self.request, self.db, self.admin)
        self.assertFalse(result['email_sent'])
        self.assertEqual(result['email_status'], 'failed')
        self.assertEqual(result['email_error'], 'Mail unavailable')
        self.assertIn('token=', result['setup_link'])
        self.db.expire_all()
        saved = self.db.query(User).filter_by(email=req.email).one()
        self.assertFalse(saved.is_active)
        self.assertFalse(saved.onboarding_completed)

    def test_pending_invite_can_be_resent_without_activating_user(self):
        result = self.reset()
        self.assertTrue(result['pending_setup'])
        self.assertEqual(result['expires_in_minutes'], 4320)
        self.assertNotIn('reset=1', result['reset_link'])
        self.assertNotEqual(self.user.onboarding_token, 'old')
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.hashed_password, 'unchanged')
        self.assertFalse(result['email_sent'])
        self.assertEqual(result['email_error'], 'Mail unavailable')
        self.assertGreater(self.user.onboarding_token_expires_at, datetime.utcnow() + timedelta(hours=71))
    def test_active_reset_has_one_hour_expiry_and_reports_provider_success(self):
        self.user.is_active = self.user.onboarding_completed = True
        namespace['send_onboarding_email'].return_value = {'sent': True}
        result = self.reset()
        self.assertFalse(result['pending_setup'])
        self.assertTrue(result['email_sent'])
        self.assertIn('reset=1&token=', result['reset_link'])
        self.assertEqual(result['expires_in_minutes'], 60)
        self.assertEqual(self.user.hashed_password, 'unchanged')
    def test_disabled_completed_account_is_rejected(self):
        self.user.onboarding_completed = True
        with self.assertRaises(HTTPException) as err: self.reset()
        self.assertEqual(err.exception.status_code, 400)
        namespace['send_onboarding_email'].assert_not_called()
    def test_admin_cannot_reset_superadmin(self):
        self.user.role = 'superadmin'
        with self.assertRaises(HTTPException) as err: self.reset()
        self.assertEqual(err.exception.status_code, 403)
    def test_delivery_exception_preserves_recovery_link(self):
        namespace['send_onboarding_email'].side_effect = TimeoutError('unavailable')
        result = self.reset()
        self.assertFalse(result['email_sent'])
        self.assertIn('token=', result['reset_link'])
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(User, 2).onboarding_token)

if __name__ == '__main__': main()
