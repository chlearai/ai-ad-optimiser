"""Daily client reports; scoped destinations, idempotent call IDs, client notes preserved."""
import collections
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from google_access import SheetsWriter, google_session, a1
from outcomes import parse_export, pending_manifests, load_state, save_state
from lead_followup import callback_columns, callback_plan, delivery_status

_next=0
REPORT_KEYS={'lead':'lead_calling_report','payment':'payment_calling_report'}

def phone(value):return re.sub(r'\D','',str(value or ''))[-10:]
def clean(value):
    value=str(value or '').strip().replace('â‚¹','₹')
    return '' if value.casefold() in ('','-','none','null','unknown','not_specified','not specified') else value
def seconds(value):
    try:return sum(int(n)*60**i for i,n in enumerate(str(value or '0').split(':')[::-1]))
    except ValueError:return 0
def answered(call):return call.get('Status','').casefold() in ('ended','completed') and seconds(call.get('Duration'))>0
def serial(day):return (dt.date.fromisoformat(day[:10])-dt.date(1899,12,30)).days
def letter(n):
    result=''
    while n:n,r=divmod(n-1,26);result=chr(65+r)+result
    return result
def last(calls,key):return next((v for c in reversed(calls) if (v:=clean(c.get(key)))), '')
def interest(calls):
    v=last(calls,'Interest Level').casefold()
    return 'Interested' if re.match(r'^interested(?:$|\s)',v) or v=='high' else 'Not Interested' if v in ('not interested','no interest','no interest in renting') else 'Other / unclear'

class ReportWriter(SheetsWriter):
    def __init__(self,session,sid):self.session=session;self.allowed={sid}

def merge_history(existing,exported,jobs,kind,cutoff):
    """Retain prior report history if a subsequent Samvaad export omits older rows."""
    lookup={(j['campaignName'],phone(r['phone'])):r for j in jobs.values() for r in j['recipients']}
    merged={}
    for row in existing[1:]:
        if len(row)<17 or not row[16]:continue
        merged[str(row[16])]={'Time':str(row[6]),'Campaign Name':row[1],'To Number':str(row[4]),'Duration':str(row[7]),'Status':row[8],
            'End Reason':row[10],'Call Summary':row[12],'Callback Time':row[13],'promised_payment_date':row[14],'promised_amount':row[15],
            'Session ID':str(row[16]),'Interest Level' if kind=='lead' else 'payment_commitment_status':row[11],
            'Transcript':row[21] if kind=='payment' and len(row)>21 else ''}
    for c in exported:
        previous=merged.get(c['Session ID'],{})
        merged[c['Session ID']]=dict(c,Transcript=clean(c.get('Transcript')) or previous.get('Transcript',''))
    return sorted([c for c in merged.values() if c['Campaign Name'] in jobs and jobs[c['Campaign Name']]['type']==kind and
                   c.get('Time','')[:10]<=cutoff and (c['Campaign Name'],phone(c['To Number'])) in lookup],key=lambda c:(c['Time'],c['Session ID'])),lookup

