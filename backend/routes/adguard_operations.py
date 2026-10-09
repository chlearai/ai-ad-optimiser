"""Owner operations and workspace installation management (no payment or calling changes)."""
import json
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import AdGuardAccount, AdGuardLead, AdGuardSupportTicket, User, AdGuardConversionEvent
from backend.db.adguard_ops import AdGuardNotification, AdGuardInternalNote, AdGuardAuditUsage
from backend.routes.auth import get_current_user_required
from backend.services.adguard_subscription import allowance, operations, next_period, access_reason
from backend.services.adguard_support_ops import ticket_dict, ticket_operations
from backend.services.activity_log import log_activity

router = APIRouter()


def require_admin(user):
    if user.role not in ('admin', 'superadmin'):
        raise HTTPException(403, 'Admin access required')


def get_workspace(db, user, workspace_id):
    ws = db.get(AdGuardAccount, workspace_id)
    if not ws:
        raise HTTPException(404, 'Workspace not found')
    if user.role not in ('admin','superadmin') and ws.owner_email != user.email:
        raise HTTPException(403, 'Not your workspace')
    return ws


def record(db, user, action, entity_id, description):
    log_activity(module='AdGuard', action=action, description=description, user_id=user.id,
        user_name=user.email, entity_type='adguard_operations', entity_id=str(entity_id), db=db)


