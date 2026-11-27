"""Mini Robos schedule, telemetry and watchdog."""
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from queue import Full, Queue
from threading import Event, Lock, Thread
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.db.database import SessionLocal
from backend.db.mini_robos_models import RoboAgent, RoboAlert, RoboAgentKey, RoboAgentVersion, RoboAudit, RoboIncident, RoboRun, utcnow
from backend.db.models import Account


logger = logging.getLogger("AdOptima")
_events = Queue(maxsize=10_000)
_queue_full = Event()
_worker_lock = Lock()
_worker = None
STATUSES = ("failing", "missed", "late", "no_signal", "paused", "on_track")
DAY = timedelta(days=1)
SIGNAL_WINDOW = 7 * DAY
IST = ZoneInfo("Asia/Kolkata")
RECIPIENT = re.compile(r"(?:[0-9]{8,15}|[0-9]{1,30}@g\.us)\Z")
MISSED_ERROR = "Scheduler missed this job execution."
REMOVED_SLUGS = ("adguard-meta-poll", "adguard-conversion-dispatcher")
EDUINFRA_OUTBOUND_RULES = (
    "Calling window 10:00 AM–6:00 PM IST",
    "Exotel number ending 0800, one shared channel, one call at a time, 4-second dial delay",
    "One attempt per number; automatic retries off",
    "Speaks first: 'Hello, am I speaking with {{CONTACT_NAME}}?' then waits for a reply",
    "Data: EduInfra Expand CRM leads, deduplicated, excluding do-not-call, opt-outs and previous batches",
)
SEEDS = (
    ("saburi-ply-mis", "Saburi Ply MIS (WhatsApp)", "whatsapp_report", "whatsapp_cloud", "Saburi Plywood", None,
     "Send the Saburi Ply MIS to WhatsApp at 09:15, 13:15 and 17:15 IST."),
    ("mantri-mis-refresh", "Mantri MIS refresh", "data_sync", "ops_app_job", "Mantri Developers", "daily_mantri_mis_refresh",
     "Refresh the Serenity project’s daily Meta and Google MIS snapshots through yesterday."),
    ("tlg-lead-routing", "TLG lead routing", "lead_routing", "ops_app_job", "The Little Gym (TLG)", "tlg_lead_routing",
     "Route new rows from enabled centre intake sheets to the configured lead sheets."),
    ("crashclub-meta-sync", "Crash.club Meta lead sync", "data_sync", "ops_app_job", "Crash Club", "crashclub_meta_leads_sync",
     "Poll Meta lead forms, deduplicate leads in the ledger and append new leads to Google Sheets."),
    ("sakha-reporting-sync", "Sakha reporting sync", "data_sync", "ops_app_job", None, "sakha_reporting_sync",
     "Refresh each Sakha company’s reporting mirror in its configured sample or live mode."),
    ("bhago-calling", "Bhago payment-reminder calls", "calling", "samvaad", "Bhago Mobility", None,
     "Run payment-reminder calls for the Bhago calling campaign."),
    ("bhago-lead-calls", "Bhago Lead-Calls", "calling", "samvaad", "Bhago Mobility", None,
     "Run AI calls to new Bhago leads through the Samvaad Lead-Calls campaign."),
    ("bhago-i-ride-calls", "Bhago I-Ride-Calls", "calling", "samvaad", "Bhago Mobility", None,
     "Run AI calls for the Bhago I-Ride campaign through Samvaad."),
    ("eduinfra-specifiers", "EduInfra Nisha — Specifiers", "calling", "samvaad", "EduInfra", None,
     "Outreach to architects, designers, engineers, consultants, and campus planning or design specialists. "
     "Confirm practice and project experience, discuss curated meetings and partner participation, and route follow-up to the curation team."),
    ("eduinfra-demand-creators", "EduInfra Nisha — Demand Creators", "calling", "samvaad", "EduInfra", None,
     "Outreach to school, college, and university owners, promoters, leaders, and campus-project decision-makers. "
     "Qualify projects and decision roles, explain delegate participation, and record follow-up."),
    ("eduinfra-commercial", "EduInfra Nisha — Commercial Participants", "calling", "samvaad", "EduInfra", None,
     "Outreach to commercial solution partners, infrastructure vendors, suppliers, and technology providers. "
     "Qualify offerings, discuss category exclusivity, pricing, showcases, and buyer meetings."),
    ("eduinfra-inbound", "EduInfra inbound agent", "calling", "samvaad", "EduInfra", None, "Incoming calls."),
) + tuple(
    (f"tlg-{slug}-script", f"TLG {name} Apps Script", "lead_routing", "apps_script", "The Little Gym (TLG)", None,
     f"Route incoming leads for the {name} centre through its branch Apps Script.")
    for slug, name in (("jayanagar", "Jayanagar"), ("new-bel-road", "New BEL Road"),
                       ("whitefield", "Whitefield"), ("yelahanka", "Yelahanka"))
)
SABURI_SCHEDULE = {"kind": "times", "tz": "Asia/Kolkata", "times": ["09:15", "13:15", "17:15"], "days": "daily"}


