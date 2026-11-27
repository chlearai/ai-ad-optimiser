"""Mini Robos control room and agent-scoped reporter API."""
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import os
import re
from secrets import token_urlsafe
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.base import JobLookupError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator
from sqlalchemy import case, func, or_, update
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.mini_robos_models import RoboAgent, RoboAlert, RoboAgentKey, RoboAgentVersion, RoboAudit, RoboIncident, RoboRun, utcnow
from backend.db.models import Account
from backend.routes.auth import get_current_user_required
from backend.services import mini_robos as service


router = APIRouter(prefix="/api/mini-robos", tags=["Mini Robos"])
ADMINS = {"admin", "superadmin"}
BODY_LIMIT = 8192
REPORTS_PER_HOUR = 120
VERSION_FIELDS = ("prompt", "rules", "input_source", "recipients", "note")


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["start", "finish"]
    run_id: UUID | None = None
    slot: AwareDatetime | None = None
    status: Literal["succeeded", "failed"] | None = None
    output_count: int | None = Field(None, ge=0, le=2_147_483_647, strict=True)
    message: str | None = None
    error: str | None = None


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field("", max_length=32000)
    rules: list[Annotated[str, Field(max_length=4000)]] = Field(default_factory=list, max_length=100)
    input_source: dict[str, JsonValue] = Field(default_factory=dict)
    recipients: list[JsonValue] = Field(default_factory=list, max_length=100)
    note: str = Field("", max_length=2000)


class Confirm(BaseModel):
    confirm_recipients: bool = Field(False, strict=True)
    recipient_diff: dict[str, list[JsonValue]] | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a reason")
        return value.strip()


class Times(BaseModel):
    kind: Literal["times"]
    tz: str
    times: list[str] = Field(min_length=1, max_length=24)
    days: Literal["daily"] = "daily"

    @field_validator("tz")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Enter an IANA time zone") from None
        return value

    @field_validator("times")
    @classmethod
    def valid_times(cls, times):
        if len(set(times)) != len(times) or not all(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in times):
            raise ValueError("Enter distinct 24-hour times as HH:MM")
        return sorted(times)


class Interval(BaseModel):
    kind: Literal["interval"]
    minutes: int = Field(ge=1, le=1440, strict=True)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    owner: str | None = Field(None, max_length=160)
    source_url: str | None = Field(None, max_length=1000)
    schedule: Annotated[Times | Interval, Field(discriminator="kind")] | None = None
    grace_minutes: int = Field(15, ge=0, le=1440, strict=True)
    expects_output: bool = False
    live_sync: bool = False

    @field_validator("source_url")
    @classmethod
    def clean_source(cls, value):
        if not value:
            return None
        try:
            url = urlsplit(value)
            if url.scheme in {"http", "https"} and url.hostname and url.username is None and not url.query:
                return value
        except ValueError:
            pass
        raise ValueError("Enter an HTTP or HTTPS source link without credentials or query parameters")


def can_edit(user):
    return user.role == "superadmin" or user.edit_mini_robos


def viewer(user=Depends(get_current_user_required)):
    if user.role not in ADMINS and (user.role not in {"user", "newuser"} or not user.access_mini_robos):
        raise HTTPException(403, "Mini Robos view access required")
    return user


def editor(user=Depends(viewer)):
    if not can_edit(user):
        raise HTTPException(403, "Mini Robos edit access required")
    return user


def scope(db, user):
    query = db.query(RoboAgent)
    if user.role not in ADMINS:
        query = query.filter(RoboAgent.account_id.in_(user.assigned_account_ids()))
    return query


def find_agent(db, user, slug, *, lock=False):
    query = scope(db, user).filter_by(slug=slug)
    agent = (query.with_for_update() if lock else query).first()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


def viewed_agent(slug: str, db: Session = Depends(get_db), user=Depends(viewer)):
    return find_agent(db, user, slug)


def edited_agent(slug: str, db: Session = Depends(get_db), user=Depends(editor)):
    return find_agent(db, user, slug, lock=True)


