"""Bhago lead-only receipt sync and at-most-once, next-day template resend.

Samvaad source session -> WAMID -> Kapso receipt. Never match a message by name,
guess a recipient, replay a call, or treat 'sent' as delivered.
"""
import collections
import datetime as dt
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote
import requests
from credential_store import read_secret
from outcomes import load_state, save_state, pending_manifests, parse_export
from lead_followup import IST

PREFIX='bhago:18:whatsapp:'
PHONE_ID='1335087533030269'
WABA_ID='1604508864489302'
NUMBER='918264006367'
PROJECT='4cc9e53b-a879-46a8-8df9-341445551dda'
TEMPLATES={'bangalore_hub_v4','delhi_hub_v4'}
_next=0
_scan_next=0
_pending=None


def digits(value):return re.sub(r'\D','',str(value or ''))
def indian_recipient(value):
    value=digits(value)
    return '91'+value if re.fullmatch(r'[6-9]\d{9}',value) else value
def moment_of(message):return dt.datetime.fromtimestamp(int(message['timestamp']),dt.timezone.utc).astimezone(IST)
def status(message):return str(message.get('kapso',{}).get('status','')).lower()


class Kapso:
    def __init__(self):
        self.secret=read_secret('kapso') or {}
        if (self.secret.get('project_id')!=PROJECT or self.secret.get('account')!='chlearbhago@gmail.com' or
                self.secret.get('phone_number_id')!=PHONE_ID):raise ValueError('Approved Bhago Kapso authorization is unavailable')
        self.session=requests.Session();self.session.headers['X-API-Key']=self.secret['api_key']
    def request(self,method,path,**kwargs):
        response=self.session.request(method,'https://api.kapso.ai/'+path,timeout=25,allow_redirects=False,**kwargs)
        if not response.ok or response.is_redirect:raise ValueError('Kapso request failed (HTTP '+str(response.status_code)+'); no automatic send retry was made')
        return response.json()
    def verify(self):
        data=self.request('GET','meta/whatsapp/v24.0/'+PHONE_ID,params={'fields':'id,display_phone_number'})
        if str(data.get('id'))!=PHONE_ID or digits(data.get('display_phone_number'))!=NUMBER:
            raise ValueError('Kapso sender verification failed')
    def messages(self):
        found={};after=None;seen=set()
        for _ in range(100):
            since=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=7)).isoformat()
            params={'limit':100,'direction':'outbound','since':since,'fields':'kapso(default)'}
            if after:params['after']=after
            page=self.request('GET','meta/whatsapp/v24.0/'+PHONE_ID+'/messages',params=params)
            for item in page.get('data',[]):
                if item.get('kapso',{}).get('direction')=='outbound':found[item['id']]=item
            paging=page.get('paging',{});after=paging.get('cursors',{}).get('after')
            if not paging.get('next') or not after:return found
            if after in seen:raise ValueError('Kapso history cursor repeated')
            seen.add(after)
        raise ValueError('Kapso history exceeds reviewed page limit')
    def message(self,ident):
        data=self.request('GET','meta/whatsapp/v24.0/'+PHONE_ID+'/messages/'+quote(ident,safe=''))
        data=data.get('data',data)
        if data.get('id')!=ident:raise ValueError('Kapso message identity mismatch')
        return data
    def templates(self):
        data=self.request('GET','meta/whatsapp/v24.0/'+WABA_ID+'/message_templates',params={'limit':100})
        return {(t['name'],t['language']):t for t in data.get('data',[]) if t.get('status')=='APPROVED' and t['name'] in TEMPLATES}
    def marketing_allowed(self,to):
        data=self.request('GET','platform/v1/whatsapp/contacts/'+quote(to,safe='')+'/marketing_preferences')
        if not isinstance(data.get('data'),list):raise ValueError('Kapso marketing preference could not be verified')
        return not any(str(r.get('phone_number_id'))==PHONE_ID and (r.get('marketing_allowed') is False or r.get('status')=='stopped') for r in data['data'])
    def send(self,payload):
        return self.request('POST','meta/whatsapp/v24.0/'+PHONE_ID+'/messages',json=payload)
    def close(self):self.session.close()


