"""
AdGuard — Google Ads lead form intake routes.

Endpoints:
  POST /api/adguard/webhook        -> Google Ads lead form webhook (score + LSQ push)
  GET  /api/adguard/leads          -> list scored leads (verified + flagged)
  GET  /api/adguard/stats          -> dashboard KPIs
  POST /api/adguard/leads/{id}/retry-lsq  -> re-push a verified lead that failed
"""
import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Account, AdGuardLead, User
from backend.routes.auth import get_current_user_required
from backend.services.activity_log import log_activity

logger = logging.getLogger("AdOptima")
router = APIRouter(prefix="/api/adguard", tags=["adguard"])

WEBHOOK_VERIFY_TOKEN = os.getenv("ADGUARD_WEBHOOK_VERIFY_TOKEN", "adguard_verify")


def _require_adguard_access(user: User) -> None:
    """Raise 403 if user lacks AdGuard access. Admins/superadmins always pass."""
    if user.role in ("admin", "superadmin"):
        return
    if not user.access_adguard:
        raise HTTPException(status_code=403, detail="AdGuard access required")


# ---------------------------------------------------------------------------
# Webhook (public — secured by verify token header/query)
# ---------------------------------------------------------------------------


def _verify_token(token: str) -> bool:
    configured = WEBHOOK_VERIFY_TOKEN
    if not configured:
        return True  # not configured -> skip (acceptable locally)
    return token == configured


@router.post("/webhook")
async def webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_adguard_token: str = Header(default="", alias="X-AdGuard-Token"),
    token: Optional[str] = None,
):
    """Receive a Google Ads lead form submission.

    Security: caller must pass the shared secret either as the
    `X-AdGuard-Token` header or `?token=` query param (Google Apps Script
    webhooks can use the query param).
    """
    raw = (await request.body()).decode("utf-8") or "{}"
    supplied = x_adguard_token or token or ""
    if not _verify_token(supplied):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=400, detail="Empty payload")

    # Optional per-client routing: payload may carry account id or the
    # AdGuard account name; otherwise falls back to the first active account
    # (or None — lead is still scored and stored).
    account: Optional[Account] = None
    acct_id = payload.get("account_id") or payload.get("Account ID")
    acct_name = payload.get("account_name") or payload.get("Account Name")
    if acct_id:
        try:
            account = db.query(Account).filter(Account.id == int(acct_id)).first()
        except (TypeError, ValueError):
            account = None
    if account is None and acct_name:
        account = db.query(Account).filter(Account.name == str(acct_name)).first()
    if account is None:
        account = db.query(Account).filter(Account.is_active == True).first()  # noqa: E712

    from backend.services.adguard import process_incoming_lead

    try:
        result = process_incoming_lead(payload, account=account, raw_payload=raw)
    except Exception as e:
        logger.error(f"[AdGuard] webhook processing failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "status": "ok",
        "lead_id": result["id"],
        "verdict": result["verdict"],
        "integrity_score": result["integrity_score"],
        "lsq_status": result["lsq_status"],
    }


# ---------------------------------------------------------------------------
# Authenticated dashboard APIs
# ---------------------------------------------------------------------------


@router.get("/leads")
def list_leads(
    verdict: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    _require_adguard_access(user)
    q = db.query(AdGuardLead)
    if verdict in ("verified", "flagged"):
        q = q.filter(AdGuardLead.verdict == verdict)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(
            (AdGuardLead.full_name.ilike(like))
            | (AdGuardLead.email.ilike(like))
            | (AdGuardLead.phone.ilike(like))
            | (AdGuardLead.campaign_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(AdGuardLead.received_at.desc()).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "leads": [r.to_dict() for r in rows]}


@router.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    total = db.query(AdGuardLead).count()
    verified = db.query(AdGuardLead).filter(AdGuardLead.verdict == "verified").count()
    flagged = db.query(AdGuardLead).filter(AdGuardLead.verdict == "flagged").count()
    pushed = db.query(AdGuardLead).filter(AdGuardLead.lsq_status == "pushed").count()
    push_failed = db.query(AdGuardLead).filter(AdGuardLead.lsq_status == "failed").count()
    avg_score = db.query(func.avg(AdGuardLead.integrity_score)).scalar()
    return {
        "total": total,
        "verified": verified,
        "flagged": flagged,
        "pushed_to_lsq": pushed,
        "lsq_push_failed": push_failed,
        "avg_integrity_score": round(float(avg_score), 1) if avg_score is not None else None,
    }


@router.post("/leads/{lead_id}/retry-lsq")
def retry_lsq(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    record = db.query(AdGuardLead).filter(AdGuardLead.id == lead_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Lead not found")
    if record.verdict != "verified":
        raise HTTPException(status_code=400, detail="Only verified leads can be pushed to LeadSquared")

    from backend.services.adguard import push_lead_to_lsq

    account = db.query(Account).filter(Account.id == record.account_id).first() if record.account_id else None
    lead_payload = {
        "full_name": record.full_name,
        "email": record.email,
        "phone": record.phone,
        "city": record.city,
        "state": record.state,
        "country": record.country,
        "campaign_name": record.campaign_name,
        "source": record.lead_type or "Google Ads Lead Form",
    }
    push = push_lead_to_lsq(lead_payload, account)
    record.lsq_status = push["status"]
    record.lsq_prospect_id = push["prospect_id"]
    record.lsq_error = push["error"]
    db.commit()

    log_activity(
        module="AdGuard",
        action="LSQ Retry",
        description=f"Re-pushed lead {record.email or record.phone} to LeadSquared ({push['status']})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_lead",
        entity_id=str(record.id),
        db=db,
    )
    return {"status": push["status"], "prospect_id": push["prospect_id"], "error": push["error"]}