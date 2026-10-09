"""Additive AdGuard operations tables; keep commercial data separate from call credits."""
from datetime import datetime
import secrets
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from backend.db.database import Base

class AdGuardWorkspaceOps(Base):
    __tablename__ = 'adguard_workspace_ops'
    workspace_id = Column(Integer, ForeignKey('adguard_accounts.id', ondelete='CASCADE'), primary_key=True)
    bonus_audits = Column(Integer, default=0, nullable=False)
    bonus_period = Column(DateTime, nullable=True)
    billing_anchor_day = Column(Integer, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    google_error = Column(Text, nullable=True)
    meta_error = Column(Text, nullable=True)
    tag_token = Column(String(100), default=lambda: secrets.token_urlsafe(32), nullable=False)
    webhook_secret = Column(String(100), default=lambda: secrets.token_urlsafe(32), nullable=False)
    allowed_origins = Column(Text, default='[]', nullable=False)

class AdGuardAuditUsage(Base):
    __tablename__ = 'adguard_audit_usage'
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey('adguard_accounts.id', ondelete='CASCADE'), nullable=False, index=True)
    event_key = Column(String(150), nullable=False)
    otp_attempts = Column(Integer, default=0, nullable=False)
    period_start = Column(DateTime, nullable=False)
    lead_id = Column(Integer, ForeignKey('adguard_leads.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('workspace_id', 'event_key', name='uq_adguard_audit_event'),)

class AdGuardTicketOps(Base):
    __tablename__ = 'adguard_ticket_ops'
    ticket_id = Column(Integer, ForeignKey('adguard_support_tickets.id', ondelete='CASCADE'), primary_key=True)
    assigned_to = Column(String(255), nullable=True)
    response_due_at = Column(DateTime, nullable=True, index=True)
    escalated_at = Column(DateTime, nullable=True)
    escalation_count = Column(Integer, default=0, nullable=False)

class AdGuardInternalNote(Base):
    __tablename__ = 'adguard_internal_notes'
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('adguard_support_tickets.id', ondelete='CASCADE'), nullable=False, index=True)
    author = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class AdGuardNotification(Base):
    __tablename__ = 'adguard_notifications'
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('adguard_support_tickets.id', ondelete='CASCADE'), nullable=True, index=True)
    dedupe_key = Column(String(200), unique=True, nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(30), default='pending', nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
