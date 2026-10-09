"""Shared workspace access and atomic audit allowances. Payment testing stays enabled."""
import calendar
import json
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, update
from fastapi import HTTPException
from backend.db.models import AdGuardAccount, User
from backend.db.adguard_ops import AdGuardWorkspaceOps, AdGuardAuditUsage

PLAN_LIMITS = {
    'trial': {'lead_quota': 300, 'workspaces': 1, 'connectors': {'google': 1, 'meta': 1}, 'call_credits': 0},
    'starter': {'lead_quota': 1000, 'workspaces': 1, 'connectors': {'google': 1, 'meta': 1}, 'call_credits': 0},
    'pro': {'lead_quota': 5000, 'workspaces': 1, 'connectors': {'google': 1, 'meta': 1}, 'call_credits': 300},
    'agency': {'lead_quota': -1, 'workspaces': 10, 'connectors': {'google': 2, 'meta': 2}, 'call_credits': 500},
    'custom': {'lead_quota': 1000, 'workspaces': 1, 'connectors': {'google': 1, 'meta': 1}, 'call_credits': 0},
}

class WorkspaceUnavailable(Exception):
    pass


def operations(db, ws):
    row = db.get(AdGuardWorkspaceOps, ws.id)
    if row is None:
        # Lock workspace first to serialize lazy initialization on PostgreSQL.
        db.query(AdGuardAccount).filter_by(id=ws.id).with_for_update().first()
        row = db.get(AdGuardWorkspaceOps, ws.id, populate_existing=True)
        if row is None:
            owner = db.query(User).filter_by(email=ws.owner_email).first()
            activated = (ws.leads_month_reset or ws.created_at or datetime.utcnow()) if owner and owner.is_active and owner.onboarding_completed else None
            row = AdGuardWorkspaceOps(workspace_id=ws.id, bonus_audits=0, allowed_origins='[]', activated_at=activated)
            db.add(row)
            db.flush()
    return row


def next_period(start, anchor_day=None):
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start.replace(year=year, month=month, day=min(anchor_day or start.day, calendar.monthrange(year, month)[1]))


def roll_period(db, ws, now=None):
    now = now or datetime.utcnow()
    start = ws.leads_month_reset
    op = operations(db, ws)
    if not op.billing_anchor_day:
        op.billing_anchor_day = (start or now).day
    if start is None:
        ws.leads_month_reset = now
        ws.leads_this_month = int(ws.leads_this_month or 0)
    elif next_period(start, op.billing_anchor_day) <= now:
        # Advance from the old boundary, never from an arbitrary request time.
        while next_period(start, op.billing_anchor_day) <= now:
            start = next_period(start, op.billing_anchor_day)
        ws.leads_month_reset = start
        ws.leads_this_month = 0
    return ws.leads_month_reset


def access_reason(db, ws, now=None):
    now = now or datetime.utcnow()
    if ws.is_archived:
        return 'Workspace is archived'
    if (ws.account_status or 'active') != 'active':
        return 'Workspace is ' + ws.account_status
    if ws.plan_expires_at and ws.plan_expires_at <= now:
        return 'Plan has expired; ask the owner to renew it'
    owner = db.query(User).filter(User.email == ws.owner_email).first()
    if not owner or not owner.is_active or not owner.onboarding_completed:
        return 'Subscriber activation is incomplete'
    # Payment status is intentionally not an access gate while beta testing is enabled.
    return None


def allowance(db, ws, now=None):
    roll_period(db, ws, now)
    op = operations(db, ws)
    bonus = int(op.bonus_audits or 0) if op.bonus_period == ws.leads_month_reset else 0
    quota = ws.lead_quota if ws.lead_quota is not None else PLAN_LIMITS.get(ws.plan, PLAN_LIMITS['trial'])['lead_quota']
    limit = -1 if quota < 0 else quota + bonus
    used = int(ws.leads_this_month or 0)
    return {'base_quota': quota, 'bonus_audits': bonus, 'audit_limit': limit, 'audits_used': used,
            'audits_remaining': None if limit < 0 else max(0, limit - used),
            'quota_pct': None if limit <= 0 else round(100 * used / limit, 1),
            'period_start': ws.leads_month_reset.isoformat(), 'period_end': next_period(ws.leads_month_reset, op.billing_anchor_day).isoformat()}


