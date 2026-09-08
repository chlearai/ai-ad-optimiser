"""
AdGuard — Google Ads lead form intake routes.

Endpoints:
  POST /api/adguard/webhook        -> Google Ads lead form webhook (score + LSQ push)
  GET  /api/adguard/leads          -> list scored leads (verified + flagged)
  GET  /api/adguard/stats          -> dashboard KPIs
  POST /api/adguard/leads/{id}/retry-lsq  -> re-push a verified lead that failed
  GET  /api/adguard/oauth/connect          -> create/reuse workspace, get Google OAuth URL
  GET  /api/adguard/oauth/callback         -> Google OAuth callback (stores tokens, discovers accounts)
  GET  /api/adguard/oauth/accounts         -> list my workspaces + discovered ad accounts
  POST /api/adguard/oauth/select           -> pick which discovered ad accounts to protect
  POST /api/adguard/oauth/disconnect       -> remove a workspace
"""
import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Account, AdGuardAccount, AdGuardLead, User
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
    x_google_ledform_digest: str = Header(default="", alias="X-Google-Leadform-Digest"),
    token: Optional[str] = None,
):
    """Receive a Google Ads lead form submission.

    Accepts two auth schemes:
      1. Native Google Ads lead form webhook — Google signs each POST with an
         HMAC-SHA256 base64 digest in the `X-Google-Leadform-Digest` header,
         computed with the secret key configured in the lead form asset.
      2. Shared token (ours) — `X-AdGuard-Token` header or `?token=` query
         param, for Apps Script / Zapier bridges and testing.

    Body: Google sends urlencoded (`form_data=<json>&google_key=<key>`) or
    XML; bridges send JSON. All are normalized downstream.
    """
    raw = (await request.body()).decode("utf-8") or ""

    # --- Scheme 1: Google native HMAC digest ---
    if x_google_ledform_digest:
        secret = os.getenv("ADGUARD_GOOGLE_WEBHOOK_KEY", "")
        if not secret:
            raise HTTPException(status_code=500, detail="ADGUARD_GOOGLE_WEBHOOK_KEY not configured")
        import base64
        import hashlib
        import hmac as hmac_mod

        expected = base64.b64encode(
            hmac_mod.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
        ).decode()
        if not hmac_mod.compare_digest(expected, x_google_ledform_digest):
            logger.warning("[AdGuard] Google leadform digest mismatch")
            raise HTTPException(status_code=403, detail="Invalid Google digest")

        # Google body: urlencoded form OR XML; extract lead data
        payload = _parse_google_native_body(raw)
        if payload is None:
            raise HTTPException(status_code=400, detail="Unparseable Google lead payload")
    else:
        # --- Scheme 2: shared token ---
        supplied = x_adguard_token or token or ""
        if not _verify_token(supplied):
            raise HTTPException(status_code=403, detail="Invalid webhook token")
        try:
            payload = json.loads(raw) if raw else {}
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


def _parse_google_native_body(raw: str) -> Optional[Dict[str, Any]]:
    """Parse Google Ads native lead form webhook body.

    Google posts either:
      - urlencoded: `form_data=<urlencoded json>&google_key=<key>` (and in
        newer versions `lead_form_type`, `campaign_id`, `gclid`, etc.)
      - XML: <LeadFormResponses><LeadFormResponse>... (legacy)

    Returns a flat dict of lead fields, or None if unparseable.
    """
    import urllib.parse
    import xml.etree.ElementTree as ET

    # Try urlencoded first
    try:
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        if "form_data" in parsed:
            inner = parsed["form_data"][0]
            # inner may itself be urlencoded JSON
            try:
                inner_decoded = urllib.parse.unquote(inner)
            except Exception:
                inner_decoded = inner
            data = json.loads(inner_decoded)
            if isinstance(data, list):
                # list of {"column_name": ..., "string_value"/"user_input": ...}
                flat: Dict[str, Any] = {}
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    key = item.get("column_name") or item.get("field_name") or ""
                    val = item.get("string_value") or item.get("user_input") or item.get("value") or ""
                    if key:
                        flat[key] = val
                return flat
            if isinstance(data, dict):
                return data
        if parsed:
            # some integrations post flat urlencoded fields directly
            return {k: v[0] for k, v in parsed.items() if v}
    except Exception as e:
        logger.warning(f"[AdGuard] urlencoded parse failed: {e}")

    # Try XML (legacy format)
    try:
        root = ET.fromstring(raw)
        flat = {}
        for field in root.iter():
            tag = field.tag.split("}")[-1]
            if tag in ("LeadFormField", "UserLeadFieldValue"):
                continue
            if field.text and field.text.strip():
                flat[tag] = field.text.strip()
        if flat:
            return flat
    except Exception as e:
        logger.warning(f"[AdGuard] XML parse failed: {e}")

    return None


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


