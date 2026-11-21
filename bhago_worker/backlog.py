"""Yesterday-first, then oldest unprocessed records; no untracked redial campaigns."""
import datetime as dt
import json
import uuid
from outcomes import bind_rows, load_state, load_states, save_state, signature, state_key
from google_access import google_session, SheetsWriter


def eligible(recipients, today, states, moment=None, lead_callbacks=False):
    yesterday=today-dt.timedelta(days=1)
    result=[]
    for recipient in recipients:
        if not recipient.get('sourceDate'):
            continue
        date=dt.date.fromisoformat(recipient['sourceDate'])
        if date>yesterday:
            continue
        prior=states.get(recipient['source_ref']['row_id'])
        callback=None
        if prior:
            if prior.get('signature')!=signature(recipient):
                # A new END/date/vehicle cycle requires a fresh source record; never borrow old outcomes.
                continue
            if lead_callbacks and prior.get('state')=='complete':
                from lead_followup import callback_plan,IST
                callback=callback_plan(prior.get('calls',[]))
                if callback and (callback['state']!='scheduled' or dt.datetime.fromisoformat(callback['due_at']) > (moment or dt.datetime.combine(today,dt.time(10),IST))):
                    continue
            if prior.get('state') in ('submitted','complete','uncertain','awaiting_second_attempt','held_review') and not callback:
                continue
        row=dict(recipient)
        if callback:row['_callback_due']=callback['due_at']
        row['_carryover']=bool(prior and prior.get('closed_campaign') and prior.get('state')=='pending')
        if prior and prior.get('calls'):
            from outcomes import merge_calls
            row['prior_calls']=merge_calls(prior.get('calls',[]),[])
            if len(row['prior_calls'])>=2:continue
        result.append(row)
    return sorted(result,key=lambda r:(not bool(r.get('_callback_due')),r.get('_callback_due',''),r['sourceDate']!=yesterday.isoformat(),not r.get('_carryover',False),r['sourceDate'],r['source_ref']['source'],r['source_ref']['row']))


def plan(kind, today, moment=None):
    from insightdesk_bridge import read_source,get_value
    sources=['swiggy','zomato'] if kind=='lead' else ['payment_reminder']
    config=json.loads(get_value('bhago:18:schedule') or '{}')
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
            if kind=='payment' and config.get('payment_policy',{}).get('mode')=='full_sheet_daily':
                run['_ignore_payment_dates']=True
            rows,_,_=read_source(run)
            # Stable IDs are established before any row enters a calling campaign.
            original_count=len(rows)
            rows=bind_rows(rows,kind,writer,skip_duplicate_ids=True)
            if len(rows)<original_count:
                skipped.append({'source':source,'reason':'Duplicate Bhago Row IDs excluded','excluded_rows':original_count-len(rows)})
            recipients.extend(rows)
    if kind=='payment' and config.get('payment_policy',{}).get('mode')=='full_sheet_daily':
        return payment_daily_rows(recipients),skipped
    stored=load_states([state_key(r['source_ref']['row_id']) for r in recipients])
    states={r['source_ref']['row_id']:stored.get(state_key(r['source_ref']['row_id'])) for r in recipients}
    queue=eligible(recipients,today,states,moment,lead_callbacks=kind=='lead')
    yesterday=(today-dt.timedelta(days=1)).isoformat()
    fresh=[r for r in queue if r['sourceDate']==yesterday]
    callbacks=[r for r in queue if r.get('_callback_due')]
    if callbacks:queue=callbacks
    elif fresh:queue=fresh
    batch_size=json.loads(get_value('bhago:18:schedule') or '{}').get('batch_size',30)
    if not isinstance(batch_size,int) or isinstance(batch_size,bool) or batch_size<1:
        raise ValueError('Bhago batch size must be a positive whole number.')
    return select_batch(queue,batch_size),skipped


def payment_daily_rows(recipients):
    """The client maintains the pending list; previous days and END dates do not filter it."""
    selected=[];phones=set()
    for recipient in recipients:
        if recipient['phone'] in phones:continue
        row=dict(recipient)
        for key in ('prior_calls','_callback_due','_carryover'):row.pop(key,None)
        selected.append(row);phones.add(row['phone'])
    return selected


def payment_daily_tick(moment,config):
    """Queue one complete current list per IST day; never resume a worker backlog plan."""
    from insightdesk_bridge import get_value,connect,KEY,now
    if not config.get('payment_reminder') or not 12<=moment.hour<18:return False
    marker=f'bhago:18:schedule:{moment.date().isoformat()}:payment_reminder'
    if get_value(marker):return False
    current=json.loads(get_value(KEY) or '{}')
    if current.get('state') in ('queued_preview','reading','ready','queued_launch','launching','uncertain'):return False
    rows,skipped=plan('payment',moment.date(),moment)
    if not rows:return False
    run_id=uuid.uuid4().hex
    run={'id':run_id,'source':'payment_reminder','calling_type':'payment',
         'start_date':None,'end_date':None,'auto_launch':True,'_skip_invalid':True,
         '_ignore_payment_dates':True,'_plan_key':'bhago:18:plan:'+run_id,
         '_skipped_sources':skipped,'state':'queued_preview','requested_by':'daily_payment_full_sheet',
         'created_at':now(),'message':'Daily noon payment run: complete current pending list, one campaign; no previous-day carryover.'}
    save_state(run['_plan_key'],rows)
    with connect() as db,db.cursor() as cur:
        cur.execute('SELECT id FROM accounts WHERE id=18 FOR UPDATE')
        cur.execute('SELECT value FROM app_settings WHERE key=%s',(marker,))
        if cur.fetchone():return False
        cur.execute('SELECT value FROM app_settings WHERE key=%s FOR UPDATE',(KEY,));old=cur.fetchone()
        if old and json.loads(old[0]).get('state') in ('queued_preview','reading','ready','queued_launch','launching','uncertain'):return False
        cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW())',(marker,json.dumps({'run_id':run_id,'policy':'full_sheet_daily','records':len(rows)})))
        cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()',(KEY,json.dumps(run)))
    return True


