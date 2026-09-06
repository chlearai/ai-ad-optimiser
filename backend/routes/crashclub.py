"""
Crash Club — Meta Lead Ads routes.

Endpoints:
  GET  /api/crashclub/webhook     -> Meta webhook verification (hub.challenge)
  POST /api/crashclub/webhook     -> Meta leadgen push (live capture)
  POST /api/crashclub/sync        -> manual/backstop poll (5-min scheduler calls this logic)
  GET  /api/crashclub/status      -> config diagnostics
  POST /api/crashclub/backfill    -> pull ALL existing leads once
"""
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger("AdOptima")
router = APIRouter(prefix="/api/crashclub", tags=["crashclub"])

WEBHOOK_VERIFY_TOKEN = os.getenv("CRASH_CLUB_WEBHOOK_VERIFY_TOKEN", "crashclub_verify")
WEBHOOK_APP_SECRET = os.getenv("CRASH_CLUB_WEBHOOK_APP_SECRET", "")


def _verify_signature(raw_body: bytes, x_hub_signature: str) -> bool:
    """X-Hub-Signature-256 check when app secret is configured."""
    if not WEBHOOK_APP_SECRET:
        return True  # not configured -> skip (acceptable locally)
    if not x_hub_signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_APP_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, x_hub_signature)


@router.get("/webhook")
def webhook_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def webhook_receive(
    request: Request,
    x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256"),
):
    raw = await request.body()
    if not _verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    entries = payload.get("entry", [])
    processed, failed = 0, 0
    for entry in entries:
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            value = change.get("value", {})
            lead_id = value.get("leadgen_id") or value.get("lead_id")
            if not lead_id:
                continue
            try:
                _process_single_lead(lead_id, value.get("created_time"))
                processed += 1
            except Exception as e:
                failed += 1
                logger.error(f"[CrashClub] webhook lead {lead_id} failed: {e}")
    return {"status": "ok", "processed": processed, "failed": failed}


def _process_single_lead(lead_id: str, created_time_unix=None) -> Dict[str, Any]:
    """Fetch details for one lead and push to sheet (webhook path)."""
    from backend.services.crashclub_leads import (
        fetch_lead_details,
        normalize_lead,
    )
    from backend.services.crashclub_sheets import append_leads, already_synced, _ledger_conn

    lead = fetch_lead_details(str(lead_id))
    if not lead:
        raise RuntimeError("empty lead payload")
    row = normalize_lead(lead)

    conn = _ledger_conn()
    try:
        is_new = not already_synced(conn, str(lead_id))
    finally:
        conn.close()
    if not is_new:
        return {"lead_id": str(lead_id), "written": False, "reason": "dedup"}

    res = append_leads([row])
    return {"lead_id": str(lead_id), "written": res.get("written", 0) > 0, "result": res}


class SyncRequest(BaseModel):
    ad_account_id: str = ""
    since_hours: int = 24


@router.post("/sync")
def sync_leads(req: SyncRequest = None):
    """Backstop poll: pull recent leads from Meta, dedup, append to sheet."""
    from backend.services.crashclub_leads import (
        fetch_account_leads,
        normalize_lead,
    )
    from backend.services.crashclub_sheets import append_leads

    import time

    since = int(time.time() - (req.since_hours if req else 24) * 3600)
    try:
        leads = fetch_account_leads(
            ad_account_id=req.ad_account_id if req and req.ad_account_id else None,
            since_unix=since,
        )
        rows = [normalize_lead(l) for l in leads]
        res = append_leads(rows)
        return {"status": "ok", "leads_found": len(leads), **res}
    except Exception as e:
        logger.error(f"[CrashClub] sync failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/backfill")
def backfill():
    """One-time: pull ALL historical leads and write them (dedup protects)."""
    from backend.services.crashclub_sheets import backfill_all_existing_leads

    try:
        return backfill_all_existing_leads()
    except Exception as e:
        logger.error(f"[CrashClub] backfill failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/status")
def status():
    """Config + connectivity diagnostics."""
    from backend.services.crashclub_leads import CRASH_CLUB_AD_ACCOUNT
    from backend.services.crashclub_sheets import (
        LEDGER_DB,
        LEDGER_TABLE,
        _load_service_account,
        get_spreadsheet_id,
    )

    info: Dict[str, Any] = {
        "ad_account": CRASH_CLUB_AD_ACCOUNT,
        "meta_token_set": bool(
            os.getenv("CRASH_CLUB_META_TOKEN") or os.getenv("META_ACCESS_TOKEN")
        ),
        "sheet_id_set": False,
        "sheet_tab": os.getenv("CRASH_CLUB_SHEET_TAB", "Crash Club Leads"),
        "sa_configured": False,
        "sa_email": None,
        "ledger_db": LEDGER_DB,
        "ledger_table": LEDGER_TABLE,
        "meta_api_reachable": None,
        "meta_api_error": None,
    }
    try:
        info["sheet_id_set"] = bool(get_spreadsheet_id())
    except Exception:
        pass
    try:
        creds = _load_service_account()
        info["sa_configured"] = True
        info["sa_email"] = creds.service_account_email
    except Exception as e:
        info["sa_error"] = str(e)

    # Probe Meta (cheap call: /me on the token)
    try:
        import urllib.parse
        import urllib.request

        token = (
            os.getenv("CRASH_CLUB_META_TOKEN") or os.getenv("META_ACCESS_TOKEN") or ""
        ).strip()
        url = f"https://graph.facebook.com/v18.0/me?access_token={urllib.parse.quote(token)}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            d = json.load(resp)
        info["meta_api_reachable"] = True
        info["meta_identity"] = d.get("name", "")
    except Exception as e:
        info["meta_api_reachable"] = False
        info["meta_api_error"] = str(e)[:300]
    return info


# ---------- scheduler hook ----------

def run_scheduled_sync():
    """Called by APScheduler every 5 minutes."""
    try:
        leads = None
        from backend.services.crashclub_leads import fetch_account_leads, normalize_lead
        from backend.services.crashclub_sheets import append_leads

        import time

        since = int(time.time() - 24 * 3600)
        leads = fetch_account_leads(since_unix=since)
        rows = [normalize_lead(l) for l in leads]
        res = append_leads(rows)
        logger.info(
            f"[CrashClub] scheduled sync: found={len(leads)} written={res.get('written')} "
            f"skipped={res.get('skipped_dedup')}"
        )
        return res
    except Exception as e:
        logger.warning(f"[CrashClub] scheduled sync failed: {e}")
        return {"status": "error", "detail": str(e)}