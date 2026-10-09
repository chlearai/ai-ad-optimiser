import ast
from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace as Obj
from unittest.mock import MagicMock
import unittest
from fastapi import HTTPException

ns=dict(datetime=datetime,timedelta=timedelta,HTTPException=HTTPException, User=MagicMock(),
        get_password_hash=lambda p:'hashed:'+p, PASSWORD_MIN_LENGTH=8)
source=Path('backend/routes/auth.py')
for node in ast.parse(source.read_text(encoding='utf-8')).body:
    if isinstance(node,ast.FunctionDef) and node.name in ('set_onboarding_password','_validate_strong_password'):
        node.decorator_list=[]; node.args.defaults=[]; node.returns=None
        for arg in node.args.args: arg.annotation=None
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
class ResetTests(unittest.TestCase):
    def setUp(self):
        self.user=Obj(is_active=True,onboarding_completed=True,hashed_password='original',onboarding_token='token',onboarding_token_expires_at=datetime.utcnow()+timedelta(hours=1))
        self.db=MagicMock(); self.db.query.return_value.filter.return_value.first.return_value=self.user
    def reset(self,password='Strong!234'):
        return ns['set_onboarding_password']('token',Obj(password=password),self.db)
    def test_success(self):
        self.assertTrue(self.reset()['password_reset'])
        self.assertIsNone(self.user.onboarding_token)
        self.assertEqual(self.user.hashed_password,'hashed:Strong!234')
        self.db.commit.assert_called_once()
    def test_expired(self):
        self.user.onboarding_token_expires_at=datetime.utcnow()-timedelta(seconds=1)
        with self.assertRaises(HTTPException): self.reset()
        self.db.commit.assert_not_called()
    def test_weak(self):
        with self.assertRaises(HTTPException): self.reset('weak')
        self.db.commit.assert_not_called()
    def test_inactive(self):
        self.user.is_active=False
        with self.assertRaises(HTTPException): self.reset()
        self.assertEqual(self.user.hashed_password,'original')
    def test_consumed_token(self):
        self.db.query.return_value.filter.return_value.first.return_value=None
        with self.assertRaises(HTTPException): self.reset()
if __name__=='__main__': unittest.main()

