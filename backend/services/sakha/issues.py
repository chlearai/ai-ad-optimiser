"""Issue radar conditions and lifecycle."""
from datetime import timedelta
from statistics import median

from backend.services.sakha.client import as_utc, iso
from backend.services.sakha.metrics import (
    DEFAULT_TARGETS, current_open, first_milestone, in_working_hours, next_tasks,
    outbound_touches, stage_index, stage_movements, work_minutes,
)


SEVERITY = {"red": 0, "amber": 1}


def evaluate(snapshot, now, stage_map, targets, last_sync_at, last_sync_ok, *, have_snapshot=True):
    found = []

    def issue(code, severity, title, detail, count, hint):
        found.append(dict(code=code, severity=severity, title=title, detail=detail,
                          affected_count=count, fix_hint=hint))

    if last_sync_ok is False:
        issue("sync_failed", "red", "Sakhaa reporting sync failed",
              "The last reporting pull could not complete.", 1,
              "Ask an administrator to check Sakha sync status and retry the pull.")
    if (last_sync_at and in_working_hours(now, targets)
            and now - as_utc(last_sync_at) >= timedelta(minutes=30)):
        issue("sync_stale", "red", "Reporting data is stale",
              "There has been no successful Sakhaa pull for at least 30 minutes during working hours.", 1,
              "Check the Sakha sync status and the Reporting API.")
    if not have_snapshot:
        return found

    paused = [row for row in snapshot["connectors"] if (row.get("captured_count") or 0) > 0
              and (row.get("status") == "paused" or row.get("rejecting_new"))]
    for row in paused:
        issue("source:" + row["id"], "red", f'{row["name"]} is paused or rejecting enquiries',
              f'Previously captured {row["captured_count"]} leads.', 1,
              "Open the connector in Sakhaa lead-source settings and check its capture status.")

    for row in snapshot["integrations"]:
        last = as_utc(row.get("last_sync_at"))
        if row.get("status") == "needs_attention" or (last and now - last >= timedelta(hours=24)):
            detail = "Sakhaa flagged this integration for attention."
            if last:
                detail += f" Last sync was {max(0, (now - last).days)} day(s) ago."
            issue("integration:" + (row.get("id") or row["name"]), "amber", f'{row["name"]} needs attention', detail, 1,
                  "Open the integration in Sakhaa settings and check its latest sync.")

    people = {row["id"]: row for row in snapshot["persons"]}
    recent = sorted(as_utc(person["created_at"]) for person in people.values() if person.get("created_at"))
    recent = [created for created in recent if created <= now][-51:]
    intervals = [work_minutes(a, b, targets) for a, b in zip(recent, recent[1:])]
    positive = [gap for gap in intervals if gap > 0]
    usual_gap = targets.get("lead_gap_minutes") or (max(60, median(positive) * 2) if len(positive) >= 3 else 240)
    if recent and in_working_hours(now, targets) and work_minutes(recent[-1], now, targets) > usual_gap:
        issue("lead_gap", "amber", "New enquiries have slowed",
              f"No new leads within the expected {round(usual_gap)} working minutes.", 0,
              "Check recent enquiries and lead-source connectors in Sakhaa.")

    touches = outbound_touches(snapshot["activities"], people)
    contact_target = targets.get("first_contact_minutes", DEFAULT_TARGETS["first_contact_minutes"])
    waiting = sum((pid not in touches or touches[pid] > now) and person.get("lifecycle_status") != "dead"
                  and work_minutes(person["created_at"], now, targets) > contact_target
                  for pid, person in people.items() if person.get("created_at"))
    if waiting:
        issue("contact_sla", "red", "Leads are waiting for first contact",
              f"These leads are past the {contact_target}-working-minute contact target.", waiting,
              "Assign the waiting leads and log the first outbound touch in Sakhaa.")

    deals = snapshot["deals"]
    stages = stage_index(snapshot["pipelines"])
    movements = stage_movements(deals, snapshot["deal-stage-events"])
    open_deals = {deal["id"]: deal for deal in deals if current_open(deal, stages, movements, stage_map)}
    future_deals, future_people, overdue = next_tasks(snapshot["tasks"], now)
    missing_next = sum(deal_id not in future_deals and deal.get("person_id") not in future_people
                       for deal_id, deal in open_deals.items())
    if missing_next:
        issue("next_step", "amber", "Open deals have no next step",
              "No future task or call is scheduled for these deals.", missing_next,
              "Schedule the next task or call on the open deals in Sakhaa.")

    overdue_deals = {task["deal_id"] for task in overdue if task.get("deal_id") in open_deals}
    overdue_people = {task["person_id"] for task in overdue if not task.get("deal_id") and task.get("person_id")}
    overdue_deals.update(deal_id for deal_id, deal in open_deals.items() if deal.get("person_id") in overdue_people)
    if overdue_deals:
        issue("overdue_tasks", "amber", "Open deals have overdue tasks",
              "These deals have at least one open task past its due time.", len(overdue_deals),
              "Complete or reschedule overdue tasks in Sakhaa.")

    missing_value = sum(deal.get("value") is None
                        and first_milestone(deal, movements, stages, "won", stage_map) is not None for deal in deals)
    if missing_value:
        issue("booking_value", "amber", "Bookings are missing a value",
              "A deal value has not been recorded for these bookings.", missing_value,
              "Fill in the value on the confirmed booking in Sakhaa.")
    return found


def reconcile(db, client_id, mode, found, now):
    from backend.db.sakha_models import SakhaIssue

    previous = {row.code: row for row in db.query(SakhaIssue).filter_by(
        sakha_client_id=client_id, data_mode=mode
    )}
    for item in found:
        row = previous.pop(item["code"], None)
        if row is None:
            db.add(SakhaIssue(sakha_client_id=client_id, data_mode=mode, first_seen=now, last_seen=now, **item))
            continue
        if row.resolved_at is not None:
            row.first_seen = now
        row.last_seen = now
        row.resolved_at = None
        for key, value in item.items():
            setattr(row, key, value)
    for row in previous.values():
        if row.resolved_at is None:
            row.resolved_at = now


def describe_issue(row):
    return {key: getattr(row, key) for key in ("code", "severity", "title", "detail", "affected_count", "fix_hint")} | {
        "first_seen": iso(row.first_seen), "last_seen": iso(row.last_seen), "resolved_at": iso(row.resolved_at),
    }
