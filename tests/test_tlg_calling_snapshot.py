import unittest
from datetime import datetime,timezone,date
from backend.services.tlg_calling_analysis import validate_snapshot,aggregate_snapshot,METRICS

class SnapshotTests(unittest.TestCase):
    def fixture(self):
        m=dict.fromkeys(METRICS,0);m.update(leads=1,called=1,not_contactable=1)
        return {'version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'centres':[{'name':'Jayanagar','daily':[{'date':'2026-10-29','metrics':m,'junk_reasons':{},'invalid_numbers':1}]}]}
    def test_valid_snapshot_and_custom_range(self):
        s=validate_snapshot(self.fixture())
        result=aggregate_snapshot(s,date(2026,10,29),date(2026,10,29))
        self.assertEqual(result['total']['not_contactable'],1)
        self.assertEqual(result['total']['contacted'],0)
        self.assertEqual(aggregate_snapshot(s,date(2026,10,1),date(2026,10,28))['total']['leads'],0)
    def test_bad_reconciliation_rejected(self):
        s=self.fixture();s['centres'][0]['daily'][0]['metrics']['contacted']=1
        with self.assertRaises(ValueError):validate_snapshot(s)
    def test_junk_reasons_must_reconcile_and_be_aggregate(self):
        s=self.fixture();s['centres'][0]['daily'][0]['junk_reasons']={'Private free text':1}
        with self.assertRaises(ValueError):validate_snapshot(s)
    def test_unknown_date_kept_consistently(self):
        s=self.fixture();s['centres'][0]['daily'][0]['date']='unknown'
        self.assertEqual(aggregate_snapshot(s,date(2026,10,1),date(2026,10,2))['rows'][0]['unknown_dates'],1)

if __name__=='__main__':unittest.main()

