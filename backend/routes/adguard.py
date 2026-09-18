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
import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Account, AdGuardAccount, AdGuardLead, AppSetting, User
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
    x_google_response_key: str = Header(default="", alias="Lead-Response-Webhook-Key"),
    token: Optional[str] = None,
):
    """Receive a Google Ads lead form submission.

    Accepts three auth schemes:
      1. Native Google Ads lead form webhook — Google signs each POST with an
         HMAC-SHA256 digest of the body in `X-Google-Leadform-Digest`
         (base64 or hex) computed with the secret key configured in the lead
         form asset. Google may also send the raw key in
         `Lead-Response-Webhook-Key`; either authenticates.
      2. Shared token (ours) — `X-AdGuard-Token` header or `?token=` query
         param, for Apps Script / Zapier bridges and testing.

    Body: Google sends urlencoded (`form_data=<json>&google_key=<key>`) or
    XML; bridges send JSON. All are normalized downstream.
    """
    raw = (await request.body()).decode("utf-8") or ""
    logger.info(
        f"[AdGuard] webhook hit: digest_hdr={'yes' if x_google_ledform_digest else 'no'} "
        f"key_hdr={'yes' if x_google_response_key else 'no'} tok_hdr={'yes' if x_adguard_token else 'no'} "
        f"q_token={'yes' if token else 'no'} body_len={len(raw)} ctype={request.headers.get('content-type','')}"
    )

    # --- Scheme 1: Google native (HMAC digest or echoed key header) ---
    # Google's CURRENT webhook format is JSON: {lead_id, user_column_data: [...],
    # google_key: "<key>"} — the key arrives INSIDE the JSON body. It may also
    # send X-Google-Leadform-Digest (HMAC) or Lead-Response-Webhook-Key headers.
    # Legacy formats: urlencoded form_data=...&google_key=... and XML.
    is_google_native = bool(x_google_ledform_digest or x_google_response_key)
    body_json: Optional[Dict[str, Any]] = None
    try:
        parsed_body = json.loads(raw) if raw else None
        if isinstance(parsed_body, dict):
            body_json = parsed_body
    except Exception:
        body_json = None
    if body_json and "google_key" in body_json:
        is_google_native = True

    if is_google_native:
        secret = os.getenv("ADGUARD_GOOGLE_WEBHOOK_KEY", "")
        if not secret:
            logger.error("[AdGuard] ADGUARD_GOOGLE_WEBHOOK_KEY not configured on server")
            raise HTTPException(status_code=500, detail="ADGUARD_GOOGLE_WEBHOOK_KEY not configured")

        authed = False
        if x_google_response_key and hmac.compare_digest(x_google_response_key.strip(), secret):
            authed = True
        if not authed and x_google_ledform_digest:
            mac = hmac.new(secret.encode(), raw.encode(), hashlib.sha256)
            candidates = {
                base64.b64encode(mac.digest()).decode(),  # base64 digest
                mac.hexdigest(),                          # hex digest
            }
            supplied = x_google_ledform_digest.strip()
            if any(hmac.compare_digest(c, supplied) for c in candidates):
                authed = True
        if not authed and body_json:
            # Google's current JSON format carries the key inside the body
            body_key = str(body_json.get("google_key") or "")
            if body_key and hmac.compare_digest(body_key.strip(), secret):
                authed = True
        if not authed:
            logger.warning("[AdGuard] Google webhook auth FAILED (digest/key mismatch)")
            raise HTTPException(status_code=403, detail="Invalid Google digest")

        # Google body: JSON (current), urlencoded, or XML (legacy)
        payload = _parse_google_native_body(raw)
        if payload is None:
            logger.error(f"[AdGuard] unparseable Google body (first 400 chars): {raw[:400]}")
            raise HTTPException(status_code=400, detail="Unparseable Google lead payload")
        logger.info(f"[AdGuard] Google native payload keys: {list(payload.keys())[:10]}")
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

    from backend.services.adguard import process_incoming_lead, QuotaExceededError as QuotaExceeded

    # Respond 200 immediately — Google's webhook test times out on slow
    # responses (Gemini scoring + LSQ push can take 5-10s synchronously).
    # Full pipeline (dedup -> score -> LSQ push -> persist) runs in background.
    import threading

    def _process_background():
        try:
            process_incoming_lead(payload, account=account, raw_payload=raw)
        except QuotaExceeded:
            logger.warning("[AdGuard] lead dropped: workspace over quota")
        except Exception as e:
            logger.error(f"[AdGuard] background lead processing failed: {e}")

    threading.Thread(target=_process_background, daemon=True).start()

    return {
        "status": "ok",
        "queued": True,
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

    # Google's CURRENT format: JSON with lead_id + user_column_data array.
    # {"lead_id": "...", "user_column_data": [{"column_name": "Full Name",
    #   "string_value": "First Last", "column_id": "FULL_NAME"}, ...],
    #  "google_key": "...", "gclid": "...", "campaign_id": ...}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("user_column_data"), list):
            flat: Dict[str, Any] = {}
            for item in data["user_column_data"]:
                if not isinstance(item, dict):
                    continue
                name = (item.get("column_name") or item.get("column_id") or "").strip()
                val = (
                    item.get("string_value")
                    or item.get("user_input")
                    or item.get("value")
                    or ""
                )
                if name:
                    flat[name] = val
                    # also index by column_id for robustness (EMAIL, PHONE_NUMBER, FULL_NAME)
                    cid = (item.get("column_id") or "").strip()
                    if cid:
                        flat[cid] = val
            if data.get("lead_id"):
                flat["lead_id"] = data["lead_id"]
            if data.get("gclid"):
                flat["gclid"] = data["gclid"]
            if data.get("campaign_id"):
                flat["campaign_id"] = data["campaign_id"]
            if data.get("form_id"):
                flat["form_id"] = data["form_id"]
            if data.get("google_key"):  # strip secret from stored payload path
                flat.pop("google_key", None)
            return flat
        if isinstance(data, dict) and data:
            # bridges / other JSON shapes: treat top-level keys as fields
            return data
    except Exception as e:
        logger.warning(f"[AdGuard] JSON parse failed: {e}")

    # Try urlencoded next
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
    workspace_id: Optional[int] = None,
    account_ids: Optional[str] = None,
    verdict: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    _require_adguard_access(user)
    q = db.query(AdGuardLead)
    if user.role not in ("admin", "superadmin"):
        # SaaS scoping: customers only see leads from their own workspace(s)
        ws_ids = [
            ws.id
            for ws in db.query(AdGuardAccount.id)
            .filter(AdGuardAccount.owner_email == user.email)
            .all()
        ]
        if workspace_id and workspace_id in ws_ids:
            q = q.filter(AdGuardLead.adguard_account_id == workspace_id)
        else:
            q = q.filter(AdGuardLead.adguard_account_id.in_(ws_ids or [0]))
    elif workspace_id:
        q = q.filter(AdGuardLead.adguard_account_id == workspace_id)

    if account_ids:
        raw_ids = [s.strip() for s in account_ids.split(",") if s.strip()]
        if raw_ids:
            acct_pk_set = set()
            for s in raw_ids:
                if s.isdigit() and len(s) < 8:
                    acct_pk_set.add(int(s))
            matched_accs = db.query(Account.id).filter(
                (Account.external_id.in_(raw_ids)) | (Account.name.in_(raw_ids))
            ).all()
            for ma in matched_accs:
                acct_pk_set.add(ma[0])

            conds = []
            if acct_pk_set:
                conds.append(AdGuardLead.account_id.in_(list(acct_pk_set)))
            for rid in raw_ids:
                conds.append(AdGuardLead.raw_payload.ilike(f"%{rid}%"))
                conds.append(AdGuardLead.campaign_name.ilike(f"%{rid}%"))
            if conds:
                q = q.filter(or_(*conds))

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
def stats(
    workspace_id: Optional[int] = None,
    account_ids: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    _require_adguard_access(user)
    q = db.query(AdGuardLead)
    if user.role not in ("admin", "superadmin"):
        ws_ids = [
            ws.id
            for ws in db.query(AdGuardAccount.id)
            .filter(AdGuardAccount.owner_email == user.email)
            .all()
        ]
        if workspace_id and workspace_id in ws_ids:
            q = q.filter(AdGuardLead.adguard_account_id == workspace_id)
        else:
            q = q.filter(AdGuardLead.adguard_account_id.in_(ws_ids or [0]))
    elif workspace_id:
        q = q.filter(AdGuardLead.adguard_account_id == workspace_id)

    if account_ids:
        raw_ids = [s.strip() for s in account_ids.split(",") if s.strip()]
        if raw_ids:
            acct_pk_set = set()
            for s in raw_ids:
                if s.isdigit() and len(s) < 8:
                    acct_pk_set.add(int(s))
            matched_accs = db.query(Account.id).filter(
                (Account.external_id.in_(raw_ids)) | (Account.name.in_(raw_ids))
            ).all()
            for ma in matched_accs:
                acct_pk_set.add(ma[0])

            conds = []
            if acct_pk_set:
                conds.append(AdGuardLead.account_id.in_(list(acct_pk_set)))
            for rid in raw_ids:
                conds.append(AdGuardLead.raw_payload.ilike(f"%{rid}%"))
                conds.append(AdGuardLead.campaign_name.ilike(f"%{rid}%"))
            if conds:
                q = q.filter(or_(*conds))

    total = q.count()
    verified = q.filter(AdGuardLead.verdict == "verified").count()
    flagged = q.filter(AdGuardLead.verdict == "flagged").count()
    pushed = q.filter(AdGuardLead.lsq_status == "pushed").count()
    push_failed = q.filter(AdGuardLead.lsq_status == "failed").count()
    avg_score = q.with_entities(func.avg(AdGuardLead.integrity_score)).scalar()
    return {
        "total": total,
        "verified": verified,
        "flagged": flagged,
        "pushed_to_lsq": pushed,
        "lsq_push_failed": push_failed,
        "avg_integrity_score": round(float(avg_score), 1) if avg_score is not None else None,
    }


class TestLeadCleanupRequest(BaseModel):
    patterns: Optional[list] = None  # email/phone/name substrings to delete
    older_than_days: Optional[int] = None
    _preview_only: Optional[bool] = None  # dry run: return count + preview, delete nothing


def _scoped_lead_ids(db: Session, user: User):
    if user.role in ("admin", "superadmin"):
        return None  # no restriction
    ws_ids = [ws.id for ws in db.query(AdGuardAccount.id).filter(AdGuardAccount.owner_email == user.email).all()]
    return ws_ids or [0]


@router.post("/leads/cleanup-test-leads")
def cleanup_test_leads(req: TestLeadCleanupRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Delete test/junk leads. Admin: all workspaces. Customer: own workspace only.

    Two modes (can combine):
      - patterns: delete leads whose email/phone/full_name/campaign contains any substring
      - older_than_days: delete leads older than N days
    Returns counts + preview of what was removed.
    """
    _require_adguard_access(user)
    if not req.patterns and not req.older_than_days:
        raise HTTPException(status_code=400, detail="Provide patterns or older_than_days")
    q = db.query(AdGuardLead)
    ids = _scoped_lead_ids(db, user)
    if ids is not None:
        q = q.filter(AdGuardLead.adguard_account_id.in_(ids))
    conditions = []
    if req.patterns:
        for p in req.patterns:
            pat = f"%{p.strip()}%"
            conditions.append(
                (AdGuardLead.email.ilike(pat))
                | (AdGuardLead.phone.ilike(pat))
                | (AdGuardLead.full_name.ilike(pat))
                | (AdGuardLead.campaign_name.ilike(pat))
            )
    if req.older_than_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=req.older_than_days)
        conditions.append(AdGuardLead.received_at < cutoff)
    from sqlalchemy import or_
    q = q.filter(or_(*conditions))
    to_delete = q.all()
    preview = [
        {"id": l.id, "name": l.full_name, "email": l.email, "phone": l.phone, "verdict": l.verdict}
        for l in to_delete[:20]
    ]
    count = len(to_delete)
    if getattr(req, "_preview_only", False):
        return {"status": "preview", "deleted": count, "preview": preview}
    for l in to_delete:
        db.delete(l)
    db.commit()
    log_activity(
        module="AdGuard",
        action="Test Lead Cleanup",
        description=f"Deleted {count} test leads (patterns={req.patterns}, older_than_days={req.older_than_days})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        db=db,
    )
    return {"status": "ok", "deleted": count, "preview": preview}


@router.get("/leads/export")
def export_leads_csv(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """CSV export of leads. Admin: all. Customer: own workspace only."""
    _require_adguard_access(user)
    q = db.query(AdGuardLead)
    ids = _scoped_lead_ids(db, user)
    if ids is not None:
        q = q.filter(AdGuardLead.adguard_account_id.in_(ids))
    rows = q.order_by(AdGuardLead.received_at.desc()).limit(5000).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "received_at", "full_name", "email", "phone", "city", "state", "country", "postal_code",
        "campaign_name", "lead_type", "integrity_score", "verdict", "lsq_status", "flags",
    ])
    for l in rows:
        writer.writerow([
            l.received_at.isoformat() if l.received_at else "",
            l.full_name or "", l.email or "", l.phone or "", l.city or "", l.state or "", l.country or "", l.postal_code or "",
            l.campaign_name or "", l.lead_type or "", l.integrity_score, l.verdict, l.lsq_status or "", l.flags or "",
        ])
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=adguard_leads.csv"},
    )


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


class CreateWorkspaceRequest(BaseModel):
    name: str
    platform: Optional[str] = "google"  # which connector to launch after creation


PLAN_WORKSPACE_LIMITS = {"trial": 1, "starter": 1, "pro": 3, "agency": 10}


@router.post("/workspaces/create")
def create_workspace(req: CreateWorkspaceRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Multi-account-per-login: create an additional named workspace (plan-limited).

    Pro = 3 workspaces, Agency = 10. Returns the OAuth URL to connect the new
    account's platform right away (one flow: create -> consent -> bound to new ws).
    """
    _require_adguard_access(user)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workspace name required")

    my_ws = db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == user.email).all()
    if user.role in ("admin", "superadmin"):
        my_count = db.query(AdGuardAccount).count()  # admins manage all; limit applies per-owner
        my_count = len(my_ws) if my_ws else 0
    else:
        my_count = len(my_ws)

    # Plan limit check (from the user's first workspace plan; default trial)
    plan = (my_ws[0].plan if my_ws else "trial") or "trial"
    limit = PLAN_WORKSPACE_LIMITS.get(plan, 1)
    if my_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Plan '{plan}' allows {limit} workspace(s). Upgrade to Pro (3) or Agency (10) for more.",
        )

    ws = AdGuardAccount(owner_email=user.email, display_name=name)
    db.add(ws)
    db.commit()
    db.refresh(ws)

    platform = (req.platform or "google").lower()
    try:
        if platform == "meta":
            from backend.services.adguard_meta import get_adguard_meta_auth_url
            url = get_adguard_meta_auth_url(ws.id)
        else:
            from backend.services.oauth import get_adguard_auth_url
            url = get_adguard_auth_url(ws.id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"workspace_id": ws.id, "display_name": ws.display_name, "authorization_url": url}


@router.get("/oauth/connect")
def oauth_connect(workspace_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Create/reuse the user's AdGuard workspace and return the Google OAuth URL.
    Admins can pass workspace_id to connect on behalf of a subscriber."""
    _require_adguard_access(user)
    admin_initiated = False
    if workspace_id and user.role in ("admin", "superadmin"):
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Subscriber workspace not found")
        admin_initiated = True
    else:
        ws = _get_or_create_workspace(db, user)
    try:
        from backend.services.oauth import get_adguard_auth_url

        url = get_adguard_auth_url(ws.id, admin_initiated=admin_initiated)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"authorization_url": url, "workspace_id": ws.id}


@router.get("/oauth/meta/connect")
def oauth_meta_connect(workspace_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Ryze-style Connect Meta Ads: return Meta's OAuth dialog URL.
    Admins can pass workspace_id to connect on behalf of a subscriber."""
    _require_adguard_access(user)
    admin_initiated = False
    if workspace_id and user.role in ("admin", "superadmin"):
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Subscriber workspace not found")
        admin_initiated = True
    else:
        ws = _get_or_create_workspace(db, user)
    try:
        from backend.services.adguard_meta import get_adguard_meta_auth_url

        url = get_adguard_meta_auth_url(ws.id, admin_initiated=admin_initiated)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"authorization_url": url, "workspace_id": ws.id}


@router.get("/oauth/meta/callback")
def oauth_meta_callback(code: Optional[str] = None, error: Optional[str] = None,
                        error_description: Optional[str] = None, state: Optional[str] = None,
                        db: Session = Depends(get_db)):
    """Meta redirects here after the consent dialog. Stores token, discovers accounts + Pages."""
    if error:
        desc = error_description or error
        return RedirectResponse(url=f"/adguard?oauth_error=meta_{error}&detail={desc}")
    if not code:
        return RedirectResponse(url="/adguard?oauth_error=meta_missing_code")

    from backend.services.adguard_meta import (
        exchange_adguard_meta_code,
        build_meta_credentials,
        discover_meta_ad_accounts,
        discover_meta_pages,
        get_meta_profile_label,
        get_meta_profile_info,
    )

    token = exchange_adguard_meta_code(code)
    if not token:
        return RedirectResponse(url="/adguard?oauth_error=meta_token_exchange_failed")

    # The OAuth `state` carries the workspace id that started the flow (SaaS-safe:
    # each customer's token lands in their own workspace).
    # Admin-initiated flows encode state as "<id>_admin".
    ws = None
    meta_admin_initiated = False
    state_clean = state or ""
    if state_clean.endswith("_admin"):
        meta_admin_initiated = True
        state_clean = state_clean[:-6]  # strip "_admin"
    if state_clean and state_clean.isdigit():
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == int(state_clean)).first()
    if ws is None:
        ws = db.query(AdGuardAccount).order_by(AdGuardAccount.created_at.asc()).first()
    if not ws:
        return RedirectResponse(url="/adguard?oauth_error=workspace_not_found")

    # Identify which Meta (Facebook) login granted access
    profile_info = get_meta_profile_info(token)
    meta_email = profile_info.get("email")
    identity_label = meta_email or profile_info.get("name") or get_meta_profile_label(token)

    ws.meta_credentials = build_meta_credentials(token)
    ws.meta_is_live = True

    # Multi-identity: append THIS Meta login's entry with its OWN creds + accounts
    try:
        identities = json.loads(ws.meta_identities) if ws.meta_identities else []
        if identity_label:
            identities = [i for i in identities if i.get("label") != identity_label and i.get("email") != identity_label]
        else:
            fallback = ws.owner_email if (ws.owner_email and "@" in ws.owner_email) else "meta-account"
            base = fallback
            placeholder = base
            n = 2
            existing = {i.get("label") for i in identities} | {i.get("email") for i in identities}
            while placeholder in existing:
                placeholder = f"{base}-{n}"
                n += 1
            identity_label = placeholder
        identities.append({
            "label": identity_label,
            "email": meta_email or (ws.owner_email if (ws.owner_email and "@" in ws.owner_email) else identity_label),
            "name": profile_info.get("name") or "",
            "credentials": build_meta_credentials(token),
            "connected_at": datetime.utcnow().isoformat(),
        })
        ws.meta_identities = json.dumps(identities)
    except Exception as e:
        logger.warning(f"[AdGuard] meta_identities update failed: {e}")
    db.commit()

    try:
        accounts = discover_meta_ad_accounts(token)
        pages = discover_meta_pages(token)
        # stash on the identity entry
        try:
            identities = json.loads(ws.meta_identities) if ws.meta_identities else []
            for i in identities:
                if i.get("label") == (identity_label or "") or i.get("email") == (identity_label or ""):
                    i["discovered_accounts"] = accounts or []
                    i["discovered_pages"] = pages or []
            ws.meta_identities = json.dumps(identities)
        except Exception:
            pass
        ws.discovered_meta_accounts = json.dumps(accounts) if accounts else "[]"
        ws.discovered_meta_pages = json.dumps(pages) if pages else "[]"
        db.commit()

        # Auto-subscribe manageable Pages to leadgen webhooks (best-effort)
        from backend.services.adguard_meta import get_page_access_token, subscribe_page_to_app

        for page in pages:
            if not page.get("can_subscribe"):
                continue
            try:
                page_token = get_page_access_token(token, page["id"])
                if page_token:
                    subscribe_page_to_app(page["id"], page_token)
            except Exception as pe:
                logger.warning(f"[AdGuard] Page subscribe skipped for {page.get('id')}: {pe}")
    except Exception as e:
        logger.warning(f"[AdGuard] Meta discovery failed (token still stored): {e}")

    redirect_target = "/adguard" if meta_admin_initiated else f"/adguard-workspace?ws={ws.id}"
    return RedirectResponse(url=f"{redirect_target}?oauth_success=meta" if not meta_admin_initiated else f"{redirect_target}?oauth_success=meta&ws={ws.id}")


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
    google_admin_initiated = payload.get("admin_initiated", False) if payload else False
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

    # Identify which Google account granted access (for multi-identity list).
    # Primary: id_token JWT (always present when userinfo.email scope granted).
    # Fallback: userinfo API. Last resort: unique placeholder so multiple
    # connects never overwrite each other.
    identity_email = None
    try:
        id_token = token_data.get("id_token") or ""
        if id_token and id_token.count(".") >= 2:
            import base64 as _b64
            payload_b64 = id_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            identity_email = (json.loads(_b64.urlsafe_b64decode(payload_b64).decode()) or {}).get("email")
    except Exception as e:
        logger.warning(f"[AdGuard] id_token decode failed: {e}")
    if not identity_email:
        for userinfo_url in (
            "https://www.googleapis.com/oauth2/v3/userinfo",
            "https://openidconnect.googleapis.com/v1/userinfo",
            "https://www.googleapis.com/oauth2/v2/userinfo",
        ):
            try:
                tok_req = urllib.request.Request(
                    userinfo_url,
                    headers={"Authorization": f"Bearer {token_data.get('access_token', '')}"},
                )
                with urllib.request.urlopen(tok_req, timeout=10) as tok_resp:
                    resp_data = json.loads(tok_resp.read().decode()) or {}
                    identity_email = resp_data.get("email")
                    if identity_email:
                        break
            except Exception as e:
                logger.warning(f"[AdGuard] {userinfo_url} failed: {e}")

    if not identity_email:
        identity_email = ws.owner_email or "connected-google-user"

    ws.google_credentials = fernet_encrypt(json.dumps(creds))
    ws.google_is_live = True

    # Multi-identity: append THIS Google login's entry with its OWN creds + accounts.
    # Same email replaces its own entry. Also prune any legacy 'google-account' placeholder.
    try:
        identities = json.loads(ws.google_identities) if ws.google_identities else []
        # Filter out identical email AND remove legacy 'google-account' placeholder
        identities = [
            i for i in identities
            if i.get("email") != identity_email and i.get("email") != "google-account" and not str(i.get("email", "")).startswith("google-account")
        ]
        identities.append({
            "email": identity_email,
            "credentials": fernet_encrypt(json.dumps(creds)),
            "connected_at": datetime.utcnow().isoformat(),
        })
        ws.google_identities = json.dumps(identities)
    except Exception as e:
        logger.warning(f"[AdGuard] google_identities update failed: {e}")
    db.commit()

    # Auto-discover accessible Google Ads accounts (never blocks connect).
    # Accounts are stored PER IDENTITY so multiple Gmails keep separate lists.
    try:
        discovered = oauth_service.discover_google_ads_customers(ws.google_credentials)
        try:
            identities = json.loads(ws.google_identities) if ws.google_identities else []
            for i in identities:
                if i.get("email") == identity_email:
                    i["discovered"] = discovered
            ws.google_identities = json.dumps(identities)
        except Exception:
            pass
        # merge ALL identities' accounts into the flat list for lead routing
        try:
            merged = []
            seen_ids = set()
            for i in json.loads(ws.google_identities or "[]"):
                for a in i.get("discovered") or []:
                    if a.get("id") not in seen_ids:
                        merged.append(a)
                        seen_ids.add(a.get("id"))
            ws.discovered_accounts = json.dumps(merged) if merged else json.dumps(discovered)
        except Exception:
            ws.discovered_accounts = json.dumps(discovered) if discovered else "[]"
        db.commit()
    except Exception as e:
        logger.warning(f"[AdGuard] post-connect discovery failed: {e}")

    if google_admin_initiated:
        return RedirectResponse(url=f"/adguard?oauth_success=google&ws={ws.id}")
    return RedirectResponse(url=f"/adguard-workspace?ws={ws.id}&oauth_success=google")


