"""Bhago-only local queue worker. No Samvaad API, passwords or browser-cookie extraction."""
import argparse
import collections
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import openpyxl
import psycopg2
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get("BHAGO_PRIVATE_DIR", str(ROOT / "private")))
JOBS = PRIVATE / "jobs"
ENV = Path(r"C:\Users\Shekhar Raju\Downloads\Clients\Shekhar_AI_Agents\AI_The_Optimiser\.env")
ACCOUNT = 18
KEY = f"bhago:{ACCOUNT}:run:current"
IST = ZoneInfo("Asia/Kolkata")
TEST_SOURCES = ("test", "test_lead", "test_payment")
GOOGLE_READER_FILE = PRIVATE / "google-sheets-service-account.json"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def calling_enabled():
    """Cloud deployments remain in standby until explicit go-live activation."""
    return os.environ.get('BHAGO_RUNTIME') != 'cloud' or os.environ.get('BHAGO_CALLING_ENABLED') == 'true'


def connect():
    database_url = os.environ.get("DATABASE_URL") or dotenv_values(ENV).get("DATABASE_URL")
    if not database_url:
        raise ValueError("Bhago worker database connection has not been configured")
    return psycopg2.connect(database_url, connect_timeout=15)


def get_value(key):
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def update_run(run_id, expected, **changes):
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key=%s FOR UPDATE", (KEY,))
        row = cur.fetchone()
        run = json.loads(row[0]) if row else None
        if not run or run["id"] != run_id or run["state"] not in expected:
            return False
        run.update(changes, updated_at=now())
        payload = json.dumps(run)
        cur.execute("UPDATE app_settings SET value=%s,updated_at=NOW() WHERE key=%s", (payload, KEY))
        cur.execute("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()",
                    (f"bhago:{ACCOUNT}:run:history:{run_id}", payload))
        return True


def atomic_json(file, data):
    temp = file.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, file)


def command(data):
    # Only one browser action at a time, shared with manually requested tests.
    target = PRIVATE / "command.json"
    if target.exists():
        return False
    temporary = PRIVATE / ('.command-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    try:
        os.rename(temporary, target)
    except FileExistsError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def heartbeat():
    browser = {}
    try:
        browser = json.loads((PRIVATE / "bridge-browser.json").read_text())
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(browser["updatedAt"])).total_seconds()
    except (OSError, ValueError, KeyError):
        age = 999
    ready = browser.get("ready", False) and age < 40
    with connect() as db, db.cursor() as cur:
        cur.execute("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()",
                    (f"bhago:{ACCOUNT}:worker", json.dumps({"browser_ready": bool(ready), "calling_enabled": calling_enabled(), "runtime": os.environ.get('BHAGO_RUNTIME', 'local'), "results_enabled":os.environ.get('BHAGO_RESULTS_ENABLED')=='true', "backlog_enabled":os.environ.get('BHAGO_BACKLOG_ENABLED')=='true', "schedule_enabled":os.environ.get('BHAGO_SCHEDULE_ENABLED')=='true', "revision":os.environ.get('RAILWAY_GIT_COMMIT_SHA',''), "updated_at": now()})))
    return ready


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def index(headers, aliases, field, optional=False):
    matches = [i for i, h in enumerate(headers) if normalized(h) in aliases]
    if len(matches) != 1:
        if not matches and optional:
            return None
        raise ValueError(f"Sheet must have one recognizable {field} column. Check its saved link and headers.")
    return matches[0]


def mobile(value):
    raw = str(value or "").strip().lstrip("'")
    if isinstance(value, float) and value.is_integer():
        raw = str(int(value))
    if not re.fullmatch(r"[+\d\s()-]+", raw):
        raise ValueError("invalid mobile number")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValueError("invalid Indian mobile number")
    return "+91" + digits


def lead_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value or "").strip()
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d/%m/%Y %H:%M", "%d %b %Y, %I:%M %p", "%d %b %Y, %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError("unrecognized lead date")


