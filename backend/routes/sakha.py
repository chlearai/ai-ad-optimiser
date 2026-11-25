"""Authenticated client-scoped Sakha dashboards and sync controls."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.sakha_models import SakhaClient, SakhaIssue
from backend.routes.auth import get_current_user_required, require_admin_or_superadmin
from backend.services.sakha.client import as_utc, config_for, iso
from backend.services.sakha.issues import SEVERITY, describe_issue
from backend.services.sakha.metrics import DEFAULT_TARGETS, IST, calculate, day_window, in_period, in_working_hours
from backend.services.sakha.sync import MANTRI_SLUG, empty_snapshot, read_snapshot, sync_client, utcnow


router = APIRouter(prefix="/api/sakha", tags=["Sakha"])
ADMINS = {"admin", "superadmin"}


class Targets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_start_hour: int = Field(9, ge=0, le=23)
    work_end_hour: int = Field(18, ge=1, le=24)
    first_contact_minutes: int = Field(30, ge=1)
    lead_gap_minutes: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_hours(self):
        if self.work_end_hour <= self.work_start_hour:
            raise ValueError("Working hours must end after they start")
        return self


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_map: dict[str, str] | None = None
    targets: Targets | None = None
    widgets: dict | None = None


def require_sakha(user=Depends(get_current_user_required)):
    if user.role not in ADMINS and (user.role not in {"user", "newuser"} or not user.access_sakha):
        raise HTTPException(403, "Sakha access is required")
    return user


def get_client(slug: str, db: Session = Depends(get_db), user=Depends(require_sakha)):
    client = db.query(SakhaClient).filter_by(slug=slug).first()
    if client is None:
        raise HTTPException(404, "Sakha client not found")
    if user.role not in ADMINS and client.account_id not in user.assigned_account_ids():
        raise HTTPException(403, "You are not assigned to this Sakha client")
    return client


def sync_status(client):
    config = config_for(client.slug, client.base_url)
    mode = config.mode
    last_sync = client.last_sync_at if client.last_sync_mode == mode else None
    attempted = client.sync_attempt_mode == mode
    return {
        "data_mode": mode, "configured": config.configured, "last_sync_at": iso(last_sync),
        "last_sync_ok": client.last_sync_ok if attempted else None,
        "last_error": client.last_error if attempted else None,
    }


def snapshot_for(db, client, status):
    if status["last_sync_at"]:
        return read_snapshot(db, client.id, status["data_mode"])
    return empty_snapshot()


def client_info(client):
    return {"slug": client.slug, "display_name": client.display_name}


def active_issues(db, client, status, now):
    issues = [describe_issue(row) for row in db.query(SakhaIssue).filter_by(
        sakha_client_id=client.id, data_mode=status["data_mode"], resolved_at=None
    )]
    last = as_utc(status["last_sync_at"])
    stale = last and in_working_hours(now, client.targets) and now - last >= timedelta(minutes=30)
    if stale and not any(row["code"] == "sync_stale" for row in issues):
        issues.append({
            "code": "sync_stale", "severity": "red", "title": "Reporting data is stale",
            "detail": "There has been no successful Sakhaa pull for at least 30 minutes during working hours.",
            "affected_count": 1, "first_seen": iso(last + timedelta(minutes=30)), "last_seen": iso(now),
            "resolved_at": None, "fix_hint": "Check the Sakha sync status and the Reporting API.",
        })
    return sorted(issues, key=lambda item: (SEVERITY[item["severity"]], item["first_seen"], item["code"]))


@router.get("/clients")
def list_clients(db: Session = Depends(get_db), user=Depends(require_sakha)):
    now = utcnow()
    today = now.astimezone(IST).date()
    query = db.query(SakhaClient).order_by(SakhaClient.display_name)
    if user.role not in ADMINS:
        query = query.filter(SakhaClient.account_id.in_(user.assigned_account_ids()))
    clients = []
    for client in query:
        status = sync_status(client)
        snapshot = snapshot_for(db, client, status)
        metrics = calculate(snapshot, today.replace(day=1), today, now, client.stage_map, client.targets)
        issues = active_issues(db, client, status, now)
        health = issues[0]["severity"] if issues else ("green" if status["last_sync_at"] else "amber")
        today_window = day_window(today, today)
        clients.append({
            **client_info(client), **status, "health": health, "open_issue_count": len(issues),
            "leads_today": sum(in_period(person.get("created_at"), today_window) for person in snapshot["persons"]),
            "leads_month": metrics["headline"]["leads"], "median_first_contact_minutes": metrics["speed"]["median_minutes"],
            "site_visits_month": metrics["headline"]["site_visits_completed"], "bookings_month": metrics["headline"]["bookings"],
        })
    modes = {client["data_mode"] for client in clients}
    mode = "mixed" if len(modes) > 1 else next(iter(modes), config_for(MANTRI_SLUG).mode)
    return {"clients": clients, "data_mode": mode}


@router.get("/clients/{slug}/dashboard")
def dashboard(
    client=Depends(get_client),
    db: Session = Depends(get_db),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    owner: str | None = Query(None, max_length=128),
):
    now = utcnow()
    today = now.astimezone(IST).date()
    start, end = start or today.replace(day=1), end or today
    if start > end or end == date.max:
        raise HTTPException(400, "Choose a valid date range")
    status = sync_status(client)
    snapshot = snapshot_for(db, client, status)
    owners = [{"id": row["id"], "name": row["name"], "active": row.get("active", True)}
              for row in sorted(snapshot["users"], key=lambda row: (row["name"], row["id"]))]
    return {
        **client_info(client), **status, "from": start, "to": end, "owner": owner, "owners": owners,
        "issues": active_issues(db, client, status, now),
        **calculate(snapshot, start, end, now, client.stage_map, client.targets, owner),
    }


@router.get("/clients/{slug}/issues")
def issues(client=Depends(get_client), db: Session = Depends(get_db)):
    status = sync_status(client)
    return {"data_mode": status["data_mode"], "issues": active_issues(db, client, status, utcnow())}


@router.get("/clients/{slug}/sync-status")
def get_sync_status(client=Depends(get_client)):
    return sync_status(client)


@router.post("/clients/{slug}/sync")
def manual_sync(
    client=Depends(get_client), db: Session = Depends(get_db), user=Depends(get_current_user_required),
):
    require_admin_or_superadmin(user)
    client_id = client.id
    db.rollback()
    outcome = sync_client(client_id)
    return {"outcome": outcome, **sync_status(db.get(SakhaClient, client_id))}


@router.put("/clients/{slug}/config")
def update_config(
    payload: ClientConfig, client=Depends(get_client), db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    require_admin_or_superadmin(user)
    if payload.stage_map is not None:
        client.stage_map = payload.stage_map
    if payload.targets is not None:
        client.targets = payload.targets.model_dump(exclude_none=True)
    if payload.widgets is not None:
        client.widgets = payload.widgets
    db.commit()
    return {
        "stage_map": client.stage_map, "targets": DEFAULT_TARGETS | client.targets,
        "widgets": client.widgets, **sync_status(client),
    }
