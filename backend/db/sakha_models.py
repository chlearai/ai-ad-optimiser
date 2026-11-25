"""Tenant-scoped mirror of the Sakhaa Reporting API."""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint

from backend.db.database import Base


class SakhaClient(Base):
    __tablename__ = "sakha_clients"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=False)
    base_url = Column(String)
    company_id = Column(String)
    workspace_id = Column(String)
    # Stage 1 reads credentials directly from the environment.
    encrypted_api_key = Column(Text)
    encrypted_api_secret = Column(Text)
    stage_map = Column(JSON, nullable=False, default=dict)
    targets = Column(JSON, nullable=False, default=dict)
    widgets = Column(JSON, nullable=False, default=dict)
    last_sync_at = Column(DateTime(timezone=True))
    last_sync_ok = Column(Boolean)
    last_error = Column(String)
    last_sync_mode = Column(String)
    sync_attempt_mode = Column(String)
    live_watermarks = Column(JSON, nullable=False, default=dict)
    live_progress = Column(JSON)
    lease_token = Column(String)
    lease_until = Column(DateTime(timezone=True))


class SakhaaRecord:
    sakha_client_id = Column(Integer, ForeignKey("sakha_clients.id", ondelete="CASCADE"), primary_key=True)
    data_mode = Column(String, primary_key=True)
    sakhaa_id = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)


class SakhaPerson(SakhaaRecord, Base):
    __tablename__ = "sakha_persons"


class SakhaDeal(SakhaaRecord, Base):
    __tablename__ = "sakha_deals"


class SakhaDealStageEvent(SakhaaRecord, Base):
    __tablename__ = "sakha_deal_stage_events"


class SakhaActivity(SakhaaRecord, Base):
    __tablename__ = "sakha_activities"


class SakhaTask(SakhaaRecord, Base):
    __tablename__ = "sakha_tasks"


class SakhaSourceStatus(SakhaaRecord, Base):
    __tablename__ = "sakha_source_statuses"


class SakhaIntegrationStatus(SakhaaRecord, Base):
    __tablename__ = "sakha_integration_statuses"


class SakhaPipeline(SakhaaRecord, Base):
    __tablename__ = "sakha_pipelines"


class SakhaOwner(SakhaaRecord, Base):
    __tablename__ = "sakha_owners"


class SakhaIssue(Base):
    __tablename__ = "sakha_issues"
    __table_args__ = (UniqueConstraint("sakha_client_id", "data_mode", "code"),)

    id = Column(Integer, primary_key=True)
    sakha_client_id = Column(Integer, ForeignKey("sakha_clients.id", ondelete="CASCADE"), nullable=False)
    data_mode = Column(String, nullable=False)
    code = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    title = Column(String, nullable=False)
    detail = Column(Text, nullable=False)
    affected_count = Column(Integer, nullable=False)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True))
    fix_hint = Column(String, nullable=False)


class SakhaRateBudget(Base):
    __tablename__ = "sakha_rate_budgets"

    sakha_client_id = Column(Integer, ForeignKey("sakha_clients.id", ondelete="CASCADE"), primary_key=True)
    version = Column(Integer, nullable=False, default=0)
    calls = Column(JSON, nullable=False, default=list)
    retry_after = Column(DateTime(timezone=True))