def fetch_workbook(url, run=None):
    parsed = urlparse(url)
    match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not match:
        raise ValueError("Invalid saved Google Sheet link")
    if run and os.environ.get('BHAGO_GOOGLE_OAUTH_ENABLED') == 'true':
        return google_reader_workbook(match[1], run)
    response = requests.get(f"https://docs.google.com/spreadsheets/d/{match[1]}/export", params={"format": "xlsx"}, timeout=45)
    if response.status_code in (401, 403):
        if GOOGLE_READER_FILE.is_file() and run:
            return google_reader_workbook(match[1], run)
        raise ValueError("This source needs Bhago's read-only Google Sheets connection. It has not been authorized yet.")
    response.raise_for_status()
    if len(response.content) > 20_000_000 or not response.content.startswith(b"PK"):
        raise ValueError("Saved link did not return a readable spreadsheet")
    return openpyxl.load_workbook(io.BytesIO(response.content), read_only=True, data_only=True)


def authenticated_workbook(url):
    request_id = uuid.uuid4().hex
    base = JOBS / request_id
    atomic_json(base.with_suffix('.sheet-request.json'), {"url": url})
    deadline = time.monotonic() + 15
    while not command({"action":"campaign_step", "stage":"insightdesk_sheet", "requestId":request_id}):
        if time.monotonic() > deadline:
            raise ValueError("The dedicated browser is busy; no calls were started")
        time.sleep(1)
    result_file = base.with_suffix('.sheet-result.json')
    deadline = time.monotonic() + 90
    while not result_file.exists():
        if time.monotonic() > deadline:
            raise ValueError("Google Sheet could not be read in the dedicated browser. Check its Google sign-in and source access.")
        time.sleep(1)
    result = json.loads(result_file.read_text())
    if result.get('state') != 'downloaded':
        raise ValueError(result['message'])
    return openpyxl.load_workbook(base.with_suffix('.xlsx'), read_only=True, data_only=True)


def source_sheet_names(names, source):
    if source == "swiggy":
        if "SSU Leads" in names:
            selected = ["SSU Leads"]
        elif len(names) == 1:
            selected = names
        else:
            raise ValueError("The saved SWIGGY link has no SSU Leads tab. Save the actual Swiggy lead workbook, not the document tracker.")
    elif source == "zomato":
        selected = [name for name in names if name.casefold() in ("bangalore", "bengaluru", "delhi")]
        if not selected and len(names) == 1:
            selected = names
        if not selected:
            raise ValueError("ZOMATO needs Bangalore and/or Delhi tabs")
    elif source == "payment_reminder":
        selected = [name for name in names if re.fullmatch(r"Unique Bookings(?:\s*\(\d+\))?", name, re.I)]
        if not selected and len(names) == 1:
            selected = names
        if len(selected) != 1:
            raise ValueError("Select the consolidated Unique Bookings payment sheet to avoid repeated reminder records")
    elif len(names) == 1:
        selected = names
    else:
        raise ValueError("This source has multiple tabs. Use a single-tab calling workbook.")
    return selected


def field_indices(headers, run):
    source, payment = run["source"], run["calling_type"] == "payment"
    phone_i = index(headers, {"phone", "phonenumber", "mobilenumber", "mobile", "deliverypartnerphonenumber", "riderphone", "ridermobilenumber"}, "mobile number")
    name_i = index(headers, {"name", "rider", "ridername", "customername", "deliverypartnername"}, "name")
    value_i = index(headers, {"city", "cityarea"}, "city/area", optional=True)
    if value_i is None:
        value_i = index(headers, {"area"}, "city/area", optional=True)
    if value_i is None and payment:
        value_i = index(headers, {"hublocation", "location"}, "city/area", optional=True)
    date_aliases = {"date", "timestamp", "leaddate", "createdat", "createddate", "creationdate", "leadcreateddate"}
    if payment:
        date_aliases.add('end')
    date_i = index(headers, date_aliases, "payment END date" if payment else "lead date", optional=payment or source in TEST_SOURCES)
    return phone_i, name_i, value_i, date_i


def amount_column(headers, required=False):
    found = index(headers, {"outstandingamount"}, "outstanding amount", optional=True)
    if found is not None:
        return found
    return index(headers, {"amountdue", "pendingamount", "balanceamount", "dueamount", "outstanding", "outst"}, "outstanding amount", optional=not required)


