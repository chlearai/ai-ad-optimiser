"""Yesterday-first, then oldest unprocessed records; no untracked redial campaigns."""
import datetime as dt
import json
import uuid
from outcomes import bind_rows, load_state, save_state, signature, state_key
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
        result.append(recipient)
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
    states={r['source_ref']['row_id']:load_state(state_key(r['source_ref']['row_id'])) for r in recipients}
    queue=eligible(recipients,today,states)
    yesterday=(today-dt.timedelta(days=1)).isoformat()
    fresh=[r for r in queue if r['sourceDate']==yesterday]
    if fresh:queue=fresh
    # One occurrence of a phone per campaign makes exported calls unambiguous across sources.
    selected=[];phones=set()
    for row in queue:
        if row['phone'] in phones:continue
        selected.append(row);phones.add(row['phone'])
        if len(selected)==10:break
    return selected,skipped


def tick(moment=None):
    from insightdesk_bridge import IST,calling_enabled,connect,KEY,now,get_value,atomic_json,JOBS
    import os
    if os.environ.get('BHAGO_RUNTIME')!='cloud' or os.environ.get('BHAGO_SCHEDULE_ENABLED')!='true' or not calling_enabled():return
    moment=moment or dt.datetime.now(IST)
    # A short final batch is not started near the cutoff; Samvaad still enforces 10:00–18:00.
    config=json.loads(get_value('bhago:18:schedule') or '{}')
    if not config.get('enabled'):return
    if config.get('start_date') and moment.date().isoformat()<config['start_date']:return
    if _control:
        control(None,None,None);return
    current=json.loads(get_value(KEY) or '{}')
    if current.get('state') in ('queued_preview','reading','ready','queued_launch','launching'):return
    for kind in ('lead','payment'):
        inflight=load_state('bhago:18:inflight:'+kind)
        if inflight and inflight.get('state')=='uncertain':continue
        if inflight and inflight.get('state')=='submitted':
            if (moment.hour==17 and moment.minute>=55) or moment.hour>=18 or inflight.get('day',moment.date().isoformat())<moment.date().isoformat():
                control(kind,inflight,'pause');return
            continue
        if not 10<=moment.hour<17:continue
        rows,skipped=plan(kind,moment.date())
        paused=load_state('bhago:18:paused:'+kind) or []
        for old in list(paused):
            job=load_state('bhago:18:manifest:'+old['run_id'])
            if job and all((load_state(state_key(r['source_ref']['row_id'])) or {}).get('state')=='complete' for r in job['recipients']):
                paused.remove(old)
        save_state('bhago:18:paused:'+kind,paused)
        yesterday=(moment.date()-dt.timedelta(days=1)).isoformat()
        if paused and not any(r['sourceDate']==yesterday for r in rows):
            control(kind,paused[0],'resume');return
        if not rows:continue
        run_id=uuid.uuid4().hex
        run={'id':run_id,'source':'lead_batch' if kind=='lead' else 'payment_reminder','calling_type':kind,
             'start_date':'0001-01-01','end_date':(moment.date()-dt.timedelta(days=1)).isoformat(),
             'lead_sources':sorted({r['source_ref']['source'] for r in rows}) if kind=='lead' else [],'auto_launch':True,'_skip_invalid':True,
             '_planned_recipients':rows,'_skipped_sources':skipped,'state':'queued_preview',
             'requested_by':'daily_backlog','created_at':now(),'message':'Yesterday first, then oldest unprocessed backlog.'}
        with connect() as db,db.cursor() as cur:
            cur.execute('SELECT value FROM app_settings WHERE key=%s FOR UPDATE',(KEY,));old=cur.fetchone()
            if old and json.loads(old[0]).get('state') in ('queued_preview','reading','ready','queued_launch','launching'):return
            cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()', (KEY,json.dumps(run)))
        return


def select_planned(run, live_rows):
    planned=run.get('_planned_recipients')
    if planned is None:return live_rows
    by_ref={(r['source_ref']['spreadsheet_id'],r['source_ref']['sheet_id'],r['source_ref']['row']):r for r in live_rows}
    result=[]
    for old in planned:
        ref=old['source_ref'];key=(ref['spreadsheet_id'],ref['sheet_id'],ref['row'])
        live=by_ref.get(key)
        if not live or signature(live)!=signature(old):raise ValueError('Planned backlog row changed; no campaign was started.')
        live['source_ref']['row_id']=ref['row_id'];result.append(live)
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
        if result['state'] not in ('paused','submitted','complete'):
            save_state('bhago:18:backlog_attention',result);return None
        inflight=dict(task['campaign'],state=result['state'])
        if result['state']=='submitted':
            from insightdesk_bridge import IST
            inflight['day']=dt.datetime.now(IST).date().isoformat()
        save_state('bhago:18:inflight:'+task['kind'],inflight)
        paused=load_state('bhago:18:paused:'+task['kind']) or []
        paused=[p for p in paused if p['run_id']!=inflight['run_id']]
        if result['state']=='paused':paused.append(inflight)
        save_state('bhago:18:paused:'+task['kind'],paused)
        return inflight
    ident=uuid.uuid4().hex
    atomic_json(JOBS/(ident+'.control.json'),{'campaign':campaign['campaign'],'action':action})
    if command({'action':'campaign_step','stage':'insightdesk_campaign_control','requestId':ident}):
        _control={'id':ident,'kind':kind,'campaign':campaign,'started':time.monotonic()}
    return None