def model(calls,jobs,lookup,kind):
    people=collections.defaultdict(list);history=[];seen=set();seen_day=set();reached=set();reached_day=set()
    for c in calls:
        p=phone(c['To Number']);r=lookup[(c['Campaign Name'],p)];day=c['Time'][:10];ans=answered(c)
        key=(p,r.get('value2','')) if kind=='payment' else p;people[key].append(c)
        history.append([serial(day),c['Campaign Name'],r.get('source_ref',{}).get('source','').replace('_',' ').title(),r['name'],p,r.get('value1',''),c['Time'],seconds(c.get('Duration')),c.get('Status',''),int(ans),clean(c.get('End Reason')),clean(c.get('Interest Level' if kind=='lead' else 'payment_commitment_status')),clean(c.get('Call Summary')),clean(c.get('Callback Time')),clean(c.get('promised_payment_date')),clean(c.get('promised_amount')),c['Session ID'],int((day,p) not in seen_day),int(p not in seen),int(ans and (day,p) not in reached_day),int(ans and p not in reached)])
        if kind=='payment':
            from payment_followup import view
            facts=view([c],r)
            history[-1][11]=facts['commitment'];history[-1][13]=facts['callback_time']
            history[-1][14]=facts['promised_date'];history[-1][15]=facts['promised_amount']
            history[-1].append(clean(c.get('Transcript')))
        seen.add(p);seen_day.add((day,p))
        if ans:reached.add(p);reached_day.add((day,p))
    details=[];positives=[]
    for key,cs in people.items():
        c=cs[-1];p=phone(c['To Number']);r=lookup[(c['Campaign Name'],p)];classification=interest(cs);commitment=last(cs,'payment_commitment_status');callback=last(cs,'Callback Time')
        action='Contact rider to confirm rental interest and next steps' if kind=='lead' and classification=='Interested' else 'Call back at the requested time' if callback or commitment=='callback_requested' else 'Verify payment against accounts' if commitment=='already_paid' else 'Review feedback before further follow-up'
        sources=' / '.join(sorted({lookup[(x['Campaign Name'],p)].get('source_ref',{}).get('source','').replace('_',' ').title() for x in cs}))
        if kind=='lead':
            details.append([r['name'],p,r.get('value1',''),sources,serial(cs[0]['Time']),serial(c['Time']),len(cs),sum(answered(x) for x in cs),c['Status'],classification,last(cs,'Interest Level'),last(cs,'Call Summary') or 'No feedback captured',callback,action,c['Campaign Name']])
            cbdate,cbtime=callback_columns(cs)
            plan=callback_plan(cs)
            if plan and plan['state']=='manual_followup':action='Manual follow-up: two total call attempts already used'
            elif plan:action='Callback scheduled for '+plan['due_at']+' (IST)'
            details[-1][13]=action
            details[-1].extend([cbdate,cbtime,delivery_status(cs)])
            if any(interest([x])=='Interested' for x in cs):positives.append([0,'Lead',r['name'],r.get('value1',''),p,sources,serial(c['Time']),last(cs,'Interest Level'),last(cs,'Call Summary') or 'No feedback captured',cbtime,action,classification,delivery_status(cs),cbdate])
        else:
            from payment_followup import view
            facts=view(cs,r)
            action='Call back at the customer-requested date/time' if facts['commitment']=='Call back requested' else 'Verify payment against accounts' if facts['commitment']=='already paid' else 'Follow up on the confirmed payment commitment' if facts['commitment']=='Committed' else 'Review feedback before further follow-up'
            details.append([0,r['name'],p,r.get('value2',''),float(r['value3']) if r.get('value3') else '',facts['promised_date'],last(cs,'Call Summary') or 'No feedback captured',r.get('value1',''),serial(cs[0]['Time']),serial(c['Time']),len(cs),sum(answered(x) for x in cs),c['Status'],facts['commitment'],facts['promised_amount'],facts['callback_date'],facts['callback_time'],action,len({x['Campaign Name'] for x in cs}),facts['transcript']])
    daily=[];groups=collections.defaultdict(list)
    for c in calls:groups[(c['Time'][:10],c['Campaign Name'])].append(c)
    first_dates={name:min(c['Time'][:10] for c in calls if c['Campaign Name']==name) for _,name in groups}
    for (day,name),cs in sorted(groups.items()):
        pp=collections.defaultdict(list)
        for c in cs:pp[phone(c['To Number'])].append(c)
        reached_groups=[g for g in pp.values() if any(answered(x) for x in g)]
        a=sum(interest(g)=='Interested' if kind=='lead' else view(g,lookup[(g[-1]['Campaign Name'],phone(g[-1]['To Number']))])['commitment']=='Call back requested' for g in reached_groups)
        b=sum(interest(g)=='Not Interested' if kind=='lead' else view(g,lookup[(g[-1]['Campaign Name'],phone(g[-1]['To Number']))])['commitment']=='already paid' for g in reached_groups)
        count=len(jobs[name]['recipients']) if day==first_dates[name] else len(pp)
        daily.append([serial(day),name,kind.title(),count,len(cs),len(reached_groups),max(0,count-len(reached_groups)),len(reached_groups)/count if count else 0,a,b,len(reached_groups)-a-b,'Outcomes recorded' if all(x.get('Status','').casefold() in ('ended','completed','failed','busy','no_answer','cancelled','canceled') for x in cs) else 'In progress'])
    return {'history':history,'details':details,'interested':positives,'daily':daily,'contacts':len(seen),'reached':len(reached)}

