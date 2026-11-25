"""Pure IST reporting calculations over a Sakhaa snapshot."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from math import ceil
from statistics import median

from backend.services.sakha.client import as_utc, iso


IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEFAULT_TARGETS = {"work_start_hour": 9, "work_end_hour": 18, "first_contact_minutes": 30}
TOUCH_TYPES = {"call", "whatsapp", "email"}


def day_window(start, end):
    return datetime.combine(start, time.min, IST), datetime.combine(end + timedelta(days=1), time.min, IST)


def in_period(value, window):
    timestamp = as_utc(value)
    return timestamp is not None and window[0] <= timestamp < window[1]


def working_hours(targets):
    return targets.get("work_start_hour", 9), targets.get("work_end_hour", 18)


def in_working_hours(now, targets):
    start, end = working_hours(targets)
    return start <= now.astimezone(IST).hour < end


def work_minutes(start, end, targets):
    start, end = as_utc(start).astimezone(IST), as_utc(end).astimezone(IST)
    if end <= start:
        return 0.0
    opening, closing = working_hours(targets)
    seconds_per_day = (closing - opening) * 3600

    def offset(value):
        midnight = datetime.combine(value.date(), time.min, IST)
        elapsed = (value - midnight).total_seconds()
        return min(seconds_per_day, max(0, elapsed - opening * 3600))

    return ((end.date() - start.date()).days * seconds_per_day + offset(end) - offset(start)) / 60


def outbound_touches(activities, persons):
    touches = {}
    for activity in activities:
        person_id = activity.get("person_id")
        if (person_id not in persons or activity.get("direction") != "outbound"
                or activity.get("type") not in TOUCH_TYPES):
            continue
        occurred = as_utc(activity.get("occurred_at"))
        created = as_utc(persons[person_id].get("created_at"))
        if occurred and created and occurred >= created and (person_id not in touches or occurred < touches[person_id]):
            touches[person_id] = occurred
    return touches


def next_tasks(tasks, now):
    future_deals, future_people, overdue = set(), set(), []
    for task in tasks:
        due = as_utc(task.get("due_at"))
        if task.get("status") != "open" or not due:
            continue
        if due < now:
            overdue.append(task)
        elif task.get("deal_id"):
            future_deals.add(task["deal_id"])
        elif task.get("person_id"):
            future_people.add(task["person_id"])
    return future_deals, future_people, overdue


def stage_index(pipelines):
    return {stage["id"]: stage for pipeline in pipelines for stage in pipeline.get("stages", [])}


def is_stage(stage, key, stage_map):
    mapped = stage_map.get(key)
    if mapped:
        return mapped in (stage.get("id"), stage.get("name"))
    return key in {"won", "lost"} and stage.get("kind") == key


def stage_movements(deals, events):
    movements = defaultdict(list)
    for event in events:
        if event.get("to_stage_id") and event.get("changed_at"):
            movements[event["deal_id"]].append((as_utc(event["changed_at"]), event["to_stage_id"]))
    for deal in deals:
        stages = movements[deal["id"]]
        current = deal.get("stage_id")
        if current and not stages:
            timestamp = as_utc(deal.get("updated_at") or deal.get("created_at"))
            if timestamp:
                stages.append((timestamp, current))
        stages.sort(key=lambda item: item[0])
    return movements


def first_milestone(deal, movements, stages, key, stage_map):
    dates = [when for when, stage_id in movements.get(deal["id"], [])
             if is_stage(stages.get(stage_id, {}), key, stage_map)]
    if dates:
        return min(dates)
    if key in {"won", "lost"} and deal.get("status") == key and not (key == "won" and stage_map.get(key)):
        return as_utc(deal.get(f"{key}_at"))
    return None


def current_open(deal, stages, movements, stage_map):
    return (deal.get("status") == "open"
            and not is_stage(stages.get(deal.get("stage_id"), {}), "lost", stage_map)
            and not first_milestone(deal, movements, stages, "won", stage_map))


def visit_results(deals, activities, movements, stages, stage_map, persons, window):
    rows = defaultdict(lambda: {"scheduled": 0, "completed": 0})
    visits = {"scheduled": set(), "completed": set()}
    seen = set()

    def record(kind, occurred, deal, person_id, actor_id=None):
        if not in_period(occurred, window):
            return
        day = as_utc(occurred).astimezone(IST).date().isoformat()
        deal_id = deal.get("id")
        person_id = person_id or deal.get("person_id")
        identity = ("person", person_id) if person_id else ("deal", deal_id)
        if identity[1] is None or (kind, *identity, day) in seen:
            return
        seen.add((kind, *identity, day))
        owner = deal.get("owner_user_id") or actor_id or persons.get(person_id, {}).get("owner_user_id")
        visits[kind].add(identity)
        rows[day, owner][kind] += 1

    by_id = {deal["id"]: deal for deal in deals}
    for deal in deals:
        for occurred, stage_id in movements.get(deal["id"], []):
            for kind in visits:
                if is_stage(stages.get(stage_id, {}), "visit_" + kind, stage_map):
                    record(kind, occurred, deal, deal.get("person_id"))
    for activity in activities:
        if activity.get("type") == "site_visit" and activity.get("outcome") in {"scheduled", "completed"}:
            record(activity["outcome"], activity.get("occurred_at"), by_id.get(activity.get("deal_id"), {}),
                   activity.get("person_id"), activity.get("user_id"))
    return {kind: len(ids) for kind, ids in visits.items()}, [
        {"date": day, "owner_user_id": owner, **counts}
        for (day, owner), counts in sorted(rows.items(), key=lambda item: (item[0][0], item[0][1] or ""))
    ]


def funnel(pipelines, deals, movements, window):
    reached = defaultdict(set)
    for deal in deals:
        for occurred, stage_id in movements.get(deal["id"], []):
            if in_period(occurred, window):
                reached[stage_id].add(deal["id"])
    result = []
    for pipeline in pipelines:
        previous = None
        rows = []
        for stage in sorted(pipeline.get("stages", []), key=lambda s: s["order"]):
            cohort = reached[stage["id"]]
            drop_off = len(previous - cohort) if previous is not None and stage["kind"] != "lost" else None
            rows.append({"stage": stage["name"], "count": len(cohort), "drop_off": drop_off})
            if stage["kind"] == "open":
                previous = cohort
        result.append({"pipeline": pipeline["name"], "stages": rows})
    return result


def nurture_activity(activities, window):
    counts = Counter({key: 0 for key in (
        "ai_calls_connected", "ai_calls_qualified", "whatsapp_sent", "whatsapp_read",
        "whatsapp_replied", "emails_sent", "emails_opened",
    )})
    for row in activities:
        if not in_period(row.get("occurred_at"), window):
            continue
        kind, outcome, direction = row["type"], row.get("outcome"), row.get("direction")
        if kind == "call" and row.get("actor") == "ai":
            counts["ai_calls_connected"] += outcome in {"answered", "connected", "qualified"}
            counts["ai_calls_qualified"] += outcome == "qualified"
        if kind == "whatsapp":
            counts["whatsapp_sent"] += direction == "outbound" and outcome in {"sent", "delivered", "read"}
            counts["whatsapp_read"] += direction == "outbound" and outcome == "read"
            counts["whatsapp_replied"] += direction == "inbound"
        if kind == "email":
            counts["emails_sent"] += direction == "outbound" and outcome in {"sent", "delivered", "open", "opened", "read"}
            counts["emails_opened"] += direction == "outbound" and outcome in {"open", "opened", "read"}
    return dict(counts)


def calculate(snapshot, start, end, now, stage_map, targets, owner=None):
    window = day_window(start, end)
    persons = {row["id"]: row for row in snapshot["persons"]}
    deals = [row for row in snapshot["deals"] if not owner or row.get("owner_user_id") == owner]
    deal_ids = {row["id"] for row in deals}
    owner_people = {row["id"] for row in persons.values() if not owner or row.get("owner_user_id") == owner}
    activities = [row for row in snapshot["activities"] if not owner or (
        row.get("deal_id") in deal_ids if row.get("deal_id") else row.get("person_id") in owner_people
    )]
    tasks = [row for row in snapshot["tasks"] if not owner or row.get("owner_user_id") == owner]
    cohort = {person_id for person_id in owner_people if in_period(persons[person_id].get("created_at"), window)}
    touches = outbound_touches(snapshot["activities"], persons)
    speed = sorted(work_minutes(persons[person_id]["created_at"], touches[person_id], targets)
                   for person_id in cohort & touches.keys() if touches[person_id] < window[1] and touches[person_id] <= now)
    target = targets.get("first_contact_minutes", DEFAULT_TARGETS["first_contact_minutes"])
    overdue_contact = sum(
        work_minutes(persons[person_id]["created_at"], min(touches.get(person_id, now), now), targets) > target
        for person_id in cohort
    )
    stages = stage_index(snapshot["pipelines"])
    movements = stage_movements(deals, snapshot["deal-stage-events"])
    future_deals, future_people, overdue = next_tasks(tasks, now)
    qualified, bookings, lost = set(), [], Counter()
    open_deals = [deal for deal in deals if current_open(deal, stages, movements, stage_map)]
    needs_next = [deal for deal in open_deals if (
        in_period(deal.get("created_at"), window)
        or any(in_period(when, window) for when, _ in movements.get(deal["id"], []))
    ) and deal["id"] not in future_deals and deal.get("person_id") not in future_people]
    active_deal_people = {deal.get("person_id") for deal in deals}
    bare_leads = sum(person_id not in active_deal_people and person_id not in future_people
                     and persons[person_id].get("lifecycle_status") != "dead" for person_id in cohort)
    for deal in deals:
        person_id = deal.get("person_id")
        if in_period(first_milestone(deal, movements, stages, "qualified", stage_map), window):
            qualified.add(person_id or deal["id"])
        booked = first_milestone(deal, movements, stages, "won", stage_map)
        if in_period(booked, window):
            bookings.append({
                "id": deal["id"], "person_id": person_id,
                "display_name": persons.get(person_id, {}).get("display_name"),
                "owner_user_id": deal.get("owner_user_id"), "booked_at": iso(booked),
                "value": deal.get("value"), "currency": deal.get("currency"),
            })
        lost_at = as_utc(deal.get("lost_at")) or first_milestone(deal, movements, stages, "lost", stage_map)
        if in_period(lost_at, window):
            lost[deal.get("lost_reason") or "Unspecified"] += 1
    visits, visit_rows = visit_results(deals, activities, movements, stages, stage_map, persons, window)
    sources = Counter((persons[pid].get("source") or "Unknown", persons[pid].get("campaign")
                       or persons[pid].get("utm_campaign") or "") for pid in cohort)
    open_ids = {deal["id"] for deal in open_deals}
    return {
        "headline": {
            "leads": len(cohort), "contacted": len(speed), "qualified": len(qualified),
            "site_visits_completed": visits["completed"], "bookings": len(bookings),
            "conversion_pct": round(100 * len(bookings) / len(cohort), 1) if cohort else None,
        },
        "funnel": funnel(snapshot["pipelines"], deals, movements, window),
        "sources": [{"source": source, "campaign": campaign, "count": count}
                    for (source, campaign), count in sorted(sources.items(), key=lambda item: (-item[1], item[0]))],
        "speed": {
            "median_minutes": round(median(speed), 1) if speed else None,
            "p90_minutes": round(speed[ceil(len(speed) * 0.9) - 1], 1) if speed else None,
            "target_minutes": target, "over_target": overdue_contact,
            "no_next_step": len(needs_next) + bare_leads,
            "overdue_tasks": sum((task.get("deal_id") in open_ids if task.get("deal_id")
                                  else task.get("person_id") in cohort)
                                 and in_period(task.get("due_at"), window) for task in overdue),
        },
        "nurture": nurture_activity(activities, window),
        "visits": {**visits, "by_day_owner": visit_rows},
        "bookings": sorted(bookings, key=lambda row: (row["booked_at"], row["id"]), reverse=True),
        "lost_reasons": [{"reason": reason, "count": count}
                         for reason, count in sorted(lost.items(), key=lambda item: (-item[1], item[0]))],
    }
