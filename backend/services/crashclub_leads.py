"""
Crash Club — Meta Lead Ads fetcher service.

Pulls individual lead details (name, phone, email, custom questions) from the
Crash Club Meta ad account and normalises them into a common row shape.

Data sources:
1. Ad account leads edge:  GET /{act_id}/leads  (requires leads_retrieval + ads_read)
2. Lead detail:            GET /{lead_id}?fields=field_data,form_id,created_time,ad_id,campaign_id,adset_id
3. Campaign/adset/ad names resolved from IDs.

This is Crash Club specific and is NOT related to LeadSquared (which is used
only for DSU/DSI per project rules).
"""
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AdOptima")

META_API_VERSION = os.getenv("META_API_VERSION", "v18.0")

# Default Crash Club ad account (overridable via env).
# NOTE: as of 2026-09-02 the current system-user token CANNOT see this account
# (403 #200 owner has not granted ads_read). Access must be granted in Meta
# Business Settings before this service returns live data.
CRASH_CLUB_AD_ACCOUNT = os.getenv("CRASH_CLUB_AD_ACCOUNT", "act_577546498668650")


class MetaLeadsError(Exception):
    """Raised when Meta API returns an error or is misconfigured."""


def _token() -> str:
    token = (
        os.getenv("CRASH_CLUB_META_TOKEN")
        or os.getenv("META_ACCESS_TOKEN")
        or ""
    ).strip()
    if not token:
        raise MetaLeadsError(
            "META_ACCESS_TOKEN not set — cannot fetch Crash Club leads."
        )
    return token


