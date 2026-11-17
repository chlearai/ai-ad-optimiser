"""Exercise saved slots with an isolated in-memory database."""
import ast
import json
import urllib.parse
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, Session

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


names = {'EduInfraSheet', '_eduinfra_access', 'get_eduinfra_sheets', 'save_eduinfra_sheet'}
tree = ast.parse((Path(__file__).parents[1] / 'backend/routes/accounts.py').read_text(encoding='utf-8'))
nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
for node in nodes:
    node.decorator_list = []
ns = dict(globals(), get_db=lambda: None, get_current_user_required=lambda: None,
          User=object, _filter_accounts_for_user=lambda query, user: query.filter(Account.id.in_(user.accounts)))
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'eduinfra', 'exec'), ns)


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Account(id=1, name='EduInfra Expand'), Account(id=2, name='Other client')])
        self.db.commit()
        self.user = SimpleNamespace(role='user', access_insightdesk=True, accounts=[1, 2])

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def save(self, slot, url, name='Leads', account=1):
        return ns['save_eduinfra_sheet'](account, slot, ns['EduInfraSheet'](url=url, name=name), self.db, self.user)

    def test_persists_three_independent_slots_and_clear(self):
        self.assertEqual(len(ns['get_eduinfra_sheets'](1, self.db, self.user)), 3)
        for slot in (1, 2, 3):
            self.save(slot, f'https://docs.google.com/spreadsheets/d/sheet{slot}/edit#gid=3')
        self.db.close()
        self.db = Session(self.engine)
        saved = ns['get_eduinfra_sheets'](1, self.db, self.user)
        self.assertIn('sheet2', saved[1]['url'])
        self.save(2, '')
        saved = ns['get_eduinfra_sheets'](1, self.db, self.user)
        self.assertEqual(saved[1]['url'], '')
        self.assertIn('sheet1', saved[0]['url'])
        self.assertIn('sheet3', saved[2]['url'])

    def test_invalid_links_and_slots_never_write(self):
        for url in ['javascript:alert(1)', 'https://docs.google.com.evil.test/spreadsheets/d/abc',
                    'http://docs.google.com/spreadsheets/d/abc', 'https://evil@docs.google.com/spreadsheets/d/abc',
                    'https://docs.google.com/document/d/abc']:
            with self.assertRaises(HTTPException):
                self.save(1, url)
        with self.assertRaises(HTTPException):
            self.save(4, '')
        self.assertEqual(self.db.query(AppSetting).count(), 0)

    def test_access_restrictions(self):
        with self.assertRaises(HTTPException):
            self.save(1, '', account=2)
        self.user.accounts = []
        with self.assertRaises(HTTPException):
            self.save(1, '')
        self.user.accounts = [1]
        self.user.access_insightdesk = False
        with self.assertRaises(HTTPException):
            ns['get_eduinfra_sheets'](1, self.db, self.user)
        self.assertEqual(self.db.query(AppSetting).count(), 0)


if __name__ == '__main__':
    unittest.main()