@router.get('/workspaces/{workspace_id}/installation')
def installation(workspace_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    ws = get_workspace(db, user, workspace_id)
    op = operations(db, ws)
    result = {'workspace_id': ws.id, 'installation_token': op.tag_token,
              'webhook_secret': op.webhook_secret, 'allowed_origins': json.loads(op.allowed_origins or '[]')}
    db.commit()
    return result


class InstallationSettings(BaseModel):
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


@router.put('/workspaces/{workspace_id}/installation')
def configure_installation(workspace_id: int, req: InstallationSettings, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    ws = get_workspace(db, user, workspace_id)
    origins = []
    for value in req.allowed_origins:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise HTTPException(400, 'Enter website origins only, for example https://example.com')
        origins.append(f'{parsed.scheme}://{parsed.netloc}'.rstrip('/'))
    operations(db, ws).allowed_origins = json.dumps(sorted(set(origins)))
    db.commit()
    record(db, user, 'Installation origins updated', ws.id, 'Updated website origins')
    return {'allowed_origins': sorted(set(origins))}


class RenewalRequest(BaseModel):
    months: int = Field(default=1, ge=1, le=12)


@router.post('/admin/subscribers/{workspace_id}/renew')
def renew(workspace_id: int, req: RenewalRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    ws = get_workspace(db, user, workspace_id)
    now = datetime.utcnow()
    until = max(now, ws.plan_expires_at or now)
    for _ in range(req.months):
        until = next_period(until)
    # Renew all brand workspaces under this subscriber without resetting audit usage.
    for sibling in db.query(AdGuardAccount).filter_by(owner_email=ws.owner_email).all():
        sibling.plan_expires_at = until
        if sibling.account_status == 'expired':
            sibling.account_status = 'active'
    db.commit()
    record(db, user, 'Plan renewed', ws.id, f'Renewed through {until.isoformat()} (testing payment bypass retained)')
    return {'plan_expires_at': until.isoformat()}


class TicketManagement(BaseModel):
    assigned_to: Optional[str] = None
    priority: Optional[str] = None
    internal_note: Optional[str] = Field(default=None, max_length=10000)


@router.put('/support/admin/tickets/{ticket_id}/manage')
def manage_ticket(ticket_id: int, req: TicketManagement, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    ticket = db.get(AdGuardSupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(404, 'Ticket not found')
    op = ticket_operations(db, ticket)
    if req.assigned_to is not None:
        email = req.assigned_to.strip().lower()
        if email and not db.query(User).filter(User.email == email, User.role.in_(['admin','superadmin']), User.is_active == True).first():
            raise HTTPException(400, 'Assignee must be an active admin email')
        op.assigned_to = email or None
    if req.priority is not None:
        if req.priority not in ('low','normal','high'):
            raise HTTPException(400, 'Invalid priority')
        ticket.priority = req.priority
    if req.internal_note and req.internal_note.strip():
        db.add(AdGuardInternalNote(ticket_id=ticket.id, author=user.email, body=req.internal_note.strip()))
    db.commit()
    record(db, user, 'Support ticket updated', ticket.id, 'Assignment, priority, or internal note updated')
    return ticket_dict(db, ticket, user, include_messages=True)


@router.post('/support/admin/notifications/{notification_id}/retry')
def retry_notification(notification_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    row = db.get(AdGuardNotification, notification_id)
    if not row:
        raise HTTPException(404, 'Notification not found')
    if row.status in ('sent','sending'):
        raise HTTPException(409, 'Notification already sent or in progress')
    row.status, row.attempts, row.next_attempt_at = 'pending', 0, datetime.utcnow()
    db.commit()
    record(db, user, 'Notification retry queued', row.id, 'Support email queued for retry')
    return {'status':'pending'}


@router.get('/admin/operations')
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    now = datetime.utcnow()
    rows = []
    for ws in db.query(AdGuardAccount).order_by(AdGuardAccount.id).all():
        limits = allowance(db, ws)
        owner = db.query(User).filter_by(email=ws.owner_email).first()
        crm_failures = db.query(func.count(AdGuardLead.id)).filter(AdGuardLead.adguard_account_id == ws.id, AdGuardLead.lsq_status == 'failed').scalar() or 0
        sample = db.query(AdGuardLead).filter(AdGuardLead.adguard_account_id == ws.id, AdGuardLead.lsq_status == 'failed').order_by(AdGuardLead.received_at.desc()).first()
        delivery = db.query(func.max(AdGuardLead.processed_at)).filter(AdGuardLead.adguard_account_id == ws.id, AdGuardLead.lsq_status.in_(['pushed','success'])).scalar()
        conversions_failed = db.query(func.count(AdGuardConversionEvent.id)).filter(AdGuardConversionEvent.adguard_account_id == ws.id, AdGuardConversionEvent.status == 'failed').scalar() or 0
        issues = []
        if not owner or not owner.onboarding_completed:
            issues.append('Activation pending')
        reason = access_reason(db, ws)
        if reason and reason != 'Subscriber activation is incomplete':
            issues.append(reason)
        if limits['audit_limit'] >= 0 and limits['audits_remaining'] == 0:
            issues.append('Audit allowance exhausted')
        elif limits['quota_pct'] is not None and limits['quota_pct'] >= 80:
            issues.append('Audit usage above 80%')
        if ws.plan_expires_at and now < ws.plan_expires_at <= now + timedelta(days=7):
            issues.append('Plan expires within 7 days')
        integrations = {}
        for platform in ('google','meta'):
            connected = bool(getattr(ws, platform + '_is_live'))
            last_sync = getattr(ws, platform + '_last_sync_at')
            status = 'not_connected' if not connected else 'unverified' if not last_sync else 'stale' if last_sync < now - timedelta(hours=24) else 'recent_sync'
            last_error = getattr(operations(db, ws), platform + '_error')
            if last_error:
                status = 'error'
                issues.append(platform.title() + ': ' + last_error)
            integrations[platform] = {'status':status, 'last_error':last_error, 'last_success_at':last_sync.isoformat() if last_sync else None}
            if connected and status in ('unverified','stale'):
                issues.append(platform.title() + ' connection needs checking')
        if crm_failures:
            issues.append(f'{crm_failures} CRM deliveries failed')
        if conversions_failed:
            issues.append(f'{conversions_failed} conversion deliveries failed')
        rows.append({'id':ws.id, 'name':ws.company_name or ws.display_name or ws.owner_email, 'owner_email':ws.owner_email,
                     'plan':ws.plan, 'account_status':ws.account_status, 'activation_status':'active' if owner and owner.onboarding_completed and owner.is_active else 'pending',
                     'plan_expires_at':ws.plan_expires_at.isoformat() if ws.plan_expires_at else None, **limits,
                     'issues':issues, 'integrations':integrations,
                     'crm':{'provider':ws.crm_preference or 'none', 'failed':crm_failures, 'last_error':sample.lsq_error if sample else None, 'last_failed_lead_id':sample.id if sample else None,
                            'last_success_at':delivery.isoformat() if delivery else None}})
    tickets = [ticket_dict(db, t, user) for t in db.query(AdGuardSupportTicket).filter_by(status='open').all()]
    email_failures = db.query(AdGuardNotification).filter(AdGuardNotification.status.in_(['retry','failed'])).count()
    # Count each reserved audit once; include historic leads without a usage record.
    # This preserves older activity without counting linked leads a second time.
    events = union_all(
        select(AdGuardAuditUsage.created_at.label('occurred_at')),
        select(AdGuardLead.received_at.label('occurred_at')).where(
            ~select(AdGuardAuditUsage.id).where(AdGuardAuditUsage.lead_id == AdGuardLead.id).exists()
        ),
    ).subquery()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
    daily = dict(db.query(func.date(events.c.occurred_at), func.count()).filter(
        events.c.occurred_at >= start, events.c.occurred_at <= now
    ).group_by(func.date(events.c.occurred_at)).all())
    daily = {str(day): count for day, count in daily.items()}
    activity = [{'date':(start + timedelta(days=i)).date().isoformat(),
                 'audits':daily.get((start + timedelta(days=i)).date().isoformat(), 0)} for i in range(30)]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_audits = db.query(func.count()).select_from(events).filter(
        events.c.occurred_at >= month_start, events.c.occurred_at <= now).scalar() or 0
    db.commit()
    return {'payment_mode':'testing_bypass', 'calling':'deferred_exotel', 'workspaces':rows, 'audit_activity':activity, 'activity_timezone':'UTC',
            'summary':{'workspaces':len(rows), 'audits_this_month':monthly_audits,
                       'active_subscribers':sum(r['activation_status']=='active' and r['account_status']=='active' and (not r['plan_expires_at'] or r['plan_expires_at'] > now.isoformat()) for r in rows),
                       'near_quota':sum(r['quota_pct'] is not None and r['quota_pct'] >= 80 for r in rows), 'needs_attention':sum(bool(r['issues']) for r in rows),
                       'pending_activation':sum(r['activation_status']=='pending' for r in rows),
                       'overdue_tickets':sum(t['overdue'] for t in tickets), 'open_tickets':len(tickets), 'email_failures':email_failures}}


@router.post('/admin/leads/{lead_id}/retry-crm')
def retry_crm(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    lead = db.get(AdGuardLead, lead_id)
    if not lead or not lead.adguard_account_id:
        raise HTTPException(404, 'Workspace lead not found')
    if lead.verdict not in ('verified', 'green'):
        raise HTTPException(400, 'Only verified leads may be delivered')
    if lead.lsq_status != 'failed':
        raise HTTPException(409, 'Only failed deliveries may be retried')
    ws = db.get(AdGuardAccount, lead.adguard_account_id)
    reason = access_reason(db, ws)
    if reason:
        raise HTTPException(403, reason)
    from backend.services.adguard_crm import deliver_lead
    result = deliver_lead(ws, {'full_name':lead.full_name, 'email':lead.email, 'phone':lead.phone,
                             'city':lead.city, 'state':lead.state, 'campaign_name':lead.campaign_name, 'source':'AdGuard retry'})
    lead.lsq_status, lead.lsq_error = result.get('status'), result.get('error')
    lead.lsq_prospect_id = result.get('id')
    lead.processed_at = datetime.utcnow()
    db.commit()
    record(db, user, 'CRM delivery retried', lead.id, 'Retried workspace CRM delivery')
    return result


class ConnectionCheck(BaseModel):
    platform: str


@router.post('/admin/workspaces/{workspace_id}/check-connection')
def check_connection(workspace_id: int, req: ConnectionCheck, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    if req.platform not in ('google', 'meta'):
        raise HTTPException(400, 'Invalid platform')
    ws = get_workspace(db, user, workspace_id)
    from backend.routes.adguard_support import rediscover_accounts, ConnectionActionRequest
    try:
        result = rediscover_accounts(ConnectionActionRequest(workspace_id=workspace_id, platform=req.platform), db, user)
        error = result.get('warning') or result.get('error')
        setattr(operations(db, ws), req.platform + '_error', str(error)[:1000] if error else None)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        setattr(operations(db, ws), req.platform + '_error', str(getattr(exc, 'detail', exc))[:1000])
        db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, 'Connection check failed; see integration status') from exc


class SlaSettings(BaseModel):
    hours_by_plan: dict[str, int]


@router.get('/support/admin/sla')
def get_sla(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    from backend.services.adguard_support_ops import SLA_HOURS, response_hours
    return {'hours_by_plan':{plan:response_hours(db, plan) for plan in SLA_HOURS}}


@router.put('/support/admin/sla')
def update_sla(req: SlaSettings, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    require_admin(user)
    from backend.services.adguard_support_ops import SLA_HOURS
    from backend.db.models import NotificationSetting
    if set(req.hours_by_plan) != set(SLA_HOURS) or any(value < 1 or value > 168 for value in req.hours_by_plan.values()):
        raise HTTPException(400, 'Set a response target of 1–168 hours for every plan')
    row = db.query(NotificationSetting).filter_by(channel='adguard_support').first()
    if not row:
        row = NotificationSetting(channel='adguard_support', enabled=True, config={})
        db.add(row)
    row.config = {**(row.config or {}), 'sla_hours':req.hours_by_plan}
    db.commit()
    record(db, user, 'Support response targets updated', 'sla', 'Updated response targets for future ticket clocks')
    return {'hours_by_plan':req.hours_by_plan}