def utc(value):
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def expected_slots(schedule, start, end):
    if not schedule or schedule["kind"] != "times":
        return []
    tz = ZoneInfo(schedule["tz"])
    first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
    slots = []
    for offset in range((last - first).days + 1):
        day = first + offset * DAY
        for time in sorted(schedule["times"]):
            hour, minute = map(int, time.split(":"))
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)
            if start <= slot <= end:
                slots.append(slot)
    return sorted(slots)


def trigger_schedule(trigger):
    if isinstance(trigger, IntervalTrigger) and trigger.interval >= timedelta(minutes=1):
        return {"kind": "interval", "minutes": int(trigger.interval.total_seconds() // 60)}
    if isinstance(trigger, CronTrigger):
        fields = {field.name: str(field) for field in trigger.fields}
        if (all(fields[name] == "*" for name in ("year", "month", "day", "week", "day_of_week"))
                and fields["hour"].isdecimal() and fields["minute"].isdecimal()):
            return {"kind": "times", "tz": str(trigger.timezone),
                    "times": [f"{int(fields['hour']):02}:{int(fields['minute']):02}"], "days": "daily"}
    return None


def seed_agents(db, scheduler):
    removed_ids = db.scalars(select(RoboAgent.id).where(RoboAgent.slug.in_(REMOVED_SLUGS))).all()
    if removed_ids:
        # SQLite can run with foreign key enforcement disabled.
        for model in (RoboAlert, RoboAgentVersion, RoboRun, RoboIncident, RoboAgentKey, RoboAudit):
            db.query(model).filter(model.agent_id.in_(removed_ids)).delete()
        db.query(RoboAgent).filter(RoboAgent.id.in_(removed_ids)).delete()

    eduinfra_ids = db.scalars(select(Account.id).where(
        func.lower(func.replace(Account.name, " ", "")).contains("eduinfra")
    ).limit(2)).all()
    eduinfra_id = eduinfra_ids[0] if len(eduinfra_ids) == 1 else None
    jobs = {job.id: job for job in scheduler.get_jobs()}
    for slug, name, kind, source, account_name, ref, description in SEEDS:
        agent = db.query(RoboAgent).filter_by(slug=slug).first()
        is_eduinfra = account_name == "EduInfra"
        account = eduinfra_id if is_eduinfra else None
        if account_name and not is_eduinfra:
            matches = db.query(Account).filter(Account.name.ilike(account_name)).limit(2).all()
            if len(matches) == 1:
                account = matches[0].id
        schedule = trigger_schedule(jobs[ref].trigger) if ref in jobs else None
        if not agent:
            source_url = None
            rules = [description]
            if is_eduinfra:
                source_url = "https://samvaad.sakhaa.ai/dashboard"
                if slug == "eduinfra-inbound":
                    rules.append("Handles incoming calls; script not yet recorded")
                else:
                    rules.extend(EDUINFRA_OUTBOUND_RULES)
            elif ref:
                source_url = "https://github.com/chlearai/ai-ad-optimiser/blob/main/backend/services/scheduler.py"
            agent = RoboAgent(slug=slug, name=name, account_id=account, type=kind,
                              runs_on=source, job_ref=ref,
                              source_url=source_url,
                              schedule=SABURI_SCHEDULE if slug == "saburi-ply-mis" else schedule,
                              expects_output=slug == "saburi-ply-mis")
            db.add(agent)
            db.flush()
            db.add(RoboAgentVersion(agent_id=agent.id, version=1, rules=rules,
                                    status="published", author="system", published_at=utcnow()))
        else:
            if is_eduinfra or (agent.account_id is None and account is not None):
                agent.account_id = account
            if ref:
                agent.schedule = schedule
        if agent.paused and ref in jobs:
            scheduler.pause_job(ref)
    db.commit()


def get_job(agent):
    from backend.services import scheduler

    runner = scheduler._scheduler
    return (runner, runner.get_job(agent.job_ref)) if runner and agent.job_ref else (runner, None)


def bad_run(agent, run):
    return (run.status == "failed" and run.error != MISSED_ERROR
            or run.status == "succeeded" and agent.expects_output and run.output_count == 0)


def slot_result(agent, slot, run, now):
    deadline = slot + timedelta(minutes=agent.grace_minutes)
    if run is None:
        return "late" if now <= deadline else "missed"
    if run.error == MISSED_ERROR:
        return "missed"
    if bad_run(agent, run):
        return "failing"
    if (run.status == "running" and now > deadline) or utc(run.started_at) > deadline:
        return "late"
    return "on_track"


def latest_finished(runs):
    return max((run for run in runs if run.finished_at), key=lambda run: utc(run.finished_at), default=None)


def assess(agent, runs, now):
    """Return the current status and open incident identities."""
    if agent.paused:
        return "paused", set()
    last = runs[0] if runs else None
    if last is None or utc(last.started_at) < now - SIGNAL_WINDOW:
        return "no_signal", set()
    finished = latest_finished(runs)
    incidents = {("failed", utc(finished.slot) if finished.slot else None)} if finished and bad_run(agent, finished) else set()
    schedule = agent.schedule
    status = "on_track"
    if schedule and schedule["kind"] == "interval":
        span = timedelta(minutes=2 * schedule["minutes"] + agent.grace_minutes)
        if last.error == MISSED_ERROR or utc(last.started_at) < now - span:
            incidents.add(("stale", None))
            status = "late"
    elif schedule:
        by_slot = runs_by_slot(runs)
        start = max(now - DAY, utc(agent.created_at))
        for slot in expected_slots(schedule, start, now):
            result = slot_result(agent, slot, by_slot.get(slot), now)
            if result == "missed":
                incidents.add(("missed", slot))
            status = "on_track" if result == "failing" else result
        if any(kind == "missed" for kind, _ in incidents):
            status = "missed"
    return ("failing" if finished and bad_run(agent, finished) else status), incidents


def runs_by_slot(runs):
    by_slot = {}
    for run in runs:
        if run.slot:
            by_slot.setdefault(utc(run.slot), run)
    return by_slot


def recent_runs(db, agent, now, history=False):
    schedule = agent.schedule or {}
    span = SIGNAL_WINDOW
    if history and schedule.get("kind") == "times":
        span = 15 * DAY
    elif schedule.get("kind") == "interval":
        span = timedelta(minutes=16 * schedule["minutes"] + agent.grace_minutes)
    query = db.query(RoboRun).filter_by(agent_id=agent.id).order_by(RoboRun.started_at.desc(), RoboRun.id.desc())
    rows = query.filter(RoboRun.started_at >= now - span).all()
    if not rows:
        rows = query.limit(14 if history else 1).all()
    if rows and latest_finished(rows) is None:
        prior = query.filter(RoboRun.finished_at.is_not(None)).order_by(None).order_by(RoboRun.finished_at.desc()).first()
        if prior:
            rows.append(prior)
    return rows


def timeline(agent, runs, now, next_run=None):
    schedule = agent.schedule or {}
    first = utc(agent.created_at)
    if schedule.get("kind") == "times":
        by_slot = runs_by_slot(runs)
        return [{"slot": slot, "status": slot_result(agent, slot, by_slot.get(slot), now)}
                for slot in expected_slots(schedule, max(now - 15 * DAY, first), now)[-14:]]
    if schedule.get("kind") == "interval" and (next_run or runs):
        step = timedelta(minutes=schedule["minutes"])
        # shortcut: infer interval slots from the current cadence, record cadence changes if exact history is needed.
        anchor = utc(next_run) - step if next_run else utc(runs[0].started_at)
        slots = [anchor - step * offset for offset in range(13, -1, -1)]
        by_interval = {}
        for run in runs:
            started = utc(run.started_at)
            # Submission time can drift a few seconds after the scheduled slot.
            slot = anchor + ((started - anchor) // step) * step
            by_interval.setdefault(slot, run)
        return [{"slot": slot, "status": slot_result(agent, slot, by_interval.get(slot), now)}
                for slot in slots if first <= slot <= now]
    return [{"slot": run.started_at, "status": "failing" if bad_run(agent, run)
             else "late" if run.status == "running" else "on_track"} for run in reversed(runs[:14])]


def next_external(agent, runs, now):
    if agent.paused or not agent.schedule:
        return None
    if agent.schedule["kind"] == "times":
        return next(iter(expected_slots(agent.schedule, now + timedelta(microseconds=1), now + DAY)), None)
    if runs:
        step = timedelta(minutes=agent.schedule["minutes"])
        last = utc(runs[0].started_at)
        return last + ((now - last) // step + 1) * step
    return None


def alert_recipients(values):
    if len(values) > 100 or any(not RECIPIENT.fullmatch(value) for value in values):
        raise ValueError("Enter up to 100 phone numbers or numeric group JIDs")
    return list(dict.fromkeys(values))


def queue_incident_alert(db, agent, incident, kind, runs, status, now):
    if not agent.alert_recipients:
        return
    if kind == "recovered":
        outcome = "is back on track." if status == "on_track" else f"incident closed. Current status: {status.replace('_', ' ')}."
    else:
        if incident.kind == "missed":
            outcome = f"missed the {incident.slot.astimezone(IST):%H:%M} IST run."
        elif incident.kind == "failed":
            outcome = "reported a failed run or zero output."
        else:
            outcome = "has not reported a run within its expected interval."
        good = next((run for run in runs if run.status == "succeeded" and not bad_run(agent, run)), None)
        if good:
            outcome += f" Last good run {utc(good.started_at).astimezone(IST):%H:%M} IST."
    db.add(RoboAlert(agent_id=agent.id, incident_id=incident.id, kind=kind, text=f"Mini Robos: {agent.name} {outcome}",
                     recipients=agent.alert_recipients, created_at=now))


def queue_daily_summary():
    raw = os.getenv("MINI_ROBOS_SUMMARY_TO", "")
    if not raw.strip():
        return
    try:
        recipients = alert_recipients([value.strip() for value in raw.split(",") if value.strip()])
        if not recipients:
            return
        now = utcnow()
        day = now.astimezone(IST).date()
        with SessionLocal.begin() as db:
            if db.query(RoboAlert.id).filter_by(summary_date=day).first():
                return
            agents = db.query(RoboAgent).order_by(RoboAgent.name).all()
            parts = []
            for status in ("on_track", "late", "missed", "failing", "paused", "no_signal"):
                names = [agent.name for agent in agents if agent.status == status]
                if names:
                    detail = f" ({', '.join(names)})" if status in {"late", "missed", "failing"} else ""
                    parts.append(f"{len(names)} {status.replace('_', ' ')}{detail}")
            text = f"Mini Robos {day.day} {day:%b}: {len(agents)} agents"
            if parts:
                text += " \u2014 " + ", ".join(parts)
            db.add(RoboAlert(kind="summary", summary_date=day, text=text + ".", recipients=recipients, created_at=now))
    except IntegrityError:
        logger.info("Mini Robos daily summary already queued")
    except Exception:
        logger.warning("Mini Robos could not queue its daily summary")


def watchdog():
    try:
        now = utcnow()
        with SessionLocal.begin() as db:
            db.query(RoboRun).filter(RoboRun.started_at < now - 90 * DAY).delete()
        with SessionLocal() as db:
            agent_ids = db.scalars(select(RoboAgent.id).order_by(RoboAgent.id)).all()
        for agent_id in agent_ids:
            with SessionLocal.begin() as db:
                agent = db.get(RoboAgent, agent_id, with_for_update=True)
                if agent is None:
                    continue
                runs = recent_runs(db, agent, now)
                status, desired = assess(agent, runs, now)
                agent.status = status
                existing = set()
                for incident in db.query(RoboIncident).filter_by(agent_id=agent.id, closed_at=None):
                    key = (incident.kind, utc(incident.slot) if incident.slot else None)
                    if key in desired and key not in existing:
                        existing.add(key)
                    else:
                        incident.closed_at = now
                        queue_incident_alert(db, agent, incident, "recovered", runs, status, now)
                for kind, slot in desired - existing:
                    incident = RoboIncident(agent_id=agent.id, kind=kind, slot=slot, opened_at=now)
                    db.add(incident)
                    if agent.alert_recipients:
                        db.flush()
                        queue_incident_alert(db, agent, incident, "opened", runs, status, now)
    except Exception:
        logger.warning("Mini Robos could not complete its watchdog cycle")


def listen(event):
    times = event.scheduled_run_times if event.code == EVENT_JOB_SUBMITTED else (event.scheduled_run_time,)
    result = getattr(event, "retval", None)
    count = result if type(result) is int and 0 <= result <= 2_147_483_647 else None
    try:
        _events.put_nowait((event.job_id, event.code, tuple(times), count, utcnow()))
    except Full:
        _queue_full.set()


def record_event(payload):
    job_id, code, times, count, now = payload
    with SessionLocal.begin() as db:
        agent = db.query(RoboAgent).filter_by(job_ref=job_id, runs_on="ops_app_job").first()
        if not agent:
            return
        intervals = (agent.schedule or {}).get("kind") == "interval"
        for scheduled in times:
            scheduled = utc(scheduled)
            run_id = str(uuid5(NAMESPACE_URL, f"mini-robos:{agent.id}:{scheduled.isoformat()}"))
            run = db.get(RoboRun, run_id)
            if not run:
                run = RoboRun(id=run_id, agent_id=agent.id, slot=None if intervals else scheduled,
                              started_at=now if code == EVENT_JOB_SUBMITTED else scheduled, status="running")
                db.add(run)
            if code == EVENT_JOB_SUBMITTED or run.finished_at:
                continue
            run.finished_at = now
            run.status = "succeeded" if code == EVENT_JOB_EXECUTED else "failed"
            if code == EVENT_JOB_MISSED:
                run.error = MISSED_ERROR
            elif code == EVENT_JOB_ERROR:
                run.error = "The scheduler reported a job error. Check the job logs at source."
            else:
                run.output_count = count


def drain_events(events, queue_full):
    warned = False
    while True:
        payload = events.get()
        try:
            if queue_full.is_set() and not warned:
                logger.warning("Mini Robos scheduler event queue is full; some telemetry was dropped")
                warned = True
            record_event(payload)
        except Exception:
            logger.warning("Mini Robos could not record an APScheduler event")
        finally:
            events.task_done()


def start_event_worker():
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = Thread(target=drain_events, args=(_events, _queue_full), name="mini-robos-events", daemon=True)
        _worker.start()
