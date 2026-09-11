"""
Meta OAuth + discovery for AdGuard (Ryze-style self-serve).

User clicks Connect Meta Ads -> Meta Login dialog -> we exchange the code for
a long-lived user token (encrypted at rest) -> discover ad accounts and Pages
-> user picks which to protect -> leadgen leads flow into the gatekeeper.

The user never sees an access token. Scopes requested: ads_read (spend/
campaign context), leads_retrieval (read lead content), pages_show_list +
pages_read_engagement (enumerate Pages to subscribe for leadgen), and
business_management (enumerate businesses/ad accounts reliably).
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from backend.services.crypto import decrypt
from backend.services.config import load_config

logger = logging.getLogger("AdOptima")

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

META_SCOPES = [
    "ads_read",
    "ads_management",
    "leads_retrieval",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_metadata",
    "business_management",
]


def _meta_oauth_cfg() -> Dict[str, Any]:
    cfg = load_config()
    return {
        "meta_app_id": cfg.get("meta_app_id", ""),
        "meta_app_secret": cfg.get("meta_app_secret", ""),
        "redirect_base_url": cfg.get("redirect_base_url", "http://127.0.0.1:8000"),
    }


def _require(cfg: Dict[str, Any], key: str) -> str:
    val = cfg.get(key, "")
    if not val:
        raise RuntimeError(f"Missing required OAuth config: {key}")
    return val


def _redirect_base(cfg: Dict[str, Any]) -> str:
    return cfg.get("redirect_base_url", "http://127.0.0.1:8000").rstrip("/")


def _graph_get(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    query = dict(params)
    query.setdefault("access_token", params.pop("token", ""))
    url = f"{GRAPH}/{path.lstrip('/')}?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"[AdGuard] Meta GET {path} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# OAuth dialog + token exchange
# ---------------------------------------------------------------------------

def get_adguard_meta_auth_url(adguard_account_id: int) -> str:
    cfg = _meta_oauth_cfg()
    app_id = _require(cfg, "meta_app_id")
    redirect_uri = f"{_redirect_base(cfg)}/api/adguard/oauth/meta/callback"
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(META_SCOPES),
        # re-prompt for declined permissions so a partial consent can be repaired
        "auth_type": "rerequest",
        "state": str(adguard_account_id),
    }
    return f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?" + urllib.parse.urlencode(params)


def exchange_adguard_meta_code(code: str) -> Optional[str]:
    """Exchange the dialog code for a long-lived user token (~60 days)."""
    cfg = _meta_oauth_cfg()
    app_id = _require(cfg, "meta_app_id")
    app_secret = _require(cfg, "meta_app_secret")
    redirect_uri = f"{_redirect_base(cfg)}/api/adguard/oauth/meta/callback"
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    data = _graph_get("oauth/access_token", params)
    short_token = (data or {}).get("access_token")
    if not short_token:
        return None
    # Upgrade to long-lived
    ext = _graph_get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    })
    return (ext or {}).get("access_token") or short_token


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_meta_ad_accounts(token: str) -> List[Dict[str, Any]]:
    """List ad accounts reachable by the connected user.

    Returns [{"id": "123", "name": "...", "currency": "INR", "selected": False}]
    """
    results: List[Dict[str, Any]] = []
    url_params = {
        "fields": "id,name,currency,account_status",
        "limit": "100",
        "token": token,
    }
    data = _graph_get("me/adaccounts", url_params)
    for acct in (data or {}).get("data", [])[:50]:
        # Meta returns act_<id>; store the bare numeric id
        raw_id = str(acct.get("id", ""))
        bare = raw_id[4:] if raw_id.startswith("act_") else raw_id
        results.append({
            "id": bare,
            "name": acct.get("name") or bare,
            "currency": acct.get("currency") or "",
            "status": acct.get("account_status"),
            "selected": False,
        })
    logger.info(f"[AdGuard] discovered {len(results)} Meta ad accounts")
    return results


def discover_meta_pages(token: str) -> List[Dict[str, Any]]:
    """List Pages the user manages (leadgen webhooks subscribe at Page level)."""
    results: List[Dict[str, Any]] = []
    data = _graph_get("me/accounts", {"fields": "id,name,tasks", "limit": "100", "token": token})
    for page in (data or {}).get("data", [])[:50]:
        tasks = page.get("tasks") or []
        results.append({
            "id": str(page.get("id", "")),
            "name": page.get("name") or "",
            "can_subscribe": "MANAGE" in tasks or "CREATE_CONTENT" in tasks,
        })
    logger.info(f"[AdGuard] discovered {len(results)} Meta Pages")
    return results


def subscribe_page_to_app(page_id: str, page_token: str) -> bool:
    """Subscribe a Page to the app's leadgen webhooks (needs pages_manage_metadata)."""
    url = f"{GRAPH}/{page_id}/subscribed_apps"
    data = urllib.parse.urlencode({"subscribed_fields": "leadgen", "access_token": page_token}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode())
            return bool(out.get("success"))
    except Exception as e:
        logger.error(f"[AdGuard] Page {page_id} subscribe failed: {e}")
        return False


