"""
Account management and dashboard summary APIs.
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, AccountGroup, AccountType, AccountStatus, User, UserAccountAssignment, CampaignTypeTag, AppSetting
from backend.db.revenueops_models import RevClient, RevClientStatus
from backend.services.crypto import encrypt, decrypt
from backend.services.connectors import get_connector, meta_system_token_configured, get_meta_access_token
from backend.services.activity_log import log_activity
from backend.services.oauth import build_google_credentials, build_meta_credentials
from backend.services.config import load_config
from backend.routes.auth import get_current_user_required

logger = logging.getLogger("AdOptima")
router = APIRouter(prefix="/api", tags=["accounts"])


class CampaignTypeTagUpdate(BaseModel):
    campaign_type: str


def _detect_campaign_type(campaign_name: str) -> str:
    name_lower = (campaign_name or "").lower()
    if "brand" in name_lower or "branded" in name_lower:
        return "brand"
    return "non_brand"


def _filter_accounts_for_user(query, user: User):
    """BM (role=user) sees only assigned accounts. Admin/superadmin see all."""
    if user.role in ("admin", "superadmin"):
        return query
    return query.join(UserAccountAssignment, UserAccountAssignment.account_id == Account.id).filter(
        UserAccountAssignment.user_id == user.id
    )


def _badges_for_tile(account, platform: str, db: Session) -> dict:
    """Compute API + Performance health badges for a dashboard tile.

    Uses cached metrics + LSQ mirror leads for today. Falls back gracefully
    if leads cannot be fetched.
    """
    from backend.services.health import compute_health_badges
    from datetime import datetime, timedelta

    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    leads_today = 0
    try:
        from backend.services.lsq_mirror import count_leads_by_course
        counts = count_leads_by_course(db, account.id, today, today)
        leads_today = sum(counts.values())
    except Exception:
        pass

    # API success is inferred from whether the account has a live platform with
    # valid credentials and no last_sync_error. Meta can use a global system
    # user token, so per-account credentials are optional for Meta.
    platform_live = account.google_is_live if platform == "google" else account.meta_is_live
    platform_creds = account.google_credentials if platform == "google" else account.meta_credentials
    has_system_meta_token = platform == "meta" and meta_system_token_configured()
    api_success = bool(platform_live and (platform_creds or has_system_meta_token) and not account.last_sync_error)

    return compute_health_badges(
        account, api_success=api_success, platform=platform, leads=leads_today,
        active_start=0, active_end=23,
    )


class AccountCreate(BaseModel):
    name: str
    group_id: Optional[int] = None
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    refresh_interval_minutes: int = 60

    has_google: bool = True
    has_meta: bool = False

    google_external_id: Optional[str] = None
    meta_external_id: Optional[str] = None
    google_is_live: bool = False
    meta_is_live: bool = False

    crm_type: str = "none"
    crm_credentials: Optional[str] = None
    target_cpa: Optional[float] = None

    brand_name: Optional[str] = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    business_manager_id: Optional[int] = None
    category_id: Optional[int] = None

    client_status: Optional[str] = None
    invoice_day: Optional[int] = None
    payment_due_days: Optional[int] = None
    billing_amount: Optional[float] = None
    gst_number: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    adpulse_refresh_interval: Optional[int] = None
    adpulse_audit_interval: Optional[int] = None
    brand_keywords: Optional[str] = None
    business_context: Optional[str] = None
    negative_rules: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    group_id: Optional[int] = None
    refresh_interval_minutes: Optional[int] = None
    audit_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None

    has_google: Optional[bool] = None
    has_meta: Optional[bool] = None

    google_external_id: Optional[str] = None
    meta_external_id: Optional[str] = None
    google_is_live: Optional[bool] = None
    meta_is_live: Optional[bool] = None
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_developer_token: Optional[str] = None
    google_login_customer_id: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    redirect_base_url: Optional[str] = None
    google_refresh_token: Optional[str] = None
    meta_access_token: Optional[str] = None

    lsq_access_key: Optional[str] = None
    lsq_secret_key: Optional[str] = None
    lsq_base_url: Optional[str] = None
    lsq_sync_interval_minutes: Optional[int] = None

    crm_type: Optional[str] = None
    crm_credentials: Optional[str] = None
    target_cpa: Optional[float] = None

    brand_name: Optional[str] = None
    brand_keywords: Optional[str] = None
    business_context: Optional[str] = None
    negative_rules: Optional[str] = None
    contact_person: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    business_manager_id: Optional[int] = None
    category_id: Optional[int] = None

    client_status: Optional[str] = None
    invoice_day: Optional[int] = None
    payment_due_days: Optional[int] = None
    billing_amount: Optional[float] = None
    gst_number: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    adpulse_refresh_interval: Optional[int] = None
    adpulse_audit_interval: Optional[int] = None


class GroupCreate(BaseModel):
    name: str


class EduInfraSheet(BaseModel):
    name: str = Field(default="", max_length=100)
    url: str = Field(default="", max_length=2048)


def _eduinfra_access(account_id, db, user):
    if user.role not in ("admin", "superadmin") and not user.access_insightdesk:
        raise HTTPException(403, "InsightDesk access required")
    account = _filter_accounts_for_user(db.query(Account), user).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "Client/Brand not found")
    if "eduinfra" not in (account.name or "").lower().replace(" ", ""):
        raise HTTPException(400, "These sheet slots are for EduInfra only")


@router.get("/accounts/{account_id}/eduinfra-sheets")
def get_eduinfra_sheets(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _eduinfra_access(account_id, db, user)
    slots = []
    for slot in range(1, 4):
        setting = db.query(AppSetting).filter_by(key=f"eduinfra_sheets:{account_id}:{slot}").first()
        slots.append(json.loads(setting.value) if setting else {"name": "", "url": ""})
    return slots


@router.put("/accounts/{account_id}/eduinfra-sheets/{slot}")
def save_eduinfra_sheet(account_id: int, slot: int, payload: EduInfraSheet, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _eduinfra_access(account_id, db, user)
    if slot not in (1, 2, 3):
        raise HTTPException(400, "Choose sheet slot 1, 2 or 3")
    url = payload.url.strip()
    parsed = urllib.parse.urlsplit(url)
    import re
    if url and (parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not re.fullmatch(r"/spreadsheets/d/[A-Za-z0-9_-]+(?:/.*)?", parsed.path)):
        raise HTTPException(400, "Enter a valid Google Sheets link (https://docs.google.com/spreadsheets/d/...)")
    value = {"name": payload.name.strip(), "url": url}
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    key = f"eduinfra_sheets:{account_id}:{slot}"
    setting = db.query(AppSetting).filter_by(key=key).first()
    if not setting:
        setting = AppSetting(key=key)
        db.add(setting)
    setting.value = json.dumps(value)
    db.commit()
    return value


@router.get("/accounts")
def list_accounts(active_only: bool = True, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    q = db.query(Account)
    if active_only:
        q = q.filter(
            Account.is_active == True,
            (Account.client_status.in_(["Active", "active"]) | (Account.client_status == None) | (Account.client_status == ""))
        )
    q = _filter_accounts_for_user(q, user)
    accounts = q.all()
    return [a.to_dict() for a in accounts]



@router.get("/accounts/summary")
def dashboard_summary(start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Grouped dashboard summary across all accounts. Optionally filtered by date range.
    Only live accounts (DSU, DSI) contribute to totals and tile metrics; non-live accounts show zeros.
    BM users only see their assigned accounts."""
    q = db.query(Account).filter(
        Account.is_active == True,
        Account.is_live == True,
        (Account.client_status.in_(["Active", "active"]) | (Account.client_status == None) | (Account.client_status == ""))
    )
    q = _filter_accounts_for_user(q, user)
    accounts = q.all()
    groups = db.query(AccountGroup).all()

    live_accounts = [a for a in accounts if a.is_live]
    result = {
        "total_accounts": len(accounts),
        "total_spend": sum(a.spend for a in live_accounts),
        "total_conversions": sum(a.conversions for a in live_accounts),
        "total_clicks": sum(a.clicks for a in live_accounts),
        "total_impressions": sum(a.impressions for a in live_accounts),
        "start_date": start_date,
        "end_date": end_date,
        "groups": [],
        "ungrouped": [],
    }

    # Group accounts by client group and split into platform-specific items
    group_map = {g.id: g.name for g in groups}
    grouped = {}
    ungrouped = []
    for a in accounts:
        d = a.to_dict()
        # Zero out metrics for non-live accounts so dashboard only shows real data
        if not a.is_live:
            d["spend"] = 0.0
            d["conversions"] = 0.0
            d["clicks"] = 0.0
            d["impressions"] = 0.0
            d["ctr"] = 0.0
            d["cpa"] = 0.0
            d["budget"] = 0.0
            d["budget_used_pct"] = 0.0
        for platform in ["google", "meta"]:
            if (platform == "google" and not a.has_google) or (platform == "meta" and not a.has_meta):
                continue
            tile = dict(d)
            tile["platform"] = platform
            tile["external_id"] = a.google_external_id if platform == "google" else a.meta_external_id or d.get("external_id")
            tile["is_live"] = a.google_is_live if platform == "google" else a.meta_is_live
            tile["credentials_masked"] = bool(a.google_credentials) if platform == "google" else bool(a.meta_credentials)
            # Health badges
            _hb = _badges_for_tile(a, platform, db)
            tile["api_health"] = _hb["api_health"]
            tile["perf_health"] = _hb["perf_health"]
            # Billing chip
            from backend.services.billing import get_billing_for_account
            tile["billing"] = get_billing_for_account(a)
            if a.group_id:
                grouped.setdefault(a.group_id, {"group_id": a.group_id, "group_name": group_map.get(a.group_id, "Unknown"), "accounts": []})
                grouped[a.group_id]["accounts"].append(tile)
            else:
                ungrouped.append(tile)

    result["groups"] = list(grouped.values())
    result["ungrouped"] = ungrouped
    return result


