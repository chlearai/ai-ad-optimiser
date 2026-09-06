"""
Mantri Salesforce lead status report.

Reads a local Excel file exported from Salesforce, maps Sub Source to Meta/Google,
and returns lead-status counts/percentages per platform and overall.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import User
from backend.routes.auth import get_current_user_required
from backend.services.activity_log import log_activity

logger = logging.getLogger("AdOptima")

router = APIRouter(prefix="/api/mis/mantri/salesforce", tags=["salesforce_mantri"])

# Path to the exported Salesforce file (relative to repo root, overridable via env var)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SALESFORCE_FILE_PATH = os.environ.get(
    "MANTRI_SALESFORCE_FILE_PATH",
    os.path.join(_PROJECT_ROOT, "Data", "mantri_salesforce.xlsx"),
)

# Sub Source -> platform mapping
SUB_SOURCE_MAP = {
    "Facebook": "Meta",
    "Facebook Lead Page": "Meta",
    "Landing Page": "Meta",
    "google_paid": "Google",
}
DEFAULT_PLATFORM = "Meta"

# Lead statuses in the order shown in the reference report
LEAD_STATUS_ORDER = [
    "Booked",
    "Closed Lost",
    "Interested",
    "New",
    "Not Interested",
    "Post Site Visit Follow-Up",
    "Pre Sales Follow Up",
    "Qualified",
    "Sales Follow up",
    "Site Visit Schedule",
    "SV Completed",
]


def _map_platform(sub_source: Any) -> str:
    if pd.isna(sub_source):
        return DEFAULT_PLATFORM
    return SUB_SOURCE_MAP.get(str(sub_source).strip(), DEFAULT_PLATFORM)


def _parse_create_date(value: Any) -> Optional[datetime.date]:
    if pd.isna(value):
        return None
    try:
        # Expected format: DD/MM/YYYY
        return pd.to_datetime(value, dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def _load_salesforce_data(file_path: str, start_date: datetime.date, end_date: datetime.date) -> Optional[pd.DataFrame]:
    if not os.path.exists(file_path):
        logger.warning(f"Salesforce file not found: {file_path}")
        return None
    try:
        df = pd.read_excel(file_path, header=9)
        # Rename columns to expected normalized names
        df = df.rename(
            columns={
                "Create Date": "create_date",
                "Sub Source": "sub_source",
                "Lead status": "lead_status",
            }
        )
        df["platform"] = df["sub_source"].apply(_map_platform)
        df["parsed_date"] = df["create_date"].apply(_parse_create_date)
        df = df[df["parsed_date"].notna()]
        df = df[df["parsed_date"] >= start_date]
        df = df[df["parsed_date"] <= end_date]
        df = df[df["lead_status"].notna()]
        return df
    except Exception as e:
        logger.error(f"Failed to read Salesforce file: {e}")
        return None


def _compute_report(df: pd.DataFrame, start_date: datetime.date, end_date: datetime.date) -> Dict[str, Any]:
    status_counts: Dict[str, Dict[str, int]] = {status: {"Meta": 0, "Google": 0} for status in LEAD_STATUS_ORDER}

    for _, row in df.iterrows():
        status = str(row["lead_status"]).strip()
        platform = row["platform"]
        if status in status_counts and platform in status_counts[status]:
            status_counts[status][platform] += 1

    meta_total = sum(status_counts[s]["Meta"] for s in LEAD_STATUS_ORDER)
    google_total = sum(status_counts[s]["Google"] for s in LEAD_STATUS_ORDER)
    overall_total = meta_total + google_total

    rows = []
    for status in LEAD_STATUS_ORDER:
        meta_count = status_counts[status]["Meta"]
        google_count = status_counts[status]["Google"]
        overall_count = meta_count + google_count
        rows.append({
            "lead_status": status,
            "meta_count": meta_count,
            "meta_pct": round((meta_count / meta_total * 100), 1) if meta_total else 0.0,
            "google_count": google_count,
            "google_pct": round((google_count / google_total * 100), 1) if google_total else 0.0,
            "overall_count": overall_count,
            "overall_pct": round((overall_count / overall_total * 100), 1) if overall_total else 0.0,
        })

    return {
        "title": "MANTRI - SALESFORCE LEAD STATUS",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
        "totals": {
            "meta_count": meta_total,
            "google_count": google_total,
            "overall_count": overall_total,
        },
    }


@router.get("/report")
def salesforce_report(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    """Return Mantri Salesforce lead status report from local Excel file.

    Date range is fixed: 16-04-2026 to yesterday (inclusive).
    Today is always excluded.
    """
    start_date = datetime.strptime("16-04-2026", "%d-%m-%Y").date()
    end_date = datetime.utcnow().date() - timedelta(days=1)
    df = _load_salesforce_data(SALESFORCE_FILE_PATH, start_date, end_date)
    if df is None:
        raise HTTPException(status_code=404, detail="Salesforce file not found or unreadable")
    if df.empty:
        raise HTTPException(status_code=404, detail="No leads found in the selected date range")

    log_activity(
        module="InsightDesk",
        action="Mantri Salesforce Report Viewed",
        description=f"Viewed Mantri Salesforce lead status report ({len(df)} leads from {start_date.isoformat()} to {end_date.isoformat()})",
        user_id=user.id,
        user_name=user.full_name or user.email,
        entity_type="mis_project",
        entity_id="1",
        details={"leads_count": len(df), "start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        db=db,
    )

    return _compute_report(df, start_date, end_date)
