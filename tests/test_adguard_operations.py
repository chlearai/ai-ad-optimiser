"""Run against an isolated database; no production data or provider calls."""
import os
import tempfile
os.environ['DATABASE_URL'] = ''
os.environ['ADOPTIMA_DB_PATH'] = os.path.join(tempfile.gettempdir(), 'adguard-tests-unused.db')
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from types import SimpleNamespace
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.db.database import Base, get_db
from backend.db.models import User, AdGuardAccount, AdGuardLead, AdGuardSupportTicket, AdGuardVerdict, AdGuardSession
from backend.db.adguard_ops import AdGuardAuditUsage, AdGuardNotification, AdGuardInternalNote
from backend.services.adguard_subscription import reserve_audit, WorkspaceUnavailable, allowance, operations, set_plan
from backend.services.adguard_support_ops import escalate_overdue, process_notifications, ticket_operations
from backend.routes.auth import get_current_user_required
from backend.routes.adguard import router as admin_router
from backend.routes.adguard_support import router as support_router
from backend.routes.adguard_v1 import router as tag_router

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
        @event.listens_for(self.engine, 'connect')
        def foreign_keys(conn, _): conn.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.admin = User(email='admin@test.local', hashed_password='test', role='admin', is_active=True, onboarding_completed=True)
        self.owner = User(email='owner@test.local', hashed_password='test', role='user', is_active=True, onboarding_completed=True, access_adguard=True)
        self.other = User(email='other@test.local', hashed_password='test', role='user', is_active=True, onboarding_completed=True, access_adguard=True)
        self.db.add_all([self.admin,self.owner,self.other]); self.db.flush()
        self.ws = AdGuardAccount(owner_email=self.owner.email, plan='starter', lead_quota=2, leads_this_month=0, leads_month_reset=datetime.utcnow(), account_status='active', plan_expires_at=datetime.utcnow()+timedelta(days=30))
        self.ws2 = AdGuardAccount(owner_email=self.other.email, plan='pro', lead_quota=5000, leads_this_month=0, leads_month_reset=datetime.utcnow(), account_status='active')
        self.db.add_all([self.ws,self.ws2]); self.db.commit()
        operations(self.db,self.ws); operations(self.db,self.ws2); self.db.commit()
        self.app = FastAPI(); self.app.include_router(admin_router); self.app.include_router(support_router); self.app.include_router(tag_router)
        self.current = self.admin
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_current_user_required] = lambda: self.current
        self.client = TestClient(self.app)
    def tearDown(self):
        self.db.close(); self.engine.dispose()
    def test_atomic_usage_exhaustion_idempotency_and_rollback(self):
        _,one,new=reserve_audit(self.db,self.ws.id,'one'); self.assertTrue(new); self.db.commit()
        _,same,new=reserve_audit(self.db,self.ws.id,'one'); self.assertFalse(new); self.assertEqual(one.id,same.id)
        reserve_audit(self.db,self.ws.id,'two'); self.db.commit()
        with self.assertRaises(WorkspaceUnavailable): reserve_audit(self.db,self.ws.id,'three')
        self.db.rollback()
        self.assertEqual(self.db.get(AdGuardAccount,self.ws.id).leads_this_month,2)
        self.assertEqual(self.db.query(AdGuardAuditUsage).count(),2)
    def test_period_anchor_and_temporary_bonus(self):
        self.ws.leads_month_reset=datetime(2026,1,31); self.ws.leads_this_month=2
        op=operations(self.db,self.ws); op.bonus_period=self.ws.leads_month_reset; op.bonus_audits=10; self.db.commit()
        limits=allowance(self.db,self.ws,datetime(2026,3,10))
        self.assertEqual(limits['audits_used'],0); self.assertEqual(limits['bonus_audits'],0)
        self.assertTrue(limits['period_end'].startswith('2026-03-31'))
    def test_all_statuses_and_expiry_enforced_payment_bypass_retained(self):
        self.ws.payment_status='pending'
        reserve_audit(self.db,self.ws.id,'allowed'); self.db.commit()
        for status in ['paused','suspended','expired']:
            self.ws.account_status=status; self.db.commit()
            with self.assertRaises(WorkspaceUnavailable): reserve_audit(self.db,self.ws.id,status)
            self.db.rollback()
        self.ws.account_status='active'; self.ws.plan_expires_at=datetime.utcnow()-timedelta(seconds=1); self.db.commit()
        with self.assertRaises(WorkspaceUnavailable): reserve_audit(self.db,self.ws.id,'expired_paid')
    def test_bonus_and_renewal_endpoints_preserve_base_and_usage(self):
        self.ws.leads_this_month=1; self.db.commit()
        r=self.client.post(f'/api/adguard/admin/subscribers/{self.ws.id}/bonus-quota',json={'bonus_leads':3})
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['audit_limit'],5)
        self.assertEqual(self.ws.lead_quota,2)
        r=self.client.post(f'/api/adguard/admin/subscribers/{self.ws.id}/renew',json={'months':1})
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(self.ws.leads_this_month,1)
    def test_installation_isolation_origin_and_session_conflict(self):
        token=operations(self.db,self.ws).tag_token
        op=operations(self.db,self.ws); op.allowed_origins='["https://client.test"]'; self.db.commit()
        data={'session_uuid':'isolation','adguard_account_id':self.ws.id,'installation_token':token}
        r=self.client.post('/api/v1/tag/session',json=data,headers={'origin':'https://bad.test'})
        self.assertEqual(r.status_code,403,r.text)
        r=self.client.post('/api/v1/tag/session',json=data,headers={'origin':'https://client.test'})
        self.assertEqual(r.status_code,200,r.text)
        data.update(adguard_account_id=self.ws2.id,installation_token=operations(self.db,self.ws2).tag_token)
        r=self.client.post('/api/v1/tag/session',json=data)
        self.assertEqual(r.status_code,409,r.text)
    def test_web_audits_count_once_and_enforce_limit(self):
        token=operations(self.db,self.ws).tag_token
        def evaluate(*args,**kwargs): return {'integrity_score':5,'risk_score':95,'verdict':'red','reasons':['test']}
        with patch('backend.routes.adguard_v1.evaluate_submit_verdict',side_effect=evaluate):
            for i in range(3):
                data={'session_uuid':f'audit-{i}','adguard_account_id':self.ws.id,'installation_token':token}
                self.assertEqual(self.client.post('/api/v1/tag/session',json=data).status_code,200)
                r=self.client.post('/api/v1/tag/verdict',json=data)
                self.assertEqual(r.status_code,200 if i<2 else 429,r.text)
                if i<2:
                    duplicate=self.client.post('/api/v1/tag/verdict',json=data)
                    self.assertEqual(duplicate.json()['lead_id'],r.json()['lead_id'])
        self.assertEqual(self.db.query(AdGuardLead).count(),2)
        self.assertEqual(self.ws.leads_this_month,2)
    def test_ticket_deadlines_notes_notifications_and_escalation(self):
        self.current=self.owner
        r=self.client.post('/api/adguard/support/tickets',json={'subject':'Connection failed','body':'Please help','workspace_id':self.ws.id})
        self.assertEqual(r.status_code,200,r.text); ticket_id=r.json()['id']
        self.assertIsNotNone(r.json()['response_due_at'])
        self.current=self.admin
        r=self.client.put(f'/api/adguard/support/admin/tickets/{ticket_id}/manage',json={'assigned_to':self.admin.email,'internal_note':'Private troubleshooting','priority':'high'})
        self.assertEqual(r.status_code,200,r.text)
        self.current=self.owner
        r=self.client.get(f'/api/adguard/support/tickets/{ticket_id}')
        self.assertNotIn('internal_notes',r.json()); self.assertNotIn('notifications',r.json())
        ticket=self.db.get(AdGuardSupportTicket,ticket_id)
        ticket_operations(self.db,ticket).response_due_at=datetime.utcnow()-timedelta(minutes=1); self.db.commit()
        escalate_overdue(self.db); escalate_overdue(self.db)
        self.assertEqual(ticket_operations(self.db,ticket).escalation_count,1)
        self.assertEqual(self.db.query(AdGuardNotification).count(),3)
        process_notifications(self.db,sender=lambda **kw:{'sent':False,'error':'SMTP unavailable'})
        self.assertEqual(self.db.query(AdGuardNotification).first().status,'retry')
        for n in self.db.query(AdGuardNotification).all(): n.next_attempt_at=datetime.utcnow()-timedelta(seconds=1)
        self.db.commit(); process_notifications(self.db,sender=lambda **kw:{'sent':True})
        self.assertTrue(all(n.status=='sent' for n in self.db.query(AdGuardNotification).all()))
        self.current=self.admin
        r=self.client.post(f'/api/adguard/support/tickets/{ticket_id}/reply',json={'body':'Resolved'})
        self.assertEqual(r.status_code,200,r.text); self.assertIsNone(r.json()['response_due_at'])
        self.current=self.owner
        r=self.client.post(f'/api/adguard/support/tickets/{ticket_id}/reply',json={'body':'Still failing'})
        self.assertEqual(r.status_code,200,r.text); self.assertIsNotNone(r.json()['response_due_at'])
    def test_owner_permissions_and_overview(self):
        self.current=self.other
        self.assertEqual(self.client.get(f'/api/adguard/workspaces/{self.ws.id}/installation').status_code,403)
        self.assertEqual(self.client.get('/api/adguard/admin/operations').status_code,403)
        self.current=self.admin
        r=self.client.get('/api/adguard/admin/operations'); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['payment_mode'],'testing_bypass')
        self.assertEqual(r.json()['summary']['workspaces'],2)
        r=self.client.get('/api/adguard/admin/subscribers'); self.assertEqual(r.status_code,200,r.text)
    def test_crm_callback_secret_and_scope(self):
        lead=AdGuardLead(adguard_account_id=self.ws2.id,email='shared@test.local',verdict='verified')
        self.db.add(lead); self.db.commit()
        data={'workspace_id':self.ws.id,'lead_id':lead.id,'stage':'qualified'}
        r=self.client.post('/api/v1/webhooks/crm',json=data); self.assertEqual(r.status_code,403,r.text)
        r=self.client.post('/api/v1/webhooks/crm',json=data,headers={'x-adguard-webhook-secret':operations(self.db,self.ws).webhook_secret})
        self.assertEqual(r.status_code,404,r.text)

    def test_google_meta_pipeline_scoped_dedup_and_no_global_crm(self):
        from backend.services.adguard import process_incoming_lead
        self.ws.crm_preference='none'; self.db.commit()
        existing=AdGuardLead(adguard_account_id=self.ws2.id,email='shared@test.local',verdict='verified')
        self.db.add(existing); self.db.commit()
        score={'verdict':'verified','integrity_score':90,'flags':[],'email_valid':True,'disposable_email':False,
               'phone_valid':True,'geo_match':True,'ai_legitimacy_score':90,'ai_reason':'test'}
        with patch('backend.db.database.SessionLocal',self.Session), patch('backend.services.adguard.score_lead',return_value=score), patch('backend.services.adguard.push_lead_to_lsq') as global_crm:
            row=process_incoming_lead({'email':'shared@test.local','full_name':'Test','platform':'meta','lead_type':'meta_leadgen'},workspace_id=self.ws.id)
            self.assertEqual(row['verdict'],'verified'); self.assertEqual(row['lead_type'],'meta_leadgen')
            global_crm.assert_not_called()
            self.db.expire_all()
            self.assertEqual(self.ws.leads_this_month,1)
            self.assertIsNotNone(self.ws.meta_last_sync_at)
    def test_plan_changes_keep_usage_and_reject_excess_connections(self):
        self.ws.leads_this_month=1; self.ws.google_identities='[{"email":"a"},{"email":"b"}]'; self.db.commit()
        r=self.client.put(f'/api/adguard/admin/subscribers/{self.ws.id}',json={'plan':'pro'})
        self.assertEqual(r.status_code,409,r.text); self.db.rollback()
        self.ws.google_identities='[{"email":"a"}]'; self.db.commit()
        r=self.client.put(f'/api/adguard/admin/subscribers/{self.ws.id}',json={'plan':'pro'})
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(self.ws.lead_quota,5000)
        self.assertEqual(self.ws.leads_this_month,1)
        r=self.client.post(f'/api/adguard/admin/subscribers/{self.ws.id}/toggle-status',json={'status':'invalid'})
        self.assertEqual(r.status_code,400,r.text)
    def test_actual_red_verdict_has_one_transaction(self):
        from backend.services.adguard_interceptor import evaluate_submit_verdict
        data={'session_uuid':'real-red','adguard_account_id':self.ws.id,'installation_token':operations(self.db,self.ws).tag_token}
        self.assertEqual(self.client.post('/api/v1/tag/session',json=data).status_code,200)
        before=self.ws.leads_this_month
        with patch('backend.services.adguard_interceptor.ai_legitimacy_score',return_value=(0,'test')):
            # A forced rollback after evaluation must also roll back its shared-network writes and usage reservation.
            reserve_audit(self.db,self.ws.id,'rollback-red')
            evaluate_submit_verdict(self.db,'real-red',{'email':'bad@example.test','webdriver':True,'time_on_page_ms':1},'127.0.0.1',commit=False)
            self.db.rollback()
        self.assertEqual(self.ws.leads_this_month,before)
        self.assertEqual(self.db.query(AdGuardVerdict).count(),0)
    def test_otp_no_universal_code_replay_or_cross_workspace(self):
        session=AdGuardSession(session_uuid='otp-test',adguard_account_id=self.ws.id)
        self.db.add(session); self.db.flush()
        lead=AdGuardLead(session_id=session.id,adguard_account_id=self.ws.id,verdict='grey')
        self.db.add(lead); self.db.flush()
        verdict=AdGuardVerdict(session_id=session.id,session_uuid=session.session_uuid,adguard_account_id=self.ws.id,otp_code='654321',otp_status='sent',otp_expires_at=datetime.utcnow()+timedelta(minutes=5),verdict='grey')
        usage=AdGuardAuditUsage(workspace_id=self.ws.id,event_key='otp-test',period_start=self.ws.leads_month_reset,lead_id=lead.id)
        self.db.add_all([verdict,usage]); self.db.commit()
        data={'session_uuid':session.session_uuid,'adguard_account_id':self.ws.id,'installation_token':operations(self.db,self.ws).tag_token,'otp_code':'123456'}
        self.assertEqual(self.client.post('/api/v1/tag/otp',json=data).status_code,400)
        data['otp_code']='654321'
        with patch('backend.routes.adguard_v1.deliver_lead',return_value={'status':'skipped'}),patch('backend.routes.adguard_v1.enqueue_bot_call'):
            self.assertEqual(self.client.post('/api/v1/tag/otp',json=data).status_code,200)
            self.assertEqual(self.client.post('/api/v1/tag/otp',json=data).status_code,400)
    def test_configurable_response_targets(self):
        values={'trial':36,'starter':12,'pro':2,'agency':1,'custom':8}
        r=self.client.put('/api/adguard/support/admin/sla',json={'hours_by_plan':values})
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.get('/api/adguard/support/config')
        self.assertEqual(r.json()['sla_by_plan']['starter'],'12h response target')
    def test_customer_cannot_retry_another_subscribers_crm(self):
        lead=AdGuardLead(adguard_account_id=self.ws.id,verdict='verified',lsq_status='failed')
        self.db.add(lead); self.db.commit(); self.current=self.other
        self.assertEqual(self.client.post(f'/api/adguard/leads/{lead.id}/retry-lsq').status_code,404)

    def test_admin_instant_onboarding_sets_plan_clock(self):
        r=self.client.post('/api/adguard/admin/create-subscriber',json={'email':'new@test.local','full_name':'New Subscriber','plan':'pro','mode':'instant'})
        self.assertEqual(r.status_code,200,r.text)
        ws=self.db.get(AdGuardAccount,r.json()['workspace_id'])
        self.assertEqual(ws.lead_quota,5000)
        self.assertIsNotNone(ws.leads_month_reset); self.assertIsNotNone(ws.plan_expires_at)
        self.assertEqual(ws.call_credits_remaining,0)  # Calling remains deferred.
    def test_pending_activation_starts_clock_when_activated(self):
        from backend.services.adguard_subscription import activate_workspace
        self.owner.onboarding_completed=False;self.owner.is_active=False
        self.db.delete(operations(self.db,self.ws));self.ws.leads_month_reset=None;self.ws.plan_expires_at=None;self.db.commit()
        allowance(self.db,self.ws,datetime(2026,1,1));self.db.commit()
        self.assertIsNone(operations(self.db,self.ws).activated_at)
        self.owner.onboarding_completed=True;self.owner.is_active=True
        activate_workspace(self.ws,datetime(2026,2,10),db=self.db)
        self.assertEqual(self.ws.leads_month_reset,datetime(2026,2,10))
        self.assertEqual(self.ws.plan_expires_at,datetime(2026,3,10))
    def test_simultaneous_requests_cannot_exceed_allowance(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            engine=create_engine('sqlite:///' + os.path.join(folder,'concurrent.db'),connect_args={'timeout':30})
            Base.metadata.create_all(engine); Session=sessionmaker(bind=engine)
            with Session() as db:
                owner=User(email='parallel@test.local',hashed_password='test',is_active=True,onboarding_completed=True)
                ws=AdGuardAccount(owner_email=owner.email,plan='starter',lead_quota=2,leads_this_month=0,leads_month_reset=datetime.utcnow(),account_status='active')
                db.add_all([owner,ws]);db.flush();operations(db,ws);db.commit();workspace_id=ws.id
            def audit(index):
                with Session() as db:
                    try:
                        reserve_audit(db,workspace_id,f'parallel-{index}');db.commit();return True
                    except WorkspaceUnavailable:
                        db.rollback();return False
            with ThreadPoolExecutor(max_workers=6) as pool:
                results=list(pool.map(audit,range(6)))
            self.assertEqual(sum(results),2)
            with Session() as db:
                self.assertEqual(db.get(AdGuardAccount,workspace_id).leads_this_month,2)
            engine.dispose()

    def test_google_webhook_requires_specific_workspace_key(self):
        from backend.routes import adguard
        secret=operations(self.db,self.ws).webhook_secret
        payload={'lead_id':'google-1','google_key':secret,'user_column_data':[{'column_name':'Email','string_value':'lead@test.local'}]}
        jobs=[]
        class DeferredThread:
            def __init__(self,target,**kwargs): jobs.append(target)
            def start(self): pass
        with patch('threading.Thread',DeferredThread), patch('backend.services.adguard.process_incoming_lead') as process:
            r=self.client.post(f'/api/adguard/webhook?workspace_id={self.ws.id}',json=payload)
            self.assertEqual(r.status_code,200,r.text)
            jobs[0]()
            self.assertEqual(process.call_args.kwargs['workspace_id'],self.ws.id)
            self.assertNotIn(secret,process.call_args.kwargs['raw_payload'])
            r=self.client.post(f'/api/adguard/webhook?workspace_id={self.ws2.id}',json=payload)
            self.assertEqual(r.status_code,403,r.text)
    def test_provider_retries_do_not_double_charge_audits(self):
        from backend.services.adguard import process_incoming_lead
        score={'verdict':'verified','integrity_score':90,'flags':[],'email_valid':True,'disposable_email':False,
               'phone_valid':True,'geo_match':True,'ai_legitimacy_score':90,'ai_reason':'test'}
        with patch('backend.db.database.SessionLocal',self.Session),patch('backend.services.adguard.score_lead',return_value=score):
            one=process_incoming_lead({'lead_id':'provider-1','email':'lead@test.local'},workspace_id=self.ws.id)
            two=process_incoming_lead({'lead_id':'provider-1','email':'lead@test.local'},workspace_id=self.ws.id)
        self.assertEqual(one['id'],two['id']);self.db.expire_all();self.assertEqual(self.ws.leads_this_month,1)

    def test_dormant_call_callback_cannot_be_forged(self):
        lead=AdGuardLead(adguard_account_id=self.ws.id,verdict='green')
        self.db.add(lead);self.db.commit()
        r=self.client.post('/api/v1/webhooks/botcaller',json={'lead_id':lead.id,'outcome':'verified'})
        self.assertEqual(r.status_code,403,r.text)
        self.assertNotEqual(lead.stage,'verified')

if __name__=='__main__': unittest.main()
