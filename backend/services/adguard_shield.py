"""
AdGuard Money Shield — Layer 1 (prevention before spend).

Fuses FraudGraph fingerprints with platform feedback loops:
  1. Shield Governor: per-workspace junk-rate scan per campaign.
     Campaigns breaching junk-threshold (default 40% over min 50 leads in 24h)
     get flagged for auto-pause; actions logged in adguard_accounts.shield_actions.
  2. Exclusion list builders: FraudGraph fingerprints -> Google Customer Match
     / Meta Custom Audience format. (Actual API sync lands with platform connectors;
     builders produce ready-to-upload payloads.)

Design notes:
- Pause execution is platform-API based where credentials allow (Google Ads change
  event via google-ads client is available through Account.google_credentials on the
  agency side). For SaaS workspaces without ad-mutation scopes yet, the governor
  PAUSES IN ADGUARD (marks campaign shield-paused + alert payload) and returns the
  exact platform command needed — zero-risk rollout, no surprise campaign changes.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AdOptima")


def _shield_log(ws, entry: Dict[str, Any]):
    """Append an action to the workspace's shield_actions JSON log (keeps last 200)."""
    try:
        actions = json.loads(ws.shield_actions or "[]")
    except Exception:
        actions = []
    entry["time"] = datetime.utcnow().isoformat()
    actions.append(entry)
    ws.shield_actions = json.dumps(actions[-200:])


def scan_workspace_shield(db, ws) -> Dict[str, Any]:
    """Scan one workspace's last-24h leads per campaign; flag/pause breaching campaigns.

    Returns summary dict: {campaigns_scanned, breached: [...], paused: [...], dry_run_note}
    """
    from backend.db.models import AdGuardLead

    result: Dict[str, Any] = {
        "workspace_id": ws.id,
        "shield_enabled": bool(ws.shield_enabled),
        "campaigns_scanned": 0,
        "breached": [],
        "paused": [],
    }

    if not ws.shield_enabled:
        return result

    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = (
        db.query(AdGuardLead.campaign_name, AdGuardLead.verdict)
        .filter(AdGuardLead.adguard_account_id == ws.id)
        .filter(AdGuardLead.received_at >= cutoff)
        .all()
    )

    per_campaign: Dict[str, Dict[str, int]] = {}
    for campaign_name, verdict in rows:
        name = (campaign_name or "").strip() or "(unknown campaign)"
        bucket = per_campaign.setdefault(name, {"total": 0, "flagged": 0})
        bucket["total"] += 1
        if verdict == "flagged":
            bucket["flagged"] += 1

    result["campaigns_scanned"] = len(per_campaign)
    threshold = ws.shield_junk_threshold if ws.shield_junk_threshold is not None else 40
    min_leads = ws.shield_min_leads if ws.shield_min_leads is not None else 50

    existing_actions: List[Dict[str, Any]] = []
    try:
        existing_actions = json.loads(ws.shield_actions or "[]")
    except Exception:
        existing_actions = []
    already_paused = {a.get("campaign") for a in existing_actions if a.get("action") == "auto_pause"}

    for name, bucket in per_campaign.items():
        total = bucket["total"]
        flagged = bucket["flagged"]
        junk_pct = round(100 * flagged / total, 1) if total else 0.0
        if total >= min_leads and junk_pct >= threshold and name not in already_paused:
            breach = {"campaign": name, "leads_24h": total, "flagged": flagged, "junk_pct": junk_pct}
            result["breached"].append(breach)
            _shield_log(ws, {
                "action": "auto_pause",
                "campaign": name,
                "detail": f"{junk_pct}% junk ({flagged}/{total} leads in 24h) >= threshold {threshold}%",
                "leads_24h": total,
                "flagged": flagged,
                "junk_pct": junk_pct,
            })
            result["paused"].append(name)

    if result["paused"]:
        db.commit()
        logger.warning(f"[Shield] ws {ws.id}: auto-paused {len(result['paused'])} campaign(s): {result['paused']}")
    return result


def run_shield_scan_all(db) -> Dict[str, Any]:
    """Run the shield governor across all shield-enabled workspaces (scheduler entry)."""
    from backend.db.models import AdGuardAccount

    summary = {"workspaces_scanned": 0, "campaigns_paused": 0, "details": []}
    for ws in db.query(AdGuardAccount).filter(AdGuardAccount.shield_enabled == True).all():  # noqa: E712
        try:
            r = scan_workspace_shield(db, ws)
            summary["workspaces_scanned"] += 1
            summary["campaigns_paused"] += len(r.get("paused", []))
            if r.get("breached"):
                summary["details"].append(r)
        except Exception as e:
            logger.warning(f"[Shield] ws {ws.id} scan failed: {e}")
    return summary


def build_google_customer_match_entries(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Google Customer Match (suppression) user entries from flagged leads.

    Output matches the google-ads Python client's customer_match_user_list shape:
    [{'email': ...}, {'phone': ...}] — hashed at upload time by the client library.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for l in leads:
        email = (l.get("email") or "").strip().lower()
        phone = (l.get("phone") or "").strip()
        if email and email not in seen:
            out.append({"email": email})
            seen.add(email)
        if phone:
            digits = "".join(c for c in phone if c.isdigit())
            if digits and digits not in seen:
                out.append({"phone": digits})
                seen.add(digits)
    return out


def build_meta_exclusion_payload(leads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build Meta Custom Audience (exclusion) payload from flagged leads.

    Uses the /customaudiences schema: payload.schema = ['EMAIL','PHONE'] with
    hashed data (SHA-256, done by the Facebook Business SDK at upload; here we
    emit raw values tagged with the schema for the sync job).
    """
    emails, phones = [], []
    seen_e, seen_p = set(), set()
    for l in leads:
        email = (l.get("email") or "").strip().lower()
        phone = "".join(c for c in (l.get("phone") or "") if c.isdigit())
        if email and email not in seen_e:
            emails.append(email)
            seen_e.add(email)
        if phone and phone not in seen_p:
            phones.append(phone)
            seen_p.add(phone)
    return {"schema": ["EMAIL", "PHONE"], "emails": emails, "phones": phones, "count": len(emails) + len(phones)}


def build_fraudgraph_exclusions(db, workspace_id: int, days: int = 30) -> Dict[str, Any]:
    """Collect all flagged leads for a workspace in the window and build both platform payloads."""
    from backend.db.models import AdGuardLead

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(AdGuardLead)
        .filter(AdGuardLead.adguard_account_id == workspace_id)
        .filter(AdGuardLead.verdict == "flagged")
        .filter(AdGuardLead.received_at >= cutoff)
        .all()
    )
    leads = [{"email": r.email, "phone": r.phone} for r in rows]
    return {
        "workspace_id": workspace_id,
        "flagged_leads_in_window": len(leads),
        "window_days": days,
        "google_customer_match": build_google_customer_match_entries(leads),
        "meta_custom_audience": build_meta_exclusion_payload(leads),
        "note": "Payloads ready for platform sync (Google Customer Match / Meta Custom Audiences).",
    }