def vehicle_column(headers):
    return index(headers, {"vehicleregno", "vehicleregistration", "vehiclenumber", "vehicleregistrationnumber", "vehicleno"}, "vehicle number")


def amount_text(value):
    raw = re.sub(r"^(?:INR|Rs\.?|₹)\s*", "", str(value if value is not None else "").strip(), flags=re.I).replace(",", "").strip()
    if not raw:
        return ""
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        raise ValueError("invalid outstanding amount")
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid outstanding amount")
    return format(amount, "f")


def google_reader_workbook(spreadsheet_id, run):
    """Read only the selected tabs and calling columns through the official Sheets API."""
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.datetime import from_excel
    if os.environ.get('BHAGO_GOOGLE_OAUTH_ENABLED') == 'true':
        from google.oauth2.credentials import Credentials as UserCredentials
        from credential_store import read_secret
        data = read_secret('google_oauth')
        if not data or data.get('email', '').casefold() != 'shekhar.chlear@gmail.com':
            raise ValueError("Connect Bhago's Google Sheets reader as shekhar.chlear@gmail.com")
        credentials = UserCredentials.from_authorized_user_info(data['credentials'])
    else:
        credentials = Credentials.from_service_account_file(str(GOOGLE_READER_FILE), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        if not credentials.service_account_email.startswith("bhago-sheets-reader@"):
            raise ValueError("Use the dedicated Bhago Sheets reader credential; another project's identity will not be used.")
    session = AuthorizedSession(credentials)
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"

    def read_json(endpoint, params):
        result = session.get(endpoint, params=params, timeout=40)
        if result.status_code in (401, 403, 404):
            raise ValueError("Bhago's Google Sheets reader cannot read this source. Check its Viewer access and that Google Sheets API is enabled.")
        result.raise_for_status()
        return result.json()

    try:
        metadata = read_json(base, {"fields": "sheets(properties(sheetId,title,hidden,gridProperties(rowCount,columnCount)))"})
        sheets = {s["properties"]["title"]: s["properties"] for s in metadata.get("sheets", []) if not s["properties"].get("hidden")}
        selected = source_sheet_names(list(sheets), run["source"])
        book = openpyxl.Workbook()
        book._bhago_spreadsheet_id = spreadsheet_id
        book.remove(book.active)
        for title in selected:
            grid = sheets[title].get("gridProperties", {})
            height, width = grid.get("rowCount", 1000), grid.get("columnCount", 26)
            if height > 50000 or width > 100:
                raise ValueError("Source exceeds the configured reading limit; narrow the calling workbook")
            quoted = "'" + title.replace("'", "''") + "'"
            header_data = read_json(base + "/values:batchGet", {"ranges": [f"{quoted}!A1:{get_column_letter(width)}1"], "valueRenderOption": "UNFORMATTED_VALUE"})
            values = header_data.get("valueRanges", [{}])[0].get("values", [])
            headers = values[0] if values else []
            phone_i, name_i, value_i, date_i = field_indices(headers, run)
            amount_i = amount_column(headers, required=run["source"] == "payment_reminder") if run["calling_type"] == "payment" else None
            vehicle_i = vehicle_column(headers) if run["calling_type"] == "payment" else None
            columns = sorted({i for i in (phone_i, name_i, value_i, vehicle_i, amount_i, date_i) if i is not None})
            row_id_i = next((i for i,h in enumerate(headers) if str(h)=='Bhago Row ID'), None)
            if row_id_i is not None:
                columns = sorted(set(columns)|{row_id_i})
            target = book.create_sheet(title)
            target._bhago_sheet_id = sheets[title].get('sheetId')
            target.append(headers)
            if height < 2:
                continue
            ranges = [f"{quoted}!{get_column_letter(i+1)}2:{get_column_letter(i+1)}{height}" for i in columns]
            column_data = read_json(base + "/values:batchGet", {"ranges": ranges, "valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"})
            value_ranges = column_data.get("valueRanges", [])
            if len(value_ranges) != len(columns):
                raise ValueError("Google Sheets returned an incomplete set of calling columns")
            sparse = {}
            for column_i, data in zip(columns, value_ranges):
                for offset, cell in enumerate(data.get("values", [])):
                    if not cell:
                        continue
                    value = cell[0]
                    if column_i == date_i and isinstance(value, (int, float)):
                        value = from_excel(value)
                    sparse.setdefault(offset, [None]*len(headers))[column_i] = value
            for offset in range(max(sparse, default=-1)+1):
                target.append(sparse.get(offset, [None]*len(headers)))
        return book
    finally:
        session.close()


def recipients_from_workbook(book, run):
    # Resolve exact metadata titles before reading any sheet values.
    source = run["source"]
    selected = source_sheet_names(book.sheetnames, source)
    payment = run["calling_type"] == "payment"
    recipients, errors = [], []
    seen = collections.Counter()
    for name in selected:
        sheet = book[name]
        if (sheet.max_row or 0) > 50000 or (sheet.max_column or 0) > 100:
            raise ValueError("Source exceeds the configured reading limit; narrow the calling workbook")
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, ())
        phone_i, name_i, value_i, date_i = field_indices(headers, run)
        amount_i = amount_column(headers, required=source == "payment_reminder") if payment else None
        vehicle_i = vehicle_column(headers) if payment else None
        for row_number, row in enumerate(rows, 2):
            if row_number > 50000 or len(row) > 100:
                raise ValueError("Source exceeds the configured reading limit; narrow the calling workbook")
            if not any(v is not None and str(v).strip() for v in row):
                continue
            try:
                if not payment and source not in TEST_SOURCES:
                    day = lead_date(row[date_i])
                    if not dt.date.fromisoformat(run["start_date"]) <= day <= dt.date.fromisoformat(run["end_date"]):
                        continue
                phone = mobile(row[phone_i])
                person = str(row[name_i] or "").strip()
                if not person or re.search(r'[,"\r\n]', person) or person[0] in "=+@-":
                    raise ValueError("name needs CSV review")
                sheet_city = {"bangalore":"Bangalore", "bengaluru":"Bangalore", "delhi":"Delhi"}.get(name.casefold(), "") if source == "zomato" else ""
                value = str(row[value_i] or "").strip() if value_i is not None else sheet_city
                value = {"bangalore": "Bangalore", "bengaluru": "Bangalore", "bangaore": "Bangalore", "delhi": "Delhi", "new delhi": "Delhi"}.get(value.casefold(), value)
                if re.search(r'[,"\r\n]', value) or value.startswith(('=', '+', '@', '-')):
                    value = ""
                recipient = {"phone": phone, "name": person, "value1": value}
                if getattr(book, '_bhago_spreadsheet_id', None) and getattr(sheet, '_bhago_sheet_id', None) is not None:
                    recipient['source_ref'] = {'spreadsheet_id':book._bhago_spreadsheet_id,
                        'sheet_id':sheet._bhago_sheet_id,'title':name,'row':row_number,'source':source}
                    row_id_i = next((i for i,h in enumerate(headers) if str(h)=='Bhago Row ID'), None)
                    if row_id_i is not None and row_id_i < len(row) and row[row_id_i]:
                        recipient['source_ref']['row_id'] = str(row[row_id_i])
                    if date_i is not None and row[date_i]:
                        recipient['sourceDate'] = lead_date(row[date_i]).isoformat()
                if payment:
                    vehicle = str(row[vehicle_i] or "").strip()
                    if not re.fullmatch(r"[A-Za-z0-9 -]{5,20}", vehicle):
                        raise ValueError("invalid vehicle number")
                    recipient["value2"] = vehicle
                    recipient["value3"] = amount_text(row[amount_i]) if amount_i is not None else ""
                    if date_i is not None and row[date_i]:
                        recipient["sourceDate"] = lead_date(row[date_i]).isoformat()
                recipients.append(recipient)
                seen[phone] += 1
            except (ValueError, IndexError) as exc:
                errors.append(f"{name}, row {row_number}: {exc}")
    if errors and not run.get('_skip_invalid'):
        raise ValueError("Source needs correction before calling. " + "; ".join(errors[:5]))
    if not recipients and not run.get('_allow_empty'):
        raise ValueError("No calling records found for the selected source and date range")
    return recipients, sum(count - 1 for count in seen.values())