def select_batch(queue,batch_size=30):
    # One occurrence of a phone per campaign makes exported calls unambiguous across sources.
    selected=[];phones=set()
    budget=None
    for row in queue:
        remaining=2-len(row.get('prior_calls',[]))
        if budget is not None and budget!=remaining:break
        budget=remaining
        if row['phone'] in phones:continue
        selected.append(row);phones.add(row['phone'])
        if len(selected)==batch_size:break
    return selected


def tick(moment=None):
    from insightdesk_bridge import IST,calling_enabled,connect,KEY,now,get_value,atomic_json,JOBS
    import os
    if os.environ.get('BHAGO_RUNTIME')!='cloud' or os.environ.get('BHAGO_SCHEDULE_ENABLED')!='true' or not calling_enabled():return
    moment=moment or dt.datetime.now(IST)
    # Samvaad enforces 10:00–18:00. Interim policy may retain unfinished campaigns.
    config=json.loads(get_value('bhago:18:schedule') or '{}')
    if not config.get('enabled'):return
    if config.get('start_date') and moment.date().isoformat()<config['start_date']:return
    full_payment=config.get('payment_policy',{}).get('mode')=='full_sheet_daily'
    if full_payment and payment_daily_tick(moment,config):return
    if _control:
        control(None,None,None);return
    inflights=load_states(['bhago:18:inflight:lead','bhago:18:inflight:payment'])
    # Closing has priority over a stalled/failed creation request. It never creates a call.
    for kind in (('lead',) if full_payment else ('lead','payment')):
        active=inflights.get('bhago:18:inflight:'+kind)
        if not config.get('resume_previous_campaign') and active and active.get('state') in ('submitted','paused','uncertain') and (
                moment.hour>=18 or moment.hour<10 or active.get('day',moment.date().isoformat())<moment.date().isoformat()):
            control(kind,active,'stop');return
    current=json.loads(get_value(KEY) or '{}')
    if current.get('state') in ('queued_preview','reading','ready','queued_launch','launching'):return
    if current.get('requested_by')=='daily_backlog':
        if current.get('state')=='uncertain':return
        if current.get('state')=='failed' and dt.datetime.fromisoformat(current['created_at']).astimezone(IST).date()==moment.date():return
    inspections=[]
    for kind in (('lead',) if full_payment else ('lead','payment')):
        inflight=inflights.get('bhago:18:inflight:'+kind)
        if inflight and inflight.get('state')=='uncertain':continue
        if inflight and inflight.get('state') in ('submitted','paused'):
            if not config.get('resume_previous_campaign') and (moment.hour>=18 or inflight.get('day',moment.date().isoformat())<moment.date().isoformat()):
                control(kind,inflight,'stop');return
            checked=inflight.get('last_checked_at')
            if not checked or (moment-dt.datetime.fromisoformat(checked).astimezone(IST)).total_seconds()>=45:
                inspections.append((kind,inflight))
            continue
        from rollover import settle
        if not settle(kind,moment):continue
        if not 10<=moment.hour<18:continue
        rows,skipped=plan(kind,moment.date(),moment)
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
    if inspections:
        kind,inflight=min(inspections,key=lambda item:item[1].get('last_checked_at',''))
        control(kind,inflight,'inspect')


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
    from insightdesk_bridge import JOBS, command, atomic_json, now
    if _control:
        file=JOBS/(_control['id']+'.control-result.json')
        if not file.exists():
            if time.monotonic()-_control['started']>90:
                save_state('bhago:18:backlog_attention',{'state':'needs_attention','reason':'Campaign ordering control timed out; automatic dialing held.'})
            return None
        result=json.loads(file.read_text());file.unlink();task=_control;_control=None
        if task.get('action')=='inspect' and result['state']=='pending':
            save_state('bhago:18:inflight:'+task['kind'],dict(task['campaign'],last_checked_at=now()))
            return None
        if result['state'] not in ('stopped','complete'):
            save_state('bhago:18:backlog_attention',result);return None
        from insightdesk_bridge import now
        verified=now()
        inflight=dict(task['campaign'],state='awaiting_reconciliation' if result['state']=='complete' else 'stopped',
                      stopped_at=verified,verified_complete=result['state']=='complete')
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
        _control={'id':ident,'kind':kind,'campaign':campaign,'action':action,'started':time.monotonic()}
    return None
