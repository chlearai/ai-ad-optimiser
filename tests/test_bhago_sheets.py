"""Exercise Bhago routes against an isolated database; no production connections."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
import json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()


class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String)


class AppSetting(Base):
    __tablename__ = 'app_settings'
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    value = Column(Text)


class UserAccountAssignment(Base):
    __tablename__ = 'user_account_assignments'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    account_id = Column(Integer)


class SheetTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([Account(id=1, name='Bhago Mobility'), Account(id=2, name='The Little Gym')])
            db.commit()
        self.user = SimpleNamespace(id=10, role='admin', access_insightdesk=True)

        def get_db():
            with self.Session() as db:
                yield db

        def current_user():
            return self.user

        # Load the exact route code without importing unrelated app integrations/auth startup.
        tree = ast.parse(Path('backend/routes/bhago.py').read_text())
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or not (n.module or '').startswith('backend.')]
        ns = dict(get_db=get_db, get_current_user_required=current_user, Account=Account,
                  AppSetting=AppSetting, UserAccountAssignment=UserAccountAssignment)
        exec(compile(tree, 'bhago_routes', 'exec'), ns)
        app = FastAPI()
        app.include_router(ns['router'])
        self.client = TestClient(app)

    def test_links_persist_and_save_independently(self):
        sources = ['swiggy', 'zomato', 'qwqer', 'payment_reminder', 'dialed', 'test', 'test_lead', 'test_payment']
        self.assertEqual(self.client.get('/api/bhago/1/sheets').json()['sheets'], dict.fromkeys(sources, ''))
        for source in sources:
            url = 'https://docs.google.com/spreadsheets/d/' + source + '/edit#gid=0'
            response = self.client.put('/api/bhago/1/sheets/' + source, json={'url': url})
            self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get('/api/bhago/1/sheets').json()['sheets']
        self.client.put('/api/bhago/1/sheets/swiggy', json={'url': 'https://docs.google.com/spreadsheets/d/replacement/edit'})
        reloaded = self.client.get('/api/bhago/1/sheets').json()['sheets']
        for source in sources[1:]:
            self.assertEqual(saved[source], reloaded[source])
        with self.Session() as db:
            self.assertEqual(db.query(AppSetting).count(), 8)

    def test_invalid_urls_and_sources_do_not_save(self):
        for url in ['javascript:alert(1)', 'https://docs.google.com.evil.test/spreadsheets/d/abc',
                    'https://user@docs.google.com/spreadsheets/d/abc', 'https://example.com/file',
                    'https://docs.google.com/document/d/abc']:
            self.assertEqual(self.client.put('/api/bhago/1/sheets/swiggy', json={'url': url}).status_code, 400)
        self.assertEqual(self.client.put('/api/bhago/1/sheets/other', json={'url': ''}).status_code, 422)
        with self.Session() as db:
            self.assertEqual(db.query(AppSetting).count(), 0)

    def test_other_accounts_and_unassigned_users_are_blocked(self):
        self.assertEqual(self.client.get('/api/bhago/2/sheets').status_code, 404)
        self.assertEqual(self.client.put('/api/bhago/2/sheets/swiggy', json={'url': ''}).status_code, 404)
        self.user.role = 'user'
        self.assertEqual(self.client.get('/api/bhago/1/sheets').status_code, 403)
        self.assertEqual(self.client.put('/api/bhago/1/sheets/swiggy', json={'url': ''}).status_code, 403)
        with self.Session() as db:
            db.add(UserAccountAssignment(user_id=10, account_id=1))
            db.commit()
        self.assertEqual(self.client.put('/api/bhago/1/sheets/swiggy', json={'url': ''}).status_code, 200)

    def ready_worker(self):
        with self.Session() as db:
            db.add(AppSetting(key='bhago:1:worker', value=json.dumps({'browser_ready': True, 'updated_at': datetime.now(timezone.utc).isoformat()})))
            db.commit()
        self.client.put('/api/bhago/1/sheets/test', json={'url':'https://docs.google.com/spreadsheets/d/current-test/edit'})

    def test_offline_and_wrong_account_cannot_queue_calls(self):
        body = {'source':'test','calling_type':'payment'}
        self.assertEqual(self.client.post('/api/bhago/1/run', json=body).status_code, 409)
        self.assertEqual(self.client.post('/api/bhago/2/run', json=body).status_code, 404)
        self.assertEqual(self.client.post('/api/bhago/1/run', json={'source':'dialed','calling_type':'lead'}).status_code, 422)

    def test_preview_must_be_ready_and_launch_is_idempotent(self):
        self.ready_worker()
        body = {'source':'test','calling_type':'payment'}
        queued = self.client.post('/api/bhago/1/run', json=body)
        self.assertEqual(queued.status_code, 200, queued.text)
        run = queued.json()['run']
        self.assertEqual(run['state'], 'queued_preview')
        self.assertEqual(self.client.post('/api/bhago/1/run', json=body).status_code, 409)
        endpoint = '/api/bhago/1/run/' + run['id'] + '/launch'
        self.assertEqual(self.client.post(endpoint).status_code, 409)
        with self.Session() as db:
            row = db.query(AppSetting).filter_by(key='bhago:1:run:current').first()
            run.update(state='ready',total=1,counts={})
            row.value = json.dumps(run)
            db.commit()
        self.assertEqual(self.client.post(endpoint).json()['run']['state'], 'queued_launch')
        self.assertEqual(self.client.post(endpoint).status_code, 409)
        self.assertEqual(self.client.get('/api/bhago/2/run').status_code, 404)

    def test_cancel_and_source_rules(self):
        self.ready_worker()
        self.assertEqual(self.client.post('/api/bhago/1/run',json={'source':'swiggy','calling_type':'payment'}).status_code,400)
        self.assertEqual(self.client.post('/api/bhago/1/run',json={'source':'swiggy','calling_type':'lead'}).status_code,400)
        run=self.client.post('/api/bhago/1/run',json={'source':'test','calling_type':'lead'}).json()['run']
        response=self.client.post('/api/bhago/1/run/'+run['id']+'/cancel')
        self.assertEqual(response.json()['run']['state'],'cancelled')
        self.assertEqual(self.client.post('/api/bhago/1/run/'+run['id']+'/launch').status_code,409)

    def test_separate_test_links_and_fixed_calling_types(self):
        self.ready_worker()
        for source in ('test_lead','test_payment'):
            url='https://docs.google.com/spreadsheets/d/'+source+'/edit'
            self.assertEqual(self.client.put('/api/bhago/1/sheets/'+source,json={'url':url}).status_code,200)
        saved=self.client.get('/api/bhago/1/sheets').json()['sheets']
        self.assertNotEqual(saved['test_lead'],saved['test_payment'])
        self.assertIn('current-test',saved['test'])
        for source,correct,wrong in [('test_lead','lead','payment'),('test_payment','payment','lead')]:
            self.assertEqual(self.client.post('/api/bhago/1/run',json={'source':source,'calling_type':wrong}).status_code,400)
            response=self.client.post('/api/bhago/1/run',json={'source':source,'calling_type':correct})
            self.assertEqual(response.status_code,200,response.text)
            run=response.json()['run']
            self.assertEqual(run['source'],source)
            self.assertEqual(self.client.post('/api/bhago/1/run/'+run['id']+'/cancel').status_code,200)


if __name__ == '__main__':
    unittest.main()
