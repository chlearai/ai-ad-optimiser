"""
Database models for AdOptima AI.
"""
import json
import base64
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, Enum, JSON, Date, UniqueConstraint, LargeBinary
from sqlalchemy.orm import relationship
from backend.db.database import Base
import enum


class AccountType(str, enum.Enum):
    GOOGLE = "google"
    META = "meta"
    BOTH = "both"


class AccountStatus(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    DISCONNECTED = "disconnected"
    SYNCING = "syncing"


class AccountGroup(Base):
    __tablename__ = "account_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accounts = relationship("Account", back_populates="group", lazy="dynamic")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    accounts = relationship("Account", back_populates="category", lazy="dynamic")


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("account_groups.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    name = Column(String, nullable=False)  # Client name, e.g. DSU
    account_type = Column(Enum(AccountType), nullable=False)  # legacy single-platform type
    external_id = Column(String, nullable=False)  # legacy single-platform external id
    currency = Column(String, default="INR")
    timezone = Column(String, default="Asia/Kolkata")

    # Per-platform flags: which platforms are enabled for this client
    has_google = Column(Boolean, default=True)
    has_meta = Column(Boolean, default=False)

    # Per-platform credentials (encrypted at rest)
    google_credentials = Column(Text, nullable=True)
    meta_credentials = Column(Text, nullable=True)

    # Per-platform external IDs
    google_external_id = Column(String, nullable=True)
    meta_external_id = Column(String, nullable=True)

    # Per-platform live flags
    google_is_live = Column(Boolean, default=False)
    meta_is_live = Column(Boolean, default=False)

    # Per-platform OAuth app credential overrides (for separate MCC accounts)
    google_client_id = Column(String, nullable=True)
    google_client_secret = Column(String, nullable=True)
    google_developer_token = Column(String, nullable=True)
    google_login_customer_id = Column(String, nullable=True)
    meta_app_id = Column(String, nullable=True)
    meta_app_secret = Column(String, nullable=True)
    redirect_base_url = Column(String, nullable=True)

    # Link to RevenueOps client (auto-created when account is added centrally)
    rev_client_id = Column(Integer, nullable=True)

    # Contact info (shared with RevenueOps client)
    brand_name = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    business_manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    business_manager = relationship("User", foreign_keys=[business_manager_id])

    # Per-account CRM (LeadSquared) credentials
    lsq_access_key = Column(String, nullable=True)
    lsq_secret_key = Column(String, nullable=True)
    lsq_base_url = Column(String, nullable=True)
    lsq_sync_interval_minutes = Column(Integer, default=10)

    # Per-account CRM type + encrypted JSON credentials
    crm_type = Column(String, default="none")
    crm_credentials = Column(Text, nullable=True)

    # Credentials (encrypted at rest via app-level encryption) - legacy fallback
    credentials = Column(Text, nullable=True)

    # Sync settings
    refresh_interval_minutes = Column(Integer, default=60)
    audit_interval_minutes = Column(Integer, default=60)  # auto audit frequency
    adpulse_refresh_interval = Column(Integer, default=5)
    adpulse_audit_interval = Column(Integer, default=60)
    is_active = Column(Boolean, default=True)
    is_live = Column(Boolean, default=False)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_error = Column(Text, nullable=True)

    # Cached dashboard metrics
    status = Column(Enum(AccountStatus), default=AccountStatus.DISCONNECTED)
    spend = Column(Float, default=0.0)
    conversions = Column(Float, default=0.0)
    clicks = Column(Float, default=0.0)
    impressions = Column(Float, default=0.0)
    ctr = Column(Float, default=0.0)
    cpa = Column(Float, default=0.0)
    budget = Column(Float, default=0.0)
    budget_used_pct = Column(Float, default=0.0)
    target_cpa = Column(Float, nullable=True)
    billing_cache = Column(Text, nullable=True)

    # Client business lifecycle status (additive; separate from technical status enum)
    client_status = Column(String, default="Active")

    # Billing / RevenueOps fields
    invoice_day = Column(Integer, nullable=True)
    payment_due_days = Column(Integer, default=45)
    billing_amount = Column(Float, nullable=True)
    gst_number = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    state = Column(String, nullable=True)
    state_code = Column(String, nullable=True)

    # Keyword / search term audit brand settings
    brand_keywords = Column(Text, nullable=True)  # comma-separated brand terms

    # AI Knowledge (optional business context for smarter audits)
    business_context = Column(Text, nullable=True)
    negative_rules = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    group = relationship("AccountGroup", back_populates="accounts")
    category = relationship("Category", back_populates="accounts")
    campaign_type_tags = relationship("CampaignTypeTag", back_populates="account", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "group_id": self.group_id,
            "group_name": self.group.name if self.group else None,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "name": self.name,
            "account_type": self.account_type.value,
            "external_id": self.external_id,
            "currency": self.currency,
            "timezone": self.timezone,
            "has_google": self.has_google,
            "has_meta": self.has_meta,
            "google_external_id": self.google_external_id,
            "meta_external_id": self.meta_external_id,
            "google_is_live": self.google_is_live,
            "meta_is_live": self.meta_is_live,
            "google_credentials_masked": bool(self.google_credentials),
            "meta_credentials_masked": bool(self.meta_credentials),
            "google_client_id": self.google_client_id,
            "google_client_secret": self.google_client_secret,
            "google_developer_token": self.google_developer_token,
            "google_login_customer_id": self.google_login_customer_id,
            "meta_app_id": self.meta_app_id,
            "meta_app_secret": self.meta_app_secret,
            "redirect_base_url": self.redirect_base_url,
            "lsq_access_key": self.lsq_access_key,
            "lsq_secret_key_masked": bool(self.lsq_secret_key),
            "lsq_base_url": self.lsq_base_url,
            "lsq_sync_interval_minutes": self.lsq_sync_interval_minutes,
            "crm_type": self.crm_type or "none",
            "crm_credentials_masked": bool(self.crm_credentials),
            "refresh_interval_minutes": self.refresh_interval_minutes,
            "audit_interval_minutes": self.audit_interval_minutes,
            "adpulse_refresh_interval": self.adpulse_refresh_interval,
            "adpulse_audit_interval": self.adpulse_audit_interval,
            "client_status": self.client_status,
            "invoice_day": self.invoice_day,
            "payment_due_days": self.payment_due_days,
            "billing_amount": self.billing_amount,
            "gst_number": self.gst_number,
            "address": self.address,
            "state": self.state,
            "state_code": self.state_code,
            "is_active": self.is_active,
            "is_live": self.is_live,
            "last_sync_at": self.last_sync_at.isoformat() + "Z" if self.last_sync_at else None,
            "last_sync_error": self.last_sync_error,
            "status": self.status.value,
            "spend": self.spend,
            "conversions": self.conversions,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "ctr": self.ctr,
            "cpa": self.cpa,
            "budget": self.budget,
            "budget_used_pct": self.budget_used_pct,
            "target_cpa": self.target_cpa,
            "billing_cache": self.billing_cache,
            "rev_client_id": self.rev_client_id,
            "brand_name": self.brand_name,
            "brand_keywords": self.brand_keywords,
            "business_context": self.business_context,
            "negative_rules": self.negative_rules,
            "contact_person": self.contact_person,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "business_manager_id": self.business_manager_id,
            "business_manager_name": self.business_manager.full_name if self.business_manager else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CampaignTypeTag(Base):
    """Manual/auto campaign type tags for brand vs non-brand detection."""
    __tablename__ = "campaign_type_tags"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    campaign_id = Column(String, nullable=False)
    campaign_name = Column(String, nullable=True)
    campaign_type = Column(String, default="auto")  # brand, non_brand, auto
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", back_populates="campaign_type_tags")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "campaign_type": self.campaign_type,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AuditRun(Base):
    __tablename__ = "audit_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_date = Column(Date, nullable=False)
    run_type = Column(String, nullable=False)  # daily_scheduled or manual
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    accounts_audited = Column(Integer, default=0)
    total_keyword_flags = Column(Integer, default=0)
    total_search_term_flags = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending, completed, failed, partial
    error_log = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "run_date": str(self.run_date) if self.run_date else None,
            "run_type": self.run_type,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "accounts_audited": self.accounts_audited,
            "total_keyword_flags": self.total_keyword_flags,
            "total_search_term_flags": self.total_search_term_flags,
            "status": self.status,
            "error_log": self.error_log,
        }


class SuppressedSearchTerm(Base):
    __tablename__ = "suppressed_search_terms"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    campaign_id = Column(String, nullable=False)
    search_term = Column(String, nullable=False)
    suppressed_until = Column(Date, nullable=False)
    rejected_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "campaign_id": self.campaign_id,
            "search_term": self.search_term,
            "suppressed_until": str(self.suppressed_until) if self.suppressed_until else None,
            "rejected_by": self.rejected_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    action_type = Column(String, nullable=False)  # e.g. ADD_NEGATIVE_KEYWORD, PAUSE_CAMPAIGN, UPDATE_BUDGET
    platform = Column(String, nullable=False)  # google or meta
    campaign_id = Column(String, nullable=True)
    adset_id = Column(String, nullable=True)
    keyword = Column(String, nullable=True)
    match_type = Column(String, nullable=True)
    new_value = Column(JSON, nullable=True)  # flexible payload
    reason = Column(Text, nullable=True)
    estimated_savings = Column(Float, nullable=True)
    status = Column(String, default="pending")  # pending, approved, rejected, applied, failed, dismissed
    campaign_status = Column(String, default="ENABLED")  # ENABLED/PAUSED/REMOVED at audit time
    confidence = Column(String, nullable=True)  # HIGH / MEDIUM / LOW
    confidence_score = Column(Integer, nullable=True)  # 0 to 100
    requested_by = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    account = relationship("Account")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
            "action_type": self.action_type,
            "platform": self.platform,
            "campaign_id": self.campaign_id,
            "adset_id": self.adset_id,
            "keyword": self.keyword,
            "match_type": self.match_type,
            "new_value": self.new_value,
            "reason": self.reason,
            "estimated_savings": self.estimated_savings,
            "status": self.status,
            "requested_by": self.requested_by,
            "reviewed_by": self.reviewed_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "error_message": self.error_message,
            "campaign_status": self.campaign_status,
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
        }


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String, nullable=False, unique=True)  # email, whatsapp
    enabled = Column(Boolean, default=False)
    provider = Column(String, nullable=True)  # e.g. "twilio", "sendgrid", "smtp"
    config = Column(JSON, nullable=True)  # credentials / recipient mapping
    notify_on_audit_complete = Column(Boolean, default=True)
    notify_on_action_reviewed = Column(Boolean, default=True)
    notify_on_sync_failure = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "channel": self.channel,
            "enabled": self.enabled,
            "provider": self.provider,
            "config": self.config,
            "notify_on_audit_complete": self.notify_on_audit_complete,
            "notify_on_action_reviewed": self.notify_on_action_reviewed,
            "notify_on_sync_failure": self.notify_on_sync_failure,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    recipients = Column(JSON, nullable=True)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String, default="queued")  # queued, sent, failed
    provider_response = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "channel": self.channel,
            "event_type": self.event_type,
            "recipients": self.recipients,
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
            "provider_response": self.provider_response,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="user")  # user, admin, superadmin
    rev_role = Column(String, nullable=True)  # admin, finance, business_manager
    mobile = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    access_adpulse = Column(Boolean, default=True)
    access_insightdesk = Column(Boolean, default=False)
    access_revenueops = Column(Boolean, default=False)
    access_audit_review = Column(Boolean, default=False)
    access_adguard = Column(Boolean, default=False)
    onboarding_token = Column(String, nullable=True, unique=True, index=True)
    onboarding_token_expires_at = Column(DateTime, nullable=True)
    onboarding_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    assigned_accounts = relationship("UserAccountAssignment", back_populates="user", cascade="all, delete-orphan")

    def assigned_account_ids(self):
        return [a.account_id for a in self.assigned_accounts]

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "rev_role": self.rev_role,
            "mobile": self.mobile,
            "is_active": self.is_active,
            "access_adpulse": self.access_adpulse,
            "access_insightdesk": self.access_insightdesk,
            "access_revenueops": self.access_revenueops,
            "access_audit_review": self.access_audit_review,
            "access_adguard": self.access_adguard,
            "onboarding_completed": self.onboarding_completed,
            "assigned_account_ids": self.assigned_account_ids(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserAccountAssignment(Base):
    """Many-to-many: which ad accounts a BM (role=user) can see."""
    __tablename__ = "user_account_assignments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="assigned_accounts")
    account = relationship("Account")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
        }


