"""Nonblocking 45-second polling. A result-export failure never triggers a call."""
import datetime as dt
import json
import time
import uuid
from pathlib import Path

_pending = None
_next = 0


def tick():
    global _pending, _next
    from insightdesk_bridge import PRIVATE, command, connect, now
    from outcomes import reconcile_export
    if _pending:
        file=PRIVATE/'jobs'/(_pending['id']+'.outcomes.json')
        if not file.exists():
            if time.monotonic()-_pending['started']<90:return
            _pending=None;_next=time.monotonic()+45;return
        result=json.loads(file.read_text())
        if result.get('state')=='downloaded':
            try:
                report=reconcile_export(Path(result['csvPath']).read_text(encoding='utf-8-sig'))
                status={'state':'ready','updated_at':now(),'exported_at':result.get('completedAt'),**report}
            except Exception as error:
                status={'state':'needs_attention','updated_at':now(),'error_type':type(error).__name__,
                        'message':str(error)[:500] if isinstance(error,ValueError) else 'Result sync could not finish; no calling retry was made.'}
            with connect() as db,db.cursor() as cur:
                cur.execute('INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()',('bhago:18:result_sync',json.dumps(status)))
        file.unlink(missing_ok=True)
        _pending=None;_next=time.monotonic()+45
    if time.monotonic()<_next:return
    request_id=uuid.uuid4().hex
    if command({'action':'campaign_step','stage':'insightdesk_results_export','requestId':request_id}):
        _pending={'id':request_id,'started':time.monotonic()}
