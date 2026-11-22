"""Counts persistence and access checks against isolated SQLite only."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta, timezone
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

class CountsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([Account(id=1, name='Mantri Webcity'), Account(id=2, name='Mantri Developers')]); db.commit()
        self.user = SimpleNamespace(id=10, role='admin', access_insightdesk=True)
        def get_db():
            with self.Session() as db: yield db
        def current_user(): return self.user
        ns = dict(get_db=get_db, get_current_user_required=current_user, Account=Account, AppSetting=AppSetting, UserAccountAssignment=UserAccountAssignment)
        tree = ast.parse(Path('backend/routes/mantri_webcity_mis.py').read_text())
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend.'))]
        exec(compile(tree, 'mantri_routes', 'exec'), ns)
        self.ns = ns
        app = FastAPI(); app.include_router(ns['router']); self.client = TestClient(app)

    def test_baseline_and_missing_days(self):
        report = self.ns['build_report']({}, datetime(2026,11,22,6,tzinfo=timezone.utc))
        self.assertEqual(report['totals'], {'inbound':7, 'outbound':26, 'leads':31})
        self.assertEqual(len(report['rows']), 5)
        report = self.ns['build_report']({}, datetime(2026,11,23,6,tzinfo=timezone.utc))
        self.assertFalse(report['complete'])
        self.assertIsNone(report['rows'][-1]['leads'])
        self.assertIsNone(report['totals']['leads'])

    def test_save_reload_and_revise_one_day(self):
        root = '/api/mantri-webcity/1/daily-counts'
        body = {'date':'2026-11-21','inbound':0,'outbound':9,'leads':29,'checked_at':datetime.now(timezone.utc).isoformat()}
        self.assertEqual(self.client.put(root,json=body).status_code,200)
        body['leads'] = 30
        self.assertEqual(self.client.put(root,json=body).status_code,200)
        report = self.client.get(root).json()
        self.assertEqual(next(r for r in report['rows'] if r['date']=='2026-11-21')['leads'],30)
        with self.Session() as db: self.assertEqual(db.query(AppSetting).count(),1)
        body['checked_at'] = (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
        self.assertEqual(self.client.put(root,json=body).status_code,409)

    def test_invalid_counts_and_scope(self):
        root = '/api/mantri-webcity/1/daily-counts'
        body={'date':'2026-11-21','inbound':-1,'outbound':9,'leads':29,'checked_at':datetime.now(timezone.utc).isoformat()}
        self.assertEqual(self.client.put(root,json=body).status_code,422)
        body['inbound']=0; body['date']='2099-01-01'
        self.assertEqual(self.client.put(root,json=body).status_code,400)
        self.assertEqual(self.client.get('/api/mantri-webcity/2/daily-counts').status_code,404)
        self.user.role='user'
        self.assertEqual(self.client.get(root).status_code,403)
        with self.Session() as db: db.add(UserAccountAssignment(user_id=10,account_id=1)); db.commit()
        self.assertEqual(self.client.get(root).status_code,200)
        self.assertEqual(self.client.put(root,json=body).status_code,403)

    def test_selected_range_and_excel_match(self):
        from io import BytesIO
        from openpyxl import load_workbook
        root = '/api/mantri-webcity/1/daily-counts'
        query = '?start=2026-11-19&end=2026-11-21'
        report = self.client.get(root+query).json()
        self.assertEqual(len(report['rows']),3)
        self.assertEqual(report['totals'],{'inbound':3,'outbound':24,'leads':29})
        export = self.client.get(root+'/export'+query)
        self.assertEqual(export.status_code,200)
        sheet = load_workbook(BytesIO(export.content)).active
        self.assertEqual(sheet['A3'].value.date().isoformat(),'2026-11-19')
        self.assertEqual(sheet['A5'].value.date().isoformat(),'2026-11-21')
        self.assertEqual([sheet.cell(6,col).value for col in range(1,5)],['Total',3,24,29])
        self.assertEqual(sheet['A3'].number_format,'DD-MM-YYYY')
        for query in ('?start=2026-11-19','?start=2026-11-21&end=2026-11-19','?start=2026-11-01&end=2026-11-21'):
            self.assertEqual(self.client.get(root+query).status_code,400)
            self.assertEqual(self.client.get(root+'/export'+query).status_code,400)
        self.user.role='user'
        self.assertEqual(self.client.get(root+'/export').status_code,403)

if __name__ == '__main__': unittest.main()
