"""Bhago's scoped Google connection. Never logs credentials or source records."""
from contextlib import contextmanager
from urllib.parse import quote
import time

_last_sheet_request=0.0

WRITE_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
META_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly'
SCOPES = [WRITE_SCOPE, META_SCOPE, 'openid', 'https://www.googleapis.com/auth/userinfo.email']


@contextmanager
def google_session(write=False):
    from credential_store import read_secret
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    data = read_secret('google_oauth') or {}
    if data.get('email') != 'shekhar.chlear@gmail.com':
        raise ValueError('Connect Google as shekhar.chlear@gmail.com')
    info = data.get('credentials', {})
    if write and not {WRITE_SCOPE, META_SCOPE}.issubset(set(info.get('scopes', []))):
        raise ValueError('Bhago needs the approved Google Sheets write and file-permission authorization before calling.')
    session = AuthorizedSession(Credentials.from_authorized_user_info(info))
    try:
        yield session
    finally:
        session.close()


class SheetsWriter:
    def __init__(self, session):
        self.session = session
        from insightdesk_bridge import connect
        from urllib.parse import urlparse
        self.allowed=set()
        with connect() as db,db.cursor() as cur:
            cur.execute('SELECT value FROM app_settings WHERE key = ANY(%s)',
                        (['bhago:18:sheet:'+s for s in ('swiggy','zomato','payment_reminder','test_lead','test_payment','test')],))
            for row in cur.fetchall():
                match=__import__('re').fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?',urlparse(row[0] or '').path)
                if match:self.allowed.add(match[1])

    def request(self, method, spreadsheet_id, suffix='', **kwargs):
        if spreadsheet_id not in self.allowed:
            raise ValueError('This spreadsheet is not an approved saved Bhago source.')
        url = 'https://sheets.googleapis.com/v4/spreadsheets/' + spreadsheet_id + suffix
        global _last_sheet_request
        # Stay below the per-user Sheets request rate, including result polling.
        for attempt in range(4):
            time.sleep(max(0,1.2-(time.monotonic()-_last_sheet_request)))
            _last_sheet_request=time.monotonic()
            response = self.session.request(method, url, timeout=45, **kwargs)
            if response.status_code!=429 or attempt==3:break
            time.sleep(15*(attempt+1))
        if response.status_code in (401, 403, 404):
            raise ValueError('Google editing access is unavailable for this saved source; calling is blocked.')
        response.raise_for_status()
        return response.json()

    def can_edit(self, spreadsheet_id):
        if spreadsheet_id not in self.allowed:return False
        response = self.session.get('https://www.googleapis.com/drive/v3/files/' + spreadsheet_id,
                                    params={'fields':'id,capabilities(canEdit)', 'supportsAllDrives':'true'}, timeout=30)
        if response.status_code in (403, 404):
            reasons=[e.get('reason') for e in response.json().get('error',{}).get('errors',[])]
            if 'accessNotConfigured' in reasons:
                raise ValueError('Google Drive metadata API is not enabled; permission checks and calling are held.')
            return False
        response.raise_for_status()
        return response.json().get('capabilities', {}).get('canEdit') is True

    def metadata(self, spreadsheet_id):
        return self.request('GET', spreadsheet_id, params={'fields':'sheets(properties,protectedRanges(requestingUserCanEdit,range))'})

    def values(self, spreadsheet_id, ranges, formulas=False):
        result=[]
        for offset in range(0,len(ranges),80):
            data = self.request('GET', spreadsheet_id, '/values:batchGet', params={
                'ranges':ranges[offset:offset+80], 'valueRenderOption':'FORMULA' if formulas else 'UNFORMATTED_VALUE',
                'dateTimeRenderOption':'FORMATTED_STRING'})
            result.extend(r.get('values', []) for r in data.get('valueRanges', []))
        if len(result)!=len(ranges):raise ValueError('Google returned an incomplete range response; no row will be guessed.')
        return result

    def write(self, spreadsheet_id, updates):
        for offset in range(0,len(updates),200):
            self.request('POST', spreadsheet_id, '/values:batchUpdate', json={'valueInputOption':'RAW','data':updates[offset:offset+200]})


def a1(title, cells):
    return "'" + title.replace("'", "''") + "'!" + cells
