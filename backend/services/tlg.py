"""TLG append-only lead routing. Each source row is a submission, including duplicates."""
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from backend.db.database import SessionLocal
from backend.db.models import TlgCentre

CENTRES = ["Yadavgiri", "Chandrashekharpur", "Yelahanka", "New BEL Road", "Coimbatore", "Elgin Road", "Alwarpet", "Whitefield", "Jayanagar"]
ACTIVE_CENTRES = {"Yelahanka", "Whitefield"}
META_AD_ACCOUNT_ID = "929681660424354"
MARKER = "_InsightDesk_source_row"

def sheet_ref(url):
    parsed = urlparse(url)
    match = re.fullmatch(r"/spreadsheets/d/([\w-]+)(?:/.*)?", parsed.path)
    if parsed.hostname != "docs.google.com" or not match:
        raise ValueError("Enter a valid Google Sheets URL")
    params = parse_qs(parsed.query + "&" + parsed.fragment)
    return match[1], int(params.get("gid", [0])[0])

def sheets_identity():
    """Resolve existing Sheets credentials without exposing their private key."""
    raw = os.getenv("TLG_GOOGLE_SA_JSON", "").strip() or os.getenv("CRASH_CLUB_GOOGLE_SA_JSON", "").strip()
    if raw:
        return json.loads(raw)
    path = os.getenv("TLG_GOOGLE_SA_FILE", "").strip() or os.getenv("CRASH_CLUB_GOOGLE_SA_FILE", "google_service_account.json").strip()
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    raise ValueError("Google Sheets is not connected. Configure a Google Sheets service account on the server, then share the master and branch sheets with its email.")

def sheets_client():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_info(sheets_identity(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

def tab_title(client, sid, gid):
    meta = client.get(spreadsheetId=sid, fields="sheets.properties").execute()
    for tab in meta["sheets"]:
        if tab["properties"]["sheetId"] == gid:
            return "'" + tab["properties"]["title"].replace("'", "''") + "'"
    raise ValueError("Worksheet not found; use the link to the correct tab")

def column(index):
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value

def plan_rows(source, destination, prefix):
    headers = source[0] if source else []
    if not headers or any(not str(x).strip() for x in headers) or len(set(headers)) != len(headers) or MARKER in headers:
        raise ValueError("Master sheet needs unique, non-empty headers in row 1")
    target = destination[0] if destination else headers + [h for h in ["Lead Status", "Remarks", "Next Follow-up", MARKER] if h not in headers]
    if len(set(target)) != len(target) or any(h not in target for h in headers):
        raise ValueError("Branch sheet headers must include every master column with matching names")
    if MARKER not in target:
        target = target + [MARKER]
    marker_index = target.index(MARKER)
    seen = {str(row[marker_index]) for row in destination[1:] if len(row) > marker_index}
    rows = []
    for index, row in enumerate(source[1:], 2):
        key = f"{prefix}:{index}"
        if not any(str(v).strip() for v in row) or key in seen:
            continue
        output = [""] * len(target)
        for i, header in enumerate(headers):
            output[target.index(header)] = row[i] if i < len(row) else ""
        output[marker_index] = key
        rows.append(output)
    return target, rows

def sync_centre(db, centre):
    source_id, source_gid = sheet_ref(centre.source_url)
    dest_id, dest_gid = sheet_ref(centre.destination_url)
    if source_id == dest_id:
        raise ValueError("Master and branch must be separate spreadsheets to prevent routing loops")
    client = sheets_client()
    source_tab = tab_title(client, source_id, source_gid)
    dest_tab = tab_title(client, dest_id, dest_gid)
    values = client.values()
    source = values.get(spreadsheetId=source_id, range=source_tab).execute().get("values", [])
    destination = values.get(spreadsheetId=dest_id, range=dest_tab).execute().get("values", [])
    headers, rows = plan_rows(source, destination, f"{source_id}:{source_gid}")
    if not destination or destination[0] != headers:
        values.update(spreadsheetId=dest_id, range=f"{dest_tab}!A1", valueInputOption="RAW", body={"values": [headers]}).execute()
    if rows:
        values.append(spreadsheetId=dest_id, range=f"{dest_tab}!A:{column(len(headers))}", valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
    centre.copied += len(rows)
    centre.last_sync = datetime.utcnow()
    centre.error = None

def sync_all():
    with SessionLocal() as db:
        ids = [r.id for r in db.query(TlgCentre).filter(TlgCentre.enabled.is_(True)).all()]
    for cid in ids:
        with SessionLocal() as db:
            centre = db.query(TlgCentre).filter(TlgCentre.id == cid).with_for_update().first()
            if not centre or not centre.enabled or not centre.source_url or not centre.destination_url:
                continue
            try:
                sync_centre(db, centre)
            except Exception as exc:
                centre.error = str(exc)[:500]
            db.commit()
