"""User edits persist access changes without requiring a password or production DB."""
import ast
from pathlib import Path
from typing import Optional, List
import unittest
from unittest.mock import MagicMock
from types import SimpleNamespace
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

Base=declarative_base()
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True)
    email=Column(String); full_name=Column(String); mobile=Column(String)
    role=Column(String); rev_role=Column(String); is_active=Column(Boolean)
    access_adpulse=Column(Boolean); access_insightdesk=Column(Boolean)
    access_revenueops=Column(Boolean); access_audit_review=Column(Boolean)
    access_adguard=Column(Boolean); hashed_password=Column(String)
    def to_dict(self): return {c.name:getattr(self,c.name) for c in self.__table__.columns}
ns=dict(BaseModel=BaseModel, Optional=Optional,List=List,User=User,
        HTTPException=HTTPException, log_activity=MagicMock(),_sync_account_assignments=MagicMock())
source=Path(__file__).resolve().parents[1]/'backend/routes/auth.py'
for node in ast.parse(source.read_text(encoding='utf-8')).body:
    if isinstance(node,ast.ClassDef) and node.name=='UserUpdateRequest':
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
    if isinstance(node,ast.FunctionDef) and node.name=='update_user':
        node.decorator_list=[]; node.args.defaults=[]; node.returns=None
        for arg in node.args.args: arg.annotation=None
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
class UserEditTests(unittest.TestCase):
    def setUp(self):
        engine=create_engine('sqlite:///:memory:'); Base.metadata.create_all(engine)
        self.db=sessionmaker(bind=engine)()
        self.user=User(id=2,email='user@example.test',full_name='User',role='user',is_active=True,
                       access_insightdesk=False,access_adpulse=True,hashed_password='unchanged')
        self.db.add(self.user);self.db.commit()
        self.admin=SimpleNamespace(id=1,email='admin@example.test',full_name='Admin',role='admin')
    def tearDown(self): self.db.close()
    def edit(self,**values): return ns['update_user'](2,ns['UserUpdateRequest'](**values),self.db,self.admin)
    def test_grant_access_persists(self):
        result=self.edit(access_insightdesk=True,access_revenueops=True)
        self.db.expire_all()
        self.assertTrue(self.db.get(User,2).access_insightdesk)
        self.assertTrue(result['access_revenueops'])
        self.assertEqual(self.db.get(User,2).hashed_password,'unchanged')
    def test_revoke_access_and_preserve_unspecified(self):
        self.edit(access_adpulse=False)
        self.assertFalse(self.db.get(User,2).access_adpulse)
        self.assertEqual(self.db.get(User,2).email,'user@example.test')
    def test_promotion_to_superadmin_blocked(self):
        with self.assertRaises(HTTPException) as err: self.edit(role='superadmin')
        self.assertEqual(err.exception.status_code,403)
    def test_assignments(self):
        self.edit(assigned_account_ids=[3,4])
        ns['_sync_account_assignments'].assert_called_with(self.user,[3,4],self.db)
if __name__=='__main__': unittest.main()