def _get(url: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AdOptima/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        raise MetaLeadsError(f"Meta API HTTP {e.code}: {body[:500]}")
    except Exception as e:
        raise MetaLeadsError(f"Meta API request failed: {e}")


def resolve_ad_object_names(
    ad_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    adset_id: Optional[str] = None,
) -> Dict[str, str]:
    """Resolve Meta IDs to human-readable campaign/adset/ad names (best-effort)."""
    out = {"campaign": "", "adset": "", "ad": ""}
    token = urllib.parse.quote(_token())
    if ad_id:
        d = _get(
            f"https://graph.facebook.com/{META_API_VERSION}/{ad_id}"
            f"?fields=name,campaign_id,adset_id&access_token={token}"
        )
        if "name" in d:
            out["ad"] = d["name"]
            campaign_id = campaign_id or d.get("campaign_id")
            adset_id = adset_id or d.get("adset_id")
    if adset_id:
        d = _get(
            f"https://graph.facebook.com/{META_API_VERSION}/{adset_id}"
            f"?fields=name,campaign_id&access_token={token}"
        )
        if "name" in d:
            out["adset"] = d["name"]
            campaign_id = campaign_id or d.get("campaign_id")
    if campaign_id:
        d = _get(
            f"https://graph.facebook.com/{META_API_VERSION}/{campaign_id}"
            f"?fields=name&access_token={token}"
        )
        if "name" in d:
            out["campaign"] = d["name"]
    return out


def fetch_lead_details(lead_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single lead's field_data by leadgen id."""
    token = _token()
    fields = "id,created_time,field_data,form_id,ad_id,campaign_id,adset_id"
    url = (
        f"https://graph.facebook.com/{META_API_VERSION}/{lead_id}"
        f"?fields={urllib.parse.quote(fields)}&access_token={urllib.parse.quote(token)}"
    )
    d = _get(url)
    if "error" in d:
        raise MetaLeadsError(d["error"].get("message", "unknown error"))
    return d


def fetch_account_leads(
    ad_account_id: str = None,
    since_unix: Optional[int] = None,
    limit_per_page: int = 100,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    """
    Pull individual leads (with field_data) for the whole ad account.
    GET /{act_id}/leads is not supported on act_* nodes, so we walk:
      act -> campaigns -> ads -> leads
    and also pull form-level leads as a backstop.
    """
    act = ad_account_id or CRASH_CLUB_AD_ACCOUNT
    token = _token()

    leads: List[Dict[str, Any]] = []
    seen: set = set()

    def _paged(url: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        pages = 0
        while url and pages < max_pages:
            d = _get(url)
            if "error" in d:
                raise MetaLeadsError(d["error"].get("message", "unknown error"))
            out.extend(d.get("data", []))
            url = d.get("paging", {}).get("next")
            pages += 1
        return out

    # Path 1: act -> campaigns -> ads -> leads
    camps_url = (
        f"https://graph.facebook.com/{META_API_VERSION}/{act}/campaigns"
        f"?fields=id,name&limit=200&access_token={urllib.parse.quote(token)}"
    )
    try:
        campaigns = _paged(camps_url)
    except MetaLeadsError as e:
        raise MetaLeadsError(f"Cannot list campaigns on {act}: {e}")

    for camp in campaigns:
        cid = camp.get("id")
        cname = camp.get("name", "")
        try:
            ads = _paged(
                f"https://graph.facebook.com/{META_API_VERSION}/{camp['id']}/ads"
                f"?fields=id,name&limit=200&access_token={urllib.parse.quote(token)}"
            )
        except MetaLeadsError as e:
            logger.warning(f"[CrashClub] ads fetch failed for campaign {cname}: {e}")
            continue
        for ad in ads:
            aid = ad.get("id")
            try:
                url = (
                    f"https://graph.facebook.com/{META_API_VERSION}/{aid}/leads"
                    f"?fields={urllib.parse.quote('id,created_time,field_data,form_id')}"
                    f"&limit={limit_per_page}&access_token={urllib.parse.quote(token)}"
                )
                if since_unix:
                    url += f"&since={int(since_unix)}"
                for lead in _paged(url):
                    if lead.get("id") in seen:
                        continue
                    seen.add(lead.get("id"))
                    lead["_campaign_name"] = cname
                    lead["_ad_name"] = ad.get("name", "")
                    leads.append(lead)
            except MetaLeadsError as e:
                logger.warning(f"[CrashClub] leads fetch failed for ad {aid}: {e}")

    return leads


def normalize_lead(lead: Dict[str, Any], extra_names: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Convert raw Meta lead JSON into a flat row matching the sheet columns.
    Dedup key = Meta lead id.
    """
    fields: Dict[str, str] = {}
    for f in lead.get("field_data", []):
        name = (f.get("name") or "").strip()
        values = f.get("values") or []
        val = "; ".join(v for v in values if v) if values else ""
        fields[name] = val

    created_unix = lead.get("created_time")
    created_iso = (
        datetime.fromtimestamp(int(created_unix), tz=timezone.utc).isoformat()
        if created_unix
        else ""
    )

    extra = extra_names or {}
    row = {
        "lead_id": lead.get("id", ""),
        "created_time": created_iso,
        "name": (
            fields.get("full_name")
            or fields.get("name")
            or fields.get("Full Name")
            or fields.get("Name")
            or ""
        ),
        "phone": (
            fields.get("phone_number")
            or fields.get("phone")
            or fields.get("Phone Number")
            or fields.get("phone_number_2")
            or ""
        ),
        "email": (
            fields.get("email")
            or fields.get("email_address")
            or fields.get("Email")
            or ""
        ),
        "city": (
            fields.get("city")
            or fields.get("City")
            or fields.get("location")
            or fields.get("current_city")
            or ""
        ),
        "campaign": lead.get("_campaign_name", ""),
        "adset": lead.get("_adset_name", ""),
        "ad": lead.get("_ad_name", ""),
        "raw": json.dumps(lead, ensure_ascii=False) if lead else "",
    }

    # Preserve any non-standard questions as extra columns (Crash Club form may ask custom Qs).
    known = {
        "full_name", "name", "Full Name", "Name", "phone_number", "phone",
        "Phone Number", "phone_number_2", "email", "email_address", "Email",
        "city", "City", "location", "current_city",
    }
    custom = {k: v for k, v in fields.items() if k not in known and v}
    row["custom_questions"] = json.dumps(custom, ensure_ascii=False) if custom else ""
    row["_custom_fields"] = custom
    return row


def build_sheet_row(row: Dict[str, str]) -> List[str]:
    """Flat list matching the 'Crash Club Leads' tab header order."""
    return [
        row.get("lead_id", ""),
        row.get("created_time", ""),
        row.get("name", ""),
        row.get("phone", ""),
        row.get("email", ""),
        row.get("city", ""),
        row.get("campaign", ""),
        row.get("adset", ""),
        row.get("ad", ""),
        row.get("custom_questions", ""),
        datetime.now(timezone.utc).isoformat(),  # synced_at
    ]


SHEET_HEADERS = [
    "Lead ID", "Lead Created (UTC)", "Name", "Phone", "Email", "City",
    "Campaign", "Ad Set", "Ad", "Custom Questions", "Synced At (UTC)",
]