def retry_payload(message,templates):
    template=message.get('template') or {};name=template.get('name');language=(template.get('language') or {}).get('code')
    if message.get('type')!='template' or name not in TEMPLATES or (name,language) not in templates:
        raise ValueError('Only the original approved lead hub template may be resent')
    to=indian_recipient(message.get('to'))
    if not re.fullmatch(r'91[6-9]\d{9}',to):raise ValueError('Original WhatsApp recipient is unavailable')
    required=set(re.findall(r'\{\{(\w+)\}\}', ' '.join(c.get('text','') for c in templates[(name,language)].get('components',[]))))
    supplied={}
    for component in template.get('components',[]):
        if component.get('type')!='body':raise ValueError('Unexpected template component; resend held')
        for p in component.get('parameters',[]):
            if p.get('type')!='text' or not str(p.get('text','')).strip() or re.search(r'\{\{|\$node',str(p['text'])):
                raise ValueError('Original template variable is missing; resend held')
            supplied[p.get('parameter_name','')]=p['text']
    if required!=set(supplied):raise ValueError('Original template variables do not match the approved template')
    return {'messaging_product':'whatsapp','to':to,'type':'template','template':template}


def retry_decision(message,prior,moment,activation_day):
    if prior:return 'already_attempted'
    if status(message)!='failed':return 'not_failed'
    day=moment_of(message).date()
    if day<dt.date.fromisoformat(activation_day):return 'historical'
    due=day+dt.timedelta(days=1)
    if moment.date()<due:return 'not_due'
    if moment.date()>due:return 'expired'
    if not 10<=moment.hour<18:return 'outside_window'
    return 'due'


def projection(entries,messages,retries,allowed_calls):
    grouped=collections.defaultdict(list)
    for entry in entries.values():
        cid=entry.get('call_id')
        if cid not in allowed_calls:continue
        if entry.get('failed_before_send'):grouped[cid].append('failed')
        for mid in entry.get('message_ids',[]):
            retry=retries.get(mid) or {};actual=retry.get('message_id') or mid
            grouped[cid].append('' if retry.get('state') in ('sending','uncertain') else status(messages.get(actual,{})))
    return {cid:('Delivered' if states and all(s in ('read','delivered') for s in states) else
                 'Not Delivered' if 'failed' in states else '') for cid,states in grouped.items()}


def allowed_calls(path):
    jobs={j['campaignName']:j for _,j in pending_manifests() if j.get('type')=='lead' and not j.get('test') and
          j.get('source') not in ('test','test_lead','test_payment','qwqer')}
    result={}
    for call in parse_export(path.read_text(encoding='utf-8-sig')):
        job=jobs.get(call['Campaign Name'])
        if not job or digits(call.get('From Number'))!='918069458173':continue
        matches=[r for r in job['recipients'] if r.get('source_ref',{}).get('source') in ('swiggy','zomato') and digits(r['phone'])[-10:]==digits(call.get('To Number'))[-10:]]
        if len(matches)==1:result[call['Session ID']]=call
    return result


