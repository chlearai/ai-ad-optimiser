"""Report count rules: sheet totals, date range and unavailable data."""
import ast
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

tree = ast.parse(Path('backend/routes/tlg.py').read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'report_lead_count')
ns = dict(json=json, datetime=datetime, timedelta=timedelta)
exec(compile(ast.Module(body=[node], type_ignores=[]), 'sheet_counts', 'exec'), ns)

class CountTests(unittest.TestCase):
    def report(self, **kw):
        values = dict(source_url='master', updated_at=datetime.utcnow(), daily_counts=json.dumps({'2026-10-01':3,'2026-10-02':2,'2026-10-09':4}))
        values.update(kw)
        return SimpleNamespace(**values)

    def test_selected_dates_only(self):
        self.assertEqual(ns['report_lead_count'](self.report(), 'master', date(2026,10,1), date(2026,10,2)), 5)

    def test_missing_or_stale_counts_are_not_zero(self):
        for report in [None, self.report(updated_at=None), self.report(updated_at=datetime.utcnow()-timedelta(minutes=16)), self.report(source_url='old')]:
            self.assertIsNone(ns['report_lead_count'](report, 'master', date(2026,10,1), date(2026,10,2)))

    def test_connected_sheet_without_leads_returns_zero(self):
        self.assertEqual(ns['report_lead_count'](self.report(daily_counts='{}'), 'master', date(2026,10,1), date(2026,10,2)), 0)

if __name__ == '__main__': unittest.main()