class SelectAccountsRequest(BaseModel):
    workspace_id: int
    selected_ids: list
    platform: Optional[str] = "google"  # google | meta


@router.post("/oauth/select")
def oauth_select(req: SelectAccountsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Mark which discovered ad accounts this user wants protected (google or meta)."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    platform = (req.platform or "google").lower()
    field = "discovered_meta_accounts" if platform == "meta" else "discovered_accounts"
    raw = getattr(ws, field)
    accounts = json.loads(raw) if raw else []
    selected = set(str(s) for s in req.selected_ids)
    for a in accounts:
        a["selected"] = str(a.get("id")) in selected
        if "children" in a and isinstance(a["children"], list):
            for c in a["children"]:
                c["selected"] = str(c.get("id")) in selected or a["selected"]
    setattr(ws, field, json.dumps(accounts))

    ident_field = "meta_identities" if platform == "meta" else "google_identities"
    ident_raw = getattr(ws, ident_field)
    if ident_raw:
        try:
            idents = json.loads(ident_raw)
            for ident in idents:
                sub_list = (ident.get("discovered_accounts") if platform == "meta" else ident.get("discovered")) or []
                for sa in sub_list:
                    sa["selected"] = str(sa.get("id")) in selected
                    if "children" in sa and isinstance(sa["children"], list):
                        for c in sa["children"]:
                            c["selected"] = str(c.get("id")) in selected or sa["selected"]
            setattr(ws, ident_field, json.dumps(idents))
        except Exception:
            pass

    db.commit()
    return {"status": "ok", "selected": list(selected)}


class WorkspaceSettingsRequest(BaseModel):
    workspace_id: int
    crm_preference: Optional[str] = None  # leadsquared|zoho|salesforce|hubspot|webhook|none
    shield_enabled: Optional[bool] = None
    shield_junk_threshold: Optional[int] = None
    shield_min_leads: Optional[int] = None


VALID_CRMS = {"leadsquared", "zoho", "salesforce", "hubspot", "webhook", "none"}


@router.post("/workspace/settings")
def workspace_settings(req: WorkspaceSettingsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Per-workspace subscriber settings (CRM delivery target + Money Shield Layer 1). Admin or owner."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    if req.crm_preference is not None:
        if req.crm_preference not in VALID_CRMS:
            raise HTTPException(status_code=400, detail="Invalid CRM choice")
        ws.crm_preference = req.crm_preference
    if req.shield_enabled is not None:
        ws.shield_enabled = req.shield_enabled
    if req.shield_junk_threshold is not None:
        if not (10 <= req.shield_junk_threshold <= 100):
            raise HTTPException(status_code=400, detail="Junk threshold must be 10-100")
        ws.shield_junk_threshold = req.shield_junk_threshold
    if req.shield_min_leads is not None:
        if not (5 <= req.shield_min_leads <= 10000):
            raise HTTPException(status_code=400, detail="Min leads must be 5-10000")
        ws.shield_min_leads = req.shield_min_leads
    db.commit()
    return {
        "status": "ok",
        "crm_preference": ws.crm_preference,
        "shield_enabled": ws.shield_enabled,
        "shield_junk_threshold": ws.shield_junk_threshold,
        "shield_min_leads": ws.shield_min_leads,
    }


class CrmConnectRequest(BaseModel):
    workspace_id: int
    crm: str  # leadsquared|zoho|hubspot|webhook
    credentials: dict  # provider-specific keys


CRM_REQUIRED_KEYS = {
    "leadsquared": ["access_key", "secret_key"],
    "zoho": ["client_id", "client_secret", "refresh_token"],
    "hubspot": ["access_token"],
    "webhook": ["url"],
}


@router.post("/crm/connect")
def crm_connect(req: CrmConnectRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Store the subscriber's CRM credentials (encrypted) + set crm_preference. Owner or admin."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    crm = (req.crm or "").strip().lower()
    if crm not in CRM_REQUIRED_KEYS:
        raise HTTPException(status_code=400, detail="Unsupported CRM (leadsquared/zoho/hubspot/webhook)")
    missing = [k for k in CRM_REQUIRED_KEYS[crm] if not (req.credentials or {}).get(k)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")

    from backend.services.crypto import encrypt as fernet_encrypt

    ws.crm_preference = crm
    ws.crm_credentials = fernet_encrypt(json.dumps(req.credentials))
    db.commit()
    log_activity(
        module="AdGuard",
        action="CRM Connected",
        description=f"Workspace {ws.display_name or ws.owner_email} connected {crm}",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(ws.id),
        db=db,
    )
    return {"status": "ok", "crm": crm, "message": "CRM connected. Verified leads will deliver here."}


class CrmTestRequest(BaseModel):
    workspace_id: int


@router.post("/crm/test")
def crm_test(req: CrmTestRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Send a test lead to the workspace's configured CRM. Owner or admin."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    if not ws.crm_preference or ws.crm_preference == "none":
        raise HTTPException(status_code=400, detail="No CRM connected for this workspace")
    from backend.services.adguard_crm import deliver_lead

    test_lead = {
        "full_name": "AdGuard Test Lead",
        "email": f"adguard-test-{int(datetime.utcnow().timestamp())}@test.local",
        "phone": "9999999999",
        "city": "Test",
        "state": "Test",
        "campaign_name": "AdGuard CRM Test",
        "source": "AdGuard Test",
    }
    result = deliver_lead(ws, test_lead)
    # Persist the test outcome so status reflects reality
    if result.get("status") == "failed":
        ws.crm_credentials = ws.crm_credentials  # unchanged; failure surfaced to UI
        db.commit()
    return result


class CrmDisconnectRequest(BaseModel):
    workspace_id: int


@router.post("/crm/disconnect")
def crm_disconnect(req: CrmDisconnectRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Remove CRM credentials + reset preference. Owner or admin."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    ws.crm_credentials = None
    ws.crm_preference = "none"
    db.commit()
    return {"status": "ok", "message": "CRM disconnected. Verified leads are held in AdGuard (CSV export anytime)."}


class ShieldScanRequest(BaseModel):
    workspace_id: Optional[int] = None  # blank = all shield-enabled workspaces (admin)


@router.post("/shield/scan")
def shield_scan(req: ShieldScanRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Run the Money Shield governor now (junk-rate scan + auto-pause). Admin or owner."""
    _require_adguard_access(user)
    from backend.services.adguard_shield import scan_workspace_shield, run_shield_scan_all

    if req.workspace_id:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Not your workspace")
        return scan_workspace_shield(db, ws)
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required for global scan")
    return run_shield_scan_all(db)


class ShieldExclusionsRequest(BaseModel):
    workspace_id: int
    days: int = 30


@router.post("/shield/exclusions")
def shield_exclusions(req: ShieldExclusionsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Build Google Customer Match + Meta Custom Audience exclusion payloads from flagged leads (FraudGraph)."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    from backend.services.adguard_shield import build_fraudgraph_exclusions
    return build_fraudgraph_exclusions(db, req.workspace_id, days=max(1, min(req.days, 365)))


class ShieldSyncRequest(BaseModel):
    workspace_id: int
    days: int = 180


@router.post("/shield/exclusions/sync")
def shield_exclusions_sync(req: ShieldSyncRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Run the Layer 1 exclusion push NOW: flagged leads -> Google Customer Match + Meta Custom Audiences."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")

    from backend.services.adguard_exclusion_sync import (
        push_google_exclusions, push_meta_exclusions_v2, log_shield_action,
    )
    from backend.services.adguard_meta import get_meta_token_from_credentials

    cutoff = datetime.utcnow() - timedelta(days=max(1, min(req.days, 365)))
    leads = (
        db.query(AdGuardLead.email, AdGuardLead.phone)
        .filter(AdGuardLead.adguard_account_id == ws.id)
        .filter(AdGuardLead.verdict == "flagged")
        .filter(AdGuardLead.received_at >= cutoff)
        .all()
    )
    lead_items = [{"email": e or "", "phone": p or ""} for e, p in leads if (e or p)]
    out: Dict[str, Any] = {"workspace_id": ws.id, "flagged_leads": len(lead_items), "window_days": req.days}
    if not lead_items:
        out["note"] = "No flagged leads in window — nothing to push."
        return out

    # Google push (first identity's creds; env-merged inside the service)
    try:
        identities = json.loads(ws.google_identities) if ws.google_identities else []
        if identities and identities[0].get("credentials"):
            g = push_google_exclusions(identities[0]["credentials"], lead_items)
            out["google"] = g
            log_shield_action(db, ws, "google_exclusion_sync" if g.get("ok") else "google_exclusion_sync_failed",
                              (f"{g.get('added', 0)} pushed (customer {g.get('customer_id')})" if g.get("ok")
                               else str(g.get("error"))[:200]))
        else:
            out["google"] = {"ok": False, "added": 0, "error": "no Google identity connected"}
    except Exception as e:
        out["google"] = {"ok": False, "added": 0, "error": str(e)[:200]}

    # Meta push (per identity x ad account)
    meta_results = []
    try:
        meta_idents = json.loads(ws.meta_identities) if ws.meta_identities else []
        if not meta_idents and ws.meta_credentials:
            meta_idents = [{"label": "meta-account", "credentials": ws.meta_credentials,
                            "discovered_accounts": json.loads(ws.discovered_meta_accounts or "[]")}]
        for ident in meta_idents:
            from backend.services.adguard_meta import get_meta_token_from_credentials
            token = get_meta_token_from_credentials(ident.get("credentials") or "")
            if not token:
                continue
            for acc in (ident.get("discovered_accounts") or [])[:10]:
                r = push_meta_exclusions_v2(token, acc.get("id"), lead_items)
                r["account"] = acc.get("name") or acc.get("id")
                meta_results.append(r)
                log_shield_action(db, ws, "meta_exclusion_sync" if r.get("ok") else "meta_exclusion_failed",
                                  f"{acc.get('name') or acc.get('id')}: " + (f"{r.get('added', 0)} pushed" if r.get("ok") else str(r.get("error"))[:180]))
    except Exception as e:
        logger.warning(f"[Shield] meta sync ws {ws.id}: {e}")
    out["meta_results"] = meta_results
    return out


@router.get("/shield/actions/{workspace_id}")
def shield_actions(workspace_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Shield action log for a workspace (pauses, exclusions). Admin or owner."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    try:
        actions = json.loads(ws.shield_actions or "[]")
    except Exception:
        actions = []
    return {
        "workspace_id": workspace_id,
        "shield_enabled": bool(ws.shield_enabled),
        "junk_threshold": ws.shield_junk_threshold,
        "min_leads": ws.shield_min_leads,
        "actions": actions,
    }


class ActivateCampaignsRequest(BaseModel):
    campaign_ids: List[str]
    activate: bool = True
    account_id: Optional[str] = None


@router.post("/workspace/{workspace_id}/campaigns/activate")
def activate_workspace_campaigns(
    workspace_id: int,
    req: ActivateCampaignsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    """Mark which campaigns/accounts are LIVE for screening vs paused/standby."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")

    cached = {}
    if ws.cached_campaigns:
        try:
            cached = json.loads(ws.cached_campaigns) or {}
        except Exception:
            cached = {}

    live_ids = set(cached.get("__live_campaign_ids__") or [])
    target_ids = set(str(cid) for cid in req.campaign_ids)

    if req.activate:
        live_ids.update(target_ids)
    else:
        live_ids.difference_update(target_ids)

    cached["__live_campaign_ids__"] = list(live_ids)

    # Update is_live flags in all account lists
    for aid, clist in cached.items():
        if aid.startswith("__") or not isinstance(clist, list):
            continue
        for item in clist:
            if isinstance(item, dict) and str(item.get("id")) in target_ids:
                item["is_live"] = bool(req.activate)

    ws.cached_campaigns = json.dumps(cached)

    # If account_id supplied, also update account screening status
    if req.account_id:
        aid_clean = str(req.account_id).replace("-", "").lower()
        for field in ("discovered_accounts", "discovered_meta_accounts"):
            raw = getattr(ws, field)
            if raw:
                try:
                    accs = json.loads(raw)
                    for a in accs:
                        if str(a.get("id", "")).replace("-", "").lower() == aid_clean:
                            a["screening_live"] = bool(req.activate)
                    setattr(ws, field, json.dumps(accs))
                except Exception:
                    pass

    db.commit()

    log_activity(
        module="AdGuard",
        action="Campaigns Go-Live" if req.activate else "Campaigns Paused",
        description=f"Workspace {ws.display_name or ws.owner_email} set {len(target_ids)} campaigns to {'LIVE' if req.activate else 'STANDBY'}",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(ws.id),
        db=db,
    )

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "live_campaign_ids": list(live_ids),
        "count": len(target_ids),
        "status": "live" if req.activate else "standby",
        "message": f"{len(target_ids)} campaign(s) are now {'LIVE for screening' if req.activate else 'paused on standby'}",
    }


@router.get("/workspace/{workspace_id}/campaigns")
def get_workspace_campaigns(workspace_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Fetch campaigns and pages map for all accounts in workspace with live screening status."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")

    from backend.services.adguard_campaigns import build_workspace_campaigns_map
    cmap = build_workspace_campaigns_map(ws, db)

    # Retrieve stored live_campaign_ids
    live_ids = []
    if ws.cached_campaigns:
        try:
            stored_cached = json.loads(ws.cached_campaigns) or {}
            live_ids = stored_cached.get("__live_campaign_ids__") or []
        except Exception:
            live_ids = []

    # Overlay is_live status onto the returned campaigns
    live_set = set(str(x) for x in live_ids)
    for aid, clist in cmap.items():
        if isinstance(clist, list):
            for c in clist:
                if isinstance(c, dict):
                    c["is_live"] = str(c.get("id")) in live_set

    return {
        "workspace_id": workspace_id,
        "campaigns_by_account": cmap,
        "live_campaign_ids": live_ids,
    }


@router.post("/workspace/{workspace_id}/sync-campaigns")
def sync_workspace_campaigns(workspace_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Re-scan and cache live campaigns and pages for all accounts in workspace."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")

    from backend.services.adguard_campaigns import build_workspace_campaigns_map
    cmap = build_workspace_campaigns_map(ws, db)

    # Preserve __live_campaign_ids__ when re-caching
    live_ids = []
    if ws.cached_campaigns:
        try:
            stored_cached = json.loads(ws.cached_campaigns) or {}
            live_ids = stored_cached.get("__live_campaign_ids__") or []
        except Exception:
            live_ids = []

    live_set = set(str(x) for x in live_ids)
    for aid, clist in cmap.items():
        if isinstance(clist, list):
            for c in clist:
                if isinstance(c, dict):
                    c["is_live"] = str(c.get("id")) in live_set

    cmap_to_store = dict(cmap)
    if live_ids:
        cmap_to_store["__live_campaign_ids__"] = live_ids

    try:
        ws.cached_campaigns = json.dumps(cmap_to_store)
        db.commit()
    except Exception as e:
        logger.warning(f"Failed caching campaigns for ws {ws.id}: {e}")

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "campaigns_by_account": cmap,
        "live_campaign_ids": live_ids,
    }


@router.get("/oauth/accounts")
def oauth_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    q = db.query(AdGuardAccount)
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    workspaces = q.all()
    out = []
    for ws in workspaces:
        lead_count = (
            db.query(func.count(AdGuardLead.id))
            .filter(AdGuardLead.adguard_account_id == ws.id)
            .scalar()
        ) or 0
        out.append(
            {
                **ws.to_dict(),
                "credentials_set": bool(ws.google_credentials),
                "lead_count": lead_count,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Admin: subscriber management (plan, quota, storage, health)
# ---------------------------------------------------------------------------

PLAN_LIMITS = {
    "trial": {"lead_quota": 100, "workspaces": 1},
    "starter": {"lead_quota": 1000, "workspaces": 1},
    "pro": {"lead_quota": 5000, "workspaces": 3},
    "agency": {"lead_quota": -1, "workspaces": 10},
    "custom": {"lead_quota": 1000, "workspaces": 1},
}


class PlanUpdateRequest(BaseModel):
    plan: Optional[str] = None
    lead_quota: Optional[int] = None
    plan_expires_at: Optional[str] = None
    is_archived: Optional[bool] = None


@router.get("/admin/subscribers")
def admin_subscribers(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """All AdGuard subscribers with storage + connection health + commercial info. Admin/superadmin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    subs = []
    for ws in db.query(AdGuardAccount).order_by(AdGuardAccount.created_at.desc()).all():
        lead_count = (
            db.query(func.count(AdGuardLead.id))
            .filter(AdGuardLead.adguard_account_id == ws.id)
            .scalar()
        ) or 0
        flagged_count = (
            db.query(func.count(AdGuardLead.id))
            .filter(AdGuardLead.adguard_account_id == ws.id, AdGuardLead.verdict == "flagged")
            .scalar()
        ) or 0
        raw_bytes = (
            db.query(func.sum(func.length(AdGuardLead.raw_payload)))
            .filter(AdGuardLead.adguard_account_id == ws.id)
            .scalar()
        ) or 0
        last_lead = (
            db.query(AdGuardLead.received_at)
            .filter(AdGuardLead.adguard_account_id == ws.id)
            .order_by(AdGuardLead.received_at.desc())
            .first()
        )
        quota = ws.lead_quota if ws.lead_quota is not None else 100
        subs.append({
            "id": ws.id,
            "owner_email": ws.owner_email,
            "display_name": ws.display_name or ws.company_name or ws.owner_email,
            "phone": ws.phone or "",
            "company_name": ws.company_name or "",
            "industry": ws.industry or "",
            "plan": ws.plan or "trial",
            "plan_expires_at": ws.plan_expires_at.isoformat() if ws.plan_expires_at else None,
            "lead_quota": quota,
            "overage_policy": ws.overage_policy or "block",
            "payment_mode": ws.payment_mode or "",
            "payment_ref": ws.payment_ref or "",
            "amount_paid": float(ws.amount_paid or 0.0),
            "gst_invoice_no": ws.gst_invoice_no or "",
            "payment_status": ws.payment_status or "paid",
            "account_status": ws.account_status or ("archived" if ws.is_archived else "active"),
            "lead_count": lead_count,
            "flagged_count": flagged_count,
            "storage_bytes": int(raw_bytes),
            "quota_pct": None if quota < 0 else round(100 * lead_count / quota, 1),
            "google_is_live": ws.google_is_live,
            "meta_is_live": ws.meta_is_live,
            "is_archived": bool(ws.is_archived),
            "last_lead_at": last_lead[0].isoformat() if last_lead and last_lead[0] else None,
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
        })
    total_leads = db.query(func.count(AdGuardLead.id)).scalar() or 0
    total_bytes = db.query(func.sum(func.length(AdGuardLead.raw_payload))).scalar() or 0
    return {
        "subscribers": subs,
        "totals": {
            "subscribers": len(subs),
            "leads": total_leads,
            "storage_bytes": int(total_bytes),
            "poller_enabled": os.getenv("ADGUARD_META_POLL_ENABLED", "true").lower() in ("true", "1", "yes"),
            "webhook_hits": list(_webhook_hits),
        },
    }


@router.put("/admin/subscribers/{sub_id}")
def admin_update_subscriber(sub_id: int, req: PlanUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Set plan / quota / archive for a subscriber. Admin/superadmin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    if req.plan is not None:
        if req.plan not in PLAN_LIMITS:
            raise HTTPException(status_code=400, detail="Invalid plan")
        ws.plan = req.plan
        ws.lead_quota = PLAN_LIMITS[req.plan]["lead_quota"]
    if req.lead_quota is not None:
        ws.lead_quota = req.lead_quota
    if req.plan_expires_at is not None:
        try:
            ws.plan_expires_at = datetime.fromisoformat(req.plan_expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date (use YYYY-MM-DD)")
    if req.is_archived is not None:
        ws.is_archived = req.is_archived
    db.commit()
    log_activity(
        module="AdGuard",
        action="Subscriber Updated",
        description=f"Updated subscriber {ws.owner_email} (plan={ws.plan}, quota={ws.lead_quota})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(ws.id),
        db=db,
    )
    return ws.to_dict()


class EditSubscriberRequest(BaseModel):
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    phone: Optional[str] = None
    industry: Optional[str] = None
    plan: Optional[str] = None
    lead_quota: Optional[int] = None
    overage_policy: Optional[str] = None
    plan_expires_at: Optional[str] = None
    payment_mode: Optional[str] = None
    payment_ref: Optional[str] = None
    amount_paid: Optional[float] = None
    gst_invoice_no: Optional[str] = None
    payment_status: Optional[str] = None
    account_status: Optional[str] = None
    is_archived: Optional[bool] = None
    reset_password: Optional[bool] = None  # generates a new password, returned once


@router.put("/admin/subscribers/{sub_id}/edit")
def admin_edit_subscriber(sub_id: int, req: EditSubscriberRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Edit a subscriber: name, company, phone, plan, quota, offline payment, expiry, archive, password reset. Admin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    ws_user = db.query(User).filter(User.email == ws.owner_email).first()

    if req.full_name is not None and req.full_name.strip():
        ws.display_name = req.full_name.strip()
        if ws_user:
            ws_user.full_name = req.full_name.strip()

    if req.company_name is not None:
        ws.company_name = req.company_name.strip()
    if req.phone is not None:
        ws.phone = req.phone.strip()
    if req.industry is not None:
        ws.industry = req.industry.strip()
    if req.overage_policy is not None:
        ws.overage_policy = req.overage_policy.strip()
    if req.payment_mode is not None:
        ws.payment_mode = req.payment_mode.strip()
    if req.payment_ref is not None:
        ws.payment_ref = req.payment_ref.strip()
    if req.amount_paid is not None:
        ws.amount_paid = req.amount_paid
    if req.gst_invoice_no is not None:
        ws.gst_invoice_no = req.gst_invoice_no.strip()
    if req.payment_status is not None:
        ws.payment_status = req.payment_status.strip()
    if req.account_status is not None:
        ws.account_status = req.account_status.strip()

    if req.plan is not None:
        if req.plan not in PLAN_LIMITS:
            raise HTTPException(status_code=400, detail="Invalid plan")
        ws.plan = req.plan
        if req.lead_quota is None:
            ws.lead_quota = PLAN_LIMITS[req.plan]["lead_quota"]

    if req.lead_quota is not None:
        ws.lead_quota = req.lead_quota

    if req.plan_expires_at is not None:
        if req.plan_expires_at.strip():
            try:
                ws.plan_expires_at = datetime.fromisoformat(req.plan_expires_at.strip())
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date (use YYYY-MM-DD)")
        else:
            ws.plan_expires_at = None

    if req.is_archived is not None:
        ws.is_archived = req.is_archived

    new_password = None
    if req.reset_password:
        import secrets as _secrets
        from backend.routes.auth import get_password_hash
        new_password = _secrets.token_urlsafe(8)
        if ws_user:
            ws_user.hashed_password = get_password_hash(new_password)
            ws_user.onboarding_completed = True
            ws_user.is_active = True

    db.commit()
    log_activity(
        module="AdGuard",
        action="Subscriber Edited",
        description=f"Edited subscriber {ws.owner_email} (plan={ws.plan}, reset_password={bool(req.reset_password)})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(ws.id),
        db=db,
    )
    return {
        **ws.to_dict(),
        "new_password": new_password,
        "message": "New password generated — share it securely (shown only once)." if new_password else None,
    }


class BonusQuotaRequest(BaseModel):
    bonus_leads: int


@router.post("/admin/subscribers/{sub_id}/bonus-quota")
def admin_add_bonus_quota(sub_id: int, req: BonusQuotaRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Inject emergency bonus quota to a subscriber. Admin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    if ws.lead_quota >= 0:
        ws.lead_quota += max(0, req.bonus_leads)
        db.commit()
    return {"status": "ok", "new_quota": ws.lead_quota}


class StatusToggleRequest(BaseModel):
    status: str  # active | paused | suspended | expired


@router.post("/admin/subscribers/{sub_id}/toggle-status")
def admin_toggle_subscriber_status(sub_id: int, req: StatusToggleRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Toggle subscriber account status (active, paused, suspended, expired). Admin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    ws.account_status = req.status
    if req.status in ("suspended", "paused"):
        ws.is_archived = False
    db.commit()
    return {"status": "ok", "account_status": ws.account_status}


class DeleteSubscriberRequest(BaseModel):
    confirm: str  # must be the subscriber's email, typed exactly


@router.delete("/admin/subscribers/{sub_id}")
def admin_delete_subscriber(sub_id: int, req: DeleteSubscriberRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Delete a subscriber permanently: workspace + leads + user login. Admin only. Cannot be undone."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    if (req.confirm or "").strip().lower() != (ws.owner_email or "").strip().lower():
        raise HTTPException(status_code=400, detail="Confirmation email does not match — deletion aborted")

    owner_email = ws.owner_email
    deleted_leads = db.query(AdGuardLead).filter(AdGuardLead.adguard_account_id == ws.id).delete()
    db.delete(ws)
    # Remove the login user too (unless it's the admin's own account record)
    ws_user = db.query(User).filter(User.email == owner_email, User.role == "user").first()
    if ws_user:
        db.delete(ws_user)
    db.commit()

    log_activity(
        module="AdGuard",
        action="Subscriber Deleted",
        description=f"Deleted subscriber {owner_email} + {deleted_leads} leads + user login",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(sub_id),
        db=db,
    )
    return {"status": "ok", "deleted_leads": deleted_leads, "deleted_user": bool(ws_user)}


class CreateSubscriberRequest(BaseModel):
    email: str
    full_name: str
    company_name: Optional[str] = None
    phone: Optional[str] = None
    industry: Optional[str] = None
    plan: str = "trial"
    lead_quota: Optional[int] = None
    overage_policy: Optional[str] = "block"
    plan_expires_at: Optional[str] = None
    payment_mode: Optional[str] = None
    payment_ref: Optional[str] = None
    amount_paid: Optional[float] = 0.0
    gst_invoice_no: Optional[str] = None
    payment_status: Optional[str] = "paid"
    password: Optional[str] = None  # auto-generated if blank (instant mode)
    mode: str = "instant"  # instant = show password now | invite = email setup link


@router.post("/admin/create-subscriber")
def admin_create_subscriber(req: CreateSubscriberRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Create a customer: user login + AdGuard workspace + commercial/payment ledger in one call. Admin/superadmin only.

    mode=invite: sends AdGuard-branded setup email; user sets own password via link.
    mode=instant: returns a one-time password and ready-to-share WhatsApp card.
    """
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    if req.plan not in PLAN_LIMITS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    from backend.routes.auth import get_password_hash, ONBOARDING_TOKEN_EXPIRE_HOURS
    import secrets as _secrets

    invite_mode = (req.mode or "instant").lower() == "invite"
    password = req.password or _secrets.token_urlsafe(8)

    if invite_mode:
        # User activates via emailed link (is_active until link used; onboarding token gates it)
        setup_token = _secrets.token_urlsafe(32)
        from datetime import timedelta
        new_user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name=req.full_name or email,
            role="user",
            access_adguard=True,
            onboarding_token=setup_token,
            onboarding_token_expires_at=datetime.utcnow() + timedelta(hours=ONBOARDING_TOKEN_EXPIRE_HOURS),
            onboarding_completed=False,
            is_active=False,
        )
    else:
        new_user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name=req.full_name or email,
            role="user",
            access_adguard=True,
            onboarding_completed=True,
            is_active=True,
        )
    db.add(new_user)

    # Quota logic: use explicit quota if passed, else fallback to plan default
    assigned_quota = req.lead_quota if req.lead_quota is not None else PLAN_LIMITS[req.plan]["lead_quota"]
    parsed_expiry = None
    if req.plan_expires_at and req.plan_expires_at.strip():
        try:
            parsed_expiry = datetime.fromisoformat(req.plan_expires_at.strip())
        except ValueError:
            parsed_expiry = None

    ws = AdGuardAccount(
        owner_email=email,
        display_name=req.full_name or req.company_name or email,
        company_name=req.company_name or req.full_name or "",
        phone=req.phone or "",
        industry=req.industry or "",
        plan=req.plan,
        lead_quota=assigned_quota,
        overage_policy=req.overage_policy or "block",
        plan_expires_at=parsed_expiry,
        payment_mode=req.payment_mode or "",
        payment_ref=req.payment_ref or "",
        amount_paid=req.amount_paid or 0.0,
        gst_invoice_no=req.gst_invoice_no or "",
        payment_status=req.payment_status or "paid",
        account_status="active",
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)

    log_activity(
        module="AdGuard",
        action="Subscriber Created",
        description=f"Created subscriber {email} ({req.company_name or req.full_name}, plan={req.plan}, mode={req.mode})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="adguard_account",
        entity_id=str(ws.id),
        db=db,
    )

    base_url = os.getenv("ADOPTIMA_PUBLIC_BASE_URL", "") or str(request.base_url).rstrip("/")
    login_url = f"{base_url}/adguard-landing"

    if not invite_mode:
        # Pre-format a friendly WhatsApp greeting message
        company_label = req.company_name or req.full_name or "your team"
        quota_display = "Unlimited" if assigned_quota < 0 else f"{assigned_quota:,} leads/month"
        wa_text = (
            f"🎉 Welcome to LeadShield AI!\n\n"
            f"Your account for *{company_label}* is now active.\n\n"
            f"🔑 *Login Credentials:*\n"
            f"• Portal URL: {login_url}\n"
            f"• Username/Email: {email}\n"
            f"• Password: {password}\n"
            f"• Plan: {req.plan.upper()} ({quota_display})\n\n"
            f"👉 Step 1: Login and click 'Connect Google Ads' or 'Connect Meta Ads' to start protecting your campaigns.\n\n"
            f"Need help? Reply directly to this message."
        )
        return {
            "status": "ok",
            "workspace_id": ws.id,
            "login_email": email,
            "login_password": password,
            "login_url": login_url,
            "company_name": req.company_name or req.full_name,
            "plan": req.plan,
            "lead_quota": ws.lead_quota,
            "whatsapp_message": wa_text,
            "message": "Subscriber created successfully. Copy credentials or the WhatsApp card below.",
        }

    # Invite mode: build setup link + send AdGuard-branded email in background
    setup_link = f"{base_url}/onboard.html?token={setup_token}"
    from backend.services.onboarding_email import send_adguard_invite_email

    refresh_token_setting = db.query(AppSetting).filter(AppSetting.key == "gmail_refresh_token").first()
    gmail_rt = refresh_token_setting.value if refresh_token_setting else None

    send_result = {"sent": False, "error": "pending"}
    try:
        send_result = send_adguard_invite_email(
            recipient_email=email,
            full_name=req.full_name or email,
            setup_link=setup_link,
            refresh_token=gmail_rt,
            timeout=30,
        )
    except Exception as e:
        logger.exception(f"AdGuard invite send crashed for {email}: {e}")
        send_result = {"sent": False, "error": str(e)}

    return {
        "status": "ok",
        "workspace_id": ws.id,
        "login_email": email,
        "login_url": login_url,
        "plan": req.plan,
        "lead_quota": ws.lead_quota,
        "invite_sent": bool(send_result.get("sent")),
        "invite_provider": send_result.get("provider"),
        "invite_error": send_result.get("error"),
        "setup_link": setup_link if not send_result.get("sent") else None,
        "message": "Invite email sent — subscriber activates by setting their own password." if send_result.get("sent") else "Email failed; share the setup link manually.",
    }


@router.post("/admin/subscribers/{sub_id}/resend-invite")
def admin_resend_invite(sub_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Re-send the setup invite email to a subscriber whose invite failed/expired. Admin only."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == sub_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    u = db.query(User).filter(User.email == ws.owner_email).first()
    if not u:
        raise HTTPException(status_code=404, detail="Login user not found for this subscriber")
    if u.onboarding_completed or (u.is_active and not u.onboarding_token):
        raise HTTPException(status_code=400, detail="This subscriber already activated their account")

    from backend.routes.auth import ONBOARDING_TOKEN_EXPIRE_HOURS
    import secrets as _secrets
    from datetime import timedelta

    # Fresh token (old one may be expired/used)
    u.onboarding_token = _secrets.token_urlsafe(32)
    u.onboarding_token_expires_at = datetime.utcnow() + timedelta(hours=ONBOARDING_TOKEN_EXPIRE_HOURS)
    u.is_active = False
    db.commit()

    base_url = os.getenv("ADOPTIMA_PUBLIC_BASE_URL", "") or str(request.base_url).rstrip("/")
    setup_link = f"{base_url}/onboard.html?token={u.onboarding_token}"
    from backend.services.onboarding_email import send_adguard_invite_email

    refresh_token_setting = db.query(AppSetting).filter(AppSetting.key == "gmail_refresh_token").first()
    gmail_rt = refresh_token_setting.value if refresh_token_setting else None

    send_result = {"sent": False, "error": "pending"}
    try:
        send_result = send_adguard_invite_email(
            recipient_email=u.email,
            full_name=ws.display_name or u.email,
            setup_link=setup_link,
            refresh_token=gmail_rt,
            timeout=30,
        )
    except Exception as e:
        logger.exception(f"AdGuard invite resend crashed for {u.email}: {e}")
        send_result = {"sent": False, "error": str(e)}

    return {
        "status": "ok",
        "email": u.email,
        "invite_sent": bool(send_result.get("sent")),
        "invite_provider": send_result.get("provider"),
        "invite_error": send_result.get("error"),
        "setup_link": setup_link if not send_result.get("sent") else None,
        "message": "Invite re-sent." if send_result.get("sent") else "Email failed again; share the setup link manually.",
    }


@router.post("/admin/email-diag")
def admin_email_diag(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Send a test email to the admin's own address and return the FULL send result + SMTP env state."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    env_state = {
        "SMTP_HOST": os.getenv("SMTP_HOST", ""),
        "SMTP_PORT": os.getenv("SMTP_PORT", ""),
        "SMTP_USER": os.getenv("SMTP_USER", ""),
        "SMTP_PASS_set": bool(os.getenv("SMTP_PASS", "")),
        "SMTP_FROM": os.getenv("SMTP_FROM", ""),
    }

    from backend.services.onboarding_email import _smtp_from_env, send_adguard_invite_email
    cfg = _smtp_from_env()
    if cfg.get("error"):
        return {"env": env_state, "cfg_error": cfg["error"], "sent": False}

    test_to = user.email
    try:
        result = send_adguard_invite_email(
            recipient_email=test_to,
            full_name=user.full_name or "Admin",
            setup_link=f"{str(request.base_url).rstrip('/')}/adguard",
            refresh_token=None,
            timeout=30,
        )
        return {"env": env_state, "cfg": {k: cfg[k] for k in ("host", "port", "user", "from")}, "test_to": test_to, **result}
    except Exception as e:
        logger.exception("email-diag crashed")
        return {"env": env_state, "sent": False, "error": str(e)}


@router.post("/oauth/meta/resubscribe")
def oauth_meta_resubscribe(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Ensure app-level leadgen webhook + subscribe all manageable Pages (bulk tokens)."""
    _require_adguard_access(user)
    results = []
    from backend.services.adguard_meta import (
        _graph_get,
        get_all_page_tokens,
        get_meta_token_from_credentials,
        subscribe_page_to_app,
    )

    # Step 1: ensure the APP itself subscribes to leadgen webhooks at app level
    app_id = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("ADGUARD_META_APP_SECRET", "") or os.getenv("META_APP_SECRET", "")
    app_token = f"{app_id}|{app_secret}" if app_id and app_secret else ""
    app_sub_ok = False
    app_sub_error = ""
    if app_token:
        try:
            callback_url = f"{os.getenv('REDIRECT_BASE_URL', '').rstrip('/')}/api/adguard/meta/webhook"
            verify_token = os.getenv("ADGUARD_META_VERIFY_TOKEN", "")
            data = urllib.parse.urlencode({
                "object": "page",
                "callback_url": callback_url,
                "verify_token": verify_token,
                "fields": '["leadgen"]',
                "access_token": app_token,
            }).encode()
            req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{app_id}/subscriptions", data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode())
                app_sub_ok = bool(out.get("success"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
            except Exception:
                pass
            app_sub_error = f"HTTP {e.code}: {body[:300]}"
            logger.error(f"[AdGuard] app-level leadgen subscribe failed: {app_sub_error}")
        except Exception as e:
            app_sub_error = f"{type(e).__name__}: {e}"
            logger.error(f"[AdGuard] app-level leadgen subscribe failed: {app_sub_error}")
    else:
        app_sub_error = "missing META_APP_ID or app secret env vars"
    results.append({"step": "app_level_subscription", "ok": app_sub_ok, "error": app_sub_error})

    # Step 2: subscribe each manageable Page using bulk page tokens
    q = db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True)  # noqa: E712
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    for ws in q.all():
        token = None
        try:
            from backend.services.adguard_meta import get_meta_token_from_credentials
            token = get_meta_token_from_credentials(ws.meta_credentials)
        except Exception:
            token = None
        if not token:
            results.append({"workspace_id": ws.id, "error": "no_token"})
            continue
        pages = []
        try:
            pages = json.loads(ws.discovered_meta_pages or "[]")
        except Exception:
            pages = []
        if not pages:
            try:
                from backend.services.adguard_meta import discover_meta_pages
                pages = discover_meta_pages(token)
                ws.discovered_meta_pages = json.dumps(pages) if pages else "[]"
            except Exception as pe:
                results.append({"workspace_id": ws.id, "error": str(pe)})
                continue
        page_tokens = get_all_page_tokens(token)
        if "__error__" in page_tokens:
            results.append({"workspace_id": ws.id, "error": "bulk_page_tokens: " + str(page_tokens["__error__"])})
            continue
        for page in pages:
            if not page.get("can_subscribe"):
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "page_name": page.get("name"), "subscribed": False, "skipped": "no_manage_permission"})
                continue
            try:
                page_token = page_tokens.get(str(page["id"])) or ""
                ok = subscribe_page_to_app(page["id"], page_token) if page_token else False
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "page_name": page.get("name"), "subscribed": ok, "had_token": bool(page_token)})
            except Exception as pe:
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "subscribed": False, "error": str(pe)})
    db.commit()
    ok_count = sum(1 for r in results if r.get("subscribed"))
    return {"status": "ok", "app_level_ok": app_sub_ok, "pages_subscribed": ok_count, "results": results}


@router.get("/meta/debug-subscriptions")
def meta_debug_subscriptions(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Show app-level webhook subscriptions + which apps each Page subscribes to."""
    _require_adguard_access(user)
    from backend.services.adguard_meta import (
        _graph_get,
        get_all_page_tokens,
        get_meta_token_from_credentials,
    )

    app_id = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("ADGUARD_META_APP_SECRET", "") or os.getenv("META_APP_SECRET", "")
    out: Dict[str, Any] = {"app_id": app_id, "app_subscriptions": None, "pages": [], "webhook_hits": list(_webhook_hits)}

    if app_id and app_secret:
        try:
            out["app_subscriptions"] = _graph_get(f"{app_id}/subscriptions", {"token": f"{app_id}|{app_secret}"})
        except Exception as e:
            out["app_subscriptions_error"] = str(e)

    q = db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True)  # noqa: E712
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    for ws in q.all():
        token = get_meta_token_from_credentials(ws.meta_credentials or "")
        if not token:
            continue
        try:
            pages = json.loads(ws.discovered_meta_pages) if ws.discovered_meta_pages else []
        except Exception:
            pages = []
        page_tokens = get_all_page_tokens(token)
        if "__error__" in page_tokens:
            out["pages"].append({"workspace_id": ws.id, "error": "bulk_page_tokens: " + str(page_tokens["__error__"])})
            continue
        for p in pages:
            pid = str(p.get("id"))
            entry: Dict[str, Any] = {"workspace_id": ws.id, "page_id": pid, "page_name": p.get("name")}
            try:
                page_token = page_tokens.get(pid) or ""
                if not page_token:
                    entry["error"] = "no_page_token"
                else:
                    apps = _graph_get(f"{pid}/subscribed_apps", {"token": page_token})
                    if apps is None:
                        from backend.services.adguard_meta import get_last_graph_error
                        entry["error"] = get_last_graph_error() or "subscribed_apps returned nothing"
                    else:
                        app_ids = [str(a.get("id")) for a in (apps or {}).get("data", [])]
                        entry["subscribed_apps"] = app_ids
                        entry["our_app_subscribed"] = str(os.getenv("META_APP_ID", "")) in app_ids
            except Exception as e:
                entry["error"] = str(e)
            out["pages"].append(entry)
    return out


@router.post("/meta/pull-leads")
def meta_pull_leads(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Manually pull recent leads from Meta Pages API and run them through the gatekeeper."""
    _require_adguard_access(user)
    from backend.services.adguard_meta import (
        _graph_get,
        get_meta_token_from_credentials,
        get_all_page_tokens,
    )
    from backend.services.adguard import process_incoming_lead
    from backend.db.models import AdGuardLead

    q = db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True)  # noqa: E712
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    processed, failed, skipped = 0, 0, 0
    details = []

    def _fields_map(field_data: list) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for item in field_data or []:
            name = (item.get("name") or "").strip()
            values = item.get("values") or []
            if name and values:
                fields[name] = values[0]
        return fields

    for ws in q.all():
        token = get_meta_token_from_credentials(ws.meta_credentials or "")
        if not token:
            details.append({"workspace_id": ws.id, "error": "no_token"})
            continue
        try:
            pages = json.loads(ws.discovered_meta_pages) if ws.discovered_meta_pages else []
        except Exception:
            pages = []
        details.append({"workspace_id": ws.id, "pages_count": len(pages)})
        page_tokens = get_all_page_tokens(token)
        for p in pages:
            pid = str(p.get("id"))
            try:
                page_token = page_tokens.get(pid) or ""
                if not page_token:
                    details.append({"page_id": pid, "page_name": p.get("name"), "error": "no_page_token"})
                    continue
                data = _graph_get(f"{pid}/leads", {
                    "fields": "id,created_time,form_id,ad_id,ad_name,campaign_id,campaign_name,field_data",
                    "limit": "25",
                    "token": page_token,
                })
                lead_list = (data or {}).get("data", [])
                details.append({"page_id": pid, "page_name": p.get("name"), "lead_count": len(lead_list)})
                for ld in lead_list:
                    lead_id = str(ld.get("id") or "")
                    if not lead_id:
                        continue
                    exists = db.query(AdGuardLead).filter(AdGuardLead.raw_payload.like(f"%{lead_id}%")).first()
                    if exists:
                        skipped += 1
                        continue
                    fields = _fields_map(ld.get("field_data"))
                    payload = {
                        "full_name": fields.get("full_name") or fields.get("name") or "",
                        "email": fields.get("email") or "",
                        "phone": fields.get("phone_number") or fields.get("phone") or "",
                        "city": fields.get("city") or "",
                        "state": fields.get("state") or "",
                        "country": fields.get("country") or "",
                        "postal_code": fields.get("zip_code") or fields.get("postal_code") or "",
                        "message": fields.get("message") or fields.get("comments") or "",
                        "campaign_name": ld.get("campaign_name") or ld.get("ad_name") or "",
                        "form_id": str(ld.get("form_id") or ""),
                        "gclid": None,
                        "lead_type": "meta_leadgen",
                        "platform": "meta",
                    }
                    try:
                        process_incoming_lead(payload, account=None, raw_payload=json.dumps(ld), workspace_id=ws.id)
                        processed += 1
                        details.append({"page_id": pid, "lead_id": lead_id, "email": payload["email"] or payload["phone"]})
                    except Exception as pe:
                        failed += 1
                        details.append({"page_id": pid, "lead_id": lead_id, "error": str(pe)})
            except Exception as e:
                details.append({"page_id": pid, "page_name": p.get("name"), "error": str(e)})
    db.commit()
    return {"status": "ok", "processed": processed, "failed": failed, "skipped": skipped, "details": details}


@router.post("/oauth/disconnect")
def oauth_disconnect(req: SelectAccountsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Remove OAuth tokens from a workspace (keeps lead history)."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    platform = (req.platform or "google").lower()
    if platform == "meta":
        ws.meta_credentials = None
        ws.meta_is_live = False
        ws.discovered_meta_accounts = "[]"
        ws.discovered_meta_pages = "[]"
    else:
        ws.google_credentials = None
        ws.google_is_live = False
        ws.discovered_accounts = "[]"
    db.commit()
    return {"status": "disconnected", "workspace_id": req.workspace_id, "platform": platform}


# ---------------------------------------------------------------------------
# Meta leadgen webhook -> AdGuard gatekeeper
# ---------------------------------------------------------------------------

@router.get("/meta/webhook")
def meta_webhook_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta webhook verification handshake (configured in the Meta App dashboard)."""
    expected = os.getenv("ADGUARD_META_VERIFY_TOKEN", WEBHOOK_VERIFY_TOKEN)
    if hub_mode == "subscribe" and hub_verify_token == expected:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


_webhook_hits: list = []

@router.post("/meta/webhook")
async def meta_webhook_receive(request: Request, db: Session = Depends(get_db), x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256")):
    """Meta pushes leadgen events here for all connected workspaces' Pages.

    Signature-verified with ADGUARD_META_APP_SECRET when configured.
    Each leadgen id is resolved via lead_retrieval using the owning
    workspace's stored token, then scored by the same gatekeeper.
    """
    raw = await request.body()
    _webhook_hits.append({"time": datetime.utcnow().isoformat(), "sig_present": bool(x_hub_signature_256), "bytes": len(raw)})
    del _webhook_hits[:-20]

    app_secret = os.getenv("ADGUARD_META_APP_SECRET", "") or os.getenv("META_APP_SECRET", "")
    if app_secret:
        if not x_hub_signature_256:
            raise HTTPException(status_code=403, detail="Missing signature")
        expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from backend.services.adguard import process_incoming_lead
    from backend.services.adguard_meta import fetch_meta_lead, get_meta_token_from_credentials

    # Map page_id -> workspace (a workspace may hold multiple Pages)
    page_to_ws: Dict[str, AdGuardAccount] = {}
    for ws in db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True).all():  # noqa: E712
        token = get_meta_token_from_credentials(ws.meta_credentials or "")
        if not token:
            continue
        try:
            pages = json.loads(ws.discovered_meta_pages) if ws.discovered_meta_pages else []
        except Exception:
            pages = []
        for p in pages:
            page_to_ws[str(p.get("id"))] = ws

    processed, failed, skipped = 0, 0, 0

    def _handle(leadgen_id: str, page_id: str):
        ws = page_to_ws.get(str(page_id))
        if ws is None:
            return "skipped"
        token = get_meta_token_from_credentials(ws.meta_credentials or "")
        if not token:
            return "skipped"
        lead = fetch_meta_lead(leadgen_id, token)
        if not lead:
            return "failed"
        fields = lead.get("fields", {})
        payload = {
            "full_name": fields.get("full_name") or fields.get("name") or "",
            "email": fields.get("email") or "",
            "phone": fields.get("phone_number") or fields.get("phone") or "",
            "city": fields.get("city") or "",
            "state": fields.get("state") or "",
            "country": fields.get("country") or "",
            "postal_code": fields.get("zip_code") or fields.get("postal_code") or "",
            "message": fields.get("message") or fields.get("comments") or "",
            "campaign_name": lead.get("campaign_name") or lead.get("ad_name") or "",
            "form_id": lead.get("form_id") or "",
            "gclid": None,
            "lead_type": "meta_leadgen",
            "platform": "meta",
        }
        import json as _json

        process_incoming_lead(payload, account=None, raw_payload=_json.dumps(lead.get("raw", lead)), workspace_id=ws.id if ws else None)
        return "processed"

    for entry in payload.get("entry", []):
        page_id = str(entry.get("id") or "")
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            value = change.get("value", {}) or {}
            leadgen_id = value.get("leadgen_id") or value.get("lead_id")
            if not leadgen_id:
                continue
            try:
                outcome = _handle(str(leadgen_id), page_id)
                if outcome == "processed":
                    processed += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.error(f"[AdGuard] Meta webhook lead {leadgen_id} failed: {e}")

    # Always 200 so Meta doesn't retry-storm; failures are logged for the poller
    return {"status": "ok", "processed": processed, "failed": failed, "skipped": skipped}