"""Idempotent call-history reconciliation and exact-row Google result writes."""
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from openpyxl.utils import get_column_letter
from google_access import SheetsWriter, google_session, a1

COMMON = ['Call ID','Campaign Name','Called At (IST)','Call Status','Duration (seconds)',
          'Outcome','Call Summary','Callback At','Recording Link']
EXTRA = {'lead':['Interest Level','Qualified For Followup'],
         'payment':['Payment Commitment','Promised Payment Date','Promised Amount']}
EMPTY = {'', '-', 'null', 'none', 'unknown'}
TERMINAL = {'ended','completed','failed','busy','no-answer','no_answer','unanswered','cancelled','canceled','not_connected','not connected'}
CALL_FIELDS = {'Session ID','Campaign Name','Time','Status','Duration','Outcome','Call Summary',
               'Callback Time','Recording URL','Interest Level','qualified_for_followup',
               'call_disposition','payment_commitment_status','promised_payment_date','promised_amount'}


def merge_calls(prior, current):
    """Retain only approved outcome fields; Session IDs deduplicate across campaigns/days."""
    found={r['Session ID']:{k:v for k,v in r.items() if k in CALL_FIELDS} for r in prior+current}
    return sorted(found.values(),key=lambda r:(r['Time'],r['Session ID']))


def calls_complete(calls, kind):
    return len(calls)>=2 or any(c.get('Status','').casefold() in ('ended','completed') and
        result_values(c,kind)['Duration (seconds)'] not in (0,'') for c in calls)


def clean(value):
    value = str(value or '').strip()
    return '' if value.casefold() in EMPTY else value


def parse_export(text):
    """Repair only the observed unquoted dynamic 'city, Area, Town' header."""
    records = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    if not records:
        return []
    headers = records.pop(0)
    widths = {len(r) for r in records if r}
    if widths and widths != {len(headers)}:
        for i in range(len(headers)-2):
            if [h.strip().casefold() for h in headers[i:i+3]] == ['city','area','town'] and widths == {len(headers)-2}:
                headers[i:i+3] = ['city, Area, Town']
                break
        if widths != {len(headers)}:
            raise ValueError('Samvaad export columns changed; no results will be mapped until reviewed.')
    rows = []
    for fields in records:
        if not fields:
            continue
        row = dict(zip(headers, fields))
        if row.get('Direction','').casefold() != 'outbound':
            continue
        if not clean(row.get('Session ID')) or not clean(row.get('Campaign Name')):
            continue
        # Only the approved outbound callers; inbound/support records are never written.
        if re.sub(r'\D','',row.get('From Number','')) not in ('918069458173','918069458175'):
            continue
        rows.append(row)
    return rows


def result_values(row, kind):
    minutes = str(row.get('Duration','0')).split(':')
    try:
        seconds = sum(int(n) * 60**i for i,n in enumerate(reversed(minutes)))
    except ValueError:
        seconds = ''
    outcome = clean(row.get('Interest Level')) if kind == 'lead' else clean(row.get('call_disposition')) or clean(row.get('payment_commitment_status'))
    values = {'Call ID':clean(row.get('Session ID')), 'Campaign Name':clean(row.get('Campaign Name')),
              'Called At (IST)':clean(row.get('Time')), 'Call Status':clean(row.get('Status')),
              'Duration (seconds)':seconds,'Outcome':outcome or clean(row.get('Outcome')),
              'Call Summary':clean(row.get('Call Summary')), 'Callback At':clean(row.get('Callback Time')),
              'Recording Link':clean(row.get('Recording URL'))}
    if kind == 'lead':
        values.update({'Interest Level':clean(row.get('Interest Level')),
                       'Qualified For Followup':clean(row.get('qualified_for_followup'))})
    else:
        # An outstanding amount is NOT a promised amount, and a paid claim is not proof of payment.
        values.update({'Payment Commitment':clean(row.get('payment_commitment_status')),
                       'Promised Payment Date':clean(row.get('promised_payment_date')),
                       'Promised Amount':clean(row.get('promised_amount'))})
    return values


def columns(kind):
    return ['Bhago Row ID'] + [f'Attempt {i} - {field}' for i in (1,2) for field in COMMON+EXTRA[kind]] + ['Bhago Last Synced At']


