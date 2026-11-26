"""Mini Robos profiles, reporter keys and operational history."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, false, text

from backend.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def agent_fk():
    return Column(Integer, ForeignKey("robo_agents.id", ondelete="CASCADE"), nullable=False)


class RoboAgent(Base):
    __tablename__ = "robo_agents"

    id = Column(Integer, primary_key=True)
    slug = Column(String(100), unique=True, nullable=False)
    name = Column(String(160), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    type = Column(String, nullable=False)
    runs_on = Column(String, nullable=False)
    source_url = Column(String)
    owner = Column(String)
    schedule = Column(JSON)
    grace_minutes = Column(Integer, nullable=False, default=15)
    expects_output = Column(Boolean, nullable=False, default=False, server_default=false())
    paused = Column(Boolean, nullable=False, default=False, server_default=false())
    pause_reason = Column(String)
    paused_by = Column(String)
    paused_at = Column(DateTime(timezone=True))
    live_sync = Column(Boolean, nullable=False, default=False, server_default=false())
    job_ref = Column(String, unique=True)
    status = Column(String, nullable=False, default="no_signal")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RoboAgentVersion(Base):
    __tablename__ = "robo_agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version"),
        Index("uq_robo_agent_draft", "agent_id", unique=True,
              postgresql_where=text("status = 'draft'"), sqlite_where=text("status = 'draft'")),
    )

    id = Column(Integer, primary_key=True)
    agent_id = agent_fk()
    version = Column(Integer, nullable=False)
    prompt = Column(Text, nullable=False, default="")
    rules = Column(JSON, nullable=False, default=list)
    input_source = Column(JSON, nullable=False, default=dict)
    recipients = Column(JSON, nullable=False, default=list)
    note = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False)
    author = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_at = Column(DateTime(timezone=True))


class RoboRun(Base):
    __tablename__ = "robo_runs"
    __table_args__ = (Index("ix_robo_run_agent_started", "agent_id", "started_at"),)

    id = Column(String(36), primary_key=True)
    agent_id = agent_fk()
    slot = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    finished_at = Column(DateTime(timezone=True))
    status = Column(String, nullable=False)
    output_count = Column(Integer)
    message = Column(Text)
    error = Column(Text)


class RoboIncident(Base):
    __tablename__ = "robo_incidents"
    __table_args__ = (Index("ix_robo_incident_agent_closed", "agent_id", "closed_at"),)

    id = Column(Integer, primary_key=True)
    agent_id = agent_fk()
    kind = Column(String, nullable=False)
    slot = Column(DateTime(timezone=True))
    opened_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = Column(DateTime(timezone=True))


class RoboAgentKey(Base):
    __tablename__ = "robo_agent_keys"

    id = Column(Integer, primary_key=True)
    agent_id = agent_fk()
    key_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at = Column(DateTime(timezone=True))
    window_start = Column(DateTime(timezone=True))
    calls = Column(Integer, nullable=False, default=0)


class RoboAudit(Base):
    __tablename__ = "robo_audits"
    __table_args__ = (Index("ix_robo_audit_agent_at", "agent_id", "at"),)

    id = Column(Integer, primary_key=True)
    who = Column(String, nullable=False)
    agent_id = agent_fk()
    action = Column(String, nullable=False)
    detail = Column(JSON, nullable=False, default=dict)
    at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
