"""
Crash Club — Google Sheets lead writer.

Appends individual Meta lead rows to a Google Sheet using a service account.
Dedup happens against a local SQLite ledger (leads.db) so the 5-min poller
never writes the same lead twice.

Required:
- Service account JSON (env CRASH_CLUB_GOOGLE_SA_JSON or CRASH_CLUB_GOOGLE_SA_FILE)
- Sheet shared with the service-account email (edit access)
- Env: CRASH_CLUB_SHEET_ID (spreadsheet id), optional CRASH_CLUB_SHEET_TAB
"""
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("AdOptima")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LEDGER_DB = os.getenv("CRASH_CLUB_LEDGER_DB", "leads.db")
LEDGER_TABLE = "crashclub_sheet_ledger"


class CrashClubSheetsError(Exception):
    pass


def _load_service_account() -> Credentials:
    sa_json = os.getenv("CRASH_CLUB_GOOGLE_SA_JSON", "").strip()
    sa_file = os.getenv("CRASH_CLUB_GOOGLE_SA_FILE", "google_service_account.json")
    if sa_json:
        info = json.loads(sa_json)
    elif os.path.exists(sa_file):
        with open(sa_file, "r", encoding="utf-8") as f:
            info = json.load(f)
    else:
        raise CrashClubSheetsError(
            "Google service account not configured. Set CRASH_CLUB_GOOGLE_SA_JSON "
            "(full JSON) or CRASH_CLUB_GOOGLE_SA_FILE and share the Sheet with the "
            "service account email."
        )
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _sheet_service():
    return build("sheets", "v4", credentials=_load_service_account())


def get_spreadsheet_id() -> str:
    sid = os.getenv("CRASH_CLUB_SHEET_ID", "").strip()
    if not sid:
        raise CrashClubSheetsError("CRASH_CLUB_SHEET_ID not set (Google spreadsheet id).")
    return sid


def _tab() -> str:
    return os.getenv("CRASH_CLUB_SHEET_TAB", "Crash Club Leads")


def _ensure_tab_with_headers(service, spreadsheet_id: str, tab: str, headers: List[str]):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if tab not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
    existing = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A1:ZZ1")
        .execute()
        .get("values", [[]])
    )
    if not existing or not existing[0]:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()


# ---------------- local ledger (dedup) ----------------

def _ledger_conn():
    conn = sqlite3.connect(LEDGER_DB)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
            lead_id TEXT PRIMARY KEY,
            sheet_row INTEGER,
            synced_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def already_synced(conn: sqlite3.Connection, lead_id: str) -> bool:
    cur = conn.execute(f"SELECT 1 FROM {LEDGER_TABLE} WHERE lead_id = ?", (lead_id,))
    return cur.fetchone() is not None


def mark_synced(conn: sqlite3.Connection, lead_id: str, sheet_row: Optional[int]):
    conn.execute(
        f"INSERT OR REPLACE INTO {LEDGER_TABLE} (lead_id, sheet_row, synced_at) VALUES (?, ?, datetime('now'))",
        (lead_id, sheet_row),
    )
    conn.commit()


# ---------------- write to sheet ----------------

def append_leads(rows: List[Dict[str, Any]], build_row_fn, headers: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Append only leads not yet in the ledger. Returns summary dict.
    rows: list of normalized lead dicts (must contain 'lead_id')
    build_row_fn: function mapping normalized lead -> flat list (column order)
    headers: column headers (written if the tab is new/empty)
    """
    if headers is None:
        from backend.services.crashclub_leads import SHEET_HEADERS
        headers = SHEET_HEADERS
    spreadsheet_id = get_spreadsheet_id()
    service = _sheet_service()
    tab = _tab()
    _ensure_tab_with_headers(service, spreadsheet_id, tab, headers)

    conn = _ledger_conn()
    written = 0
    skipped = 0
    errors: List[str] = []
    try:
        pending: List[List[str]] = []
        for row in rows:
            lid = str(row.get("lead_id") or "")
            if not lid or already_synced(conn, lid):
                skipped += 1
                continue
            pending.append(build_row_fn(row))

        if pending:
            res = (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{tab}!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": pending},
                )
                .execute()
            )
            written = len(pending)
            first_row = res.get("updates", {}).get("updatedRange", "")
            # mark all as synced
            start_row = 2
            try:
                import re

                m = re.search(r"!A(\d+)", first_row)
                if m:
                    start_row = int(m.group(1))
                for i in range(len(pending)):
                    mark_synced(conn, str(rows[i].get("lead_id")), start_row + i)
            except Exception:
                for r in rows:
                    mark_synced(conn, str(r.get("lead_id")), None)
        else:
            for row in rows:
                lid = str(row.get("lead_id") or "")
                if lid and not already_synced(conn, lid):
                    pass
    finally:
        conn.close()

    return {
        "written": written,
        "skipped_dedup": skipped,
        "errors": errors,
        "tab": tab,
        "spreadsheet_id": spreadsheet_id,
    }


def backfill_all_existing_leads() -> Dict[str, Any]:
    """Fetch ALL leads from Meta (no since filter) and push them (dedup applies)."""
    from backend.services.crashclub_leads import (
        SHEET_HEADERS,
        build_sheet_row,
        fetch_account_leads,
        normalize_lead,
    )

    leads = fetch_account_leads()
    rows = [normalize_lead(l) for l in leads]
    return append_leads(rows, build_sheet_row, SHEET_HEADERS)