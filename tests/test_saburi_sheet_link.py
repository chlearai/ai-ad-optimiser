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


class SaburiSheetTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([Account(id=1, name='Saburi Plywood'), Account(id=2, name='Bhago Mobility')])
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
        tree = ast.parse(Path('backend/routes/saburi.py').read_text())
        names = {'_sheet_access', 'SaburiSheetLink', 'get_sheet_link', 'save_sheet_link'}
        tree.body = [n for n in tree.body if
            (isinstance(n, (ast.Import, ast.ImportFrom)) and not (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend.')))
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'router' for t in n.targets))
            or (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)]
        exec(compile(tree, 'saburi_routes', 'exec'), ns)
        app = FastAPI(); app.include_router(ns['router']); self.client = TestClient(app)

    def test_save_reload_replace_and_clear(self):
        root = '/api/saburi/1/sheet'
        self.assertEqual(self.client.get(root).json(), {'url': ''})
        for url in ('https://docs.google.com/spreadsheets/d/abc/edit#gid=255017501', 'https://docs.google.com/spreadsheets/d/replacement/edit', ''):
            response = self.client.put(root, json={'url': url})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.get(root).json()['url'], url)
        with self.Session() as db:
            self.assertEqual(db.query(AppSetting).count(), 1)

    def test_scope_and_permissions(self):
        self.assertEqual(self.client.get('/api/saburi/2/sheet').status_code, 404)
        self.assertEqual(self.client.put('/api/saburi/2/sheet', json={'url': ''}).status_code, 404)
        self.user.role = 'user'
        self.assertEqual(self.client.get('/api/saburi/1/sheet').status_code, 403)
        with self.Session() as db:
            db.add(UserAccountAssignment(user_id=10, account_id=1)); db.commit()
        self.assertEqual(self.client.get('/api/saburi/1/sheet').status_code, 200)
        self.user.access_insightdesk = False
        self.assertEqual(self.client.put('/api/saburi/1/sheet', json={'url': ''}).status_code, 403)

    def test_bad_urls_do_not_save(self):
        for url in ('javascript:alert(1)', 'https://docs.google.com.evil.test/spreadsheets/d/abc', 'https://user@docs.google.com/spreadsheets/d/abc', 'https://docs.google.com/document/d/abc'):
            self.assertEqual(self.client.put('/api/saburi/1/sheet', json={'url': url}).status_code, 400)
        with self.Session() as db:
            self.assertEqual(db.query(AppSetting).count(), 0)

if __name__ == '__main__': unittest.main()