@router.post("/accounts")
def create_account(req: AccountCreate, db: Session = Depends(get_db)):
    # Backward-compatible: at least one platform must be enabled; default type from first enabled platform
    if req.has_google and req.has_meta:
        account_type = AccountType.BOTH
        external_id = req.google_external_id or req.meta_external_id or ""
    elif req.has_google:
        account_type = AccountType.GOOGLE
        external_id = req.google_external_id or ""
    elif req.has_meta:
        account_type = AccountType.META
        external_id = req.meta_external_id or ""
    else:
        raise HTTPException(status_code=400, detail="At least one platform (Google or Meta) must be enabled")

    account = Account(
        name=req.name,
        account_type=account_type,
        external_id=external_id,
        group_id=req.group_id,
        category_id=req.category_id,
        has_google=req.has_google,
        has_meta=req.has_meta,
        google_external_id=req.google_external_id,
        meta_external_id=req.meta_external_id,
        google_credentials=None,
        meta_credentials=None,
        google_is_live=req.google_is_live,
        meta_is_live=req.meta_is_live,
        currency=req.currency,
        timezone=req.timezone,
        refresh_interval_minutes=req.refresh_interval_minutes,
        status=AccountStatus.DISCONNECTED,
        is_live=req.google_is_live or req.meta_is_live,
        is_active=(req.client_status or "Active").lower() in ["active"],
        crm_type=req.crm_type or "none",
        crm_credentials=req.crm_credentials,
        target_cpa=req.target_cpa,
        brand_name=req.brand_name,
        brand_keywords=req.brand_keywords,
        business_context=req.business_context,
        negative_rules=req.negative_rules,
        contact_person=req.contact_person,
        contact_email=req.contact_email,
        contact_phone=req.contact_phone,
        business_manager_id=req.business_manager_id,
        client_status=req.client_status or "Active",
        invoice_day=req.invoice_day,
        payment_due_days=req.payment_due_days if req.payment_due_days is not None else 45,
        billing_amount=req.billing_amount,
        gst_number=req.gst_number,
        address=req.address,
        state=req.state,
        state_code=req.state_code,
        adpulse_refresh_interval=req.adpulse_refresh_interval if req.adpulse_refresh_interval is not None else 5,
        adpulse_audit_interval=req.adpulse_audit_interval if req.adpulse_audit_interval is not None else 60,
    )
    db.add(account)
    db.flush()

    # Auto-create or link a RevenueOps client
    existing_client = db.query(RevClient).filter(RevClient.client_name == req.name).first()
    if existing_client:
        account.rev_client_id = existing_client.id
    else:
        rev_client = RevClient(
            account_id=account.id,
            client_name=req.name,
            brand_name=req.brand_name,
            contact_person=req.contact_person,
            contact_email=req.contact_email,
            contact_phone=req.contact_phone,
            business_manager_id=req.business_manager_id,
            client_status=RevClientStatus.ACTIVE.value,
        )
        db.add(rev_client)
        db.flush()
        account.rev_client_id = rev_client.id

    db.commit()
    db.refresh(account)
    log_activity(
        module="System",
        action="Account Created",
        description=f"Created Client/Brand {account.name}",
        user_id=None,
        user_name="System",
        account_id=account.id,
        account_name=account.name,
        entity_type="account",
        entity_id=str(account.id),
        db=db,
    )
    return account.to_dict()


