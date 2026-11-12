"""Yesterday-first, then oldest unprocessed records; no untracked redial campaigns."""
import datetime as dt
import json
import uuid
from outcomes import bind_rows, load_state, load_states, save_state, signature, state_key
from google_access import google_session, SheetsWriter


def eligible(recipients, today, states):
    yesterday=today-dt.timedelta(days=1)
    result=[]
    for recipient in recipients:
        if not recipient.get('sourceDate'):
            continue
        date=dt.date.fromisoformat(recipient['sourceDate'])
        if date>yesterday:
            continue
        prior=states.get(recipient['source_ref']['row_id'])
        if prior:
            if prior.get('signature')!=signature(recipient):
                # A new END/date/vehicle cycle requires a fresh source record; never borrow old outcomes.
                continue
            if prior.get('state') in ('submitted','complete','uncertain','awaiting_second_attempt'):
                continue
        row=dict(recipient)
        if prior and prior.get('calls'):
            from outcomes import merge_calls
            row['prior_calls']=merge_calls(prior.get('calls',[]),[])
            if len(row['prior_calls'])>=2:continue
        result.append(row)
    return sorted(result,key=lambda r:(r['sourceDate']!=yesterday.isoformat(),r['sourceDate'],r['source_ref']['source'],r['source_ref']['row']))


def plan(kind, today):
    from insightdesk_bridge import read_source,get_value
    sources=['swiggy','zomato'] if kind=='lead' else ['payment_reminder']
    recipients=[];skipped=[]
    with google_session(write=True) as session:
        writer=SheetsWriter(session)
        for source in sources:
            url=get_value('bhago:18:sheet:'+source)
            if not url:
                skipped.append({'source':source,'reason':'No saved sheet'});continue
            sid=url.split('/spreadsheets/d/')[1].split('/')[0]
            if not writer.can_edit(sid):
                skipped.append({'source':source,'reason':'No Editor access'});continue
            run={'source':source,'calling_type':kind,'start_date':'0001-01-01',
                 'end_date':(today-dt.timedelta(days=1)).isoformat(),'_allow_empty':True,'_skip_invalid':True}
            rows,_,_=read_source(run)
            # Stable IDs are established before any row enters a calling campaign.
            bind_rows(rows,kind,writer)
            recipients.extend(rows)
    stored=load_states([state_key(r['source_ref']['row_id']) for r in recipients])
    states={r['source_ref']['row_id']:stored.get(state_key(r['source_ref']['row_id'])) for r in recipients}
    queue=eligible(recipients,today,states)
    yesterday=(today-dt.timedelta(days=1)).isoformat()
    fresh=[r for r in queue if r['sourceDate']==yesterday]
    if fresh:queue=fresh
    # One occurrence of a phone per campaign makes exported calls unambiguous across sources.
    selected=[];phones=set()
    budget=None
    for row in queue:
        remaining=2-len(row.get('prior_calls',[]))
        if budget is not None and budget!=remaining:break
        budget=remaining
        if row['phone'] in phones:continue
        selected.append(row);phones.add(row['phone'])
        if len(selected)==10:break
    return selected,skipped