def read_source(run):
    if run['source'] == 'qwqer':
        raise ValueError('QWQER calling is suspended until explicitly reinstated by Shekhar.')
    if run['source'] == 'lead_batch':
        sources = run.get('lead_sources', [])
        if not sources or len(sources) != len(set(sources)) or any(s not in ('swiggy', 'zomato', 'qwqer') for s in sources):
            raise ValueError('Daily lead sources must be the selected Bhago lead sheets')
        sources = [source for source in sources if source != 'qwqer']
        combined, snapshots = [], []
        for source in sources:
            rows, _, snapshot = read_source(dict(run, source=source, _allow_empty=True))
            combined.extend(rows)
            snapshots.append([source, snapshot])
        if not combined:
            raise ValueError('No calling records found for the selected source and date range')
        fingerprint = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
        return combined, sum(n-1 for n in collections.Counter(r['phone'] for r in combined).values()), fingerprint
    url = get_value(f"bhago:{ACCOUNT}:sheet:{run['source']}")
    if not url:
        raise ValueError("No saved source sheet")
    book = fetch_workbook(url, run)
    try:
        rows, duplicates = recipients_from_workbook(book, run)
    finally:
        book.close()
    snapshot = hashlib.sha256(json.dumps({"url": url, "rows": rows}, sort_keys=True).encode()).hexdigest()
    return rows, duplicates, snapshot