def audit(db, user, agent, action, **detail):
    db.add(RoboAudit(agent_id=agent.id, who=user.full_name or user.email, action=action, detail=detail))
    db.commit()


def expose(row, fields):
    return {field: service.utc(value) if isinstance(value := getattr(row, field), datetime) else value
            for field in fields.split()}


def run_data(run):
    return expose(run, "id slot started_at finished_at status output_count message error") if run else None


def latest_version(db, agent, status):
    return db.query(RoboAgentVersion).filter_by(agent_id=agent.id, status=status).order_by(
        RoboAgentVersion.version.desc()
    ).first()


def version_data(row):
    return {field: getattr(row, field) for field in VERSION_FIELDS} | expose(
        row, "id version status author created_at published_at"
    )


def key_data(row):
    return expose(row, "id created_at revoked_at")


def authenticated_reporter(db, raw_key, slug=None):
    if not raw_key or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_key):
        raise HTTPException(401, "Invalid reporter key")
    digest = sha256(raw_key.encode("ascii")).hexdigest()
    key = db.query(RoboAgentKey).filter_by(key_hash=digest).first()
    matches = compare_digest(digest, key.key_hash if key else "0" * 64)
    if not matches or key is None or key.revoked_at:
        raise HTTPException(401, "Invalid reporter key")
    agent = db.get(RoboAgent, key.agent_id)
    if agent is None or (slug is not None and agent.slug != slug):
        raise HTTPException(401, "Invalid reporter key")
    now = utcnow()
    expired = or_(RoboAgentKey.window_start.is_(None), RoboAgentKey.window_start <= now - timedelta(hours=1))
    result = db.execute(update(RoboAgentKey).where(
        RoboAgentKey.id == key.id, RoboAgentKey.revoked_at.is_(None),
        or_(expired, RoboAgentKey.calls < REPORTS_PER_HOUR),
    ).values(window_start=case((expired, now), else_=RoboAgentKey.window_start),
             calls=case((expired, 1), else_=RoboAgentKey.calls + 1)).execution_options(synchronize_session=False))
    if not result.rowcount:
        db.refresh(key)
        if key.revoked_at:
            raise HTTPException(401, "Invalid reporter key")
        raise HTTPException(429, "Reporter rate limit exceeded")
    db.commit()
    return agent


async def heartbeat_body(request: Request):
    size = request.headers.get("content-length", "")
    if size.isdecimal() and len(size.lstrip("0")) > 4:
        raise HTTPException(413, "Heartbeat exceeds 8 KB")
    if size.isdecimal() and int(size.lstrip("0") or "0") > BODY_LIMIT:
        raise HTTPException(413, "Heartbeat exceeds 8 KB")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > BODY_LIMIT:
            raise HTTPException(413, "Heartbeat exceeds 8 KB")
        body.extend(chunk)
    try:
        report = Heartbeat.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid heartbeat") from None
    if (report.event == "finish") != (report.status is not None):
        raise HTTPException(422, "Only a finish event can specify a result, and it requires one")
    return report


@router.post("/heartbeat")
def heartbeat(request: Request, body: Heartbeat = Depends(heartbeat_body), db: Session = Depends(get_db)):
    raw_key = request.headers.get("X-Robo-Key")
    agent = authenticated_reporter(db, raw_key)
    # Serialize retries with scheduler events for the same agent.
    agent = db.query(RoboAgent).filter_by(id=agent.id).with_for_update().first()
    if not agent:
        raise HTTPException(401, "Invalid reporter key")
    now = utcnow()
    slot = service.utc(body.slot) if body.slot else None
    schedule = agent.schedule or {}
    if schedule.get("kind") == "interval":
        slot = None
    elif slot is None and not body.run_id and schedule.get("kind") == "times":
        possible = service.expected_slots(schedule, now - timedelta(minutes=agent.grace_minutes), now)
        slot = possible[-1] if possible else None
    if slot and slot > now + timedelta(minutes=1):
        raise HTTPException(422, "Run slot cannot be in the future")
    run_id = str(body.run_id or (
        uuid5(NAMESPACE_URL, f"robo-report:{agent.id}:{slot.isoformat()}") if slot else uuid4()
    ))
    run = db.get(RoboRun, run_id)
    if run and run.agent_id != agent.id or body.event == "finish" and body.run_id and not run:
        raise HTTPException(404, "Run not found")
    if run and slot and run.slot and service.utc(run.slot) != slot:
        raise HTTPException(409, "Run already belongs to a different slot")
    if not run:
        run = RoboRun(id=run_id, agent_id=agent.id, slot=slot, started_at=now, status="running")
        db.add(run)
    if body.event == "finish" and not run.finished_at:
        run.finished_at = now
        run.status = body.status
        run.output_count = body.output_count
        run.message = body.message.replace(raw_key, "[redacted]")[:2000] if body.message else None
        run.error = body.error.replace(raw_key, "[redacted]")[:2000] if body.error else None
    db.commit()
    return {"run_id": run.id, "status": run.status}


