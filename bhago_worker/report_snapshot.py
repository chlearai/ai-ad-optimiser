"""Read-only, scoped daily report from the cloud worker's existing history export."""
import datetime as dt
import json
import re
from outcomes import parse_export, pending_manifests, save_state

FIELDS={'Time','Agent Name','Contact Name','From Number','To Number','Direction','Campaign Name',
        'Duration','Status','End Reason','Sentiment','Lead Intent','Outcome','Next Action',
        'Callback Time','Call Summary','Recording URL','Session ID','Interest Level',
        'qualified_for_followup','payment_commitment_status','call_disposition',
        'promised_payment_date','promised_amount'}


def extract(text, day, campaigns):
    return [{k:v for k,v in row.items() if k in FIELDS} for row in parse_export(text)
            if row.get('Time','')[:10]==day and row.get('Campaign Name') in campaigns]


def tick():
    from insightdesk_bridge import get_value, connect, PRIVATE, now
    raw=get_value('bhago:18:report_request')
    if not raw:return
    request=json.loads(raw)
    if request.get('state')!='queued':return
    ident=request.get('id','');day=request.get('date','')
    if not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('Invalid report request')
    dt.date.fromisoformat(day)
    path=PRIVATE/'latest-outcomes.csv'
    if not path.exists():return
    campaigns={j['campaignName'] for _,j in pending_manifests() if j.get('source') not in ('test','test_lead','test_payment','qwqer')}
    rows=extract(path.read_text(encoding='utf-8-sig'),day,campaigns)
    save_state('bhago:18:report:'+ident,{'date':day,'captured_at':now(),'export_file_updated_at':dt.datetime.fromtimestamp(path.stat().st_mtime,dt.timezone.utc).isoformat(),'rows':rows})
    request['state']='ready';request['captured_at']=now()
    with connect() as db,db.cursor() as cur:
        cur.execute('UPDATE app_settings SET value=%s,updated_at=NOW() WHERE key=%s',(json.dumps(request),'bhago:18:report_request'))