def schedule_tick(moment=None):
    """Queue each approved daily campaign at most once; tests are always manual."""
    if not calling_enabled() or os.environ.get('BHAGO_RUNTIME') != 'cloud' or os.environ.get('BHAGO_SCHEDULE_ENABLED') != 'true':
        return
    moment = moment or dt.datetime.now(IST)
    if not 10 <= moment.hour < 18:
        return
    config = json.loads(get_value('bhago:18:schedule') or '{}')
    if not config.get('enabled'):
        return
    groups = []
    if config.get('lead_sources'):
        sources = config['lead_sources']
        if any(s not in ('swiggy','zomato','qwqer') for s in sources) or len(set(sources)) != len(sources):
            raise ValueError('Invalid scheduled lead source selection')
        sources = [source for source in sources if source != 'qwqer']
        if sources and config.get('combine_leads', False):
            groups.append(('lead_batch', sources))
        else:
            groups.extend((source, None) for source in sources)
    if config.get('payment_reminder'):
        groups.append(('payment_reminder', None))
    today = moment.date().isoformat()
    yesterday = (moment.date()-dt.timedelta(days=1)).isoformat()
    with connect() as db, db.cursor() as cur:
        cur.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE', (ACCOUNT,))
        cur.execute('SELECT value FROM app_settings WHERE key=%s', (KEY,))
        current = cur.fetchone()
        if current and json.loads(current[0]).get('state') in ('queued_preview','reading','ready','queued_launch','launching'):
            return
        for source, sources in groups:
            marker = f'bhago:18:schedule:{today}:{source}'
            cur.execute('SELECT 1 FROM app_settings WHERE key=%s', (marker,))
            if cur.fetchone():
                continue
            run = {'id':uuid.uuid4().hex, 'source':source, 'calling_type':'payment' if source=='payment_reminder' else 'lead',
                   'start_date':None if source=='payment_reminder' else yesterday, 'end_date':None if source=='payment_reminder' else yesterday,
                   'auto_launch':True, 'state':'queued_preview', 'requested_by':'daily_schedule', 'created_at':now(),
                   'message':'Daily 10 am IST campaign queued from the saved source sheets.'}
            if sources:
                run['lead_sources'] = sources
            payload = json.dumps(run)
            cur.execute("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()", (KEY,payload))
            cur.execute("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW())", (marker,json.dumps({'run_id':run['id']})))
            return