@router.get("/agents/{slug}/config")
def reporter_config(slug: str, request: Request, response: Response, db: Session = Depends(get_db)):
    agent = authenticated_reporter(db, request.headers.get("X-Robo-Key"), slug)
    version = latest_version(db, agent, "published") if agent.live_sync else None
    if not version:
        raise HTTPException(404, "Live configuration unavailable")
    response.headers["Cache-Control"] = "no-store"
    return {field: getattr(version, field) for field in VERSION_FIELDS if field != "note"} | {
        "version": version.version, "schedule": agent.schedule,
    }


def summary(db, agent, client, now):
    runs = service.recent_runs(db, agent, now, history=True)
    _, job = service.get_job(agent)
    next_run = None
    if not agent.paused:
        next_run = getattr(job, "next_run_time", None) if agent.job_ref else service.next_external(agent, runs, now)
    status, _ = service.assess(agent, runs, now)
    return expose(agent, "slug name type runs_on account_id schedule live_sync paused") | {
        "client": client, "status": status, "last_run": run_data(runs[0]) if runs else None,
        "next_run": next_run, "slots": service.timeline(agent, runs, now, next_run),
    }


@router.get("/agents")
def list_agents(db: Session = Depends(get_db), user=Depends(viewer)):
    now = utcnow()
    agents = [summary(db, agent, name, now) for agent, name in scope(db, user).outerjoin(
        Account, Account.id == RoboAgent.account_id
    ).add_columns(Account.name)]
    return {"agents": sorted(agents, key=lambda row: (service.STATUSES.index(row["status"]), row["name"].casefold()))}


@router.get("/agents/{slug}")
def agent_detail(agent=Depends(viewed_agent), db: Session = Depends(get_db), user=Depends(viewer)):
    account = db.get(Account, agent.account_id) if agent.account_id else None
    failed = db.query(RoboRun).filter_by(agent_id=agent.id, status="failed").order_by(RoboRun.finished_at.desc()).first()
    incidents = db.query(RoboIncident).filter_by(agent_id=agent.id, closed_at=None).order_by(RoboIncident.opened_at).all()
    return summary(db, agent, account.name if account else None, utcnow()) | expose(
        agent, "owner source_url grace_minutes expects_output pause_reason paused_by paused_at job_ref"
    ) | {
        "can_edit": can_edit(user), "alert_recipients": (agent.alert_recipients or []) if can_edit(user) else [],
        "latest_error": (failed.error or failed.message or "The last failed run did not include details.") if failed else None,
        "incidents": [expose(row, "kind slot opened_at") for row in incidents],
    }


class AlertRecipients(BaseModel):
    alert_recipients: list[str] = Field(max_length=100)

    @field_validator("alert_recipients")
    @classmethod
    def validate_recipients(cls, values):
        return service.alert_recipients(values)


class AlertResult(BaseModel):
    status: Literal["sent", "failed"]
    error: str | None = Field(None, max_length=2000)


