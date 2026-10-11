"""
AdGuard CRM Delivery Engine — per-subscriber CRM push.

Each workspace's verified leads are delivered to the CRM the subscriber chose
(crm_preference) using their own stored credentials (crm_credentials, Fernet-encrypted).

Supported CRMs (v1):
  - leadsquared: Lead.Capture API (same as legacy global push, per-workspace creds)
  - zoho: Zoho CRM Leads API (refresh-token -> access token -> POST lead)
  - hubspot: HubSpot private-app token -> POST /crm/v3/objects/leads (falls back to contacts)
  - webhook: generic JSON POST with optional HMAC signature header
  - salesforce: TODO (needs OAuth refresh flow — preference accepted, delivery pending)

Design: one entrypoint `deliver_lead(workspace, lead_payload)` called by
process_incoming_lead for verified leads. Never raises — returns a status dict;
failures are recorded on the AdGuardLead row by the caller.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("AdOptima")

_HTTP_TIMEOUT = 20


def get_crm_credentials(encrypted_creds: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decrypt the workspace's CRM credentials. Returns None on any failure."""
    if not encrypted_creds:
        return None
    try:
        from backend.services.crypto import decrypt
        raw = decrypt(encrypted_creds)
        return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning(f"[CRM] decrypt failed: {e}")
        return None


