import unittest
from datetime import date

from backend.services.sakha.client import SampleSakhaaClient, as_utc
from backend.services.sakha.issues import evaluate
from backend.services.sakha.metrics import DEFAULT_TARGETS, calculate, work_minutes


NOW = as_utc("2026-11-25T10:00:00Z")
FROM = date(2026, 11, 1)
TO = date(2026, 11, 25)
MAP = {"qualified": "Lead Qualified", "visit_scheduled": "Site Visit Scheduled",
       "visit_completed": "Site Visit Completed", "won": "Booking Confirmed"}


def snapshot():
    sample = SampleSakhaaClient(NOW)
    return {key: sample.page(key)["data"] for key in (
        "pipelines", "users", "persons", "deals", "deal-stage-events", "activities", "tasks",
    )} | sample.page("health")


class SakhaMetricsTests(unittest.TestCase):
    def test_mantri_funnel_conversion_and_working_time_from_fixtures(self):
        report = calculate(snapshot(), FROM, TO, NOW, MAP, DEFAULT_TARGETS)
        self.assertEqual(report["headline"], {"leads": 50, "contacted": 32, "qualified": 44,
                         "site_visits_completed": 19, "bookings": 4, "conversion_pct": 8.0})
        stages = report["funnel"][0]["stages"]
        self.assertEqual([stage["count"] for stage in stages], [44, 23, 19, 7, 6, 5, 4, 2, 1, 0, 3])
        self.assertEqual(stages[1]["drop_off"], 21)
        self.assertEqual(report["speed"]["median_minutes"], 23)
        self.assertEqual(report["speed"]["p90_minutes"], 35)
        self.assertTrue(any(booking["value"] is None for booking in report["bookings"]))
        self.assertEqual(sum(row["count"] for row in report["lost_reasons"]), 3)
        self.assertGreater(report["nurture"]["ai_calls_connected"], report["nurture"]["ai_calls_qualified"])
        self.assertEqual(work_minutes("2026-11-24T12:20:00Z", "2026-11-25T03:40:00Z", DEFAULT_TARGETS), 20)

    def test_owner_date_and_stage_kind_fallback(self):
        rows = snapshot()
        filtered = calculate(rows, TO, TO, NOW, MAP, DEFAULT_TARGETS, owner="owner_1")
        self.assertEqual(filtered["headline"]["leads"], 2)
        self.assertEqual(filtered["headline"]["bookings"], 0)
        self.assertEqual(calculate(rows, FROM, TO, NOW, {}, DEFAULT_TARGETS)["headline"]["bookings"], 0)
        deal = next(row for row in rows["deals"] if row["id"] == "deal_38")
        rows["deal-stage-events"].append({"deal_id": deal["id"], "to_stage_id": "st_10",
                                          "changed_at": "2026-11-24T05:00:00Z"})
        self.assertEqual(calculate(rows, FROM, TO, NOW, {}, DEFAULT_TARGETS)["headline"]["bookings"], 1)
        self.assertEqual(calculate(rows, FROM, TO, NOW, MAP, DEFAULT_TARGETS)["headline"]["bookings"], 4)

    def test_health_rules_detect_pause_and_staleness_independently(self):
        rows = snapshot()
        rows["integrations"][0]["status"] = "active"
        found = {issue["code"]: issue for issue in evaluate(rows, NOW, MAP, DEFAULT_TARGETS, NOW, False)}
        self.assertEqual(found["source:lc_1"]["severity"], "red")
        self.assertIn("90", found["source:lc_1"]["detail"])
        self.assertIn("7 day", found["integration:Samvaad AI Voice"]["detail"])
        self.assertIn("sync_failed", found)
        self.assertIn("overdue_tasks", found)
        self.assertIn("booking_value", found)


if __name__ == "__main__":
    unittest.main()
