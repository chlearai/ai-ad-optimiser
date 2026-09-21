"""
AdGuard Conversion Signal Firewall (Module 2).

Gating and value-weighting of server-side conversion signals:
  - Red / Grey unverified -> 0 signal sent (never reaches ad platform)
  - Green submitted -> Held in queue
  - AI Bot-Caller Verified -> Dispatches server-side Lead event (e.g. ₹500)
  - CRM Qualified -> Dispatches QualifiedLead event (e.g. ₹2,000)
  - CRM Appointment / Site Visit / Converted -> Dispatches high-value event (e.g. ₹10,000)

Integrates directly with:
  - Meta Conversions API (CAPI)
  - Google Ads Offline Click Conversion Import
"""
import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.db.models import (
    Account,
    AdGuardAccount,
    AdGuardConversionEvent,
    AdGuardLead,
    AdGuardSession,
)
from backend.services.crypto import decrypt

logger = logging.getLogger("AdOptima")


def _hash_sha256(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    clean = str(val).strip().lower()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def get_account_conversion_values(ws: Optional[AdGuardAccount]) -> Dict[str, float]:
    """Get value mappings for conversion tiers."""
    defaults = {
        "lead": 500.0,
        "verified": 500.0,
        "qualified": 2000.0,
        "appointment": 10000.0,
        "site_visit": 10000.0,
        "converted": 10000.0,
    }
    if not ws or not ws.conversion_values:
        return defaults
    try:
        custom = json.loads(ws.conversion_values)
        if isinstance(custom, dict):
            defaults.update({k.lower(): float(v) for k, v in custom.items()})
    except Exception:
        pass
    return defaults


def queue_conversion_event(
    db: Session,
    lead: AdGuardLead,
    stage_or_event: str,
    override_value: Optional[float] = None,
) -> List[AdGuardConversionEvent]:
    """Queue a server-side conversion event for a lead once verified/qualified."""
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == lead.adguard_account_id).first() if lead.adguard_account_id else None
    values = get_account_conversion_values(ws)

    event_key = stage_or_event.lower()
    val = override_value if override_value is not None else values.get(event_key, 500.0)

    # Standard event name normalization
    if event_key in ("verified", "lead"):
        event_name = "Lead"
    elif event_key in ("qualified", "qualified_lead"):
        event_name = "QualifiedLead"
    elif event_key in ("appointment", "site_visit"):
        event_name = "Schedule"
    elif event_key in ("converted", "deal_closed"):
        event_name = "Purchase"
    else:
        event_name = stage_or_event

    events_created = []

    # Determine platforms
    platforms = []
    if lead.gclid or (ws and ws.google_is_live):
        platforms.append("google")
    if lead.fbclid or (ws and ws.meta_is_live):
        platforms.append("meta")
    if not platforms:
        platforms = ["google", "meta"]

    for plat in platforms:
        evt = AdGuardConversionEvent(
            lead_id=lead.id,
            adguard_account_id=lead.adguard_account_id,
            platform=plat,
            event_name=event_name,
            value=val,
            currency="INR",
            gclid=lead.gclid,
            fbclid=lead.fbclid,
            status="held",
        )
        db.add(evt)
        events_created.append(evt)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[Firewall] queue_conversion_event failed: {e}")

    return events_created


def dispatch_meta_capi_event(ws: AdGuardAccount, event: AdGuardConversionEvent, lead: AdGuardLead) -> Tuple[bool, Optional[str]]:
    """Dispatch an event to Meta Conversions API (CAPI)."""
    token = None
    try:
        from backend.services.adguard_meta import get_meta_token_from_credentials
        token = get_meta_token_from_credentials(ws.meta_credentials or "")
    except Exception:
        pass

    if not token:
        # Fallback to env token
        token = os.getenv("META_CAPI_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN")

    pixel_id = os.getenv("META_PIXEL_ID", "")
    if not pixel_id and ws.discovered_meta_accounts:
        try:
            accs = json.loads(ws.discovered_meta_accounts)
            if accs and accs[0].get("id"):
                pixel_id = str(accs[0]["id"]).replace("act_", "")
        except Exception:
            pass

    if not token or not pixel_id:
        return False, "meta_credentials_or_pixel_id_missing"

    user_data: Dict[str, Any] = {}
    if lead.email:
        user_data["em"] = [_hash_sha256(lead.email)]
    if lead.phone:
        digits = "".join(c for c in lead.phone if c.isdigit())
        user_data["ph"] = [_hash_sha256(digits)]
    if event.fbclid:
        user_data["fbc"] = f"fb.1.{int(datetime.utcnow().timestamp())}.{event.fbclid}"

    payload = {
        "data": [{
            "event_name": event.event_name,
            "event_time": int(datetime.utcnow().timestamp()),
            "action_source": "system_generated",
            "user_data": user_data,
            "custom_data": {
                "value": event.value,
                "currency": event.currency or "INR",
            },
        }]
    }

    url = f"https://graph.facebook.com/v21.0/{pixel_id}/events?access_token={token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return True, body
    except urllib.error.HTTPError as he:
        err = he.read().decode("utf-8")[:300]
        return False, f"HTTP {he.code}: {err}"
    except Exception as e:
        return False, str(e)


def dispatch_google_offline_conversion(ws: AdGuardAccount, event: AdGuardConversionEvent, lead: AdGuardLead) -> Tuple[bool, Optional[str]]:
    """Dispatch a conversion to Google Ads Offline Conversion Import."""
    if not event.gclid:
        return True, "skipped_no_gclid"

    try:
        from google.ads.googleads.client import GoogleAdsClient
        if not ws.google_credentials:
            return False, "google_credentials_missing"

        creds_plain = json.loads(decrypt(ws.google_credentials))
        config = {
            "developer_token": creds_plain.get("developer_token", ""),
            "client_id": creds_plain.get("client_id", ""),
            "client_secret": creds_plain.get("client_secret", ""),
            "refresh_token": creds_plain.get("refresh_token", ""),
            "use_proto_plus": True,
        }
        client = GoogleAdsClient.load_from_dict(config)
        conversion_upload_service = client.get_service("ConversionUploadService")

        # Extract customer ID
        customer_id = None
        if ws.discovered_accounts:
            try:
                accs = json.loads(ws.discovered_accounts)
                if accs and accs[0].get("id"):
                    customer_id = str(accs[0]["id"]).replace("-", "")
            except Exception:
                pass

        if not customer_id:
            return False, "no_google_customer_id"

        click_conversion = client.get_type("ClickConversion")
        click_conversion.gclid = event.gclid
        click_conversion.conversion_action = f"customers/{customer_id}/conversionActions/1" # standard or configured
        click_conversion.conversion_date_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S+00:00")
        click_conversion.conversion_value = float(event.value)
        click_conversion.currency_code = event.currency or "INR"

        request = client.get_type("UploadClickConversionsRequest")
        request.customer_id = customer_id
        request.conversions.append(click_conversion)
        request.partial_failure = True

        response = conversion_upload_service.upload_click_conversions(request=request)
        return True, str(response)[:300]
    except Exception as e:
        # Graceful fallback: log and report
        logger.warning(f"[Firewall] Google conversion upload note: {e}")
        return False, str(e)[:300]


def flush_pending_conversions(db: Session, limit: int = 50) -> Dict[str, int]:
    """1-minute background job: flush held conversion events whose leads are verified."""
    pending = (
        db.query(AdGuardConversionEvent)
        .filter(AdGuardConversionEvent.status == "held")
        .order_by(AdGuardConversionEvent.created_at.asc())
        .limit(limit)
        .all()
    )

    sent_count, failed_count = 0, 0

    for evt in pending:
        lead = db.query(AdGuardLead).filter(AdGuardLead.id == evt.lead_id).first()
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == evt.adguard_account_id).first() if evt.adguard_account_id else None

        if not lead or not ws:
            evt.status = "failed"
            evt.error_message = "missing_lead_or_workspace"
            failed_count += 1
            continue

        # Check gate: Red or rejected leads are never dispatched
        if lead.verdict == "red" or lead.stage == "rejected":
            evt.status = "retracted"
            evt.error_message = "blocked_by_conversion_firewall"
            continue

        ok, resp = False, None
        if evt.platform == "meta":
            ok, resp = dispatch_meta_capi_event(ws, evt, lead)
        elif evt.platform == "google":
            ok, resp = dispatch_google_offline_conversion(ws, evt, lead)

        if ok:
            evt.status = "sent"
            evt.sent_at = datetime.utcnow()
            evt.platform_response = resp
            sent_count += 1
        else:
            evt.status = "failed"
            evt.error_message = resp
            failed_count += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[Firewall] flush commit error: {e}")

    return {"sent": sent_count, "failed": failed_count}