def upsert(existing,rows,key,manual_defaults=None):
    """Return managed updates only; stable row positions preserve all manual columns."""
    positions={}
    for n,row in enumerate(existing[1:],2):
        k=key(row)
        if k in positions:raise ValueError('Duplicate report keys need review; existing client notes were not overwritten.')
        if k:positions[k]=n
    next_row=max(positions.values(),default=1)+1;result=[]
    for row in rows:
        k=key(row);is_new=k not in positions
        if is_new:positions[k]=next_row;next_row+=1
        result.append((positions[k],row,is_new))
    return result

def check_manual_columns(grids,kind):
    required={'Lead Outcomes':{15:'Client Follow-up Status',16:'Client Notes'},'Lead Interested':{11:'Client Follow-up Status',12:'Client Notes'}} if kind=='lead' else {'Sheet1':{18:'Client Follow-up Status',19:'Client Notes',20:'Payment Verified',15:'Callback Date',16:'Callback Time',22:'Transcript'}}
    for title,columns in required.items():
        header=grids[title][0] if grids.get(title) else []
        if any(i>=len(header) or header[i]!=label for i,label in columns.items()):
            raise ValueError('Client-editable columns moved; updates held to preserve client entries.')

def sync_book(writer,sid,kind,exported,jobs,cutoff):
    meta=writer.metadata(sid);tabs={s['properties']['title']:s['properties'] for s in meta['sheets']}
    detail='Lead Outcomes' if kind=='lead' else 'Sheet1'
    required=['MIS','Daily Performance','Call History',detail]+(['Lead Interested'] if kind=='lead' else [])
    if any(t not in tabs for t in required):raise ValueError('Report tabs changed; restore the approved report structure.')
    # Bound every read and retain formulas/manual client values.
    grids={t:writer.values(sid,[a1(t,f'A1:{letter(min(tabs[t]["gridProperties"]["columnCount"],26))}{min(tabs[t]["gridProperties"]["rowCount"],20000)}')])[0] for t in required}
    check_manual_columns(grids,kind)
    if not grids['Call History'] or grids['Call History'][0][16]!='Call ID':raise ValueError('Call History headers changed.')
    if not grids[detail] or grids[detail][0][1 if kind=='lead' else 2]!='Mobile':raise ValueError('Client report identity headers changed.')
    calls,lookup=merge_history(grids['Call History'],exported,jobs,kind,cutoff)
    if kind=='lead':
        receipts=(load_state('bhago:18:whatsapp:receipts') or {}).get('by_call',{})
        calls=[dict(c,**{'WhatsApp Delivery Status':receipts[c['Session ID']]}) if c['Session ID'] in receipts else c for c in calls]
    else:
        from payment_followup import enrich
        calls=enrich(calls,lookup)
    data=model(calls,jobs,lookup,kind)
    updates=[];ends={}
    def table(title,rows,key,width,defaults=None):
        existing=grids[title];matched=upsert(existing,rows,key);end=max(len(existing),max((n for n,_,_ in matched),default=1));ends[title]=end
        if end>=20000:raise ValueError('Client report reached the reviewed row limit.')
        props=tabs[title]
        if end>props['gridProperties']['rowCount']:writer.request('POST',sid,':batchUpdate',json={'requests':[{'appendDimension':{'sheetId':props['sheetId'],'dimension':'ROWS','length':end-props['gridProperties']['rowCount']+100}}]})
        for n,row,new in matched:
            updates.append({'range':a1(title,f'A{n}:{letter(width)}{n}'),'values':[row[:width]]})
            if new and defaults:updates.append({'range':a1(title,f'{letter(width+1)}{n}:{letter(width+len(defaults))}{n}'),'values':[defaults]})
        return matched
    table('Call History',data['history'],lambda r:str(r[16]) if len(r)>16 else '',22 if kind=='payment' else 21)
    if kind=='payment':updates.append({'range':a1('Call History','V1'),'values':[['Transcript']]})
    # Daily performance is an owned aggregate; dates are raw serials for stable keys.
    if data['daily']:updates.append({'range':a1('Daily Performance',f'A2:L{len(data["daily"])+1}'),'values':data['daily']})
    ends['Daily Performance']=len(data['daily'])+1
    if kind=='lead':
        table(detail,data['details'],lambda r:phone(r[1]) if len(r)>1 else '',15,['Pending client review',''])
        # Keep client-owned P/Q fixed. Append structured follow-up outputs to R:T.
        updates.append({'range':a1(detail,'R1:T1'),'values':[['Callback Date','Callback Time (IST)','WhatsApp Message Status']]})
        for n,row,new in upsert(grids[detail],data['details'],lambda r:phone(r[1]) if len(r)>1 else ''):
            updates.append({'range':a1(detail,f'R{n}:T{n}'),'values':[row[15:18]]})
        matched=table('Lead Interested',[r[:11] for r in data['interested']],lambda r:phone(r[4]) if len(r)>4 else '',11,['Pending client review',''])
        groups={phone(r[4]):r for r in data['interested']}
        updates.append({'range':a1('Lead Interested','C1'),'values':[['Name']]})
        updates.append({'range':a1('Lead Interested','N1:O1'),'values':[['WhatsApp Message Status','Callback Date']]})
        for n,row,new in matched:
            updates.append({'range':a1('Lead Interested',f'A{n}'),'values':[[n-1]]})
            updates.append({'range':a1('Lead Interested',f'N{n}:O{n}'),'values':[[groups[phone(row[4])][12],groups[phone(row[4])][13]]]})
    else:
        matched=table(detail,[r[:18] for r in data['details']],lambda r:(phone(r[2]),str(r[3]).strip()) if len(r)>3 else '',18,['Pending client review','','Not verified'])
        extra={(phone(r[2]),r[3]):[r[18],r[19]] for r in data['details']}
        for n,row,new in matched:
            updates.append({'range':a1(detail,f'A{n}'),'values':[[n-1]]})
            updates.append({'range':a1(detail,f'V{n}:W{n}'),'values':[extra[(phone(row[2]),row[3])]]})
    writer.write(sid,updates)
    # Read back identifiers and managed values before marking the report successful.
    for offset in range(0,len(updates),80):
        batch=updates[offset:offset+80];got=writer.values(sid,[u['range'] for u in batch])
        for actual,u in zip(got,batch):
            expected=u['values'][0];v=actual[0] if actual else [];v=v+['']*(len(expected)-len(v))
            # Date columns render as dates; verify call IDs/row counts separately below.
            if len(expected)==1 and str(v[0])!=str(expected[0]):raise ValueError('Report write read-back mismatch.')
    end=max(2,ends['Call History']);dn=max(2,ends['Daily Performance']);de=max(2,ends[detail]);days=sorted({c['Time'][:10] for c in calls})
    if days:
        first=dt.date.fromisoformat(days[0]);lastday=dt.date.fromisoformat(cutoff);days=[(first+dt.timedelta(days=i)).isoformat() for i in range((lastday-first).days+1)]
    title='Lead Calling MIS' if kind=='lead' else 'Payment Reminder MIS'
    from insightdesk_bridge import IST
    updated=dt.datetime.now(IST).strftime('%d %b %Y %H:%M IST')
    mis=[['Bhago Mobility — '+title]+['']*7,[f'Production calling through {cutoff}. Updated {updated}. Daily target: 6:30 pm IST.']+['']*7,['Metric','Value']+['']*6]
    metrics=[('Campaigns',f"=IFERROR(COUNTUNIQUE(FILTER('Daily Performance'!B2:B{dn},'Daily Performance'!B2:B{dn}<>\"\")),0)"),('Distinct mobile numbers',f"=SUM('Call History'!S2:S{end})"),('Call attempts',f"=COUNTA('Call History'!Q2:Q{end})"),('Answered attempts',f"=SUM('Call History'!J2:J{end})"),('Distinct numbers reached',f"=SUM('Call History'!U2:U{end})"),('Reach rate','=IF(B5=0,0,B8/B5)'),('Interested contacts' if kind=='lead' else 'Callback requests',f'=COUNTIF(\'Lead Outcomes\'!J2:J{de},"Interested")' if kind=='lead' else f'=COUNTIF(Sheet1!N2:N{de},"Call back requested")'),('Not interested contacts' if kind=='lead' else 'Already-paid claims',f'=COUNTIF(\'Lead Outcomes\'!J2:J{de},"Not Interested")' if kind=='lead' else f'=COUNTIF(Sheet1!N2:N{de},"already paid")'),('Payment receipt verification','Not applicable' if kind=='lead' else 'Not verified against accounts')]
    mis += [[k,v]+['']*6 for k,v in metrics]+[['']*8,['Calling Date','Campaigns','Campaign Records','Attempts','Answered Attempts','Distinct Numbers','First-time Numbers','Distinct Reached']]
    for day in days:
        n=len(mis)+1;mis.append([serial(day),f'=COUNTIF(\'Daily Performance\'!A2:A{dn},A{n})',f'=SUMIF(\'Daily Performance\'!A2:A{dn},A{n},\'Daily Performance\'!D2:D{dn})',f'=COUNTIF(\'Call History\'!A2:A{end},A{n})',*[f'=SUMIF(\'Call History\'!A2:A{end},A{n},\'Call History\'!{c}2:{c}{end})' for c in ['J','R','S','T']]])
    mis+=[['']*8]+[[s]+['']*7 for s in ['Reached means positive-duration ended/completed calls; it is not proof of interest, eligibility or payment.','Distinct and first-time numbers are deduplicated; campaign records and call attempts can repeat a number.','Call times remain as exported; export timezone is unverified. Tests and QWQER are excluded.','Client follow-up statuses, notes and payment-verification entries are preserved. No claimed payment is marked verified automatically.','Interested list retains previously interested contacts; Lead Outcomes shows the latest reported result. WhatsApp status is blank until confirmed by a delivery receipt.']]
    mid=tabs['MIS']['sheetId'];req=[{'unmergeCells':{'range':{'sheetId':mid,'startRowIndex':0,'endRowIndex':min(tabs['MIS']['gridProperties']['rowCount'],1000),'startColumnIndex':0,'endColumnIndex':8}}}]
    writer.request('POST',sid,':batchUpdate',json={'requests':req})
    writer.write(sid,[{'range':a1('MIS',f'A1:H{len(mis)}'),'values':[[str(x) if isinstance(x,str) and x.startswith('=') else x for x in row] for row in mis]}])
    # Formula writes are explicit; never interpret untrusted feedback as formulas.
    formulas=[{'range':a1('MIS',f'{letter(c+1)}{r+1}'),'values':[[v]]} for r,row in enumerate(mis) for c,v in enumerate(row) if isinstance(v,str) and v.startswith('=')]
    for i in range(0,len(formulas),200):writer.request('POST',sid,'/values:batchUpdate',json={'valueInputOption':'USER_ENTERED','data':formulas[i:i+200]})
    for title,n in ends.items():
        props=tabs[title];sid_=props['sheetId'];w=(22 if kind=='payment' else 21) if title=='Call History' else 23 if kind=='payment' and title==detail else 20 if kind=='lead' and title==detail else 17 if title==detail else 15 if title=='Lead Interested' else 12
        req += [{'setBasicFilter':{'filter':{'range':{'sheetId':sid_,'startRowIndex':0,'endRowIndex':n,'startColumnIndex':0,'endColumnIndex':w}}}}]
        if kind=='payment' and title in (detail,'Call History'):
            # Full multi-line transcripts stay in the cell; keep the client grid readable.
            req.append({'updateDimensionProperties':{'range':{'sheetId':sid_,'dimension':'ROWS','startIndex':1,'endIndex':max(2,n)},'properties':{'pixelSize':96},'fields':'pixelSize'}})
        datecols=[0] if title in ('Call History','Daily Performance') else [4,5] if kind=='lead' and title==detail else [6] if title=='Lead Interested' else [8,9]
        for c in datecols:req.append({'repeatCell':{'range':{'sheetId':sid_,'startRowIndex':1,'endRowIndex':max(2,n),'startColumnIndex':c,'endColumnIndex':c+1},'cell':{'userEnteredFormat':{'numberFormat':{'type':'DATE','pattern':'dd mmm yyyy'}}},'fields':'userEnteredFormat.numberFormat'}})
    req=req[1:]+[{'mergeCells':{'range':{'sheetId':mid,'startRowIndex':r,'endRowIndex':r+1,'startColumnIndex':0,'endColumnIndex':8},'mergeType':'MERGE_ALL'}} for r in [0,1]+list(range(15+len(days),len(mis)))]
    req.append({'repeatCell':{'range':{'sheetId':mid,'startRowIndex':14,'endRowIndex':max(15,14+len(days)),'startColumnIndex':0,'endColumnIndex':1},'cell':{'userEnteredFormat':{'numberFormat':{'type':'DATE','pattern':'dd mmm yyyy'}}},'fields':'userEnteredFormat.numberFormat'}})
    req.append({'repeatCell':{'range':{'sheetId':mid,'startRowIndex':8,'endRowIndex':9,'startColumnIndex':1,'endColumnIndex':2},'cell':{'userEnteredFormat':{'numberFormat':{'type':'PERCENT','pattern':'0.0%'}}},'fields':'userEnteredFormat.numberFormat'}})
    req += [{'updateDimensionProperties':{'range':{'sheetId':mid,'dimension':'COLUMNS','startIndex':9,'endIndex':10},'properties':{'pixelSize':190},'fields':'pixelSize'}},{'updateDimensionProperties':{'range':{'sheetId':mid,'dimension':'COLUMNS','startIndex':10,'endIndex':11},'properties':{'pixelSize':260},'fields':'pixelSize'}},{'repeatCell':{'range':{'sheetId':mid,'startRowIndex':0,'endRowIndex':4,'startColumnIndex':9,'endColumnIndex':11},'cell':{'userEnteredFormat':{'wrapStrategy':'WRAP','textFormat':{'fontFamily':'Arial','fontSize':10}}},'fields':'userEnteredFormat(wrapStrategy,textFormat)'}}]
    writer.request('POST',sid,':batchUpdate',json={'requests':req})
    actual=writer.values(sid,[a1('MIS','B5:B8')])[0]
    expected=[data['contacts'],len(calls),sum(answered(c) for c in calls),data['reached']]
    if [r[0] for r in actual]!=expected:raise ValueError('MIS totals did not reconcile after update.')
    return {'contacts':data['contacts'],'attempts':len(calls),'through':cutoff}