def reserve_audit(db, workspace_id, event_key):
    ws = db.query(AdGuardAccount).filter_by(id=workspace_id).with_for_update().populate_existing().first()
    if not ws:
        raise WorkspaceUnavailable('Workspace not found')
    reason = access_reason(db, ws)
    if reason:
        raise WorkspaceUnavailable(reason)
    previous = db.query(AdGuardAuditUsage).filter_by(workspace_id=ws.id, event_key=event_key).first()
    if previous:
        return ws, previous, False
    limits = allowance(db, ws)
    db.flush()
    condition = [AdGuardAccount.id == ws.id]
    if limits['audit_limit'] >= 0:
        condition.append(func.coalesce(AdGuardAccount.leads_this_month, 0) < limits['audit_limit'])
    changed = db.execute(update(AdGuardAccount).where(*condition).values(
        leads_this_month=func.coalesce(AdGuardAccount.leads_this_month, 0) + 1).execution_options(synchronize_session=False)).rowcount
    if not changed:
        raise WorkspaceUnavailable('Monthly audit allowance exhausted; add a bonus or change plan')
    usage = AdGuardAuditUsage(workspace_id=ws.id, event_key=event_key, period_start=ws.leads_month_reset)
    db.add(usage)
    db.flush()
    db.refresh(ws)
    return ws, usage, True


def activate_workspace(ws, now=None, db=None):
    now = now or datetime.utcnow()
    op = operations(db, ws) if db is not None else None
    if ws.leads_month_reset is None or (op is not None and op.activated_at is None):
        ws.leads_month_reset = now
        ws.leads_this_month = 0
    if ws.plan_expires_at is None:
        ws.plan_expires_at = now + timedelta(days=14) if (ws.plan or 'trial') == 'trial' else next_period(now)
    ws.account_status = 'active'
    if op is not None and op.activated_at is None:
        op.activated_at = now
        op.billing_anchor_day = now.day


def set_plan(ws, plan, quota=None):
    if plan not in PLAN_LIMITS:
        raise HTTPException(400, 'Invalid plan')
    if quota is not None and quota < -1:
        raise HTTPException(400, 'Quota must be -1 (unlimited), zero, or a positive number')
    ws.plan = plan
    ws.lead_quota = PLAN_LIMITS[plan]['lead_quota'] if quota is None else quota
    # Changing a plan preserves current usage and expiry. Renewal is a separate explicit action.


def validate_installation(db, workspace_id, token, request):
    if not str(workspace_id or '').isdigit():
        raise HTTPException(400, 'A specific workspace installation is required')
    ws = db.get(AdGuardAccount, int(workspace_id))
    if not ws:
        raise HTTPException(404, 'Workspace not found')
    op = operations(db, ws)
    if not token or not secrets.compare_digest(token, op.tag_token):
        raise HTTPException(403, 'Invalid installation token; copy the updated workspace script tag')
    origins = json.loads(op.allowed_origins or '[]')
    origin = (request.headers.get('origin') or '').rstrip('/')
    if origins and origin not in origins:
        raise HTTPException(403, 'This website origin is not allowed for this installation')
    reason = access_reason(db, ws)
    if reason:
        raise HTTPException(403, reason)
    return ws


def change_subscriber_plan(db, ws, plan, quota=None):
    """Validate retained brand workspaces/connections before changing subscriber entitlements."""
    if plan not in PLAN_LIMITS:
        raise HTTPException(400, 'Invalid plan')
    siblings = db.query(AdGuardAccount).filter_by(owner_email=ws.owner_email).order_by(AdGuardAccount.id).with_for_update().all()
    active = [row for row in siblings if not row.is_archived]
    definition = PLAN_LIMITS[plan]
    if len(active) > definition['workspaces']:
        raise HTTPException(409, 'Archive extra brand workspaces before changing to this plan')
    for row in active:
        for platform in ('google','meta'):
            identities = json.loads(getattr(row, platform + '_identities') or '[]')
            if len(identities) > definition['connectors'][platform]:
                raise HTTPException(409, 'Disconnect extra ' + platform + ' logins before changing to this plan')
    for row in siblings:
        set_plan(row, plan, quota)


def change_subscriber_status(db, ws, status):
    if status not in ('active','paused','suspended','expired'):
        raise HTTPException(400, 'Invalid account status')
    for sibling in db.query(AdGuardAccount).filter_by(owner_email=ws.owner_email).all():
        sibling.account_status = status


def parse_expiry(value):
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError as exc:
        raise HTTPException(400, 'Invalid expiry date (use YYYY-MM-DD or an ISO timestamp)') from exc