def schema(writer, ref, kind):
    sid = ref['spreadsheet_id']
    cache=getattr(writer,'_bhago_schemas',{})
    cache_key=(sid,ref['sheet_id'],kind)
    if cache_key in cache:return cache[cache_key]
    if not writer.can_edit(sid):
        raise ValueError('This source has no Editor access; no campaign will be started.')
    meta = writer.metadata(sid)
    sheet = next((s for s in meta['sheets'] if s['properties']['sheetId'] == ref['sheet_id']), None)
    if not sheet:
        raise ValueError('Source tab no longer exists.')
    props = sheet['properties']; title = props['title']; width = props['gridProperties']['columnCount']
    headers = writer.values(sid, [a1(title,f'A1:{get_column_letter(width)}1')])[0]
    headers = list(headers[0]) if headers else []
    if len(set(h for h in headers if h)) != len([h for h in headers if h]):
        raise ValueError('Duplicate source column names need review before result updates.')
    missing = [c for c in columns(kind) if c not in headers]
    targets=[i for i,h in enumerate(headers) if h in columns(kind)]+list(range(width,width+len(missing)))
    for protection in sheet.get('protectedRanges', []):
        area=protection.get('range',{})
        if protection.get('requestingUserCanEdit') is False and any(area.get('startColumnIndex',0)<=i<area.get('endColumnIndex',width+len(missing)) for i in targets):
            raise ValueError('The result columns are protected against this Google account; calling is held for review.')
    if missing:
        # Append after the existing grid: no source formula, value or blank-header column is overwritten.
        start = width
        writer.request('POST', sid, ':batchUpdate', json={'requests':[{'appendDimension':{'sheetId':ref['sheet_id'],'dimension':'COLUMNS','length':len(missing)}}]})
        writer.write(sid,[{'range':a1(title,f'{get_column_letter(start+1)}1:{get_column_letter(start+len(missing))}1'),'values':[missing]}])
        headers += ['']*(start-len(headers)) + missing
        # Format only the added output area; client columns and row heights stay untouched.
        writer.request('POST',sid,':batchUpdate',json={'requests':[
            {'repeatCell':{'range':{'sheetId':ref['sheet_id'],'startRowIndex':0,'endRowIndex':1,'startColumnIndex':start,'endColumnIndex':start+len(missing)},'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.07,'green':0.2,'blue':0.3},'textFormat':{'bold':True,'fontSize':10,'foregroundColor':{'red':1,'green':1,'blue':1}},'wrapStrategy':'CLIP'}},'fields':'userEnteredFormat'}},
            {'updateDimensionProperties':{'range':{'sheetId':ref['sheet_id'],'dimension':'COLUMNS','startIndex':start,'endIndex':start+len(missing)},'properties':{'pixelSize':280},'fields':'pixelSize'}}]})
    result=title, {str(h):i for i,h in enumerate(headers) if h}
    cache[cache_key]=result;writer._bhago_schemas=cache
    return result


def identity_matches(row, recipient, indices):
    from insightdesk_bridge import mobile, normalized
    try:
        if mobile(row[indices[0]]) != recipient['phone'] or normalized(row[indices[1]]) != normalized(recipient['name']):
            return False
        if recipient.get('sourceDate'):
            from insightdesk_bridge import lead_date
            if indices[3] is None or lead_date(row[indices[3]]).isoformat()!=recipient['sourceDate']:
                return False
        return True
    except (ValueError, IndexError):
        return False


def bind_rows(recipients, kind, writer):
    """Assign a durable row UUID before dialing, with re-read identity checks."""
    from insightdesk_bridge import field_indices
    grouped = collections.defaultdict(list)
    for recipient in recipients:
        ref = recipient.get('source_ref')
        if not ref or ref.get('source') == 'qwqer':
            raise ValueError('Source-row provenance is missing or suspended. Reread the source before calling.')
        grouped[(ref['spreadsheet_id'],ref['sheet_id'])].append(recipient)
    for group in grouped.values():
        ref=group[0]['source_ref']; title, mapping=schema(writer,ref,kind); width=max(mapping.values())+1
        ranges=[a1(title,f'A{r["source_ref"]["row"]}:{get_column_letter(width)}{r["source_ref"]["row"]}') for r in group]
        current=writer.values(ref['spreadsheet_id'],ranges)
        headers=writer.values(ref['spreadsheet_id'],[a1(title,f'A1:{get_column_letter(width)}1')])[0][0]
        indices=field_indices(headers,{'source':ref['source'],'calling_type':kind})
        updates=[]
        idcol=get_column_letter(mapping['Bhago Row ID']+1)
        props=next(s['properties'] for s in writer.metadata(ref['spreadsheet_id'])['sheets'] if s['properties']['sheetId']==ref['sheet_id'])
        ids=writer.values(ref['spreadsheet_id'],[a1(title,f'{idcol}2:{idcol}{props["gridProperties"]["rowCount"]}')])[0]
        counts=collections.Counter(str(r[0]) for r in ids if r and r[0])
        if any(n>1 for n in counts.values()):raise ValueError('Duplicate Bhago Row IDs must be corrected before calling.')
        for recipient, data in zip(group,current):
            row=data[0] if data else []
            if not identity_matches(row,recipient,indices):
                raise ValueError('A source row moved or changed before dialing; no campaign was started.')
            col=mapping['Bhago Row ID']; old=str(row[col]).strip() if len(row)>col else ''
            if old and not re.fullmatch(r'[a-f0-9]{32}',old):
                raise ValueError('Bhago Row ID contains unexpected data; review the source.')
            recipient['source_ref'].update(row_id=old or uuid.uuid4().hex,title=title)
            if not old:
                updates.append({'range':a1(title,f'{get_column_letter(col+1)}{recipient["source_ref"]["row"]}'),'values':[[recipient['source_ref']['row_id']]]})
        writer.write(ref['spreadsheet_id'],updates)
    return recipients


def state_key(row_id):
    return 'bhago:18:row:'+row_id


def save_state(key, value):
    from insightdesk_bridge import connect
    from credential_store import cipher
    encrypted=cipher().encrypt(json.dumps(value).encode()).decode()
    with connect() as db, db.cursor() as cur:
        cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()', (key,encrypted))


def load_state(key):
    from insightdesk_bridge import get_value
    from credential_store import cipher
    value=get_value(key)
    return json.loads(cipher().decrypt(value.encode())) if value else None


def load_states(keys):
    from insightdesk_bridge import connect
    from credential_store import cipher
    if not keys:return {}
    with connect() as db,db.cursor() as cur:
        cur.execute('SELECT key,value FROM app_settings WHERE key = ANY(%s)',(list(keys),))
        codec=cipher()
        return {key:json.loads(codec.decrypt(value.encode())) for key,value in cur.fetchall()}


def signature(recipient):
    ref=recipient.get('source_ref',{})
    values=[recipient['phone'],recipient['name'],recipient.get('sourceDate',''),recipient.get('value2','')]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def register_manifest(job):
    save_state('bhago:18:manifest:'+job['runId'],job)


def mark_submitted(job, state='submitted'):
    for r in job['recipients']:
        ref=r.get('source_ref') or {}
        if not ref.get('row_id'):
            continue
        carried=merge_calls(r.get('prior_calls',[]),[])
        save_state(state_key(ref['row_id']),{'signature':signature(r),'state':state,'run_id':job['runId'],
                    'campaign':job['campaignName'],'source':ref['source'],'attempts':[c['Session ID'] for c in carried],'calls':carried})


def pending_manifests():
    from insightdesk_bridge import connect
    from credential_store import cipher
    with connect() as db,db.cursor() as cur:
        cur.execute('SELECT key,value FROM app_settings WHERE key LIKE %s',('bhago:18:manifest:%',))
        return [(key,json.loads(cipher().decrypt(value.encode()))) for key,value in cur.fetchall()]


def write_attempts(writer, recipient, kind, calls):
    from insightdesk_bridge import field_indices, now
    ref=recipient['source_ref'];title,mapping=schema(writer,ref,kind);width=max(mapping.values())+1
    metadata=writer.metadata(ref['spreadsheet_id']);props=next(s['properties'] for s in metadata['sheets'] if s['properties']['sheetId']==ref['sheet_id'])
    idcol=get_column_letter(mapping['Bhago Row ID']+1)
    ids=writer.values(ref['spreadsheet_id'],[a1(title,f'{idcol}2:{idcol}{props["gridProperties"]["rowCount"]}')])[0]
    matches=[n+2 for n,row in enumerate(ids) if row and str(row[0])==ref['row_id']]
    if len(matches)!=1:
        raise ValueError('Source row ID was removed or duplicated; result held without changing any row.')
    number=matches[0]
    current=writer.values(ref['spreadsheet_id'],[a1(title,f'A{number}:{get_column_letter(width)}{number}'),a1(title,f'A1:{get_column_letter(width)}1')])
    row=current[0][0] if current[0] else [];headers=current[1][0]
    if any(col>=len(headers) or headers[col]!=label for label,col in mapping.items() if label in columns(kind)):
        raise ValueError('Result columns moved during sync; updates held for a fresh source read.')
    indices=field_indices(headers,{'source':ref['source'],'calling_type':kind})
    if not identity_matches(row,recipient,indices):
        raise ValueError('Phone or name changed on the matched row; results held for review.')
    if kind=='payment':
        from insightdesk_bridge import vehicle_column
        if str(row[vehicle_column(headers)] or '').strip()!=recipient.get('value2',''):
            raise ValueError('Vehicle changed on the matched payment row; results held for review.')
    ordered=sorted({c['Session ID']:c for c in calls}.values(),key=lambda c:(c['Time'],c['Session ID']))
    if len(ordered)>2:
        raise ValueError('More than two attempts found for this campaign/recipient; results held for review.')
    updates=[]
    first_campaign_col=mapping['Attempt 1 - Campaign Name']
    first_campaign=row[first_campaign_col] if len(row)>first_campaign_col else ''
    if first_campaign and first_campaign!=ordered[0]['Campaign Name']:
        for label,col in mapping.items():
            if label.startswith('Attempt 2 - ') and len(row)>col and row[col]!='':
                updates.append({'range':a1(title,f'{get_column_letter(col+1)}{number}'),'values':[['']]})
    for attempt, call in enumerate(ordered,1):
        values=result_values(call,kind)
        for label,value in values.items():
            col=mapping[f'Attempt {attempt} - {label}'];existing=row[col] if len(row)>col else ''
            if label=='Call ID' and existing and existing!=value:
                # A newer explicit manual test may replace the displayed campaign, but never a different row.
                old_campaign_col=mapping[f'Attempt {attempt} - Campaign Name']
                old_campaign=row[old_campaign_col] if len(row)>old_campaign_col else ''
                if old_campaign==call['Campaign Name']:
                    raise ValueError('Call-attempt order changed; refusing to overwrite another call ID.')
            if str(existing)!=str(value):
                updates.append({'range':a1(title,f'{get_column_letter(col+1)}{number}'),'values':[[value]]})
    if updates:
        updates.append({'range':a1(title,f'{get_column_letter(mapping["Bhago Last Synced At"]+1)}{number}'),'values':[[now()]]})
        writer.write(ref['spreadsheet_id'],updates)
        actual=writer.values(ref['spreadsheet_id'],[u['range'] for u in updates])
        if any(str(rows[0][0] if rows and rows[0] else '')!=str(u['values'][0][0]) for rows,u in zip(actual,updates)):
            raise ValueError('Google result read-back failed; sync remains pending.')
    return [c['Session ID'] for c in ordered]


def reconcile_export(text):
    from insightdesk_bridge import mobile
    calls=parse_export(text); grouped=collections.defaultdict(list)
    for call in calls:
        if clean(call.get('Status')).casefold() not in TERMINAL:
            continue
        try:phone=mobile(call.get('To Number'))
        except ValueError:continue
        grouped[(call['Campaign Name'],phone)].append(call)
    result={'synced_rows':0,'held_rows':0}
    with google_session(write=True) as session:
        writer=SheetsWriter(session)
        manifests=pending_manifests()
        keys=[state_key(r['source_ref']['row_id']) for _,job in manifests for r in job['recipients'] if (r.get('source_ref') or {}).get('row_id')]
        keys+=['bhago:18:inflight:lead','bhago:18:inflight:payment']
        states=load_states(keys)
        for key,job in manifests:
            for recipient in job['recipients']:
                row_id=(recipient.get('source_ref') or {}).get('row_id')
                if not row_id:
                    continue
                previous=states.get(state_key(row_id)) or {}
                if previous.get('run_id') and previous['run_id']!=job['runId']:
                    continue
                relevant=grouped.get((job['campaignName'],recipient['phone']),[])
                if not relevant:
                    continue
                relevant=merge_calls(recipient.get('prior_calls',[]),relevant)
                fingerprint=hashlib.sha256(json.dumps(relevant,sort_keys=True).encode()).hexdigest()
                if previous.get('result_fingerprint')==fingerprint:
                    continue
                peers=[r for r in job['recipients'] if r['phone']==recipient['phone']]
                if len(peers)>1:
                    # Do not guess between two source rows with the same phone in one campaign.
                    result['held_rows']+=1;continue
                try:
                    ids=write_attempts(writer,recipient,job['type'],relevant)
                    updated={'signature':signature(recipient),
                        'state':'complete' if calls_complete(relevant,job['type']) else 'pending' if previous.get('closed_campaign') else 'awaiting_second_attempt',
                        'closed_campaign':previous.get('closed_campaign',False),'calls':relevant,
                        'run_id':job['runId'],'source':recipient['source_ref']['source'],'attempts':ids,'campaign':job['campaignName'],'result_fingerprint':fingerprint}
                    save_state(state_key(row_id),updated);states[state_key(row_id)]=updated
                    result['synced_rows']+=1
                except ValueError:
                    result['held_rows']+=1
            inflight=states.get('bhago:18:inflight:'+job['type']) or {}
            if inflight.get('run_id')==job['runId'] and all((states.get(state_key(r['source_ref']['row_id'])) or {}).get('state')=='complete' for r in job['recipients']):
                save_state('bhago:18:inflight:'+job['type'],dict(inflight,state='complete'))
    return result