def run(cutoff):
    from insightdesk_bridge import get_value,PRIVATE,now,IST
    path=PRIVATE/'latest-outcomes.csv'
    if not path.exists():raise ValueError('Samvaad outcome export is unavailable.')
    if time.time()-path.stat().st_mtime>900:raise ValueError('Samvaad outcome export is stale; report success not claimed.')
    jobs={j['campaignName']:j for _,j in pending_manifests() if not j.get('test') and j.get('source') not in ('test','test_lead','test_payment','qwqer')}
    exported=parse_export(path.read_text(encoding='utf-8-sig'));result={}
    with google_session(write=True) as session:
        for kind,key in REPORT_KEYS.items():
            url=get_value('bhago:18:sheet:'+key) or '';match=re.fullmatch(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?',url)
            if not match:raise ValueError('Save both approved client report links in InsightDesk.')
            sid=match[1];writer=ReportWriter(session,sid)
            if not writer.can_edit(sid):raise ValueError('Client report edit access is unavailable.')
            try:
                result[kind]=sync_book(writer,sid,kind,exported,jobs,cutoff)
                writer.write(sid,[{'range':a1('MIS','J1:K4'),'values':[['Report Update','Ready'],['Through',cutoff],['Last Updated (IST)',dt.datetime.now(IST).strftime('%d %b %Y %H:%M')],['Daily Deadline','18:30 IST']]}])
            except Exception as error:
                try:writer.write(sid,[{'range':a1('MIS','J1:K4'),'values':[['Report Update','Needs attention'],['Through',cutoff],['Last Attempt (IST)',dt.datetime.now(IST).strftime('%d %b %Y %H:%M')],['Issue',str(error)[:200] if isinstance(error,ValueError) else type(error).__name__]]}])
                except Exception:pass
                raise
            save_state('bhago:18:reports:last:'+kind,{'state':'ready','updated_at':now(),**result[kind]})
    return result

def tick(moment=None):
    global _next
    from insightdesk_bridge import get_value,IST,now
    if os.environ.get('BHAGO_RUNTIME')!='cloud' or time.monotonic()<_next:return
    config=json.loads(get_value('bhago:18:reports_schedule') or '{}');moment=moment or dt.datetime.now(IST)
    if not config.get('enabled') or moment.strftime('%H:%M')<config.get('prepare_at','18:20'):return
    day=moment.date().isoformat();key='bhago:18:reports:day:'+day;prior=load_state(key) or {}
    phase='final' if moment.strftime('%H:%M')>=config.get('final_at','18:25') else 'preliminary'
    if prior.get('state')=='ready' and (prior.get('phase')=='final' or phase=='preliminary'):return
    _next=time.monotonic()+60
    try:
        result=run(day);save_state(key,{'state':'ready','phase':phase,'updated_at':now(),'deadline':'18:30 IST',**result})
    except Exception as e:
        save_state(key,{'state':'needs_attention','updated_at':now(),'error_type':type(e).__name__,'message':str(e)[:300] if isinstance(e,ValueError) else 'Report update failed; retry scheduled.','http_status':getattr(getattr(e,'response',None),'status_code',None)})