def write_report(projections):
    from client_reports import ReportWriter
    from google_access import google_session,a1
    from insightdesk_bridge import get_value
    url=get_value('bhago:18:sheet:lead_calling_report') or ''
    sid=re.fullmatch(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?',url)
    if not sid:raise ValueError('Save the Bhago lead report link')
    sid=sid[1];changed=0
    with google_session(write=True) as session:
        writer=ReportWriter(session,sid);meta=writer.metadata(sid)
        tabs={s['properties']['title']:s['properties'] for s in meta['sheets']}
        if not writer.can_edit(sid):raise ValueError('Lead report editing permission unavailable')
        history=writer.values(sid,[a1('Call History','A1:Q'+str(tabs['Call History']['gridProperties']['rowCount']))])[0]
        if not history or len(history[0])<17 or history[0][16]!='Call ID':raise ValueError('Call History identity columns changed')
        latest={}
        for row in sorted(history[1:],key=lambda r:str(r[6]) if len(r)>6 else ''):
            if len(row)>16 and str(row[16]) in projections:latest[digits(row[4])[-10:]]=projections[str(row[16])]
        for title,phonecol,statuscol in [('Lead Interested',4,13),('Lead Outcomes',1,19)]:
            width=statuscol+1;end=tabs[title]['gridProperties']['rowCount']
            rows=writer.values(sid,[a1(title,f'A1:{chr(64+width)}{end}')])[0]
            if len(rows[0])<width or rows[0][phonecol]!='Mobile' or rows[0][statuscol]!='WhatsApp Message Status':
                raise ValueError('Lead report status columns changed; writes held')
            counts=collections.Counter(digits(r[phonecol])[-10:] for r in rows[1:] if len(r)>phonecol)
            updates=[];guards=[]
            for n,row in enumerate(rows[1:],2):
                if len(row)<=phonecol:continue
                phone=digits(row[phonecol])[-10:]
                if counts[phone]!=1 or phone not in latest:continue
                old=row[statuscol] if len(row)>statuscol else ''
                if old==latest[phone]:continue
                guards.append((a1(title,f'{chr(65+phonecol)}{n}'),phone))
                updates.append({'range':a1(title,f'{chr(65+statuscol)}{n}'),'values':[[latest[phone]]]})
            if updates:
                current=writer.values(sid,[r for r,_ in guards])
                if any(digits(v[0][0] if v and v[0] else '')[-10:]!=p for v,(_,p) in zip(current,guards)):
                    raise ValueError('Lead report rows moved during receipt update')
                writer.write(sid,updates);actual=writer.values(sid,[u['range'] for u in updates])
                if any((v[0][0] if v and v[0] else '')!=u['values'][0][0] for v,u in zip(actual,updates)):
                    raise ValueError('WhatsApp report write read-back failed')
                changed+=len(updates)
    return changed


def sync(config,moment=None,allow_send=True):
    from insightdesk_bridge import PRIVATE,now,calling_enabled
    moment=moment or dt.datetime.now(IST);path=PRIVATE/'latest-outcomes.csv'
    if not path.exists():raise ValueError('Call history is required to bind WhatsApp receipts')
    calls=allowed_calls(path);entries=load_state(PREFIX+'links') or {};retries=load_state(PREFIX+'retries') or {}
    api=Kapso()
    try:
        api.verify();messages=(load_state(PREFIX+'receipts') or {}).get('messages',{})
        messages.update(api.messages());templates=None;held=0;resent=0
        linked={mid for e in entries.values() if e.get('call_id') in calls for mid in e.get('message_ids',[])}
        if allow_send and config.get('retry_enabled') and calling_enabled() and time.time()-path.stat().st_mtime<180:
            for mid in sorted(linked,key=lambda m:messages.get(m,{}).get('timestamp',''),reverse=True):
                message=messages.get(mid)
                if not message or retry_decision(message,retries.get(mid),moment,config['activation_day'])!='due':continue
                # Re-read the original status immediately before claiming a resend.
                message=api.message(mid);messages[mid]=message
                if status(message)!='failed':continue
                try:
                    if templates is None:templates=api.templates()
                    payload=retry_payload(message,templates)
                    # Kapso contact lookup uses its stored identifier, which may be local-format.
                    # Normalize only the wire send address; do not change the preference identity.
                    if not api.marketing_allowed(str(message['to'])):raise ValueError('Contact stopped marketing messages')
                except ValueError:
                    held+=1;continue
                # Persist BEFORE POST. Any crash/timeout is held; never replay a possibly sent message.
                # Two source attempts can trigger identical hub messages. Resend that case only once.
                group=(indian_recipient(message.get('to')),message.get('template',{}).get('name'),moment_of(message).date())
                peers=[m for m in linked if m in messages and (indian_recipient(messages[m].get('to')),messages[m].get('template',{}).get('name'),moment_of(messages[m]).date())==group]
                if any(status(messages[m]) in ('read','delivered') for m in peers):continue
                existing=next((retries[m] for m in peers if m in retries),None)
                if existing:
                    for m in peers:retries[m]=dict(existing)
                    save_state(PREFIX+'retries',retries);continue
                claim={'state':'sending','claimed_at':now(),'attempts':1,'original_message_id':mid}
                for m in peers:retries[m]=dict(claim)
                save_state(PREFIX+'retries',retries)
                try:
                    response=api.send(payload);newid=response.get('messages',[{}])[0].get('id')
                    if not newid:raise ValueError('Resend returned no message ID')
                    retries[mid].update(state='sent',message_id=newid,updated_at=now());resent+=1
                except Exception:
                    retries[mid].update(state='uncertain',updated_at=now());held+=1
                for m in peers:retries[m]=dict(retries[mid])
                save_state(PREFIX+'retries',retries)
        # A newly accepted resend still awaits a real receipt. Never mark it delivered here.
        for retry in retries.values():
            mid=retry.get('message_id')
            if mid and mid not in messages:messages[mid]=api.message(mid)
        projected=projection(entries,messages,retries,calls)
        save_state(PREFIX+'receipts',{'updated_at':now(),'by_call':projected,'messages':messages})
        changed=write_report(projected) if projected else 0
        return {'state':'ready','updated_at':now(),'linked_calls':len(projected),'delivered':sum(s=='Delivered' for s in projected.values()),
                'not_delivered':sum(s=='Not Delivered' for s in projected.values()),'pending':sum(not s for s in projected.values()),
                'report_cells_updated':changed,'resent':resent,'held_resends':held,'saved_sequence_runs':len(entries)}
    finally:api.close()


def tick(browser_ready=False):
    global _next,_scan_next,_pending
    from insightdesk_bridge import get_value,PRIVATE,command,atomic_json,now
    if os.environ.get('BHAGO_RUNTIME')!='cloud':return
    config=json.loads(get_value(PREFIX+'config') or '{}')
    if not config.get('enabled'):return
    if _pending:
        file=PRIVATE/'jobs'/(_pending['id']+'.whatsapp.json')
        if file.exists():
            result=json.loads(file.read_text());links=load_state(PREFIX+'links') or {}
            for e in result.get('entries',[]):
                if e.get('execution_status') in ('Completed','Failed'):links[e['execution_id']]=e
            save_state(PREFIX+'links',links);file.unlink();_pending=None
            _scan_next=time.monotonic()+(5 if result.get('more') else 120);_next=0
            if result.get('state')!='ready':save_state(PREFIX+'scan_status',{'state':'needs_attention','updated_at':now()})
            else:save_state(PREFIX+'scan_status',{'state':'ready','updated_at':now(),'runs':len(links),'more':result.get('more',False),
                'observed_statuses':sorted({e.get('execution_status','') for e in result.get('entries',[])}),
                'parsed_calls':sum(bool(e.get('call_id')) for e in result.get('entries',[]))})
        elif time.monotonic()-_pending['started']>150:
            _pending=None;_scan_next=time.monotonic()+120
            save_state(PREFIX+'scan_status',{'state':'needs_attention','updated_at':now(),'message':'WhatsApp sequence scan timed out'})
    if time.monotonic()>=_next:
        _next=time.monotonic()+120
        try:save_state(PREFIX+'status',sync(config))
        except Exception as error:
            save_state(PREFIX+'status',{'state':'needs_attention','updated_at':now(),'error_type':type(error).__name__,
                                      'message':str(error)[:250] if isinstance(error,ValueError) else 'Kapso sync could not complete; no send was replayed'})
    if browser_ready and not _pending and time.monotonic()>=_scan_next:
        ident=uuid.uuid4().hex;links=load_state(PREFIX+'links') or {}
        atomic_json(PRIVATE/'jobs'/(ident+'.whatsapp-request.json'),{'known':list(links)})
        if command({'action':'campaign_step','stage':'insightdesk_whatsapp_history','requestId':ident}):
            _pending={'id':ident,'started':time.monotonic()}
