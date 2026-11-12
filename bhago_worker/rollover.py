"""Close old campaigns permanently; carry only remaining attempts into NEW campaigns."""
import datetime as dt
import json
from outcomes import (load_state, save_state, state_key, signature, parse_export,
                      merge_calls, calls_complete, TERMINAL, write_attempts)
from google_access import google_session, SheetsWriter


def pending_after_stop(recipient, kind, exported):
    """A verified stop and a fresh next-day export are prerequisites at the caller."""
    from insightdesk_bridge import mobile
    current=[]
    for call in exported:
        if call['Campaign Name']!=recipient['_closed_campaign']:continue
        try:matches=mobile(call.get('To Number'))==recipient['phone']
        except ValueError:matches=False
        if matches:current.append(call)
    calls=merge_calls(recipient.get('prior_calls',[]),current)
    if len(calls)>2 or any(c.get('Status','').casefold() not in TERMINAL for c in calls):
        raise ValueError('Closed campaign has unresolved or excessive attempts; pending row held for review.')
    return calls, 'complete' if calls_complete(calls,kind) else 'pending'


def settle(kind, moment):
    """Never reuse yesterday's CSV or assume a campaign's missing export means completed."""
    from insightdesk_bridge import get_value, PRIVATE, IST
    stopped=load_state('bhago:18:stopped:'+kind) or []
    if not stopped:return True
    sync=json.loads(get_value('bhago:18:result_sync') or '{}')
    if sync.get('state')!='ready' or not sync.get('exported_at'):return False
    exported_at=dt.datetime.fromisoformat(sync['exported_at'].replace('Z','+00:00'))
    remaining=[]
    for closed in stopped:
        stopped_at=dt.datetime.fromisoformat(closed['stopped_at'])
        # Do not requeue on the same day; allow final in-flight outcomes to settle overnight.
        if moment.date().isoformat()<=closed['day'] or exported_at.astimezone(IST).date()!=moment.date() or exported_at<=stopped_at+dt.timedelta(minutes=1):
            remaining.append(closed);continue
        job=load_state('bhago:18:manifest:'+closed['run_id'])
        if not job:
            remaining.append(closed);continue
        exported=parse_export((PRIVATE/'latest-outcomes.csv').read_text(encoding='utf-8-sig'))
        held=False
        with google_session(write=True) as session:
            writer=SheetsWriter(session)
            for row in job['recipients']:
                key=state_key(row['source_ref']['row_id']);prior=load_state(key) or {}
                if prior.get('run_id') and prior['run_id']!=job['runId']:continue
                try:
                    calls,state=pending_after_stop(dict(row,_closed_campaign=job['campaignName']),kind,exported)
                    if calls:write_attempts(writer,row,kind,calls)
                    save_state(key,{'state':state,'closed_campaign':True,'signature':signature(row),
                        'run_id':job['runId'],'source':row['source_ref']['source'],'campaign':job['campaignName'],
                        'attempts':[c['Session ID'] for c in calls],'calls':calls})
                except ValueError:
                    held=True
        if held:remaining.append(closed)
    save_state('bhago:18:stopped:'+kind,remaining)
    return not remaining