def deliver_lead(ws, lead_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route a verified lead to the workspace's chosen CRM.

    Returns {"status": "pushed"|"failed"|"skipped", "provider": crm, "id": ..., "error": ...}
    """
    from backend.services.adguard_subscription import plan_entitlements
    if plan_entitlements(ws)['observation_only']:
        return {"status": "skipped", "provider": "none", "error": "Free Trial is observation only"}
    crm = (ws.crm_preference or "").strip().lower()
    if not crm or crm == "none":
        return {"status": "skipped", "provider": "none", "error": "no CRM connected"}

    creds = get_crm_credentials(ws.crm_credentials)
    if creds is None:
        return {"status": "failed", "provider": crm, "error": "CRM credentials missing or undecryptable"}

    try:
        if crm == "leadsquared":
            return _push_leadsquared(creds, lead_payload)
        if crm == "zoho":
            return _push_zoho(creds, lead_payload)
        if crm == "hubspot":
            return _push_hubspot(creds, lead_payload)
        if crm == "webhook":
            return _push_webhook(creds, lead_payload)
        if crm == "salesforce":
            return {"status": "failed", "provider": crm, "error": "Salesforce delivery not yet implemented"}
        return {"status": "failed", "provider": crm, "error": f"Unknown CRM '{crm}'"}
    except Exception as e:
        logger.exception(f"[CRM] delivery crashed ({crm})")
        return {"status": "failed", "provider": crm, "error": str(e)}


# ---------------------------------------------------------------------------
# LeadSquared (Lead.Capture — same API as the legacy global push)
# ---------------------------------------------------------------------------

def _push_leadsquared(creds: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    access_key = creds.get("access_key") or ""
    secret_key = creds.get("secret_key") or ""
    host = (creds.get("host") or "api-in21.leadsquared.com").strip()
    if not access_key or not secret_key:
        return {"status": "failed", "provider": "leadsquared", "error": "missing access_key/secret_key"}

    post_data = [
        {"Attribute": "FirstName", "Value": (lead.get("full_name") or "").split(" ")[0][:50]},
        {"Attribute": "LastName", "Value": " ".join((lead.get("full_name") or "").split(" ")[1:])[:50]},
        {"Attribute": "EmailAddress", "Value": lead.get("email") or ""},
        {"Attribute": "Phone", "Value": lead.get("phone") or ""},
        {"Attribute": "mx_City", "Value": lead.get("city") or ""},
        {"Attribute": "mx_State", "Value": lead.get("state") or ""},
        {"Attribute": "ProspectID", "Value": lead.get("email") or lead.get("phone") or ""},
        {"Attribute": "mx_Lead_Source", "Value": lead.get("source") or "AdGuard"},
        {"Attribute": "SearchBy", "Value": "EmailAddress"},
    ]
    if lead.get("campaign_name"):
        post_data.append({"Attribute": "mx_Student_Source", "Value": lead.get("campaign_name")[:100]})

    url = f"https://{host}/v2/LeadManagement.svc/Lead.Capture"
    url = url + "?" + urllib.parse.urlencode({"accessKey": access_key, "secretKey": secret_key})
    body = json.dumps({"PostData": post_data}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            out = json.loads(resp.read().decode() or "{}")
            status = out.get("Status") or ""
            prospect_id = ""
            try:
                prospect_id = (out.get("Message", {}) or {}).get("ProspectID", "")
            except Exception:
                pass
            if status.lower() in ("success", "errormxduplicateentry"):
                return {"status": "pushed", "provider": "leadsquared", "id": prospect_id, "error": None}
            return {"status": "failed", "provider": "leadsquared", "error": f"unexpected status: {status}"}
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode()[:300]
        except Exception:
            pass
        return {"status": "failed", "provider": "leadsquared", "error": f"HTTP {e.code}: {body_txt}"}


# ---------------------------------------------------------------------------
# Zoho CRM (refresh token -> access token -> Leads POST)
# ---------------------------------------------------------------------------

def _zoho_access_token(creds: Dict[str, Any]) -> Optional[str]:
    client_id = creds.get("client_id") or ""
    client_secret = creds.get("client_secret") or ""
    refresh_token = creds.get("refresh_token") or ""
    api_domain = (creds.get("api_domain") or "https://www.zohoapis.com").rstrip("/")
    if not (client_id and client_secret and refresh_token):
        return None
    token_url = f"{api_domain}/oauth/v2/token"
    data = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(token_url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        out = json.loads(resp.read().decode())
    return out.get("access_token")


def _push_zoho(creds: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    token = _zoho_access_token(creds)
    if not token:
        return {"status": "failed", "provider": "zoho", "error": "missing client_id/secret/refresh_token or token refresh failed"}
    api_domain = (creds.get("api_domain") or "https://www.zohoapis.com").rstrip("/")
    last_name = " ".join((lead.get("full_name") or "").split(" ")[1:]) or (lead.get("full_name") or "Unknown")
    payload = {
        "data": [{
            "First_Name": (lead.get("full_name") or "").split(" ")[0] or None,
            "Last_Name": last_name[:100],
            "Email": lead.get("email") or None,
            "Phone": lead.get("phone") or None,
            "City": lead.get("city") or None,
            "State": lead.get("state") or None,
            "Lead_Source": "AdGuard",
            "Campaign_Source": (lead.get("campaign_name") or "")[:100] or None,
        }],
        "duplicate_check_fields": ["Email", "Phone"],
    }
    req = urllib.request.Request(
        f"{api_domain}/crm/v3/Leads",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            out = json.loads(resp.read().decode())
            rows = (out.get("data") or [{}])
            code = rows[0].get("code", "")
            if code in ("SUCCESS", "DUPLICATE_DATA"):
                return {"status": "pushed", "provider": "zoho", "id": rows[0].get("details", {}).get("id", ""), "error": None}
            return {"status": "failed", "provider": "zoho", "error": f"zoho code: {code}"}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "provider": "zoho", "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}


# ---------------------------------------------------------------------------
# HubSpot (private app token)
# ---------------------------------------------------------------------------

def _push_hubspot(creds: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    token = (creds.get("access_token") or "").strip()
    if not token:
        return {"status": "failed", "provider": "hubspot", "error": "missing access_token"}
    name_parts = (lead.get("full_name") or "").split(" ", 1)
    payload = {
        "properties": {
            "firstname": name_parts[0][:100] if name_parts else None,
            "lastname": name_parts[1][:100] if len(name_parts) > 1 else "-",
            "email": lead.get("email") or None,
            "phone": lead.get("phone") or None,
            "city": lead.get("city") or None,
            "state": lead.get("state") or None,
            "hs_lead_status": "NEW",
        }
    }
    payload["properties"] = {k: v for k, v in payload["properties"].items() if v}
    req = urllib.request.Request(
        "https://api.hubapi.com/crm/v3/objects/contacts",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            out = json.loads(resp.read().decode())
            return {"status": "pushed", "provider": "hubspot", "id": out.get("id", ""), "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        # 409 conflict = existing contact; treat as pushed-duplicate
        if e.code == 409:
            return {"status": "pushed", "provider": "hubspot", "id": "", "error": "duplicate contact (treated as success)"}
        return {"status": "failed", "provider": "hubspot", "error": f"HTTP {e.code}: {body}"}


# ---------------------------------------------------------------------------
# Generic webhook (custom CRM / Make / Zapier)
# ---------------------------------------------------------------------------

def _push_webhook(creds: Dict[str, Any], lead: Dict[str, Any]) -> Dict[str, Any]:
    url = (creds.get("url") or "").strip()
    if not url:
        return {"status": "failed", "provider": "webhook", "error": "missing webhook url"}
    secret = (creds.get("secret") or "").strip()
    body = json.dumps(lead).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-AdGuard-Signature"] = "sha256=" + sig
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return {"status": "pushed", "provider": "webhook", "id": "", "error": None}
            return {"status": "failed", "provider": "webhook", "error": f"HTTP {resp.status}"}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "provider": "webhook", "error": f"HTTP {e.code}"}
