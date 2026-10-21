"""Verified funds are distinct from billing debt and carry their actual age."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

tree = ast.parse(Path('backend/routes/tlg.py').read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'funds_snapshot_payload')
ns = dict(datetime=datetime, timedelta=timedelta, timezone=timezone, META_AD_ACCOUNT_ID='929681660424354')
exec(compile(ast.Module(body=[node], type_ignores=[]), 'funds_snapshot', 'exec'), ns)


class FundsTests(unittest.TestCase):
    def test_spendable_funds_are_not_gross_funds(self):
        result = ns['funds_snapshot_payload'](981098, 838096, datetime.now(timezone.utc))
        self.assertEqual(result['available'], 8380.96)
        self.assertEqual(result['funds'], 9810.98)
        self.assertEqual(result['pending'], 1430.02)
        self.assertFalse(result['stale'])

    def test_old_reading_is_marked_stale(self):
        checked = datetime.now(timezone.utc) - timedelta(hours=25)
        result = ns['funds_snapshot_payload'](981098, 838096, checked.replace(tzinfo=None))
        self.assertTrue(result['stale'])
        self.assertEqual(datetime.fromisoformat(result['checked_at']), checked)

    def test_zero_available_funds_remains_zero(self):
        self.assertEqual(ns['funds_snapshot_payload'](10000, 0, datetime.now(timezone.utc))['available'], 0)
