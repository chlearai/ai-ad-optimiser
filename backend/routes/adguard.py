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
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
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

    from backend.services.adguard import process_incoming_lead

    # Respond 200 immediately — Google's webhook test times out on slow
    # responses (Gemini scoring + LSQ push can take 5-10s synchronously).
    # Full pipeline (dedup -> score -> LSQ push -> persist) runs in background.
    import threading

    def _process_background():
        try:
            process_incoming_lead(payload, account=account, raw_payload=raw)
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


@router.get("/oauth/meta/connect")
def oauth_meta_connect(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Ryze-style Connect Meta Ads: return Meta's OAuth dialog URL."""
    _require_adguard_access(user)
    ws = _get_or_create_workspace(db, user)
    try:
        from backend.services.adguard_meta import get_adguard_meta_auth_url

        url = get_adguard_meta_auth_url(ws.id)
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
    )

    token = exchange_adguard_meta_code(code)
    if not token:
        return RedirectResponse(url="/adguard?oauth_error=meta_token_exchange_failed")

    # The OAuth `state` carries the workspace id that started the flow (SaaS-safe:
    # each customer's token lands in their own workspace).
    ws = None
    if state and state.isdigit():
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == int(state)).first()
    if ws is None:
        ws = db.query(AdGuardAccount).order_by(AdGuardAccount.created_at.asc()).first()
    if not ws:
        return RedirectResponse(url="/adguard?oauth_error=workspace_not_found")

    ws.meta_credentials = build_meta_credentials(token)
    ws.meta_is_live = True
    db.commit()

    try:
        accounts = discover_meta_ad_accounts(token)
        pages = discover_meta_pages(token)
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

    return RedirectResponse(url="/adguard?oauth_success=meta")


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
        a["selected"] = str(a["id"]) in selected
    setattr(ws, field, json.dumps(accounts))
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


@router.post("/oauth/meta/resubscribe")
def oauth_meta_resubscribe(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Re-subscribe all manageable Pages to the app's leadgen webhooks."""
    _require_adguard_access(user)
    q = db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True)  # noqa: E712
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    results = []
    from backend.services.adguard_meta import get_page_access_token, subscribe_page_to_app
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
        for page in pages:
            if not page.get("can_subscribe"):
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "page_name": page.get("name"), "subscribed": False, "skipped": "no_manage_permission"})
                continue
            try:
                page_token = get_page_access_token(token, page["id"])
                ok = subscribe_page_to_app(page["id"], page_token) if page_token else False
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "page_name": page.get("name"), "subscribed": ok})
            except Exception as pe:
                results.append({"workspace_id": ws.id, "page_id": page.get("id"), "subscribed": False, "error": str(pe)})
    db.commit()
    ok_count = sum(1 for r in results if r.get("subscribed"))
    return {"status": "ok", "pages_subscribed": ok_count, "results": results}


@router.get("/meta/debug-subscriptions")
def meta_debug_subscriptions(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Show app-level webhook subscriptions + which apps each Page subscribes to."""
    _require_adguard_access(user)
    from backend.services.adguard_meta import (
        _graph_get,
        get_meta_token_from_credentials,
        get_page_access_token,
    )

    app_id = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("ADGUARD_META_APP_SECRET", "") or os.getenv("META_APP_SECRET", "")
    out: Dict[str, Any] = {"app_id": app_id, "app_subscriptions": None, "pages": []}

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
        for p in pages:
            pid = str(p.get("id"))
            entry: Dict[str, Any] = {"workspace_id": ws.id, "page_id": pid, "page_name": p.get("name")}
            try:
                page_token = get_page_access_token(token, pid)
                if not page_token:
                    entry["error"] = "no_page_token"
                else:
                    apps = _graph_get(f"{pid}/subscribed_apps", {"token": page_token})
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
        get_page_access_token,
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
        for p in pages:
            pid = str(p.get("id"))
            try:
                page_token = get_page_access_token(token, pid)
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
                        process_incoming_lead(payload, account=None, raw_payload=json.dumps(ld))
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


@router.post("/meta/webhook")
async def meta_webhook_receive(request: Request, db: Session = Depends(get_db), x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256")):
    """Meta pushes leadgen events here for all connected workspaces' Pages.

    Signature-verified with ADGUARD_META_APP_SECRET when configured.
    Each leadgen id is resolved via lead_retrieval using the owning
    workspace's stored token, then scored by the same gatekeeper.
    """
    raw = await request.body()

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

        process_incoming_lead(payload, account=None, raw_payload=_json.dumps(lead.get("raw", lead)))
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