def preview(run):
    if not update_run(run["id"], ["queued_preview"], state="reading", message="Reading the latest saved sheet. No calls started."):
        return
    try:
        rows, duplicates, snapshot = read_source(run)
        if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true':
            from google_access import google_session, SheetsWriter
            from outcomes import bind_rows, register_manifest
            from backlog import select_planned
            rows = select_planned(run, rows)
            if len({row['phone'] for row in rows}) != len(rows):
                raise ValueError('Repeated mobile numbers need separate batches so each result maps to one source row.')
            with google_session(write=True) as session:
                bind_rows(rows, run['calling_type'], SheetsWriter(session))
            fresh_rows, _, snapshot = read_source(run)
            if select_planned(run, fresh_rows) != rows:
                raise ValueError('Source data changed while preparing result tracking; no campaign was started.')
        today = dt.datetime.now(IST).strftime("%Y-%m-%d")
        name = (f"Payment_Reminder_{today}" if run["calling_type"] == "payment" else
                f"Lead_Calling_{run['start_date'] or today}_to_{run['end_date'] or today}_{today}")
        name += ("_TEST" if run["source"] in TEST_SOURCES else "") + "_" + run["id"][:8]
        job = {"runId": run["id"], "source": run["source"], "type": run["calling_type"], "test": run["source"] in TEST_SOURCES,
               "campaignName": name, "recipients": rows, "sourceSnapshot": snapshot}
        job['totalAttempts']=2
        if run.get('requested_by')=='daily_backlog':
            budgets={2-len(row.get('prior_calls',[])) for row in rows}
            if len(budgets)!=1 or not budgets.issubset({1,2}):raise ValueError('Pending attempt budgets need separate campaigns.')
            job['totalAttempts']=budgets.pop()
        if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true':
            register_manifest(job)
        atomic_json(JOBS / (run["id"] + ".json"), job)
        counts = dict(collections.Counter(row["value1"] or "City not provided" for row in rows)) if run["calling_type"] == "lead" else {}
        update_run(run["id"], ["reading"], state="queued_launch" if run.get("auto_launch") else "ready",
                   campaign_name=name, total=len(rows), counts=counts, duplicate_count=duplicates,
                   message="Recipients validated. Creating the Samvaad campaign and starting calls." if run.get("auto_launch") else
                           "Preview ready. Review the counts, then choose Launch campaign.")
    except Exception as exc:
        update_run(run["id"], ["reading"], state="failed", message=safe_error(exc))


def safe_error(exc):
    # Never send connection details, URLs with credentials, or raw stack traces to the portal.
    if isinstance(exc, ValueError):
        return str(exc)[:900]
    return "The source or local worker could not be reached. Check its connection and access, then start a new run."


