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


def build_report(saved, now=None, start=None, end=None):
    yesterday = (now or datetime.now(timezone.utc)).astimezone(IST).date() - timedelta(days=1)
    if (start is None) != (end is None):
        raise HTTPException(400, 'Select both From and To dates')
    start, end = start or START, end or yesterday
    if start < START or end > yesterday or start > end:
        raise HTTPException(400, 'Select a valid completed date range from 17 November 2026')
    known = {r['date']: {**r, 'checked_at': BASELINE_CHECKED} for r in BASELINE}
    known.update(saved)
    rows = []
    day = start
    while day <= end:
        rows.append(known.get(day.isoformat(), {'date': day.isoformat(), 'inbound': None, 'outbound': None, 'leads': None, 'checked_at': None}))
        day += timedelta(days=1)
    verified_rows = [r for r in rows if all(r[key] is not None for key in ('inbound', 'outbound', 'leads'))]
    complete = len(verified_rows) == len(rows)
    totals = {key: sum(r[key] for r in verified_rows) if verified_rows else None for key in ('inbound', 'outbound', 'leads')}
    verified_through = None
    for row in rows:
        if not row['checked_at']:
            break
        verified_through = row['date']
    return {'rows': rows, 'totals': totals, 'start': start.isoformat(), 'end': end.isoformat(),
            'through': yesterday.isoformat(), 'complete': complete, 'pending_days': len(rows) - len(verified_rows),
            'verified_through': verified_through,
            'checked_at': max((r['checked_at'] for r in rows if r['checked_at']), default=None),
            'timezone': 'Asia/Kolkata'}


@router.get('/{account_id}/daily-counts')
def daily_counts(account_id: int, start: date | None = None, end: date | None = None,
                 db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    setting = db.query(AppSetting).filter_by(key=f'mantri-webcity:{account_id}:daily-counts').first()
    saved = json.loads(setting.value) if setting and setting.value else {}
    return {**build_report(saved, start=start, end=end), 'can_update': user.role in ('admin', 'superadmin')}


@router.get('/{account_id}/daily-counts/export')
def export_daily_counts(account_id: int, start: date | None = None, end: date | None = None,
                        db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from fastapi.responses import StreamingResponse
    report = daily_counts(account_id, start, end, db, user)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Mantri MIS'
    sheet.append(['Date', 'Call', None, 'Leads in CRM'])
    sheet.append([None, 'Inbound', 'Outbound', None])
    for cell_range in ('A1:A2', 'B1:C1', 'D1:D2'):
        sheet.merge_cells(cell_range)
    for row in report['rows']:
        sheet.append([date.fromisoformat(row['date']), row['inbound'], row['outbound'], row['leads']])
        sheet.cell(sheet.max_row, 1).number_format = 'DD-MM-YYYY'
    sheet.append(['Total' if report['complete'] else 'Verified subtotal', report['totals']['inbound'], report['totals']['outbound'], report['totals']['leads']])
    total_row = sheet.max_row
    edge = Side(style='thin', color='999999')
    border = Border(left=edge, right=edge, top=edge, bottom=edge)
    for row in sheet.iter_rows(min_row=1, max_row=total_row, max_col=4):
        for cell in row:
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
            cell.font = Font(name='Arial', size=12, bold=cell.row <= 2 or cell.row == total_row)
        sheet.row_dimensions[row[0].row].height = 25
    for col in ('A','B','C','D'):
        sheet.column_dimensions[col].width = 20
    sheet.freeze_panes = 'B3'
    sheet.append(['IST · All Exotel call transactions · CRM People created'])
    sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=4)
    if not report['complete']:
        sheet.append([f"Incomplete: {report['pending_days']} day(s) pending. Subtotal includes verified dates only."])
        sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=4)
    output = BytesIO()
    book.save(output)
    output.seek(0)
    filename = f"Mantri_MIS_{report['start']}_to_{report['end']}.xlsx"
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})


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