@router.put("/accounts/{account_id}")
def update_account(account_id: int, req: AccountUpdate, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if req.name is not None:
        account.name = req.name
    if req.group_id is not None:
        account.group_id = req.group_id
    if req.category_id is not None:
        account.category_id = req.category_id
    if req.refresh_interval_minutes is not None:
        account.refresh_interval_minutes = req.refresh_interval_minutes
    if req.audit_interval_minutes is not None:
        account.audit_interval_minutes = req.audit_interval_minutes
    if req.is_active is not None:
        account.is_active = req.is_active

    if req.has_google is not None:
        account.has_google = req.has_google
    if req.has_meta is not None:
        account.has_meta = req.has_meta
    if req.google_external_id is not None:
        account.google_external_id = req.google_external_id
    if req.meta_external_id is not None:
        account.meta_external_id = req.meta_external_id
    if req.google_is_live is not None:
        account.google_is_live = req.google_is_live
    if req.meta_is_live is not None:
        account.meta_is_live = req.meta_is_live

    if req.google_client_id is not None:
        account.google_client_id = req.google_client_id or None
    if req.google_client_secret is not None:
        account.google_client_secret = req.google_client_secret or None
    if req.google_developer_token is not None:
        account.google_developer_token = req.google_developer_token or None
    if req.google_login_customer_id is not None:
        account.google_login_customer_id = req.google_login_customer_id or None
    # Encrypt raw tokens supplied from the integrations page (direct entry path)
    if hasattr(req, "google_refresh_token") and req.google_refresh_token:
        encrypted = build_google_credentials(
            {"refresh_token": req.google_refresh_token, "token_type": "Bearer"},
            account.id,
        )
        if encrypted:
            account.google_credentials = encrypted
            account.google_is_live = True
    if hasattr(req, "meta_access_token") and req.meta_access_token:
        encrypted = build_meta_credentials(
            {"access_token": req.meta_access_token},
            account.id,
        )
        if encrypted:
            account.meta_credentials = encrypted
            account.meta_is_live = True
    if req.meta_app_id is not None:
        account.meta_app_id = req.meta_app_id or None
    if req.meta_app_secret is not None:
        account.meta_app_secret = req.meta_app_secret or None
    if req.redirect_base_url is not None:
        account.redirect_base_url = req.redirect_base_url or None

    if req.lsq_access_key is not None:
        account.lsq_access_key = req.lsq_access_key or None
    if req.lsq_secret_key is not None:
        account.lsq_secret_key = req.lsq_secret_key or None
    if req.lsq_base_url is not None:
        account.lsq_base_url = req.lsq_base_url or None
    if req.lsq_sync_interval_minutes is not None:
        account.lsq_sync_interval_minutes = req.lsq_sync_interval_minutes

    # Reschedule LSQ sync job for this account if LSQ settings changed
    if (
        req.lsq_access_key is not None
        or req.lsq_secret_key is not None
        or req.lsq_base_url is not None
        or req.lsq_sync_interval_minutes is not None
        or req.crm_type is not None
    ):
        try:
            from backend.services.scheduler import reschedule_lsq_sync_for_account
            reschedule_lsq_sync_for_account(account.id)
        except Exception as e:
            logger.warning(f"Failed to reschedule LSQ sync for account {account.id}: {e}")

    if req.crm_type is not None:
        account.crm_type = req.crm_type or "none"
    if req.crm_credentials is not None:
        account.crm_credentials = req.crm_credentials or None
    if req.target_cpa is not None:
        account.target_cpa = req.target_cpa or None

    if req.client_status is not None:
        account.client_status = req.client_status or "Active"
        account.is_active = (account.client_status.lower() in ["active"])
    if req.invoice_day is not None:
        account.invoice_day = req.invoice_day
    if req.payment_due_days is not None:
        account.payment_due_days = req.payment_due_days
    if req.billing_amount is not None:
        account.billing_amount = req.billing_amount
    if req.gst_number is not None:
        account.gst_number = req.gst_number or None
    if req.address is not None:
        account.address = req.address or None
    if req.state is not None:
        account.state = req.state or None
    if req.state_code is not None:
        account.state_code = req.state_code or None
    if req.adpulse_refresh_interval is not None:
        account.adpulse_refresh_interval = req.adpulse_refresh_interval
        account.refresh_interval_minutes = req.adpulse_refresh_interval
    if req.adpulse_audit_interval is not None:
        account.adpulse_audit_interval = req.adpulse_audit_interval
        account.audit_interval_minutes = req.adpulse_audit_interval

    if req.brand_name is not None:
        account.brand_name = req.brand_name or None
    if req.brand_keywords is not None:
        account.brand_keywords = req.brand_keywords or None
    if req.business_context is not None:
        account.business_context = req.business_context or None
    if req.negative_rules is not None:
        account.negative_rules = req.negative_rules or None
    if req.contact_person is not None:
        account.contact_person = req.contact_person or None
    if req.contact_email is not None:
        account.contact_email = req.contact_email or None
    if req.contact_phone is not None:
        account.contact_phone = req.contact_phone or None
    if req.business_manager_id is not None:
        account.business_manager_id = req.business_manager_id or None

    # Keep legacy fields in sync
    if account.has_google and account.has_meta:
        account.account_type = AccountType.BOTH
    elif account.has_google:
        account.account_type = AccountType.GOOGLE
    elif account.has_meta:
        account.account_type = AccountType.META
    account.external_id = account.google_external_id or account.meta_external_id or ""
    account.is_live = account.google_is_live or account.meta_is_live

    # Sync contact fields to linked RevenueOps client
    if account.rev_client_id:
        rev_client = db.query(RevClient).filter(RevClient.id == account.rev_client_id).first()
        if rev_client:
            if req.name is not None:
                rev_client.client_name = req.name
            if req.brand_name is not None:
                rev_client.brand_name = req.brand_name or None
            if req.contact_person is not None:
                rev_client.contact_person = req.contact_person or None
            if req.contact_email is not None:
                rev_client.contact_email = req.contact_email or None
            if req.contact_phone is not None:
                rev_client.contact_phone = req.contact_phone or None
            if req.business_manager_id is not None:
                rev_client.business_manager_id = req.business_manager_id or None
            # Sync new billing fields back to linked RevenueOps client
            if req.client_status is not None:
                rev_client.client_status = (req.client_status or "Active").lower().replace(" ", "_")
            if req.invoice_day is not None:
                rev_client.invoice_day = req.invoice_day or 1
            if req.payment_due_days is not None:
                rev_client.default_due_days = req.payment_due_days or 30

    db.commit()
    db.refresh(account)
    log_activity(
        module="System",
        action="Account Updated",
        description=f"Updated Client/Brand {account.name}",
        user_id=None,
        user_name="System",
        account_id=account.id,
        account_name=account.name,
        entity_type="account",
        entity_id=str(account.id),
        db=db,
    )
    return account.to_dict()


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, delete_revops: bool = False, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    rev_client_id = account.rev_client_id
    account_name = account.name
    account_id_val = account.id
    db.delete(account)
    if delete_revops and rev_client_id:
        rev_client = db.query(RevClient).filter(RevClient.id == rev_client_id).first()
        if rev_client:
            db.delete(rev_client)
    db.commit()
    log_activity(
        module="System",
        action="Account Deleted",
        description=f"Deleted Client/Brand {account_name}",
        user_id=None,
        user_name="System",
        account_id=account_id_val,
        account_name=account_name,
        entity_type="account",
        entity_id=str(account_id_val),
        db=db,
    )
    return {"status": "success", "rev_client_deleted": delete_revops and bool(rev_client_id)}


@router.post("/accounts/{account_id}/test-pull/{platform}")
def test_pull(account_id: int, platform: str, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if platform not in ("google", "meta"):
        raise HTTPException(status_code=400, detail="Platform must be google or meta")

    platform_creds = account.google_credentials if platform == "google" else account.meta_credentials
    platform_live = account.google_is_live if platform == "google" else account.meta_is_live
    # Meta can use a global system user token; in that case per-account credentials are optional.
    has_system_meta_token = platform == "meta" and meta_system_token_configured()
    use_live = platform_live and (bool(platform_creds) or has_system_meta_token)

    if not use_live:
        raise HTTPException(status_code=400, detail=f"{platform.title()} is not live or has no credentials")

    connector = get_connector(account, platform=platform)
    if not connector or not connector.is_valid:
        raise HTTPException(status_code=400, detail="Invalid credentials or connector not configured")

    try:
        metrics = connector.fetch_account_metrics()
        if "error" in metrics:
            raise HTTPException(status_code=400, detail=metrics["error"])
        campaigns = connector.fetch_campaigns()
        return {
            "status": "success",
            "platform": platform,
            "metrics": metrics,
            "campaigns_count": len(campaigns),
            "sample_campaigns": campaigns[:5],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"{platform.title()} test pull failed for account {account_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/accounts/{account_id}/refresh")
def refresh_account(account_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, platform: Optional[str] = None, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    target_platform = platform
    if not target_platform:
        if account.account_type == AccountType.GOOGLE:
            target_platform = "google"
        elif account.account_type == AccountType.META:
            target_platform = "meta"
        elif account.has_google:
            target_platform = "google"
        elif account.has_meta:
            target_platform = "meta"
        else:
            raise HTTPException(status_code=400, detail="No platform enabled for this account")

    platform_creds = account.google_credentials if target_platform == "google" else account.meta_credentials
    platform_live = account.google_is_live if target_platform == "google" else account.meta_is_live
    has_system_meta_token = target_platform == "meta" and meta_system_token_configured()
    use_live = platform_live and (bool(platform_creds) or has_system_meta_token)

    if use_live:
        connector = get_connector(account, platform=target_platform, start_date=start_date, end_date=end_date)
        if connector and connector.is_valid:
            metrics = connector.fetch_account_metrics()
            if "error" not in metrics:
                # Aggregate into account-level metrics: if only one platform enabled, overwrite; otherwise add
                if not (account.has_google and account.has_meta):
                    account.spend = metrics.get("spend", 0.0)
                    account.clicks = metrics.get("clicks", 0)
                    account.impressions = metrics.get("impressions", 0)
                    account.conversions = metrics.get("conversions", 0)
                else:
                    account.spend = (account.spend or 0.0) + metrics.get("spend", 0.0)
                    account.clicks = (account.clicks or 0) + metrics.get("clicks", 0)
                    account.impressions = (account.impressions or 0) + metrics.get("impressions", 0)
                    account.conversions = (account.conversions or 0) + metrics.get("conversions", 0)
                account.ctr = round((account.clicks / account.impressions) * 100, 2) if account.impressions else 0.0
                account.cpa = round(account.spend / account.conversions, 2) if account.conversions else 0.0
                # Health badges: compute API + Performance health
                from backend.services.health import compute_health_badges
                from datetime import datetime as _dt, timedelta as _td
                _today = (_dt.utcnow() + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
                _leads_today = 0
                try:
                    from backend.services.lsq_mirror import count_leads_by_course as _clbc
                    _counts = _clbc(db, account.id, _today, _today)
                    _leads_today = sum(_counts.values())
                except Exception:
                    pass
                _badges = compute_health_badges(account, api_success=True, platform=target_platform, leads=_leads_today, active_start=0, active_end=23)
                _api_status = _badges["api_health"]["status"]
                _perf_status = _badges["perf_health"]["status"]
                if _api_status == "DISCONNECTED":
                    account.status = AccountStatus.DISCONNECTED
                elif _perf_status == "CRITICAL":
                    account.status = AccountStatus.CRITICAL
                elif _perf_status in ("WARNING", "UNKNOWN"):
                    account.status = AccountStatus.WARNING
                else:
                    account.status = AccountStatus.HEALTHY
                account.last_sync_at = __import__('datetime').datetime.utcnow()
                account.last_sync_error = None
                # Fetch and cache billing data (best-effort)
                try:
                    _billing = connector.fetch_billing()
                    if _billing:
                        import json as _json
                        account.billing_cache = _json.dumps(_billing)
                except Exception:
                    pass
                db.commit()
                db.refresh(account)
                data = account.to_dict()
                data["platform"] = target_platform
                data["api_health"] = _badges["api_health"]
                data["perf_health"] = _badges["perf_health"]
                from backend.services.billing import get_billing_for_account
                data["billing"] = get_billing_for_account(account)
                return data
            else:
                account.last_sync_error = metrics.get("error")
                account.status = AccountStatus.DISCONNECTED
        else:
            account.last_sync_error = "Invalid credentials or connector not configured"
            account.status = AccountStatus.DISCONNECTED

    db.commit()
    db.refresh(account)
    data = account.to_dict()
    data["platform"] = target_platform
    # Compute badges even on failure so frontend can show DISCONNECTED / UNKNOWN
    from backend.services.health import compute_health_badges
    _badges = compute_health_badges(account, api_success=False, platform=target_platform, active_start=0, active_end=23)
    data["api_health"] = _badges["api_health"]
    data["perf_health"] = _badges["perf_health"]
    from backend.services.billing import get_billing_for_account
    data["billing"] = get_billing_for_account(account)
    return data


@router.get("/meta/token-status")
def meta_token_status(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Health/status summary for Meta token and connected accounts."""
    is_system = meta_system_token_configured()
    token_type = "system_user" if is_system else "per_account"
    # Count accounts that are Meta live and have an ad account ID
    connected = db.query(Account).filter(
        Account.is_active == True,
        Account.has_meta == True,
        Account.meta_is_live == True,
        Account.meta_external_id != None,
        Account.meta_external_id != "",
    ).count()
    last_success = db.query(Account.last_sync_at).filter(
        Account.is_active == True,
        Account.has_meta == True,
        Account.meta_is_live == True,
    ).order_by(Account.last_sync_at.desc()).first()
    # Validate token by calling /me if a system token is configured
    is_valid = False
    expires = None
    if is_system:
        token = os.environ.get("META_SYSTEM_USER_TOKEN")
        try:
            url = f"https://graph.facebook.com/v18.0/me?access_token={urllib.parse.quote(token)}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                is_valid = resp.status == 200
        except Exception as e:
            logger.warning(f"Meta system user token validation failed: {e}")
            is_valid = False
    else:
        # Check the first per-account token for expiry info (best effort)
        account_with_token = db.query(Account).filter(
            Account.meta_credentials != None,
            Account.meta_credentials != "",
        ).first()
        if account_with_token:
            token = get_meta_access_token(account_with_token)
            if token:
                try:
                    url = f"https://graph.facebook.com/v18.0/debug_token?input_token={urllib.parse.quote(token)}&access_token={urllib.parse.quote(token)}"
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        debug = json.loads(resp.read().decode())
                        data = debug.get("data", {})
                        is_valid = bool(data.get("is_valid"))
                        exp = data.get("expires_at")
                        if exp:
                            expires = datetime.utcfromtimestamp(exp).isoformat()
                except Exception as e:
                    logger.warning(f"Meta per-account token validation failed: {e}")
                    is_valid = False
    return {
        "token_type": token_type,
        "is_valid": is_valid,
        "expires": expires,
        "accounts_connected": connected,
        "last_successful_pull": last_success[0].isoformat() if last_success and last_success[0] else None,
    }


@router.get("/accounts/{account_id}")
def get_account(account_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, platform: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role in ("user", "newuser") and account_id not in user.assigned_account_ids():
        raise HTTPException(status_code=403, detail="Access denied to this account")
    data = account.to_dict()
    # Zero out metrics for non-live accounts
    if not account.is_live:
        data["spend"] = 0.0
        data["conversions"] = 0.0
        data["clicks"] = 0.0
        data["impressions"] = 0.0
        data["ctr"] = 0.0
        data["cpa"] = 0.0
        data["budget"] = 0.0
        data["budget_used_pct"] = 0.0
    data["start_date"] = start_date
    data["end_date"] = end_date
    data["platform"] = platform
    return data


@router.get("/accounts/{account_id}/campaigns")
def get_account_campaigns(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Return campaigns for an account from its live platform(s)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role in ("user", "newuser") and account_id not in user.assigned_account_ids():
        raise HTTPException(status_code=403, detail="Access denied to this account")

    campaigns = []
    if account.has_google and account.google_is_live:
        try:
            connector = get_connector(account, platform="google")
            if connector and connector.is_valid:
                campaigns.extend(connector.fetch_campaigns())
        except Exception as e:
            logger.warning(f"Failed to fetch Google campaigns for account {account_id}: {e}")

    has_system_meta = meta_system_token_configured()
    if account.has_meta and (account.meta_is_live or has_system_meta):
        try:
            connector = get_connector(account, platform="meta")
            if connector and connector.is_valid:
                campaigns.extend(connector.fetch_campaigns())
        except Exception as e:
            logger.warning(f"Failed to fetch Meta campaigns for account {account_id}: {e}")

    tags = db.query(CampaignTypeTag).filter(CampaignTypeTag.account_id == account_id).all()
    tag_map = {t.campaign_id: t for t in tags}

    results = []
    for camp in campaigns:
        cid = camp.get("id")
        tag = tag_map.get(cid)
        manual_type = tag.campaign_type if tag else "auto"
        auto_type = _detect_campaign_type(camp.get("name", ""))
        final_type = auto_type if manual_type == "auto" else manual_type
        results.append({
            "campaign_id": cid,
            "campaign_name": camp.get("name"),
            "campaign_status": camp.get("status"),
            "auto_type": auto_type,
            "manual_type": manual_type,
            "final_type": final_type,
            "updated_by": tag.updated_by if tag else None,
            "updated_at": tag.updated_at.isoformat() if tag and tag.updated_at else None,
        })
    return {"account_id": account_id, "campaigns": results}


@router.get("/accounts/{account_id}/last-audit-summary")
def get_last_audit_summary(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Return last audited timestamp and count of unique campaigns audited for an account."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role in ("user", "newuser") and account_id not in user.assigned_account_ids():
        raise HTTPException(status_code=403, detail="Access denied to this account")
    from backend.db.models import PendingAction
    last_action = db.query(PendingAction).filter(
        PendingAction.account_id == account_id,
    ).order_by(PendingAction.created_at.desc()).first()
    campaigns_audited = db.query(PendingAction.campaign_id).filter(
        PendingAction.account_id == account_id,
    ).distinct().count()
    return {
        "account_id": account_id,
        "account_name": account.name,
        "last_audited_at": last_action.created_at.isoformat() if last_action else None,
        "campaigns_audited": campaigns_audited,
    }


@router.put("/accounts/{account_id}/campaign-type-tags/{campaign_id}")
def update_campaign_type_tag(account_id: int, campaign_id: str, req: CampaignTypeTagUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Set manual campaign type override for a campaign."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.role in ("user", "newuser") and account_id not in user.assigned_account_ids():
        raise HTTPException(status_code=403, detail="Access denied to this account")
    if req.campaign_type not in ("brand", "non_brand", "auto"):
        raise HTTPException(status_code=400, detail="campaign_type must be brand, non_brand, or auto")

    tag = db.query(CampaignTypeTag).filter(
        CampaignTypeTag.account_id == account_id,
        CampaignTypeTag.campaign_id == campaign_id,
    ).first()
    from datetime import datetime
    if tag:
        tag.campaign_type = req.campaign_type
        tag.updated_by = user.email or user.role
        tag.updated_at = datetime.utcnow()
    else:
        tag = CampaignTypeTag(
            account_id=account_id,
            campaign_id=campaign_id,
            campaign_type=req.campaign_type,
            updated_by=user.email or user.role,
            updated_at=datetime.utcnow(),
        )
        db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag.to_dict()


@router.get("/account-groups")
def list_groups(db: Session = Depends(get_db)):
    groups = db.query(AccountGroup).all()
    return [{"id": g.id, "name": g.name} for g in groups]


@router.get("/accounts/{account_id}/leads")
def get_account_leads(account_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, db: Session = Depends(get_db)):
    """Fetch lead count from LeadSquared for a live account (DSU/DSI). Returns 0 for non-live accounts."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.is_live:
        return {"account_id": account_id, "leads": 0, "source": "leadsquared"}

    from datetime import datetime, timedelta
    if not end_date:
        end_date = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    if not start_date:
        start_date = end_date

    try:
        from backend.services.dsu_data import _fetch_lsq_leads
        course_leads = _fetch_lsq_leads(start_date, end_date, account_id=account_id)
        total = sum(course_leads.values())
        return {"account_id": account_id, "leads": total, "source": "leadsquared", "by_course": course_leads}
    except Exception as e:
        return {"account_id": account_id, "leads": 0, "source": "leadsquared", "error": str(e)}


@router.post("/account-groups")
def create_group(req: GroupCreate, db: Session = Depends(get_db)):
    group = AccountGroup(name=req.name)
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name}


@router.post("/seed-demo-accounts")
def seed_demo_accounts(db: Session = Depends(get_db)):
    """Seed demo accounts if none exist. Deprecated — disabled."""
    return {"status": "skipped", "message": "Demo account seeding is disabled. Create accounts manually."}