def launch(run):
    if not calling_enabled():
        return
    if not update_run(run["id"], ["queued_launch"], state="launching", message="Checking the sheet again and launching Samvaad. Do not submit again."):
        return
    dispatched = False
    try:
        job = json.loads((JOBS / (run["id"] + ".json")).read_text())
        _, _, snapshot = read_source(run)
        if snapshot != job["sourceSnapshot"]:
            raise ValueError("The saved sheet or its calling data changed after preview. Start a new run to review the new counts.")
        if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true':
            from outcomes import mark_submitted
            mark_submitted(job,'uncertain')
        deadline = time.monotonic() + 15
        while not command({"action": "campaign_step", "stage": "insightdesk_run", "runId": run["id"]}):
            if time.monotonic() > deadline:
                raise ValueError("The browser is busy. No launch request was sent; start a new run later.")
            time.sleep(1)
        dispatched = True
        if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true' and run.get('requested_by')=='daily_backlog':
            from outcomes import save_state
            save_state('bhago:18:inflight:'+job['type'],{'state':'uncertain','run_id':job['runId'],'campaign':job['campaignName'],'day':dt.datetime.now(IST).date().isoformat()})
        result_file = JOBS / (run["id"] + ".bridge-result.json")
        deadline = time.monotonic() + 300
        last_beat = 0
        while not result_file.exists():
            if time.monotonic() > deadline:
                update_run(run["id"], ["launching"], state="uncertain", message="Launch status could not be confirmed. Check Samvaad before placing any further calls; this request will not retry.")
                return
            if time.monotonic() - last_beat > 10:
                heartbeat()
                last_beat = time.monotonic()
            time.sleep(1)
        result = json.loads(result_file.read_text())
        success = result["state"] == "campaign_created"
        if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true':
            from outcomes import mark_submitted, save_state
            mark_submitted(job, 'submitted' if success else 'uncertain' if result['state']=='uncertain' else 'not_dialed')
            if success and run.get('requested_by')=='daily_backlog':
                save_state('bhago:18:inflight:'+job['type'],{'state':'submitted','run_id':job['runId'],'campaign':job['campaignName'],'day':dt.datetime.now(IST).date().isoformat()})
            elif result['state']=='failed' and run.get('requested_by')=='daily_backlog':
                save_state('bhago:18:inflight:'+job['type'],{'state':'complete','run_id':job['runId'],'campaign':job['campaignName'],'day':dt.datetime.now(IST).date().isoformat()})
        update_run(run["id"], ["launching"], state="created" if success else result["state"],
                   campaign_status=result.get("campaignRow", "")[:600],
                   message="Campaign created in Samvaad. Call receipt is not yet confirmed." if success else result.get("message", "Check Samvaad before retrying this campaign."))
    except Exception as exc:
        update_run(run["id"], ["launching"], state="uncertain" if dispatched else "failed", message=safe_error(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-source", choices=["swiggy", "zomato", "qwqer", "payment_reminder", "test", "test_lead", "test_payment"])
    parser.add_argument("--calling-type", choices=["lead", "payment"], default="payment")
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True)
    JOBS.mkdir(exist_ok=True)
    if args.check_source:
        rows, duplicates, _ = read_source({"source": args.check_source, "calling_type": args.calling_type,
                                         "start_date": "2026-10-10", "end_date": "2026-10-30"})
        print(json.dumps({"recipient_count": len(rows), "duplicate_count": duplicates, "calling_type": args.calling_type}))
        return
    # Prevent two local daemons competing for the same browser or queue.
    lock = (PRIVATE / "bridge.lock").open("a+b")
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Bhago bridge is already running")
    (PRIVATE / "bridge.pid").write_text(str(os.getpid()), encoding="ascii")
    # One owner across local/cloud hosts, held for the lifetime of this connection.
    owner_connection = connect()
    owner_connection.autocommit = True
    with owner_connection.cursor() as owner_cursor:
        owner_cursor.execute('SELECT pg_try_advisory_lock(%s)', (184182,))
        if not owner_cursor.fetchone()[0]:
            raise SystemExit('Another Bhago bridge owns the campaign queue')
    with connect() as db, db.cursor() as cur:
        cur.execute("SELECT name FROM accounts WHERE id=%s", (ACCOUNT,))
        account = cur.fetchone()
        if not account or account[0].strip().casefold() != "bhago mobility":
            raise SystemExit("Bhago account verification failed")
    first = True
    last_ping = 0
    while True:
        try:
            with owner_connection.cursor() as owner_cursor:
                owner_cursor.execute('SELECT 1')
            if time.monotonic() - last_ping > 12:
                command({"action": "campaign_step", "stage": "insightdesk_ping"})
                last_ping = time.monotonic()
            ready = heartbeat()
            if ready and not first:
                if os.environ.get('BHAGO_RESULTS_ENABLED') == 'true':
                    from result_sync import tick as sync_tick
                    sync_tick()
                if os.environ.get('BHAGO_BACKLOG_ENABLED') == 'true':
                    from backlog import tick as backlog_tick
                    backlog_tick()
                else:
                    schedule_tick()
            raw = get_value(KEY)
            run = json.loads(raw) if raw else None
            if first and run and run["state"] in ("reading", "launching"):
                update_run(run["id"], [run["state"]], state="uncertain" if run["state"] == "launching" else "failed",
                           message="Local worker restarted. Review Samvaad before starting another campaign; no automatic retry was made.")
            elif run and ready and calling_enabled():
                if run["state"] == "queued_preview":
                    preview(run)
                elif run["state"] == "queued_launch":
                    launch(run)
            first = False
        except psycopg2.Error:
            raise SystemExit('Bhago database connection interrupted; stopping the queue owner safely')
        except Exception:
            # Avoid leaking database credentials through exception text.
            print(now() + " Bhago bridge connection unavailable", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
