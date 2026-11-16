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


class CrashClubSheetTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([Account(id=1, name='crash club'), Account(id=2, name='Bhago Mobility')])
            db.commit()
        self.user = SimpleNamespace(id=10, role='admin', access_insightdesk=True)
        def get_db():
            with self.Session() as db: yield db
        def current_user(): return self.user
        ns = dict(get_db=get_db, get_current_user_required=current_user, Account=Account,
                  AppSetting=AppSetting, UserAccountAssignment=UserAccountAssignment)
        # Use the production URL validator and sheet routes without integration startup.
        tree = ast.parse(Path('backend/routes/bhago.py').read_text())
        tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))
                     and not (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend.'))
                     and not (isinstance(n, ast.FunctionDef) and n.name != 'validate_sheet_url')]
        exec(compile(tree, 'validator', 'exec'), ns)
        tree = ast.parse(Path('backend/routes/crashclub.py').read_text())
        names = {'_sheet_access', 'CrashClubSheetLink', 'get_sheet_links', 'save_sheet_link'}
        tree.body = [n for n in tree.body if
            (isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend.')))
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'router' for t in n.targets))
            or (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)]
        exec(compile(tree, 'crashclub_routes', 'exec'), ns)
        app = FastAPI(); app.include_router(ns['router']); self.client = TestClient(app)

    def test_independent_links_persist_replace_and_clear(self):
        root='/api/crashclub/ui/1/sheets'
        self.assertEqual(self.client.get(root).json()['sheets'], {'goa':'','mw':''})
        for source in ('goa','mw'):
            url='https://docs.google.com/spreadsheets/d/'+source+'/edit#gid=123'
            self.assertEqual(self.client.put(root+'/'+source,json={'url':url}).status_code,200)
        self.client.put(root+'/goa',json={'url':''})
        saved=self.client.get(root).json()['sheets']
        self.assertEqual(saved['goa'],''); self.assertIn('/mw/',saved['mw'])
        with self.Session() as db: self.assertEqual(db.query(AppSetting).count(),2)

    def test_account_and_user_scope(self):
        self.assertEqual(self.client.get('/api/crashclub/ui/2/sheets').status_code,404)
        self.assertEqual(self.client.put('/api/crashclub/ui/2/sheets/goa',json={'url':''}).status_code,404)
        self.user.role='user'
        self.assertEqual(self.client.get('/api/crashclub/ui/1/sheets').status_code,403)
        with self.Session() as db:
            db.add(UserAccountAssignment(user_id=10,account_id=1)); db.commit()
        self.assertEqual(self.client.get('/api/crashclub/ui/1/sheets').status_code,200)
        self.user.access_insightdesk=False
        self.assertEqual(self.client.put('/api/crashclub/ui/1/sheets/goa',json={'url':''}).status_code,403)

    def test_invalid_links_and_unknown_slot_rejected(self):
        root='/api/crashclub/ui/1/sheets/'
        for url in ('javascript:alert(1)','https://docs.google.com.evil.test/spreadsheets/d/abc','https://user@docs.google.com/spreadsheets/d/abc','https://docs.google.com/document/d/abc'):
            self.assertEqual(self.client.put(root+'goa',json={'url':url}).status_code,400)
        self.assertEqual(self.client.put(root+'other',json={'url':''}).status_code,422)
        with self.Session() as db: self.assertEqual(db.query(AppSetting).count(),0)

if __name__ == '__main__': unittest.main()