def sender_key(request: Request):
    expected = os.getenv("MINI_ROBOS_SENDER_KEY_HASH", "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise HTTPException(503, "Mini Robos sender is not configured")
    raw = request.headers.get("X-Robo-Sender-Key", "")
    if not raw or not compare_digest(sha256(raw.encode()).hexdigest(), expected):
        raise HTTPException(401, "Invalid Mini Robos sender key")
    return raw


@router.get("/alerts/pending")
def pending_alerts(response: Response, limit: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db), _=Depends(sender_key)):
    response.headers["cache-control"] = "no-store"
    rows = db.query(RoboAlert).filter_by(status="pending").order_by(RoboAlert.created_at, RoboAlert.id).limit(limit)
    return {"alerts": [expose(row, "id text recipients") for row in rows]}


@router.post("/alerts/{id}/result")
def alert_result(id: int, body: AlertResult, db: Session = Depends(get_db), raw=Depends(sender_key)):
    values = {"attempts": RoboAlert.attempts + 1}
    if body.status == "sent":
        values.update(status="sent", sent_at=utcnow(), last_error=None)
    else:
        values.update(status=case((RoboAlert.attempts >= 2, "failed"), else_="pending"),
                      last_error=body.error.replace(raw, "[redacted]")[:2000] if body.error else None)
    db.execute(update(RoboAlert).where(RoboAlert.id == id, RoboAlert.status == "pending").values(**values))
    row = db.get(RoboAlert, id)
    if not row:
        raise HTTPException(404, "Alert not found")
    result = {"status": row.status, "attempts": row.attempts}
    db.commit()
    return result


@router.put("/agents/{slug}/alerts")
def set_alert_recipients(body: AlertRecipients, agent=Depends(edited_agent),
                         db: Session = Depends(get_db), user=Depends(editor)):
    agent.alert_recipients = body.alert_recipients
    audit(db, user, agent, "edit_profile", fields=["alert_recipients"])
    return {"alert_recipients": agent.alert_recipients}