class DsuBudgetEntry(Base):
    """Manual budget entries for DSU Table 7 (Monthly Spend Summary)."""
    __tablename__ = "dsu_budget_entries"

    id = Column(Integer, primary_key=True, index=True)
    entry_date = Column(String, nullable=False)  # YYYY-MM-DD
    amount = Column(Float, nullable=False, default=0.0)
    invoice = Column(String, nullable=True, default="")
    campus = Column(String, nullable=True, default="")  # "Campus 3" or "Campus 4"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.entry_date,
            "amount": self.amount,
            "invoice": self.invoice or "",
            "campus": self.campus or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DsuMonthlySpendFixed(Base):
    """Fixed historical Google Ads monthly spend for DSU Table 7 (Nov-25 to May-26).
    These values are frozen and will not change. No GST applied to these months."""
    __tablename__ = "dsu_monthly_spend_fixed"

    id = Column(Integer, primary_key=True, index=True)
    month_key = Column(String, nullable=False, unique=True)  # e.g. "Nov-25", "Dec-25"
    google_spend = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "month_key": self.month_key,
            "google_spend": self.google_spend,
        }


class DsiBudgetEntry(Base):
    """Manual budget entries for DSI Table 5 (Budget MIS)."""
    __tablename__ = "dsi_budget_entries"

    id = Column(Integer, primary_key=True, index=True)
    entry_date = Column(String, nullable=False)  # YYYY-MM-DD
    amount = Column(Float, nullable=False, default=0.0)
    invoice = Column(String, nullable=True, default="")
    section = Column(String, nullable=True, default="")  # "DSCE", "DSIT", etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.entry_date,
            "amount": self.amount,
            "invoice": self.invoice or "",
            "section": self.section or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DsuLegacySpend(Base):
    """Hardcoded spend from the old Google Ads account (Nov-25 to Mar-26).
    
    The old account is no longer accessible via API, so course-wise monthly
    spend is stored here and merged with live API data for Table 2.
    """
    __tablename__ = "dsu_legacy_spend"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, nullable=False, index=True)  # "2025-11", "2025-12", etc.
    course = Column(String, nullable=False)  # "B.Tech", "MBA", etc.
    spend = Column(Float, nullable=False, default=0.0)
    leads = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "month": self.month,
            "course": self.course,
            "spend": self.spend,
            "leads": self.leads,
        }


