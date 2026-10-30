"""Exercise Bhago routes against an isolated database; no production connections."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
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

    def test_six_links_persist_and_save_independently(self):
        sources = ['swiggy', 'zomato', 'qwqer', 'payment_reminder', 'dialed', 'test']
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
            self.assertEqual(db.query(AppSetting).count(), 6)

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


if __name__ == '__main__':
    unittest.main()
