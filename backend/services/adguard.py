"""
AdGuard — Lead Integrity Gatekeeper service.

Scores incoming Google Ads lead form submissions:
  1. Disposable email check        (static blocklist)
  2. Phone format validation       (Indian mobile formats)
  3. Geo-mismatch check            (declared geo vs client's target state)
  4. Gemini legitimacy score       (cheap one-shot LLM call)

Verdict:
  - score >= threshold AND no hard flags  -> verified -> pushed to LeadSquared
  - otherwise                             -> flagged  -> stored in adguard_leads only
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AdOptima")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VERIFICATION_THRESHOLD = int(os.getenv("ADGUARD_VERIFICATION_THRESHOLD", "70"))
AI_SCORE_MAX_POINTS = 30
EMAIL_POINTS = 25
PHONE_POINTS = 30
GEO_POINTS = 15

# Common disposable/temporary email providers.
DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "throwawaymail.com", "yopmail.com", "getnada.com",
    "trashmail.com", "sharklasers.com", "dispostable.com", "maildrop.cc",
    "fakeinbox.com", "mailnesia.com", "maildrop.com", "tempr.email",
    "spam4.me", "grr.la", "mytemp.email", "emailondeck.com", "moakt.com",
    "mohmal.com", "email-temp.com", "tmpmail.org", "1secmail.com",
    "1secmail.org", "mcafee.com", "snapmail.cc", "mail.tm", "tmailor.com",
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_email(email: Optional[str]) -> Tuple[bool, bool, List[str]]:
    """Return (email_valid, disposable, flags)."""
    flags: List[str] = []
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return False, False, ["invalid_email"]
    domain = email.split("@")[-1]
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return True, True, ["disposable_email"]
    return True, False, []


def check_phone(phone: Optional[str]) -> Tuple[bool, List[str]]:
    """Validate Indian mobile number (10 digits starting 6-9, optional +91/0 prefix).

    Non-Indian numbers with a valid country-code length are accepted too.
    Returns (phone_valid, flags).
    """
    flags: List[str] = []
    raw = (phone or "").strip()
    if not raw:
        return False, ["missing_phone"]
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return True, []
    if 8 <= len(digits) <= 15:
        # Plausible international number, not an Indian mobile.
        return True, ["phone_non_indian_format"]
    return False, ["invalid_phone"]


def check_geo(lead: Dict[str, Any], account: Any) -> Tuple[bool, List[str]]:
    """Compare the lead's declared geo with the client's target state.

    Only flags when BOTH sides are known and clearly differ (state-level).
    Returns (geo_match, flags).
    """
    flags: List[str] = []
    lead_state = (lead.get("state") or "").strip().lower()
    lead_country = (lead.get("country") or "").strip().lower()
    if lead_country and lead_country not in ("india", "in"):
        return False, ["geo_country_mismatch"]
    expected = ((getattr(account, "state", None) or "") if account else "").strip().lower()
    if not expected or not lead_state:
        return True, []
    # Tolerant containment: "karnataka" vs "karnataka, india" etc.
    if expected in lead_state or lead_state in expected:
        return True, []
    return False, ["geo_state_mismatch"]


# ---------------------------------------------------------------------------
# Gemini legitimacy score (cheap single-shot)
# ---------------------------------------------------------------------------

_AI_PROMPT = """You screen education-admission leads for a digital marketing agency.
Score the legitimacy of this lead from 0 (almost certainly junk/spam) to 100 (clearly genuine).
Consider: name realism, email/phone plausibility, city consistency, message quality.
Reply ONLY with compact JSON: {{"score": <int>, "reason": "<max 15 words>"}}

