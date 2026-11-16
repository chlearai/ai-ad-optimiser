"""Lead-only callback policy. Structured outcomes, IST, and two total attempts."""
import datetime as dt
import re
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
EMPTY = {'', '-', 'none', 'null', 'unknown', 'not specified', 'not_specified', 'n/a', 'na', 'not requested', 'not_requested', 'not required', 'false'}


def clean(value):
    value = str(value or '').strip()
    return '' if value.casefold() in EMPTY else value


def callback_datetime(value):
    """Accept explicit dates and times only; never guess a date from free text."""
    value = clean(value)
    if not value:
        return None
    value = re.sub(r'\s+(?:IST|Asia/(?:Calcutta|Kolkata))$', '', value, flags=re.I)
    try:
        if not re.search(r'\d[ T]\d{1,2}:\d{2}', value):
            return None
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=IST) if result.tzinfo is None else result.astimezone(IST)
    except ValueError:
        for fmt in ('%d/%m/%Y %H:%M', '%d-%m-%Y %H:%M', '%d %b %Y %H:%M', '%d/%m/%Y %I:%M %p'):
            try:
                return dt.datetime.strptime(value, fmt).replace(tzinfo=IST)
            except ValueError:
                pass
    return None


def requested(call):
    return bool(clean(call.get('Callback Time'))) or clean(call.get('call_disposition')).casefold() in (
        'callback_requested', 'callback requested', 'call_back', 'callback')


def in_window(moment):
    moment = moment.astimezone(IST)
    if moment.hour >= 18:
        return (moment + dt.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    if moment.hour < 10:
        return moment.replace(hour=10, minute=0, second=0, microsecond=0)
    return moment


def callback_plan(calls):
    """Only the latest attempt may request a callback. It consumes the remaining attempt."""
    calls = sorted({c['Session ID']: c for c in calls}.values(), key=lambda c: (c['Time'], c['Session ID']))
    if not calls or not requested(calls[-1]):
        return None
    call = calls[-1]
    explicit = callback_datetime(call.get('Callback Time'))
    if len(calls) >= 2:
        return {'state': 'manual_followup', 'due_at': None, 'reason': 'Two total attempts already used'}
    if explicit is None:
        try:
            day = dt.date.fromisoformat(call['Time'][:10]) + dt.timedelta(days=1)
        except (KeyError, ValueError):
            return {'state': 'manual_followup', 'due_at': None, 'reason': 'Call date unavailable'}
        explicit = dt.datetime.combine(day, dt.time(10), IST)
    return {'state': 'scheduled', 'due_at': in_window(explicit).isoformat(), 'reason': 'Customer callback'}


def callback_columns(calls):
    latest = next((c for c in reversed(calls) if requested(c)), {})
    moment = callback_datetime(latest.get('Callback Time'))
    return (moment.strftime('%Y-%m-%d'), moment.strftime('%H:%M')) if moment else ('', '')


def delivery_status(calls):
    """A send/acceptance response is not proof of delivery. Unknown remains blank."""
    for call in reversed(calls):
        status = clean(call.get('WhatsApp Delivery Status')).casefold()
        if status in ('delivered', 'read'):
            return 'Delivered'
        if status in ('failed', 'undeliverable', 'not delivered'):
            return 'Not Delivered'
    return ''
