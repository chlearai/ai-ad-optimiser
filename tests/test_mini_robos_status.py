from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from backend.services.mini_robos import SABURI_SCHEDULE, assess, expected_slots, timeline


NOW = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
DAY = timedelta(days=1)


def agent(**fields):
    return SimpleNamespace(**({
        "paused": False, "grace_minutes": 15, "schedule": SABURI_SCHEDULE,
        "created_at": NOW - 15 * DAY, "expects_output": True,
    } | fields))


def run(started, *, slot=None, status="succeeded", output=1):
    return SimpleNamespace(started_at=started, slot=slot, status=status, output_count=output,
                           error=None, finished_at=None if status == "running" else started + MINUTE)


class StatusTests(unittest.TestCase):
    def test_timed_ist_slots_and_status_transitions(self):
        slots = expected_slots(SABURI_SCHEDULE, NOW - DAY, NOW)
        self.assertEqual([slot.strftime("%H:%M") for slot in slots], ["11:45", "03:45", "07:45"])
        runs = [run(slot, slot=slot) for slot in reversed(slots)]
        self.assertEqual(assess(agent(), runs, NOW), ("on_track", set()))
        self.assertEqual(assess(agent(), runs[1:], NOW), ("late", set()))
        self.assertEqual(assess(agent(), runs[1:], NOW + MINUTE), ("missed", {("missed", slots[-1])}))
        delayed = NOW + MINUTE
        self.assertEqual(assess(agent(), [run(delayed, slot=slots[-1]), *runs[1:]], delayed), ("late", set()))
        self.assertEqual(len(timeline(agent(), runs, NOW)), 14)

    def test_interval_stale_failure_zero_output_pause_and_no_signal(self):
        interval = agent(schedule={"kind": "interval", "minutes": 5})
        recent = run(NOW - 10 * MINUTE)
        stale = run(NOW - 26 * MINUTE)
        self.assertEqual(assess(interval, [recent], NOW), ("on_track", set()))
        self.assertEqual(assess(interval, [stale], NOW), ("late", {("stale", None)}))
        for failed in (run(NOW - MINUTE, output=0), run(NOW - MINUTE, status="failed")):
            self.assertEqual(assess(interval, [failed], NOW), ("failing", {("failed", None)}))
        interval.expects_output = False
        self.assertEqual(assess(interval, [run(NOW - MINUTE, output=0)], NOW), ("on_track", set()))
        self.assertEqual(assess(interval, [], NOW), ("no_signal", set()))
        self.assertEqual(assess(interval, [run(NOW - 8 * DAY)], NOW), ("no_signal", set()))
        self.assertEqual(assess(agent(schedule=None), [], NOW), ("no_signal", set()))
        interval.paused = True
        self.assertEqual(assess(interval, [stale], NOW), ("paused", set()))