Lead:
Name: {name}
Email: {email}
Phone: {phone}
City: {city}
State: {state}
Country: {country}
Campaign: {campaign}
Message: {message}"""


def ai_legitimacy_score(lead: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Cheap Gemini call. Returns (score 0-100, reason) or (None, None) when unavailable."""
    try:
        import google.generativeai as genai
        from backend.services.config import load_config

        config = load_config()
        api_key = config.get("gemini_api_key", "")
        if not api_key or len(api_key) <= 10 or api_key.startswith("●●●●"):
            return None, None

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = _AI_PROMPT.format(
            name=lead.get("full_name") or "-",
            email=lead.get("email") or "-",
            phone=lead.get("phone") or "-",
            city=lead.get("city") or "-",
            state=lead.get("state") or "-",
            country=lead.get("country") or "-",
            campaign=lead.get("campaign_name") or "-",
            message=lead.get("message") or "-",
        )
        response = model.generate_content(prompt)
        text = (response.text or "").strip()
        # Tolerate markdown fences from the model
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
        score = int(data.get("score"))
        return max(0, min(100, score)), str(data.get("reason", ""))[:200]
    except Exception as e:
        logger.warning(f"[AdGuard] Gemini legitimacy score failed: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Gatekeeper
# ---------------------------------------------------------------------------


def score_lead(lead: Dict[str, Any], account: Any = None) -> Dict[str, Any]:
    """Score one lead and return the gatekeeper result dict.

    Scoring:
      email valid +25 | phone valid +30 | geo match +15 | ai_score/100*30
    Hard flags (auto-flagged regardless of score):
      disposable_email, invalid_email, invalid_phone, missing_phone,
      geo_country_mismatch
    """
    flags: List[str] = []

    email_valid, disposable, email_flags = check_email(lead.get("email"))
    flags.extend(email_flags)

    phone_valid, phone_flags = check_phone(lead.get("phone"))
    flags.extend(phone_flags)

    geo_match, geo_flags = check_geo(lead, account)
    flags.extend(geo_flags)

    ai_score, ai_reason = ai_legitimacy_score(lead)

    score = 0
    if email_valid and not disposable:
        score += EMAIL_POINTS
    if phone_valid:
        score += PHONE_POINTS
    if geo_match:
        score += GEO_POINTS
    if ai_score is not None:
        score += round(ai_score / 100.0 * AI_SCORE_MAX_POINTS)

    hard_flags = {"disposable_email", "invalid_email", "invalid_phone", "missing_phone", "geo_country_mismatch"}
    hard_flagged = any(f in hard_flags for f in flags)

    if hard_flagged or score < VERIFICATION_THRESHOLD:
        verdict = "flagged"
    else:
        verdict = "verified"

    return {
        "integrity_score": score,
        "verdict": verdict,
        "email_valid": email_valid,
        "disposable_email": disposable,
        "phone_valid": phone_valid,
        "geo_match": geo_match,
        "ai_legitimacy_score": ai_score,
        "ai_reason": ai_reason,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# LeadSquared push (Lead.Capture)
# ---------------------------------------------------------------------------


def _get_lsq_credentials(account: Any) -> Tuple[str, str, str]:
    """(access_key, secret_key, base_url) for an account with global fallback."""
    from backend.services.config import load_config

    access_key = (getattr(account, "lsq_access_key", None) or "").strip()
    secret_key = (getattr(account, "lsq_secret_key", None) or "").strip()
    base_url = (getattr(account, "lsq_base_url", None) or "").strip()

    if not access_key or not secret_key:
        cfg = load_config()
        access_key = cfg.get("leadsquared_access_key", "")
        secret_key = cfg.get("leadsquared_secret_key", "")
        base_url = cfg.get("leadsquared_base_url", "")

    if base_url:
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v2"):
            base_url = base_url + "/v2"
    return access_key, secret_key, base_url


def push_lead_to_lsq(lead: Dict[str, Any], account: Any = None) -> Dict[str, Any]:
    """Create/update a lead in LeadSquared via Lead.Capture.

    Returns {"status": "pushed"|"failed", "prospect_id": ..., "error": ...}.
    """
    import requests

    access_key, secret_key, base_url = _get_lsq_credentials(account)
    if not access_key or not secret_key or not base_url:
        return {"status": "failed", "prospect_id": None, "error": "LeadSquared credentials not configured"}

    url = f"{base_url}/LeadManagement.svc/Lead.Capture"
    params = {"accessKey": access_key, "secretKey": secret_key}
    payload = [
        {"Attribute": "FirstName", "Value": (lead.get("full_name") or "").split(" ")[0] or "Unknown"},
        {"Attribute": "LastName", "Value": " ".join((lead.get("full_name") or "").split(" ")[1:])},
        {"Attribute": "EmailAddress", "Value": lead.get("email") or ""},
        {"Attribute": "Phone", "Value": lead.get("phone") or ""},
        {"Attribute": "Source", "Value": lead.get("source") or "Google Ads Lead Form"},
        {"Attribute": "SourceCampaign", "Value": lead.get("campaign_name") or ""},
    ]
    if lead.get("city"):
        payload.append({"Attribute": "City", "Value": lead["city"]})
    if lead.get("state"):
        payload.append({"Attribute": "State", "Value": lead["state"]})
    if lead.get("country"):
        payload.append({"Attribute": "Country", "Value": lead["country"]})

    try:
        r = requests.post(url, params=params, json=payload, timeout=30)
        # LeadSquared returns HTTP 500 with MXDuplicateEntryException when a lead
        # with the same phone/email already exists — the lead IS in the CRM, so
        # treat that as a successful push.
        if r.status_code == 500 and "DuplicateEntry" in r.text:
            logger.info(f"[AdGuard] LSQ push: duplicate lead already exists for {lead.get('email')}")
            return {"status": "pushed", "prospect_id": None, "error": None}
        r.raise_for_status()
        resp = r.json()
        message = resp.get("Message") or {}
        prospect_id = None
        if isinstance(message, dict):
            prospect_id = message.get("ProspectID") or message.get("prospectid")
        if resp.get("Status") in ("Success", "success", None):
            logger.info(f"[AdGuard] LSQ push OK for {lead.get('email')} prospect_id={prospect_id}")
            return {"status": "pushed", "prospect_id": prospect_id, "error": None}
        return {"status": "failed", "prospect_id": None, "error": str(resp)[:500]}
    except Exception as e:
        logger.error(f"[AdGuard] LSQ push failed for {lead.get('email')}: {e}")
        return {"status": "failed", "prospect_id": None, "error": str(e)[:500]}


# ---------------------------------------------------------------------------
# Normalize Google Ads webhook payloads
# ---------------------------------------------------------------------------


def normalize_google_ads_lead(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Google Ads lead form webhook payload into the internal shape.

    Handles the common shapes:
      - LeadFormData / lead form webhooks (keyed by column names)
      - Zapier/app-script style flat payloads (name/email/phone keys)
    Unknown keys are ignored; raw payload is stored by the route anyway.
    """
    def _pick(*keys: str) -> Any:
        for k in keys:
            lk = k.lower().replace(" ", "_")
            if lk in payload and payload[lk]:
                return payload[lk]
            # also try original spacing
            if k in payload and payload[k]:
                return payload[k]
        # fallback: case-insensitive scan
        for k, v in payload.items():
            if k.lower().replace(" ", "_") in {kk.lower().replace(" ", "_") for kk in keys} and v:
                return v
        return None

    lead = {
        "gclid": _pick("gclid", "Google Click ID", "gcl_id"),
        "form_id": _pick("form_id", "Form ID", "formid"),
        "campaign_name": _pick("campaign_name", "Campaign", "Campaign Name", "CampaignName") or "",
        "full_name": str(_pick("full_name", "Full Name", "Your Name", "Name", "name") or ""),
        "email": str(_pick("email", "Email", "Email Address", "email_address") or ""),
        "phone": str(_pick("phone", "Phone", "Phone Number", "Mobile", "mobile", "Contact Number") or ""),
        "city": str(_pick("city", "City", "Town") or ""),
        "state": str(_pick("state", "State", "Region", "Province") or ""),
        "country": str(_pick("country", "Country") or ""),
        "postal_code": str(_pick("postal_code", "Postal Code", "ZIP", "Zip Code", "Pincode", "pin_code") or ""),
        "message": str(_pick("message", "Message", "Comments", "Remarks") or ""),
        "lead_type": "google_lead_form",
    }
    return lead


def process_incoming_lead(payload: Dict[str, Any], account: Any = None, raw_payload: Optional[str] = None) -> Dict[str, Any]:
    """Full pipeline: normalize -> dedup -> score -> (verified) LSQ push -> persist.

    Returns the saved AdGuardLead.to_dict().
    """
    from backend.db.database import SessionLocal
    from backend.db.models import AdGuardLead

    lead = normalize_google_ads_lead(payload)
    db = SessionLocal()
    try:
        # Dedup: same email or same phone in the last 7 days.
        dup = None
        now = datetime.utcnow()
        cutoff = now.timestamp() - 7 * 86400
        q = db.query(AdGuardLead).filter(AdGuardLead.received_at >= datetime.utcfromtimestamp(cutoff))
        if lead.get("email"):
            dup = q.filter(AdGuardLead.email == lead["email"]).first()
        if dup is None and lead.get("phone"):
            dup = q.filter(AdGuardLead.phone == lead["phone"]).first()

        result = score_lead(lead, account)
        if dup is not None:
            result["verdict"] = "flagged"
            result["flags"] = list(set(result["flags"]) | {"duplicate_recent_lead"})

        record = AdGuardLead(
            account_id=getattr(account, "id", None) if account is not None else None,
            gclid=lead.get("gclid"),
            form_id=lead.get("form_id"),
            campaign_name=lead.get("campaign_name"),
            lead_type=lead.get("lead_type"),
            full_name=lead.get("full_name"),
            email=lead.get("email"),
            phone=lead.get("phone"),
            city=lead.get("city"),
            state=lead.get("state"),
            country=lead.get("country"),
            postal_code=lead.get("postal_code"),
            raw_payload=raw_payload,
            integrity_score=result["integrity_score"],
            verdict=result["verdict"],
            email_valid=result["email_valid"],
            disposable_email=result["disposable_email"],
            phone_valid=result["phone_valid"],
            geo_match=result["geo_match"],
            ai_legitimacy_score=result["ai_legitimacy_score"],
            ai_reason=result["ai_reason"],
            flags=json.dumps(result["flags"]),
        )

        if result["verdict"] == "verified":
            push = push_lead_to_lsq(lead, account)
            record.lsq_status = push["status"]
            record.lsq_prospect_id = push["prospect_id"]
            record.lsq_error = push["error"]
        else:
            # Flagged/low-score: stored in AdGuard's own table, NOT pushed to LSQ.
            record.lsq_status = "skipped_flagged"

        db.add(record)
        db.commit()
        db.refresh(record)
        record.processed_at = datetime.utcnow()
        db.commit()
        logger.info(
            f"[AdGuard] lead id={record.id} verdict={record.verdict} "
            f"score={record.integrity_score} lsq={record.lsq_status}"
        )
        return record.to_dict()
    finally:
        db.close()