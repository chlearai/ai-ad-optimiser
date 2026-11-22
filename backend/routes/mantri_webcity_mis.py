"""Verified, counts-only Mantri Webcity MIS. No unrelated CRM integrations."""
from datetime import date, datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, AppSetting, UserAccountAssignment
from backend.routes.auth import get_current_user_required

router = APIRouter(prefix='/api/mantri-webcity', tags=['Mantri Webcity MIS'])
IST = timezone(timedelta(hours=5, minutes=30))
START = date(2026, 11, 17)
# Verified from all Mantri CRM People and distinct Exotel Call SIDs on 22 Nov.
BASELINE = [
    {'date': '2026-11-17', 'inbound': 4, 'outbound': 1, 'leads': 0},
    {'date': '2026-11-18', 'inbound': 0, 'outbound': 1, 'leads': 2},
    {'date': '2026-11-19', 'inbound': 3, 'outbound': 6, 'leads': 0},
    {'date': '2026-11-20', 'inbound': 0, 'outbound': 9, 'leads': 0},
    {'date': '2026-11-21', 'inbound': 0, 'outbound': 9, 'leads': 29},
]
BASELINE_CHECKED = '2026-11-22T14:37:31+00:00'


def access(account_id, db, user, edit=False):
    admin = user.role in ('admin', 'superadmin')
    if not admin and (edit or not user.access_insightdesk or not db.query(
            UserAccountAssignment).filter_by(user_id=user.id, account_id=account_id).first()):
        raise HTTPException(403, 'Not authorized for this account')
    account = db.query(Account).filter_by(id=account_id).first()
    if not account or account.name.strip().casefold() != 'mantri webcity':
        raise HTTPException(404, 'Mantri Webcity account not found')


class VerifiedDay(BaseModel):
    date: date
    inbound: int = Field(ge=0, le=10000000, strict=True)
    outbound: int = Field(ge=0, le=10000000, strict=True)
    leads: int = Field(ge=0, le=10000000, strict=True)
    checked_at: datetime


def build_report(saved, now=None):
    yesterday = (now or datetime.now(timezone.utc)).astimezone(IST).date() - timedelta(days=1)
    known = {r['date']: {**r, 'checked_at': BASELINE_CHECKED} for r in BASELINE}
    known.update(saved)
    rows = []
    day = START
    while day <= yesterday:
        rows.append(known.get(day.isoformat(), {'date': day.isoformat(), 'inbound': None, 'outbound': None, 'leads': None, 'checked_at': None}))
        day += timedelta(days=1)
    complete = all(r['inbound'] is not None and r['outbound'] is not None and r['leads'] is not None for r in rows)
    totals = {key: sum(r[key] for r in rows) if complete else None for key in ('inbound', 'outbound', 'leads')}
    verified_through = None
    for row in rows:
        if not row['checked_at']:
            break
        verified_through = row['date']
    return {'rows': rows, 'totals': totals, 'through': yesterday.isoformat(), 'complete': complete,
            'verified_through': verified_through,
            'checked_at': max((r['checked_at'] for r in rows if r['checked_at']), default=None),
            'timezone': 'Asia/Kolkata'}


@router.get('/{account_id}/daily-counts')
def daily_counts(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    setting = db.query(AppSetting).filter_by(key=f'mantri-webcity:{account_id}:daily-counts').first()
    saved = json.loads(setting.value) if setting and setting.value else {}
    return {**build_report(saved), 'can_update': user.role in ('admin', 'superadmin')}


@router.put('/{account_id}/daily-counts')
def save_daily_counts(account_id: int, body: VerifiedDay, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user, edit=True)
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    if body.date < START or body.date >= today:
        raise HTTPException(400, 'Use a completed reporting day from 17 November 2026')
    if body.checked_at.tzinfo is None or body.checked_at.astimezone(IST).date() <= body.date:
        raise HTTPException(400, 'Verification must be after the reporting day and include a time zone')
    checked = body.checked_at.astimezone(timezone.utc)
    if checked > now + timedelta(minutes=5):
        raise HTTPException(400, 'Verification time cannot be in the future')
    db.query(Account).filter_by(id=account_id).with_for_update().first()
    key = f'mantri-webcity:{account_id}:daily-counts'
    setting = db.query(AppSetting).filter_by(key=key).first()
    saved = json.loads(setting.value) if setting and setting.value else {}
    previous = saved.get(body.date.isoformat())
    if previous and datetime.fromisoformat(previous['checked_at']) > checked:
        raise HTTPException(409, 'A newer verified count is already saved')
    saved[body.date.isoformat()] = {'date': body.date.isoformat(), 'inbound': body.inbound,
        'outbound': body.outbound, 'leads': body.leads, 'checked_at': checked.isoformat()}
    if setting:
        setting.value = json.dumps(saved)
    else:
        db.add(AppSetting(key=key, value=json.dumps(saved)))
    db.commit()
    return {'saved': True, **build_report(saved)}