def get_page_access_token(user_token: str, page_id: str) -> Optional[str]:
    data = _graph_get(str(page_id), {"fields": "access_token", "token": user_token})
    return (data or {}).get("access_token")


def get_all_page_tokens(user_token: str) -> Dict[str, str]:
    """Fetch ALL page access tokens in one call (avoids per-page rate limits).

    Returns {page_id: page_access_token}.
    """
    out: Dict[str, str] = {}
    data = _graph_get("me/accounts", {"fields": "id,access_token", "limit": "100", "token": user_token})
    for page in (data or {}).get("data", []):
        pid = str(page.get("id") or "")
        tok = page.get("access_token") or ""
        if pid and tok:
            out[pid] = tok
    # handle pagination just in case
    next_url = (data or {}).get("paging", {}).get("next")
    while next_url and len(out) < 200:
        try:
            req = urllib.request.Request(next_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.error(f"[AdGuard] page token pagination failed: {e}")
            break
        for page in (data or {}).get("data", []):
            pid = str(page.get("id") or "")
            tok = page.get("access_token") or ""
            if pid and tok:
                out[pid] = tok
        next_url = (data or {}).get("paging", {}).get("next")
    return out


# ---------------------------------------------------------------------------
# Lead retrieval
# ---------------------------------------------------------------------------

def fetch_meta_lead(leadgen_id: str, token: str) -> Optional[Dict[str, Any]]:
    """Exchange a leadgen id for the full lead (field data + context).

    Returns {"leadgen_id", "form_id", "form_name", "page_id", "ad_id",
             "ad_name", "campaign_id", "campaign_name", "created_time",
             "fields": {name: value}, "raw": <original dict>} or None.
    """
    data = _graph_get(str(leadgen_id), {
        "fields": "id,created_time,form_id,ad_id,ad_name,campaign_id,campaign_name,platform,field_data",
        "token": token,
    })
    if not data:
        return None
    fields: Dict[str, str] = {}
    for item in data.get("field_data", []):
        name = (item.get("name") or "").strip()
        values = item.get("values") or []
        if name and values:
            fields[name] = values[0]
    # Form name (nice for the feed)
    form_name = ""
    if data.get("form_id"):
        form_data = _graph_get(str(data["form_id"]), {"fields": "name", "token": token})
        form_name = (form_data or {}).get("name") or ""
    return {
        "leadgen_id": str(data.get("id") or leadgen_id),
        "form_id": str(data.get("form_id") or ""),
        "form_name": form_name,
        "page_id": str(data.get("page_id") or ""),
        "ad_id": str(data.get("ad_id") or ""),
        "ad_name": data.get("ad_name") or "",
        "campaign_id": str(data.get("campaign_id") or ""),
        "campaign_name": data.get("campaign_name") or "",
        "created_time": data.get("created_time") or "",
        "fields": fields,
        "raw": data,
    }


# ---------------------------------------------------------------------------
# Workspace credential helpers
# ---------------------------------------------------------------------------

def build_meta_credentials(token: str) -> str:
    from backend.services.crypto import encrypt
    return encrypt(json.dumps({"access_token": token}))


def get_meta_token_from_credentials(credentials_encrypted: str) -> Optional[str]:
    try:
        return json.loads(decrypt(credentials_encrypted)).get("access_token")
    except Exception as e:
        logger.error(f"[AdGuard] failed to read Meta credentials: {e}")
        return None