@router.put("/agents/{slug}/profile")
def edit_profile(body: Profile, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    changes = body.model_dump()
    if agent.job_ref:
        changes.pop("schedule")
    for field, value in changes.items():
        setattr(agent, field, value)
    audit(db, user, agent, "edit_profile", fields=list(changes))
    return {"ok": True}


@router.get("/agents/{slug}/runs")
def list_runs(agent=Depends(viewed_agent), db: Session = Depends(get_db),
              page: int = Query(1, ge=1, le=10000), limit: int = Query(25, ge=1, le=100)):
    rows = db.query(RoboRun).filter_by(agent_id=agent.id).order_by(RoboRun.started_at.desc(), RoboRun.id.desc()).offset(
        (page - 1) * limit
    ).limit(limit + 1).all()
    return {"runs": [run_data(run) for run in rows[:limit]], "has_next": len(rows) > limit}


@router.get("/agents/{slug}/versions")
def list_versions(agent=Depends(viewed_agent), db: Session = Depends(get_db)):
    rows = db.query(RoboAgentVersion).filter_by(agent_id=agent.id).order_by(RoboAgentVersion.version.desc())
    return {"versions": [version_data(row) for row in rows]}


def next_version(db, agent):
    return (db.query(func.max(RoboAgentVersion.version)).filter_by(agent_id=agent.id).scalar() or 0) + 1


@router.put("/agents/{slug}/draft")
def save_draft(body: Draft, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    draft = latest_version(db, agent, "draft")
    if draft is None:
        draft = RoboAgentVersion(agent_id=agent.id, version=next_version(db, agent), status="draft")
        db.add(draft)
    for key, value in body.model_dump().items():
        setattr(draft, key, value)
    draft.author = user.full_name or user.email
    db.commit()
    return version_data(draft)


def check_recipients(db, agent, recipients, confirmation):
    latest = latest_version(db, agent, "published")
    previous = latest.recipients if latest else []
    diff = {"from": previous, "to": recipients}
    if previous != recipients and (not confirmation.confirm_recipients or
                                     confirmation.recipient_diff not in (None, diff)):
        raise HTTPException(409, {"message": "Confirm the recipient changes", **diff})
    return latest


@router.post("/agents/{slug}/publish")
def publish(body: Confirm, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    draft = latest_version(db, agent, "draft")
    if not draft:
        raise HTTPException(404, "No draft to publish")
    latest = check_recipients(db, agent, draft.recipients, body)
    if latest and draft.version < latest.version:
        draft.version = next_version(db, agent)
    draft.status = "published"
    draft.published_at = utcnow()
    audit(db, user, agent, "publish", version=draft.version)
    return version_data(draft)


@router.post("/agents/{slug}/versions/{version}/rollback")
def rollback(version: int, body: Confirm, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    previous = db.query(RoboAgentVersion).filter_by(agent_id=agent.id, version=version, status="published").first()
    if not previous:
        raise HTTPException(404, "Published version not found")
    check_recipients(db, agent, previous.recipients, body)
    row = RoboAgentVersion(agent_id=agent.id, version=next_version(db, agent), status="published",
                           author=user.full_name or user.email, published_at=utcnow(),
                           **{field: getattr(previous, field) for field in VERSION_FIELDS})
    db.add(row)
    audit(db, user, agent, "rollback", from_version=version, version=row.version)
    return version_data(row)


@router.post("/agents/{slug}/pause")
@router.post("/agents/{slug}/resume")
def toggle_pause(request: Request, body: Reason, agent=Depends(edited_agent),
                 db: Session = Depends(get_db), user=Depends(editor)):
    paused = request.url.path.endswith("/pause")
    runner, job = service.get_job(agent)
    if job:
        try:
            (runner.pause_job if paused else runner.resume_job)(job.id)
        except JobLookupError:
            raise HTTPException(409, "Scheduler job is unavailable") from None
    agent.paused = paused
    agent.pause_reason = body.reason if paused else None
    agent.paused_by = (user.full_name or user.email) if paused else None
    agent.paused_at = utcnow() if paused else None
    audit(db, user, agent, "pause" if paused else "resume", reason=body.reason)
    return {"paused": paused}


@router.post("/agents/{slug}/run-now")
def run_now(agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    if agent.runs_on != "ops_app_job":
        raise HTTPException(400, "run at source")
    if agent.paused:
        raise HTTPException(409, "Resume this agent before running it")
    runner, job = service.get_job(agent)
    if not runner or not runner.running or not job:
        raise HTTPException(409, "Scheduler job is unavailable")
    try:
        runner.modify_job(job.id, next_run_time=utcnow())
    except JobLookupError:
        raise HTTPException(409, "Scheduler job is unavailable") from None
    audit(db, user, agent, "run_now")
    return {"ok": True}


@router.get("/agents/{slug}/keys")
def list_keys(agent=Depends(edited_agent), db: Session = Depends(get_db)):
    return {"keys": [key_data(row) for row in db.query(RoboAgentKey).filter_by(agent_id=agent.id).order_by(RoboAgentKey.id.desc())]}


@router.post("/agents/{slug}/keys", status_code=201)
def create_key(response: Response, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    raw = token_urlsafe(32)
    row = RoboAgentKey(agent_id=agent.id, key_hash=sha256(raw.encode("ascii")).hexdigest())
    db.add(row)
    db.flush()
    audit(db, user, agent, "key_create", key_id=row.id)
    response.headers["Cache-Control"] = "no-store"
    return key_data(row) | {"key": raw}


@router.delete("/agents/{slug}/keys/{key_id}")
def revoke_key(key_id: int, agent=Depends(edited_agent), db: Session = Depends(get_db), user=Depends(editor)):
    key = db.query(RoboAgentKey).filter_by(id=key_id, agent_id=agent.id, revoked_at=None).first()
    if not key:
        raise HTTPException(404, "Active key not found")
    key.revoked_at = utcnow()
    audit(db, user, agent, "key_revoke", key_id=key.id)
    return {"ok": True}


@router.get("/agents/{slug}/audit")
def list_audit(agent=Depends(viewed_agent), db: Session = Depends(get_db), page: int = Query(1, ge=1, le=10000)):
    rows = db.query(RoboAudit).filter_by(agent_id=agent.id).order_by(RoboAudit.at.desc(), RoboAudit.id.desc()).offset(
        (page - 1) * 25
    ).limit(26).all()
    return {"audit": [expose(row, "who action detail at") for row in rows[:25]], "has_next": len(rows) > 25}
