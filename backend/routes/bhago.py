"""Bhago-only sheet links and explicit, locally executed campaign requests."""
import json
from datetime import date, datetime, timezone, timedelta
from uuid import uuid4
from typing import Literal
from urllib.parse import urlparse
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, AppSetting, UserAccountAssignment
from backend.routes.auth import get_current_user_required

router = APIRouter(prefix="/api/bhago", tags=["Bhago Mobility"])
SOURCES = ("swiggy", "zomato", "qwqer", "payment_reminder", "dialed", "test", "test_lead", "test_payment")
TEST_SOURCES = ("test", "test_lead", "test_payment")


def access(account_id, db, user):
    if user.role not in ("admin", "superadmin") and (
        not user.access_insightdesk or not db.query(UserAccountAssignment).filter_by(
            user_id=user.id, account_id=account_id).first()
    ):
        raise HTTPException(403, "Not authorized for this account")
    account = db.query(Account).filter_by(id=account_id).first()
    if not account or account.name.strip().casefold() != "bhago mobility":
        raise HTTPException(404, "Bhago Mobility account not found")
    return account


def setting_key(account_id, source):
    return f"bhago:{account_id}:sheet:{source}"


def validate_sheet_url(value):
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.netloc != "docs.google.com"
            or not re.fullmatch(r"/spreadsheets/d/[A-Za-z0-9_-]+(?:/.*)?", parsed.path)):
        raise HTTPException(400, "Enter a valid Google Sheets link starting with https://docs.google.com/spreadsheets/d/")
    return value


class SheetLink(BaseModel):
    url: str = Field(max_length=2048)


@router.get("/{account_id}/sheets")
def get_sheets(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    keys = [setting_key(account_id, source) for source in SOURCES]
    rows = db.query(AppSetting).filter(AppSetting.key.in_(keys)).all()
    saved = {row.key: row.value or "" for row in rows}
    return {"sheets": {source: saved.get(setting_key(account_id, source), "") for source in SOURCES}}


@router.put("/{account_id}/sheets/{source}")
def save_sheet(account_id: int, source: Literal["swiggy", "zomato", "qwqer", "payment_reminder", "dialed", "test", "test_lead", "test_payment"],
               body: SheetLink, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    url = validate_sheet_url(body.url)
    # Serialize first-save inserts for this account without locking other accounts.
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    key = setting_key(account_id, source)
    row = db.query(AppSetting).filter_by(key=key).first()
    if row:
        row.value = url
    else:
        db.add(AppSetting(key=key, value=url))
    db.commit()
    return {"source": source, "url": url, "saved": True}


RUN_SOURCES = ("swiggy", "zomato", "qwqer", "payment_reminder", "test", "test_lead", "test_payment")
ACTIVE_STATES = ("queued_preview", "reading", "ready", "queued_launch", "launching")


class RunRequest(BaseModel):
    source: Literal["swiggy", "zomato", "qwqer", "payment_reminder", "test", "test_lead", "test_payment"]
    calling_type: Literal["lead", "payment"]
    start_date: date | None = None
    end_date: date | None = None
    auto_launch: bool = False


def run_key(account_id):
    return f"bhago:{account_id}:run:current"


def read_json(row):
    return json.loads(row.value) if row and row.value else None


def runner_online(db, account_id):
    heartbeat = read_json(db.query(AppSetting).filter_by(key=f"bhago:{account_id}:worker").first())
    try:
        return bool(heartbeat and heartbeat.get("browser_ready") and
                    datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat["updated_at"]) < timedelta(seconds=45))
    except (KeyError, ValueError, TypeError):
        return False


@router.get("/{account_id}/run")
def get_run(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    return {"run": read_json(db.query(AppSetting).filter_by(key=run_key(account_id)).first()),
            "worker_online": runner_online(db, account_id)}


@router.post("/{account_id}/run")
def request_run(account_id: int, body: RunRequest, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    if body.source != "test" and body.calling_type != ("payment" if body.source in ("payment_reminder", "test_payment") else "lead"):
        raise HTTPException(400, "Calling type does not match this source")
    if body.calling_type == "lead" and body.source not in TEST_SOURCES:
        if not body.start_date or not body.end_date or body.start_date > body.end_date:
            raise HTTPException(400, "Choose a valid lead date range")
    if not runner_online(db, account_id):
        raise HTTPException(409, "Bhago calling worker is offline. Open the dedicated Bhago browser worker on Shekhar's computer.")
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    row = db.query(AppSetting).filter_by(key=run_key(account_id)).first()
    previous = read_json(row)
    if previous and previous.get("state") in ACTIVE_STATES:
        raise HTTPException(409, "Finish or cancel the current Bhago run first")
    sheet = db.query(AppSetting).filter_by(key=setting_key(account_id, body.source)).first()
    if not sheet or not sheet.value:
        raise HTTPException(400, "Save a Google Sheet link for this source first")
    run = {"id": uuid4().hex, "source": body.source, "calling_type": body.calling_type,
           "start_date": body.start_date.isoformat() if body.start_date else None,
           "end_date": body.end_date.isoformat() if body.end_date else None,
           "auto_launch": body.auto_launch,
           "state": "queued_preview", "requested_by": user.id,
           "created_at": datetime.now(timezone.utc).isoformat(),
           "message": "Reading the latest saved sheet. No calls started."}
    if not row:
        row = AppSetting(key=run_key(account_id))
        db.add(row)
    row.value = json.dumps(run)
    db.commit()
    return {"run": run}


@router.post("/{account_id}/run/{request_id}/{action}")
def run_action(account_id: int, request_id: str, action: Literal["launch", "cancel"],
               db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    row = db.query(AppSetting).filter_by(key=run_key(account_id)).with_for_update().first()
    run = read_json(row)
    if not run or run.get("id") != request_id:
        raise HTTPException(409, "This run is no longer current")
    if action == "launch":
        if run.get("state") != "ready":
            raise HTTPException(409, "Only a validated preview can be launched")
        if not runner_online(db, account_id):
            raise HTTPException(409, "Calling worker is offline")
        run.update(state="queued_launch", launched_by=user.id,
                   message="Campaign launch requested. Do not submit again.")
    else:
        if run.get("state") not in ("queued_preview", "ready"):
            raise HTTPException(409, "This run cannot be cancelled at its current stage")
        run.update(state="cancelled", message="Cancelled before launch. No calls started.")
    row.value = json.dumps(run)
    db.commit()
    return {"run": run}
