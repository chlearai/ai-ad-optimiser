"""Support response deadlines and a retryable notification outbox."""
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import or_
from backend.db.models import AdGuardAccount, AdGuardSupportTicket
from backend.db.adguard_ops import AdGuardTicketOps, AdGuardInternalNote, AdGuardNotification

SLA_HOURS = {'trial': 48, 'starter': 24, 'pro': 4, 'agency': 1, 'custom': 24}


def response_hours(db, plan):
    from backend.db.models import NotificationSetting
    setting = db.query(NotificationSetting).filter_by(channel='adguard_support').first()
    configured = (setting.config or {}).get('sla_hours', {}) if setting else {}
    return int(configured.get(plan, SLA_HOURS.get(plan, 24)))


def support_email(db):
    from backend.routes.adguard_support import _get_support_email
    return _get_support_email(db)


def ticket_operations(db, ticket):
    row = db.get(AdGuardTicketOps, ticket.id)
    if row is None:
        row = AdGuardTicketOps(ticket_id=ticket.id, escalation_count=0)
        db.add(row)
        if ticket.status == 'open':
            plan = ticket.workspace.plan if ticket.workspace else 'trial'
            row.response_due_at = (ticket.updated_at or ticket.created_at or datetime.utcnow()) + timedelta(hours=response_hours(db, plan))
        db.flush()
    return row


def begin_response_clock(db, ticket, now=None):
    row = ticket_operations(db, ticket)
    plan = ticket.workspace.plan if ticket.workspace else 'trial'
    row.response_due_at = (now or datetime.utcnow()) + timedelta(hours=response_hours(db, plan))
    row.escalated_at = None
    return row


def finish_response_clock(db, ticket):
    row = ticket_operations(db, ticket)
    row.response_due_at = None
    row.escalated_at = None


def ticket_dict(db, ticket, user, include_messages=False):
    result = ticket.to_dict(include_messages=include_messages)
    row = ticket_operations(db, ticket)
    result.update(assigned_to=row.assigned_to, response_due_at=row.response_due_at.isoformat() if row.response_due_at else None,
                  overdue=bool(ticket.status == 'open' and row.response_due_at and row.response_due_at < datetime.utcnow()),
                  escalated_at=row.escalated_at.isoformat() if row.escalated_at else None,
                  escalation_count=row.escalation_count or 0)
    if user.role in ('admin', 'superadmin'):
        if include_messages:
            result['internal_notes'] = [{'id': n.id, 'author': n.author, 'body': n.body, 'created_at': n.created_at.isoformat()}
                for n in db.query(AdGuardInternalNote).filter_by(ticket_id=ticket.id).order_by(AdGuardInternalNote.created_at).all()]
        result['notifications'] = [{'id': n.id, 'status': n.status, 'attempts': n.attempts, 'last_error': n.last_error,
                                   'sent_at': n.sent_at.isoformat() if n.sent_at else None}
            for n in db.query(AdGuardNotification).filter_by(ticket_id=ticket.id).order_by(AdGuardNotification.id.desc()).limit(10).all()]
    return result


def queue_ticket_email(db, ticket, body, to_customer=False, dedupe=None, recipient=None):
    base = os.getenv('APP_BASE_URL', '').rstrip('/')
    target = recipient or (ticket.requester_email if to_customer else support_email(db))
    payload = dict(recipient_email=target, subject=f'[AdGuard Support #{ticket.id}] {ticket.subject}',
        title=f'Ticket #{ticket.id}: {ticket.subject}', message_body=body, ticket_id=ticket.id,
        cta_link=base + ('/adguard-workspace' if to_customer else '/adguard') if base else None,
        cta_text='Open ticket', reply_to=support_email(db) if to_customer else ticket.requester_email)
    row = AdGuardNotification(ticket_id=ticket.id, dedupe_key=dedupe or str(uuid4()), payload=json.dumps(payload),
                              status='pending', attempts=0, next_attempt_at=datetime.utcnow())
    db.add(row)
    return row


def process_notifications(db, sender=None):
    if sender is None:
        from backend.services.onboarding_email import send_adguard_support_notification
        sender = send_adguard_support_notification
    now = datetime.utcnow()
    ids = [r.id for r in db.query(AdGuardNotification).filter(AdGuardNotification.status.in_(['pending','retry','sending']),
        AdGuardNotification.next_attempt_at <= now).order_by(AdGuardNotification.id).limit(25).all()]
    for notification_id in ids:
        # A conditional claim prevents two scheduler replicas sending the same pending message.
        claimed = db.query(AdGuardNotification).filter_by(id=notification_id).filter(
            AdGuardNotification.status.in_(['pending','retry','sending']), AdGuardNotification.next_attempt_at <= now
        ).update({'status':'sending', 'next_attempt_at': now + timedelta(minutes=5)}, synchronize_session=False)
        db.commit()
        if not claimed:
            continue
        row = db.get(AdGuardNotification, notification_id, populate_existing=True)
        try:
            result = sender(**json.loads(row.payload))
        except Exception as exc:
            result = {'sent':False, 'error':str(exc)}
        row.attempts += 1
        if result.get('sent'):
            row.status, row.sent_at, row.last_error = 'sent', datetime.utcnow(), None
        else:
            row.status = 'failed' if row.attempts >= 5 else 'retry'
            row.last_error = str(result.get('error') or 'Email provider did not confirm delivery')[:1000]
            row.next_attempt_at = datetime.utcnow() + timedelta(minutes=min(60, 2 ** row.attempts))
        db.commit()


def escalate_overdue(db):
    now = datetime.utcnow()
    # Lazy metadata creation also covers tickets created before this release.
    for ticket in db.query(AdGuardSupportTicket).filter_by(status='open').all():
        row = ticket_operations(db, ticket)
        if row.response_due_at and row.response_due_at < now and row.escalated_at is None:
            row.escalated_at = now
            row.escalation_count = (row.escalation_count or 0) + 1
            queue_ticket_email(db, ticket, f'Response overdue. Assigned to: {row.assigned_to or "unassigned"}. Please review this ticket.',
                               dedupe=f'escalation:{ticket.id}:{row.response_due_at.isoformat()}')
            if row.assigned_to and row.assigned_to != support_email(db):
                queue_ticket_email(db, ticket, 'Assigned ticket response is overdue. Please review it.',
                                   recipient=row.assigned_to, dedupe=f'assignee-escalation:{ticket.id}:{row.response_due_at.isoformat()}')
    db.commit()


def run_support_operations():
    from backend.db.database import SessionLocal
    with SessionLocal() as db:
        escalate_overdue(db)
        process_notifications(db)