class DsuTable2Historical(Base):
    """Exact historical figures for DSU Table 2 (from inception to yesterday).
    
    This stores the user's raw data so that the default Table 2 view matches
    the shared client report exactly. Custom date ranges still compute from
    live + legacy data.
    """
    __tablename__ = "dsu_table2_historical"

    id = Column(Integer, primary_key=True, index=True)
    course = Column(String, nullable=False, unique=True)
    leads = Column(Integer, nullable=False, default=0)
    spend = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "course": self.course,
            "leads": self.leads,
            "spend": self.spend,
        }


class LeadSquaredLead(Base):
    """Local mirror of LeadSquared leads for fast MIS reporting.
    
    Each row stores the immutable properties needed by InsightDesk.
    The mirror is refreshed periodically from LeadSquared.RecentlyModified.
    """
    __tablename__ = "leadsquared_leads"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    prospect_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=True, default="")
    source_campaign = Column(String, nullable=True, default="")
    student_source = Column(String, nullable=True, default="")
    latest_source = Column(String, nullable=True, default="")
    secondary_source = Column(String, nullable=True, default="")
    student_stage = Column(String, nullable=True, default="")
    application_status = Column(String, nullable=True, default="")
    city = Column(String, nullable=True, default="")
    state = Column(String, nullable=True, default="")
    created_on = Column(String, nullable=True, index=True)  # ISO date YYYY-MM-DD
    modified_on = Column(String, nullable=True)              # ISO date YYYY-MM-DD
    course = Column(String, nullable=True, default="")     # resolved course mapping
    raw_json = Column(Text, nullable=True)                   # full LeadPropertyList JSON for traceability
    synced_at = Column(DateTime, default=datetime.utcnow, index=True)

    account = relationship("Account")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "prospect_id": self.prospect_id,
            "source": self.source,
            "source_campaign": self.source_campaign,
            "student_source": self.student_source,
            "latest_source": self.latest_source,
            "secondary_source": self.secondary_source,
            "student_stage": self.student_stage,
            "application_status": self.application_status,
            "city": self.city,
            "state": self.state,
            "created_on": self.created_on,
            "modified_on": self.modified_on,
            "course": self.course,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }


class DsiLegacySpend(Base):
    """Hardcoded spend from DSI's old Google Ads account (Jan-26 to Mar-26).

    The old account is no longer accessible via API, so course-wise monthly
    spend is stored here and merged with live API data for DSI Table 2.
    """
    __tablename__ = "dsi_legacy_spend"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, nullable=False, index=True)  # "2026-01", "2026-02", etc.
    course = Column(String, nullable=False)  # "DSCE", "DSIT", "BCA", etc.
    spend = Column(Float, nullable=False, default=0.0)
    leads = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "month": self.month,
            "course": self.course,
            "spend": self.spend,
            "leads": self.leads,
        }


class DsiTable2Historical(Base):
    """Exact historical figures for DSI Table 2 (from inception to yesterday).

    This stores the user's raw data so that the default Table 2 view matches
    the shared client report exactly. Custom date ranges still compute from
    live + legacy data.
    """
    __tablename__ = "dsi_table2_historical"

    id = Column(Integer, primary_key=True, index=True)
    course = Column(String, nullable=False, unique=True)
    department = Column(String, nullable=True, default="")
    leads = Column(Integer, nullable=False, default=0)
    spend = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "course": self.course,
            "department": self.department or "",
            "leads": self.leads,
            "spend": self.spend,
        }


class DsiMonthlySpendFixed(Base):
    """Fixed historical Google Ads monthly spend for DSI (Jan-26 to Mar-26).
    These values are frozen and will not change. No GST applied to these months."""
    __tablename__ = "dsi_monthly_spend_fixed"

    id = Column(Integer, primary_key=True, index=True)
    month_key = Column(String, nullable=False, unique=True)  # e.g. "Jan-26", "Feb-26"
    google_spend = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "month_key": self.month_key,
            "google_spend": self.google_spend,
        }