def tick(moment=None):
    from insightdesk_bridge import IST,calling_enabled,connect,KEY,now,get_value,atomic_json,JOBS
    import os
    if os.environ.get('BHAGO_RUNTIME')!='cloud' or os.environ.get('BHAGO_SCHEDULE_ENABLED')!='true' or not calling_enabled():return
    moment=moment or dt.datetime.now(IST)
    # Samvaad enforces 10:00–18:00; closing the campaign prevents next-day resumption.
    config=json.loads(get_value('bhago:18:schedule') or '{}')
    if not config.get('enabled'):return
    if config.get('start_date') and moment.date().isoformat()<config['start_date']:return
    if _control:
        control(None,None,None);return
    inflights=load_states(['bhago:18:inflight:lead','bhago:18:inflight:payment'])
    # Closing has priority over a stalled/failed creation request. It never creates a call.
    for kind in ('lead','payment'):
        active=inflights.get('bhago:18:inflight:'+kind)
        if active and active.get('state') in ('submitted','paused','uncertain') and (
                moment.hour>=18 or moment.hour<10 or active.get('day',moment.date().isoformat())<moment.date().isoformat()):
            control(kind,active,'stop');return
    current=json.loads(get_value(KEY) or '{}')
    if current.get('state') in ('queued_preview','reading','ready','queued_launch','launching'):return
    if current.get('requested_by')=='daily_backlog':
        if current.get('state')=='uncertain':return
        if current.get('state')=='failed' and dt.datetime.fromisoformat(current['created_at']).astimezone(IST).date()==moment.date():return
    for kind in ('lead','payment'):
        inflight=inflights.get('bhago:18:inflight:'+kind)
        if inflight and inflight.get('state')=='uncertain':continue
        if inflight and inflight.get('state') in ('submitted','paused'):
            if moment.hour>=18 or inflight.get('day',moment.date().isoformat())<moment.date().isoformat():
                control(kind,inflight,'stop');return
            continue
        from rollover import settle
        if not settle(kind,moment):continue
        if not 10<=moment.hour<18:continue
        rows,skipped=plan(kind,moment.date())
        if not rows:continue
        run_id=uuid.uuid4().hex
        run={'id':run_id,'source':'lead_batch' if kind=='lead' else 'payment_reminder','calling_type':kind,
             'start_date':min(r['sourceDate'] for r in rows),'end_date':max(r['sourceDate'] for r in rows),
             'lead_sources':sorted({r['source_ref']['source'] for r in rows}) if kind=='lead' else [],'auto_launch':True,'_skip_invalid':True,
             '_plan_key':'bhago:18:plan:'+run_id,'_skipped_sources':skipped,'state':'queued_preview',
             'requested_by':'daily_backlog','created_at':now(),'message':'Yesterday first, then oldest unprocessed backlog.'}
        save_state(run['_plan_key'],rows)
        with connect() as db,db.cursor() as cur:
            cur.execute('SELECT value FROM app_settings WHERE key=%s FOR UPDATE',(KEY,));old=cur.fetchone()
            if old and json.loads(old[0]).get('state') in ('queued_preview','reading','ready','queued_launch','launching'):return
            cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()', (KEY,json.dumps(run)))
        return


def select_planned(run, live_rows):
    planned=load_state(run['_plan_key']) if run.get('_plan_key') else run.get('_planned_recipients')
    if run.get('_plan_key') and planned is None:raise ValueError('Encrypted calling plan is unavailable; no campaign was started.')
    if planned is None:return live_rows
    by_ref={(r['source_ref']['spreadsheet_id'],r['source_ref']['sheet_id'],r['source_ref']['row']):r for r in live_rows}
    result=[]
    for old in planned:
        ref=old['source_ref'];key=(ref['spreadsheet_id'],ref['sheet_id'],ref['row'])
        live=by_ref.get(key)
        if not live or signature(live)!=signature(old):raise ValueError('Planned backlog row changed; no campaign was started.')
        live['source_ref']['row_id']=ref['row_id']
        if 'prior_calls' in old:live['prior_calls']=old['prior_calls']
        result.append(live)
    return result


_control=None

def control(kind, campaign, action):
    """Nonblocking control of registered campaigns; ambiguity holds automatic dialing."""
    global _control
    import time
    from insightdesk_bridge import JOBS, command, atomic_json
    if _control:
        file=JOBS/(_control['id']+'.control-result.json')
        if not file.exists():
            if time.monotonic()-_control['started']>90:
                save_state('bhago:18:backlog_attention',{'state':'needs_attention','reason':'Campaign ordering control timed out; automatic dialing held.'})
            return None
        result=json.loads(file.read_text());file.unlink();task=_control;_control=None
        if result['state'] not in ('stopped','complete'):
            save_state('bhago:18:backlog_attention',result);return None
        from insightdesk_bridge import now
        inflight=dict(task['campaign'],state='stopped',stopped_at=now())
        save_state('bhago:18:inflight:'+task['kind'],inflight)
        stopped=load_state('bhago:18:stopped:'+task['kind']) or []
        stopped=[p for p in stopped if p['run_id']!=inflight['run_id']]
        stopped.append(inflight)
        save_state('bhago:18:stopped:'+task['kind'],stopped)
        from insightdesk_bridge import update_run
        update_run(inflight['run_id'],['uncertain'],state='created',campaign_status='Stopped',
                   message='Old campaign stopped. Pending rows will move to new campaigns after next-day outcome reconciliation.')
        return inflight
    ident=uuid.uuid4().hex
    atomic_json(JOBS/(ident+'.control.json'),{'campaign':campaign['campaign'],'action':action})
    if command({'action':'campaign_step','stage':'insightdesk_campaign_control','requestId':ident}):
        _control={'id':ident,'kind':kind,'campaign':campaign,'started':time.monotonic()}
    return None
