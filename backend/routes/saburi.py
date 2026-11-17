"""Saved client-facing Google Sheet link for the Saburi InsightDesk account."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Account, AppSetting, UserAccountAssignment
from backend.routes.auth import get_current_user_required
from backend.routes.bhago import validate_sheet_url

router = APIRouter(prefix="/api/saburi", tags=["Saburi Plywood"])


def _sheet_access(account_id, db, user):
    if user.role not in ("admin", "superadmin") and (
        not user.access_insightdesk or not db.query(UserAccountAssignment).filter_by(
            user_id=user.id, account_id=account_id).first()
    ):
        raise HTTPException(403, "Not authorized for this account")
    account = db.query(Account).filter_by(id=account_id).first()
    if not account or account.name.strip().casefold() != "saburi plywood":
        raise HTTPException(404, "Saburi Plywood account not found")


class SaburiSheetLink(BaseModel):
    url: str = Field(max_length=2048)


@router.get("/{account_id}/sheet")
def get_sheet_link(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    _sheet_access(account_id, db, user)
    row = db.query(AppSetting).filter_by(key=f"saburi:{account_id}:sheet:leads").first()
    return {"url": row.value or "" if row else ""}


@router.put("/{account_id}/sheet")
def save_sheet_link(account_id: int, body: SaburiSheetLink,
                    db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    _sheet_access(account_id, db, user)
    url = validate_sheet_url(body.url)
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    key = f"saburi:{account_id}:sheet:leads"
    row = db.query(AppSetting).filter_by(key=key).first()
    if row:
        row.value = url
    else:
        db.add(AppSetting(key=key, value=url))
    db.commit()
    return {"url": url, "saved": True}