class AppSetting(Base):
    """Generic key-value application settings stored in PostgreSQL."""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "key": self.key, "value": self.value}


class ActivityLog(Base):
    """Central activity history log across all modules."""
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    module = Column(String, nullable=False)  # AdPulse, InsightDesk, RevenueOps, System
    action = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user_name = Column(String, nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    account_name = Column(String, nullable=True)
    entity_type = Column(String, nullable=True)  # campaign, keyword, invoice, payment, user, account
    entity_id = Column(String, nullable=True)
    details_json = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "module": self.module,
            "action": self.action,
            "description": self.description,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "details": self.details_json,
        }


class MisProject(Base):
    """MIS project (e.g. Mantri -> Serenity) for platform-daily snapshots."""
    __tablename__ = "mis_projects"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Account")
    snapshots = relationship("MisDailySnapshot", back_populates="project")

    def to_dict(self):
        return {
            "id": self.id,
            "client_id": self.client_id,
            "client_name": self.client.name if self.client else None,
            "name": self.name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MisSalesforceUpload(Base):
    """Uploaded Salesforce raw Excel export for the Mantri Salesforce report.

    Stores the raw .xlsx bytes in the DB so the report works on ephemeral
    hosting (Railway) without a local file. Only the latest upload is used;
    older uploads are retained for audit history.
    """
    __tablename__ = "mis_salesforce_uploads"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("mis_projects.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    content = Column(LargeBinary, nullable=False)  # raw .xlsx bytes
    uploaded_by = Column(String, nullable=True)    # user email
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)

    project = relationship("MisProject")

    def to_dict(self, include_content: bool = False):
        d = {
            "id": self.id,
            "project_id": self.project_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }
        if include_content:
            d["content_b64"] = base64.b64encode(self.content).decode("ascii")
        return d


class MisDailySnapshot(Base):
    """Daily platform spend/leads snapshot for an MIS project."""
    __tablename__ = "mis_daily_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("mis_projects.id"), nullable=False)
    platform = Column(String, nullable=False)  # meta | google
    date = Column(Date, nullable=False)
    leads = Column(Float, default=0.0)
    amount_spent = Column(Float, default=0.0)
    cpl = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("MisProject", back_populates="snapshots")

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "platform": self.platform,
            "date": str(self.date) if self.date else None,
            "leads": self.leads,
            "amount_spent": self.amount_spent,
            "cpl": self.cpl,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CallFeedbackActivity(Base):
    """Local mirror of LeadSquared "Call Feedback" custom activities (EventCode 210).

    Used by InsightDesk Table 8 (Call Quality) to audit the outbound team's
    call remarks. One row per Call Feedback activity posted on a lead.

    Field mapping discovered from the LSQ tenant:
      mx_Custom_1 = next follow-up date
      mx_Custom_2 = call status when not connected (Ringing No Answer / Number Busy / ...)
      mx_Custom_3 = connected outcome (Interested / Not Interested / Not Eligible / Not Yet Decided)
      mx_Custom_6 = not-interested reason (Fees is Too High / ...)
      mx_Custom_7 = not-eligible reason (Wrong Number / By Mistake Enquired / ...)
    """
    __tablename__ = "lsq_call_feedback"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    activity_id = Column(String, nullable=False, index=True)      # LSQ ProspectActivityId
    prospect_id = Column(String, nullable=False, index=True)      # LSQ RelatedProspectId
    call_status = Column(String, nullable=True, default="")       # mx_Custom_2 (empty = connected)
    outcome = Column(String, nullable=True, default="")           # mx_Custom_3
    reason = Column(String, nullable=True, default="")            # mx_Custom_6 or mx_Custom_7
    next_followup = Column(String, nullable=True, default="")     # mx_Custom_1
    created_by_name = Column(String, nullable=True, default="")   # agent who logged the call
    created_on = Column(DateTime, nullable=False, index=True)     # UTC activity time
    synced_at = Column(DateTime, default=datetime.utcnow, index=True)

    account = relationship("Account")

    __table_args__ = (
        UniqueConstraint("activity_id", name="uq_lsq_call_feedback_activity"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "activity_id": self.activity_id,
            "prospect_id": self.prospect_id,
            "call_status": self.call_status,
            "outcome": self.outcome,
            "reason": self.reason,
            "next_followup": self.next_followup,
            "created_by_name": self.created_by_name,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }


class CampaignLandingPage(Base):
    """Landing page URL + crawled content per campaign, used for smarter search term audits."""
    __tablename__ = "campaign_landing_pages"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    campaign_id = Column(String, nullable=False)
    campaign_name = Column(String, nullable=True)
    landing_page_url = Column(String, nullable=True)
    landing_page_content = Column(Text, nullable=True)  # JSON summary from Gemini
    last_crawled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "landing_page_url": self.landing_page_url,
            "landing_page_content": self.landing_page_content,
            "last_crawled_at": self.last_crawled_at.isoformat() if self.last_crawled_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AdGuardAccount(Base):
    """AdGuard SaaS workspace — one per self-serve connected Google login.

    Ryze-style: user clicks Connect -> OAuth consent -> tokens stored encrypted
    here. Discovered Google Ads customer IDs are listed so the user picks which
    ones to protect. Independent from the agency `accounts` table.
    """
    __tablename__ = "adguard_accounts"

    id = Column(Integer, primary_key=True, index=True)
    owner_email = Column(String, nullable=False, index=True)  # user identity (OAuth login)
    display_name = Column(String, nullable=True, default="")

    # Encrypted OAuth credentials (same Fernet scheme as Account.google_credentials)
    google_credentials = Column(Text, nullable=True)
    google_is_live = Column(Boolean, default=False)

    # Google Ads customer IDs discovered via listAccessibleCustomers after connect
    # JSON list: [{"id": "1234567890", "name": "DSU", "selected": true}, ...]
    discovered_accounts = Column(Text, nullable=True)

    # Gatekeeper settings
    verification_threshold = Column(Integer, default=70)
    auto_push_enabled = Column(Boolean, default=False)  # Module 3 CAPI later; LSQ edge toggled off by default

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "owner_email": self.owner_email,
            "display_name": self.display_name,
            "google_is_live": self.google_is_live,
            "discovered_accounts": json.loads(self.discovered_accounts) if self.discovered_accounts else [],
            "verification_threshold": self.verification_threshold,
            "auto_push_enabled": self.auto_push_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AdGuardLead(Base):
    """AdGuard — Google Ads lead form intake with integrity scoring.

    Every incoming lead is scored by the Lead Integrity Gatekeeper
    (disposable email, phone format, geo-mismatch, Gemini legitimacy).
    - Verified leads  -> pushed to LeadSquared and stored here for audit trail.
    - Flagged leads   -> stored here ONLY (never pushed to LSQ), with reasons.
    """
    __tablename__ = "adguard_leads"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)
    adguard_account_id = Column(Integer, ForeignKey("adguard_accounts.id"), nullable=True, index=True)
    gclid = Column(String, nullable=True, index=True)
    form_id = Column(String, nullable=True)
    campaign_name = Column(String, nullable=True, default="")
    lead_type = Column(String, nullable=True, default="")  # e.g. google_lead_form

    full_name = Column(String, nullable=True, default="")
    email = Column(String, nullable=True, default="")
    phone = Column(String, nullable=True, default="")
    city = Column(String, nullable=True, default="")
    state = Column(String, nullable=True, default="")
    country = Column(String, nullable=True, default="")
    postal_code = Column(String, nullable=True, default="")

    raw_payload = Column(Text, nullable=True)  # full webhook JSON for traceability

    # Gatekeeper output
    integrity_score = Column(Integer, nullable=False, default=0)  # 0-100
    verdict = Column(String, nullable=False, default="pending")  # verified | flagged
    email_valid = Column(Boolean, default=True)
    disposable_email = Column(Boolean, default=False)
    phone_valid = Column(Boolean, default=False)
    geo_match = Column(Boolean, default=True)
    ai_legitimacy_score = Column(Integer, nullable=True)  # 0-100 from Gemini
    ai_reason = Column(Text, nullable=True)
    flags = Column(Text, nullable=True)  # JSON list of flag strings

    # LSQ outcome
    lsq_status = Column(String, nullable=True, default="not_pushed")  # not_pushed | pushed | failed | skipped_flagged
    lsq_prospect_id = Column(String, nullable=True)
    lsq_error = Column(Text, nullable=True)

    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)

    account = relationship("Account")
    adguard_account = relationship("AdGuardAccount")

    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
            "adguard_account_id": self.adguard_account_id,
            "gclid": self.gclid,
            "form_id": self.form_id,
            "campaign_name": self.campaign_name,
            "lead_type": self.lead_type,
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "postal_code": self.postal_code,
            "integrity_score": self.integrity_score,
            "verdict": self.verdict,
            "email_valid": self.email_valid,
            "disposable_email": self.disposable_email,
            "phone_valid": self.phone_valid,
            "geo_match": self.geo_match,
            "ai_legitimacy_score": self.ai_legitimacy_score,
            "ai_reason": self.ai_reason,
            "flags": json.loads(self.flags) if self.flags else [],
            "lsq_status": self.lsq_status,
            "lsq_prospect_id": self.lsq_prospect_id,
            "lsq_error": self.lsq_error,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }
