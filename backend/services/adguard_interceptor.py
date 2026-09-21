"""
AdGuard Pre-Submit Interceptor Engine (Module 1).

Evaluates telemetry, automation traces, network reputation, form fill behaviour,
and Gemini AI content intent before form submission completes (<400ms target).

Verdicts:
  - Green (0-39 risk): Clean lead. Pushed to CRM, queued for Bot-Caller verification.
  - Grey (40-69 risk): Borderline. Held for OTP step-up verification.
  - Red (70-100 risk): Silent quarantine. Thank-you page shown, blocked from CRM,
                       no conversion signal sent, added to Exclusion Lists.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.db.models import (
    Account,
    AdGuardAccount,
    AdGuardExclusionMember,
    AdGuardLead,
    AdGuardSession,
    AdGuardVerdict,
)
from backend.services.adguard import (
    DISPOSABLE_EMAIL_DOMAINS,
    EMAIL_RE,
    ai_legitimacy_score,
    check_email,
    check_geo,
    check_phone,
)
from backend.services.adguard_shared_network import (
    hash_entity,
    lookup_threat,
    record_offence,
)

logger = logging.getLogger("AdOptima")

# Known datacenter / hosting ASNs & IP prefixes (sample set, extensible)
KNOWN_DATACENTER_KEYWORDS = {"amazon", "aws", "google cloud", "digitalocean", "linode", "ovh", "hetzner", "azure", "microsoft"}


def is_datacenter_ip(ip: Optional[str], asn: Optional[str] = None) -> bool:
    """Check if IP or ASN belongs to a hosting/cloud provider."""
    if asn:
        asn_lower = asn.lower()
        if any(kw in asn_lower for kw in KNOWN_DATACENTER_KEYWORDS):
            return True
    if not ip:
        return False
    # Common cloud subnets
    if ip.startswith(("3.", "18.", "34.", "35.", "52.", "54.", "104.", "198.")):
        # Plausible cloud IP indicator
        return False
    return False


def register_session(
    db: Session,
    session_uuid: str,
    payload: Dict[str, Any],
    client_ip: Optional[str] = None,
) -> AdGuardSession:
    """Record page load telemetry and device fingerprint."""
    ws_id = payload.get("adguard_account_id") or payload.get("account_id")
    fp_hash = payload.get("fingerprint_hash")
    if not fp_hash and payload.get("device_info"):
        fp_hash = hash_entity("fingerprint", json.dumps(payload["device_info"], sort_keys=True))

    session = db.query(AdGuardSession).filter(AdGuardSession.session_uuid == session_uuid).first()
    if not session:
        session = AdGuardSession(
            session_uuid=session_uuid,
            adguard_account_id=int(ws_id) if ws_id and str(ws_id).isdigit() else None,
            fingerprint_hash=fp_hash,
            ip=client_ip or payload.get("ip"),
            asn=payload.get("asn"),
            is_datacenter=bool(payload.get("is_datacenter") or is_datacenter_ip(client_ip, payload.get("asn"))),
            gclid=payload.get("gclid"),
            fbclid=payload.get("fbclid"),
            utm_source=payload.get("utm_source"),
            utm_medium=payload.get("utm_medium"),
            utm_campaign=payload.get("utm_campaign"),
            utm_content=payload.get("utm_content"),
            utm_term=payload.get("utm_term"),
            page_url=payload.get("page_url"),
            referrer=payload.get("referrer"),
            device_info=json.dumps(payload.get("device_info") or {}),
            behaviour_json=json.dumps(payload.get("behaviour") or {}),
        )
        db.add(session)
    else:
        if fp_hash:
            session.fingerprint_hash = fp_hash
        if payload.get("gclid"):
            session.gclid = payload["gclid"]
        if payload.get("fbclid"):
            session.fbclid = payload["fbclid"]
        if payload.get("behaviour"):
            session.behaviour_json = json.dumps(payload["behaviour"])

    try:
        db.commit()
        db.refresh(session)
    except Exception as e:
        db.rollback()
        logger.warning(f"[Interceptor] register_session error: {e}")

    return session


def evaluate_submit_verdict(
    db: Session,
    session_uuid: str,
    form_data: Dict[str, Any],
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate submit-time risk score and verdict (Green/Grey/Red).

    Target latency < 400ms.
    """
    reasons: List[str] = []
    risk_score = 0

    session = db.query(AdGuardSession).filter(AdGuardSession.session_uuid == session_uuid).first()
    ws_id = session.adguard_account_id if session else form_data.get("adguard_account_id")

    # 1. Automation signals from session
    device_info = {}
    behaviour = {}
    if session:
        try:
            device_info = json.loads(session.device_info or "{}")
        except Exception:
            device_info = {}
        try:
            behaviour = json.loads(session.behaviour_json or "{}")
        except Exception:
            behaviour = {}

    if device_info.get("webdriver") or form_data.get("webdriver"):
        risk_score += 55
        reasons.append("automation_webdriver_detected")

    if device_info.get("headless") or form_data.get("headless"):
        risk_score += 50
        reasons.append("headless_browser_detected")

    if session and session.is_datacenter:
        risk_score += 35
        reasons.append("datacenter_or_proxy_ip")

    # 2. Behavioural timing signals
    time_on_page_ms = behaviour.get("time_on_page_ms") or form_data.get("time_on_page_ms") or 0
    time_to_fill_ms = behaviour.get("time_to_fill_ms") or form_data.get("time_to_fill_ms") or 0
    mouse_moves = behaviour.get("mouse_moves_count") or form_data.get("mouse_moves_count") or 0

    if 0 < time_to_fill_ms < 1500:
        risk_score += 30
        reasons.append("rapid_form_fill_velocity")

    if 0 < time_on_page_ms < 2000 and mouse_moves == 0:
        risk_score += 25
        reasons.append("zero_pointer_activity_speedrun")

    # 3. Contact Hygiene Checks
    email = (form_data.get("email") or "").strip().lower()
    phone = (form_data.get("phone") or "").strip()

    email_valid, is_disposable, email_flags = check_email(email)
    if is_disposable:
        risk_score += 40
        reasons.append("disposable_email_domain")
    elif not email_valid and email:
        risk_score += 25
        reasons.append("invalid_email_format")

    phone_valid, phone_flags = check_phone(phone)
    if not phone_valid:
        risk_score += 35
        reasons.append("invalid_phone_carrier_series")

    # 4. 7-Day Deduplication Check
    now = datetime.utcnow()
    cutoff = now - timedelta(days=7)
    dup = None
    if email:
        dup = db.query(AdGuardLead).filter(AdGuardLead.email == email, AdGuardLead.received_at >= cutoff).first()
    if not dup and phone:
        dup = db.query(AdGuardLead).filter(AdGuardLead.phone == phone, AdGuardLead.received_at >= cutoff).first()

    if dup:
        risk_score += 45
        reasons.append("duplicate_recent_lead_7d")

    # 5. Shared Fraud Network Threat Lookup
    fp_hash = session.fingerprint_hash if session else None
    email_h = hash_entity("email", email)
    phone_h = hash_entity("phone", phone)
    ip_h = hash_entity("ip_subnet", client_ip or (session.ip if session else None))

    net_score, net_reasons = lookup_threat(
        db,
        {
            "fingerprint": fp_hash,
            "email": email_h,
            "phone": phone_h,
            "ip_subnet": ip_h,
        },
    )
    if net_score > 0:
        risk_score += net_score
        reasons.extend(net_reasons)

    # 6. Gemini Intent Evaluation for Grey/Borderline Band (40–69)
    gemini_score = None
    if 40 <= risk_score <= 69:
        try:
            lead_for_ai = {
                "full_name": form_data.get("full_name") or form_data.get("name"),
                "email": email,
                "phone": phone,
                "city": form_data.get("city"),
                "state": form_data.get("state"),
                "country": form_data.get("country"),
                "campaign_name": form_data.get("campaign_name"),
                "message": form_data.get("message"),
            }
            g_score, g_reason = ai_legitimacy_score(lead_for_ai)
            if g_score is not None:
                gemini_score = g_score
                if g_score < 40:
                    risk_score += 25
                    reasons.append(f"gemini_low_intent_score_{g_score}")
                elif g_score > 80:
                    risk_score = max(0, risk_score - 15)
        except Exception as ex:
            logger.warning(f"[Interceptor] Gemini intent check skipped: {ex}")

    risk_score = max(0, min(100, risk_score))

    # Verdict Decision
    if risk_score >= 70:
        verdict = "red"
    elif risk_score >= 40:
        verdict = "grey"
    else:
        verdict = "green"

    # Persist verdict
    verdict_record = AdGuardVerdict(
        session_id=session.id if session else None,
        session_uuid=session_uuid,
        adguard_account_id=ws_id if ws_id and str(ws_id).isdigit() else None,
        risk_score=risk_score,
        verdict=verdict,
        reasons=json.dumps(reasons),
        gemini_score=gemini_score,
        otp_status="required" if verdict == "grey" else "none",
    )
    db.add(verdict_record)

    # If Red, record to Shared Fraud Network & create exclusion member
    if verdict == "red":
        if email:
            record_offence(db, "email", email, ws_id)
        if phone:
            record_offence(db, "phone", phone, ws_id)
        if fp_hash:
            record_offence(db, "fingerprint", fp_hash, ws_id)
        if client_ip or (session and session.ip):
            record_offence(db, "ip_subnet", client_ip or session.ip, ws_id)

        # Queue exclusion member for 15-min sync
        if ws_id and str(ws_id).isdigit():
            if phone_h:
                db.add(AdGuardExclusionMember(
                    adguard_account_id=int(ws_id),
                    list_type="google_customer_match",
                    entity_hash=phone_h,
                    entity_type="phone",
                    source="red_verdict",
                ))
                db.add(AdGuardExclusionMember(
                    adguard_account_id=int(ws_id),
                    list_type="meta_custom_audience",
                    entity_hash=phone_h,
                    entity_type="phone",
                    source="red_verdict",
                ))
            if email_h:
                db.add(AdGuardExclusionMember(
                    adguard_account_id=int(ws_id),
                    list_type="google_customer_match",
                    entity_hash=email_h,
                    entity_type="email",
                    source="red_verdict",
                ))

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[Interceptor] verdict commit error: {e}")

    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "integrity_score": 100 - risk_score,
        "reasons": reasons,
        "session_uuid": session_uuid,
        "otp_required": verdict == "grey",
        "gemini_score": gemini_score,
    }
