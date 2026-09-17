"""
CRM integration endpoints for MIS Reports.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account
from backend.services.crm_connectors import fetch_all_crm_data, get_crm_connector
from backend.services.config import load_config
from backend.routes.auth import get_current_user
from backend.services.activity_log import log_activity

router = APIRouter(prefix="/api", tags=["crm"])


@router.get("/accounts/{account_id}/crm-summary")
def crm_summary(
    account_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = fetch_all_crm_data(account, start_date=start_date, end_date=end_date)

    # Crash Club: inject Meta lead-ads leads stored locally so the
    # Source-wise Breakdown "Meta Ads" row counts real leads.
    try:
        from backend.services.crashclub_db import get_leads_for_account

        meta_leads = get_leads_for_account(account.id, start_date=start_date, end_date=end_date)
        if meta_leads:
            existing = data.get("salesforce", {}).get("leads", [])
            data["salesforce"]["leads"] = existing + meta_leads
    except Exception:
        pass
    # Also include account platform flags and ad metrics
    data["account"] = {
        "id": account.id,
        "name": account.name,
        "has_google": account.has_google,
        "has_meta": account.has_meta,
        "google_external_id": account.google_external_id,
        "meta_external_id": account.meta_external_id,
        "spend": account.spend,
        "conversions": account.conversions,
        "clicks": account.clicks,
        "impressions": account.impressions,
        "ctr": account.ctr,
        "cpa": account.cpa,
    }
    data["start_date"] = start_date
    data["end_date"] = end_date
    log_activity(module="InsightDesk", action="Report Viewed", description=f"CRM summary viewed for {account.name}",
                 user_id=getattr(current_user, "id", None), user_name=getattr(current_user, "full_name", None) or getattr(current_user, "email", None),
                 account_id=account.id, account_name=account.name, db=db)
    return data


@router.get("/accounts/{account_id}/crm/{platform}")
def crm_platform_data(
    account_id: int,
    platform: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if platform not in ("salesforce", "leadsquared"):
        raise HTTPException(status_code=400, detail="Unsupported CRM platform")
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    connector = get_crm_connector(platform, account, start_date=start_date, end_date=end_date)
    if not connector:
        raise HTTPException(status_code=500, detail="Failed to initialize CRM connector")
    return {
        "platform": platform,
        "connected": connector.is_valid,
        "leads": connector.fetch_leads(),
        "opportunities": connector.fetch_opportunities(),
        "start_date": start_date,
        "end_date": end_date,
    }


@router.get("/crm-status")
def crm_status(current_user=Depends(get_current_user)):
    cfg = load_config()
    return {
        "salesforce_configured": bool(
            cfg.get("salesforce_url") and cfg.get("salesforce_client_id") and cfg.get("salesforce_client_secret") and cfg.get("salesforce_refresh_token")
        ),
        "leadsquared_configured": bool(
            cfg.get("leadsquared_access_key") and cfg.get("leadsquared_secret_key") and cfg.get("leadsquared_base_url")
        ),
    }