# ---------------------------------------------------------------------------
# Self-serve OAuth (Ryze-style Connect flow)
# ---------------------------------------------------------------------------


def _get_or_create_workspace(db: Session, user: User) -> AdGuardAccount:
    """One AdGuard workspace per user email (extend to many later if needed)."""
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == user.email).first()
    if not ws:
        ws = AdGuardAccount(
            owner_email=user.email,
            display_name=user.full_name or user.email,
        )
        db.add(ws)
        db.commit()
        db.refresh(ws)
    return ws


@router.get("/oauth/connect")
def oauth_connect(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Create/reuse the user's AdGuard workspace and return the Google OAuth URL."""
    _require_adguard_access(user)
    ws = _get_or_create_workspace(db, user)
    try:
        from backend.services.oauth import get_adguard_auth_url

        url = get_adguard_auth_url(ws.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"authorization_url": url, "workspace_id": ws.id}


@router.get("/oauth/callback")
def oauth_callback(code: str, state: str, error: Optional[str] = None, db: Session = Depends(get_db)):
    """Google redirects here after consent. Stores tokens, discovers accounts."""
    if error:
        return RedirectResponse(url=f"/adguard?oauth_error={error}")
    try:
        from backend.services.oauth import parse_state

        payload = parse_state(state)
    except Exception:
        payload = None
    if not payload or payload.get("platform") != "adguard_google":
        return RedirectResponse(url="/adguard?oauth_error=invalid_state")
    ws_id = payload.get("adguard_account_id")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == ws_id).first()
    if not ws:
        return RedirectResponse(url="/adguard?oauth_error=workspace_not_found")

    from backend.services.config import load_config
    from backend.services import oauth as oauth_service

    cfg = load_config()
    cfg_redirect_base = (cfg.get("redirect_base_url") or "http://127.0.0.1:8000").rstrip("/")
    redirect_uri = f"{cfg_redirect_base}/api/adguard/oauth/callback"

    token_data = oauth_service.exchange_google_code(code, redirect_uri, None)
    if not token_data:
        return RedirectResponse(url="/adguard?oauth_error=google_token_exchange_failed")

    # Reuse build_google_credentials pattern but bypass the Account lookup (AdGuard workspace).
    refresh_token = token_data.get("refresh_token") or token_data.get("access_token")
    if not refresh_token:
        return RedirectResponse(url="/adguard?oauth_error=missing_refresh_token")
    creds = {
        "developer_token": cfg.get("google_developer_token", ""),
        "client_id": cfg.get("google_client_id", ""),
        "client_secret": cfg.get("google_client_secret", ""),
        "refresh_token": refresh_token,
        "login_customer_id": "",
    }
    from backend.services.crypto import encrypt as fernet_encrypt

    ws.google_credentials = fernet_encrypt(json.dumps(creds))
    ws.google_is_live = True
    db.commit()

    # Auto-discover accessible Google Ads accounts (never blocks connect).
    try:
        discovered = oauth_service.discover_google_ads_customers(ws.google_credentials)
        ws.discovered_accounts = json.dumps(discovered) if discovered else "[]"
        db.commit()
    except Exception as e:
        logger.warning(f"[AdGuard] post-connect discovery failed: {e}")

    return RedirectResponse(url="/adguard?oauth_success=google")


class SelectAccountsRequest(BaseModel):
    workspace_id: int
    selected_ids: list


@router.post("/oauth/select")
def oauth_select(req: SelectAccountsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Mark which discovered Google Ads accounts this user wants protected."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    accounts = json.loads(ws.discovered_accounts) if ws.discovered_accounts else []
    selected = set(str(s) for s in req.selected_ids)
    for a in accounts:
        a["selected"] = str(a["id"]) in selected
    ws.discovered_accounts = json.dumps(accounts)
    db.commit()
    return {"status": "ok", "selected": list(selected)}


@router.get("/oauth/accounts")
def oauth_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """List my workspaces with connection + discovered account status."""
    _require_adguard_access(user)
    q = db.query(AdGuardAccount)
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    workspaces = q.all()
    return [
        {
            **ws.to_dict(),
            "credentials_set": bool(ws.google_credentials),
        }
        for ws in workspaces
    ]


@router.post("/oauth/disconnect")
def oauth_disconnect(req: SelectAccountsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Remove OAuth tokens from a workspace (keeps lead history)."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    ws.google_credentials = None
    ws.google_is_live = False
    ws.discovered_accounts = "[]"
    db.commit()
    return {"status": "disconnected", "workspace_id": req.workspace_id}