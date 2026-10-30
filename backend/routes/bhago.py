"""Bhago Mobility's saved sheet links. Does not read sheets or start calls."""
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
SOURCES = ("swiggy", "zomato", "qwqer", "payment_reminder", "dialed", "test")


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
def save_sheet(account_id: int, source: Literal["swiggy", "zomato", "qwqer", "payment_reminder", "dialed", "test"],
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
