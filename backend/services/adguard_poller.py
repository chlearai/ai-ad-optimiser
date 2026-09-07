"""
AdGuard Lead Form Poller.

Pulls daily lead-form submission counts per campaign from the Google Ads API
using the workspace's stored OAuth credentials. This is the server-side
backstop for the webhook path: webhooks deliver lead CONTENT (name/email/phone)
in real time; this poller guarantees the lead COUNT is tracked even when a
webhook is missed, so flagged-vs-total ratios stay honest.

Usage (scheduler or manual):
    from backend.services.adguard_poller import run_poll_for_workspace
    run_poll_for_workspace(workspace_id=1)
"""
import json
import logging
from datetime import datetime, timedelta

from backend.db.database import SessionLocal
from backend.db.models import AdGuardAccount, AdGuardLead
from backend.services.crypto import decrypt

logger = logging.getLogger("AdOptima")

# Conversion-action names that represent lead form submissions on Chlear accounts.
# Extend this list as new forms/campaigns come online.
LEAD_FORM_ACTIONS = [
    "Submit lead form",
    "Lead form - Submit",
    "Submit lead form (1)",
    "Submit lead form (2)",
]


def _client_for(ws: AdGuardAccount):
    from google.ads.googleads.client import GoogleAdsClient

    creds = json.loads(decrypt(ws.google_credentials))
    return GoogleAdsClient.load_from_dict({
        "developer_token": creds.get("developer_token", ""),
        "client_id": creds.get("client_id", ""),
        "client_secret": creds.get("client_secret", ""),
        "refresh_token": creds.get("refresh_token", ""),
        "use_proto_plus": True,
    })


def poll_lead_form_submissions(ws: AdGuardAccount, days: int = 2):
    """For each selected ad account, fetch per-day lead form submissions and
    record placeholder AdGuardLead rows (lead_type='poll_summary') so the
    dashboard reflects submission volume even without webhook traffic.

    Real lead content still arrives via webhook; these rows carry counts only.
    Returns dict {customer_id: rows_written}.
    """
    if not ws.google_credentials:
        return {}

    accounts = json.loads(ws.discovered_accounts) if ws.discovered_accounts else []
    selected = [a for a in accounts if a.get("selected") and not a.get("manager")]
    if not selected:
        return {}

    client = _client_for(ws)
    service = client.get_service("GoogleAdsService")
    written = {}

    for acct in selected:
        cid = acct["id"]
        q = (
            "SELECT segments.date, segments.conversion_action_name, campaign.id, campaign.name, "
            "metrics.conversions FROM campaign "
            "WHERE segments.conversion_action_name IN (" + ",".join(f"'{a}'" for a in LEAD_FORM_ACTIONS) + ") "
            f"AND segments.date DURING LAST_{max(days,1)}_DAYS"
        )
        try:
            rows = list(service.search(customer_id=cid, query=q))
        except Exception as e:
            logger.warning(f"[AdGuard poller] query failed for {cid}: {e}")
            written[cid] = 0
            continue

        count = 0
        db = SessionLocal()
        try:
            for r in rows:
                date = str(r.segments.date)
                total = float(r.metrics.conversions or 0)
                if total <= 0:
                    continue
                # idempotency: one summary row per (workspace, date, campaign, action)
                exists = db.query(AdGuardLead).filter(
                    AdGuardLead.adguard_account_id == ws.id,
                    AdGuardLead.lead_type == "poll_summary",
                    AdGuardLead.campaign_name == f"{r.campaign.name}|{r.segments.conversion_action_name}|{date}",
                ).first()
                if exists:
                    continue
                rec = AdGuardLead(
                    adguard_account_id=ws.id,
                    lead_type="poll_summary",
                    campaign_name=f"{r.campaign.name}|{r.segments.conversion_action_name}|{date}",
                    full_name=f"(poll) {int(total)} lead form submission(s)",
                    integrity_score=100,
                    verdict="verified",
                    lsq_status="not_pushed",
                    flags=json.dumps(["poll_summary_row", "count_only"]),
                )
                db.add(rec)
                count += 1
            db.commit()
        finally:
            db.close()
        written[cid] = count
        logger.info(f"[AdGuard poller] {cid}: {count} summary rows written")

    return written


def run_poll_for_workspace(workspace_id: int, days: int = 2):
    db = SessionLocal()
    try:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
        if not ws:
            return {"error": "workspace not found"}
        return poll_lead_form_submissions(ws, days=days)
    finally:
        db.close()