"""
Live data fetcher for DSU course performance report.
Pulls campaign spend from Google Ads API and leads from LeadSquared.
Maps both to the DSU course taxonomy.
"""
import json
import logging
import os
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, Any, List, Optional

from backend.services.crypto import decrypt
from backend.db.database import get_raw_connection

logger = logging.getLogger("AdOptima")


def _db_connect():
    """Return a DB-API connection. Works for SQLite and PostgreSQL."""
    return get_raw_connection()

# Cache for LSQ lead counts (keyed by "start_date_end_date")
_LSQ_CACHE: Dict[str, Dict[str, int]] = {}
_LSQ_CACHE_TIME: Dict[str, float] = {}
_LSQ_CACHE_TTL = 1800  # 30 minutes in seconds

# Course list as shown in DSU Table 1
DSU_COURSES = [
    "B.Tech", "MBA", "B.Sc Cyber Security", "School of Law", "BCA", "BBA",
    "B.Sc Biological Sciences", "M.Sc Data Science", "M.Sc Biological Sciences",
    "B.Sc Data Science", "M.Sc Cyber Security", "MCA", "B.Design", "JMC",
    "B.Pharm", "B.Com", "B.Sc Nursing", "BPT", "M.Tech", "DSAT",
]

# Keyword -> course mapping (order matters: more specific first)
CAMPAIGN_KEYWORDS = [
    ("msc_cybersecurity", "M.Sc Cyber Security"),
    ("msc_cyber", "M.Sc Cyber Security"),
    ("bsc_cybersecurity", "B.Sc Cyber Security"),
    ("bsc_cyber", "B.Sc Cyber Security"),
    ("cyber_security_display", "B.Sc Cyber Security"),
    ("cybersecurity", "B.Sc Cyber Security"),
    ("cyber security", "B.Sc Cyber Security"),
    ("biological_science_bsc", "B.Sc Biological Sciences"),
    ("bsc_biological", "B.Sc Biological Sciences"),
    ("biological_science_msc", "M.Sc Biological Sciences"),
    ("msc_biological", "M.Sc Biological Sciences"),
    ("biological science", "B.Sc Biological Sciences"),
    ("data_science_msc", "M.Sc Data Science"),
    ("msc_data", "M.Sc Data Science"),
    ("data_science_bsc", "B.Sc Data Science"),
    ("bsc_data", "B.Sc Data Science"),
    ("b.sc_data", "B.Sc Data Science"),
    ("data_science_display", "B.Sc Data Science"),
    ("data science s", "B.Sc Data Science"),
    ("b tech", "B.Tech"),
    ("btech", "B.Tech"),
    ("b_tech", "B.Tech"),
    ("b-tech", "B.Tech"),
    ("pmax_btech", "B.Tech"),
    ("iit jee", "B.Tech"),
    ("law", "School of Law"),
    ("llb", "School of Law"),
    ("mba", "MBA"),
    ("bca", "BCA"),
    ("bba", "BBA"),
    ("bcom", "B.Com"),
    ("b.com", "B.Com"),
    ("mca", "MCA"),
    ("b design", "B.Design"),
    ("bdesign", "B.Design"),
    ("b.design", "B.Design"),
    ("jmc", "JMC"),
    ("cjmc", "JMC"),
    ("b pharm", "B.Pharm"),
    ("bpharm", "B.Pharm"),
    ("pharm", "B.Pharm"),
    ("nursing", "B.Sc Nursing"),
    ("bpt", "BPT"),
    ("m.tech", "M.Tech"),
    ("mtech", "M.Tech"),
    ("dsat", "DSAT"),
]

# Student Source -> course mapping for LeadSquared
SOURCE_TO_COURSE = {
    "GGL-BTech": "B.Tech",
    "GGL-B.tech": "B.Tech",
    "GGL-DSAT": "B.Tech",
    "GGL-MBA": "MBA",
    "GGL-BCA": "BCA",
    "GGL-Law": "School of Law",
    "GGL_BBA": "BBA",
    "GGL_Biological_Science_BSc": "B.Sc Biological Sciences",
    "GGL_Biological_Science_MSc": "M.Sc Biological Sciences",
    "GGL_Cyber_Security_BSc": "B.Sc Cyber Security",
    "GGL_Cyber_Security_MSc": "M.Sc Cyber Security",
    "GGL_Data_Science_MSc": "M.Sc Data Science",
    "GGL_Data_Science_BSc": "B.Sc Data Science",
    "GGL-B.Pharm": "B.Pharm",
    "GGL-B.Design": "B.Design",
    "GGL-JMC": "JMC",
    "GGL-MCA": "MCA",
    "GGL_B.Com": "B.Com",
    "GGL-MSC.Biological.Science": "M.Sc Biological Sciences",
}

# Sources that should be excluded from lead counts entirely
EXCLUDED_SOURCES = {"DSPS-GGL"}


def _map_campaign_to_course(name: str) -> Optional[str]:
    """Map a Google Ads campaign name to a DSU course.

    Uses normalized substring matching (dots/underscores/hyphens stripped)
    following the AI_The_MIS logic so 'B.tech Ads' matches keyword 'b tech'.
    """
    low = name.lower()
    for kw, course in CAMPAIGN_KEYWORDS:
        if kw in low:
            return course
    # Normalized fallback: strip dots, underscores, hyphens
    norm = low.replace(".", "").replace("_", "").replace("-", "")
    for kw, course in CAMPAIGN_KEYWORDS:
        if kw.replace(".", "").replace("_", "").replace("-", "") in norm:
            return course
    return None


def _get_dsu_account_creds() -> Dict[str, Any]:
    """Load DSU's Google Ads credentials from the DB."""
    c = _db_connect()
    cur = c.cursor()
    cur.execute("SELECT google_credentials, google_login_customer_id FROM accounts WHERE name='DSU'")
    row = cur.fetchone()
    c.close()
    if not row or not row[0]:
        raise ValueError("DSU has no stored google_credentials")
    creds = json.loads(decrypt(row[0]))
    login_cid = (
        creds.get("login_customer_id")
        or (row[1] if len(row) > 1 and row[1] else None)
        or os.environ.get("GOOGLE_LOGIN_CUSTOMER_ID")
        or os.environ.get("GOOGLE_MCC_ID")
    )
    if login_cid:
        creds["login_customer_id"] = str(login_cid).replace("-", "").strip()
    return creds


def _fetch_google_ads_campaign_status() -> Dict[str, bool]:
    """Return {course: is_live} based on actual Google Ads campaign statuses.

    A course is considered live if it has at least one ENABLED campaign.
    Mapping uses the same keyword-based rules as spend reporting.
    """
    try:
        creds = _get_dsu_account_creds()
        from google.ads.googleads.client import GoogleAdsClient

        client_dict = {
            "developer_token": creds["developer_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "use_proto_plus": True,
        }
        login_cid = creds.get("login_customer_id")
        if login_cid:
            client_dict["login_customer_id"] = str(login_cid).replace("-", "").strip()

        gclient = GoogleAdsClient.load_from_dict(client_dict)
        service = gclient.get_service("GoogleAdsService")
        customer_id = "2909919094"  # DSU customer ID

        query = """
            SELECT
              campaign.id,
              campaign.name,
              campaign.status
            FROM campaign
        """
        response = service.search(customer_id=customer_id, query=query)
        live_courses = set()
        all_courses = set()
        for row in response:
            course = _map_campaign_to_course(row.campaign.name)
            if not course:
                continue
            all_courses.add(course)
            status_str = str(row.campaign.status).upper() if row.campaign.status else ""
            # GoogleAdsStatus enum: ENABLED, PAUSED, REMOVED, UNKNOWN
            if status_str in ("ENABLED", "CAMPAIGNSTATUS.ENABLED", "2"):
                live_courses.add(course)
        return {course: course in live_courses for course in all_courses}
    except Exception as e:
        logger.warning(f"DSU Google Ads campaign status fetch skipped or failed: {e}")
        return {}


def _fetch_google_ads_spend(start_date: str, end_date: str, live_only: bool = False) -> Dict[str, float]:
    """Fetch campaign-level spend from Google Ads, mapped to courses.
    GST (18%) is applied per-day on spend from 19-Jun-2026 onwards.
    
    Args:
        live_only: if True, only include enabled/live/active campaigns (Table 1).
                   if False, include all campaigns regardless of status (Table 2).
    
    Returns {course: spend_float}."""
    try:
        creds = _get_dsu_account_creds()
        from google.ads.googleads.client import GoogleAdsClient

        client_dict = {
            "developer_token": creds["developer_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "use_proto_plus": True,
        }
        # Only pass login_customer_id if a manager (MCC) account is linked.
        # DSU account 2909919094 has no linked manager, so sending an MCC id
        # causes USER_PERMISSION_DENIED.
        login_cid = creds.get("login_customer_id")
        if login_cid:
            client_dict["login_customer_id"] = str(login_cid).replace("-", "").strip()

        gclient = GoogleAdsClient.load_from_dict(client_dict)
        service = gclient.get_service("GoogleAdsService")
        customer_id = "2909919094"  # DSU customer ID

        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              segments.date,
              metrics.cost_micros
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        """
        response = service.search(customer_id=customer_id, query=query)
        course_spend = defaultdict(float)
        for row in response:
            cost = (row.metrics.cost_micros or 0) / 1_000_000.0
            if cost == 0:
                continue
            if live_only:
                status_str = str(row.campaign.status).lower() if row.campaign.status else ""
                if status_str not in ("enabled", "live", "active", "campaignstatus.enabled", "2"):
                    continue
            spend_date = str(row.segments.date)
            if spend_date >= DSU_GST_TRANSITION_DATE:
                cost = cost * GST_MULTIPLIER
            course = _map_campaign_to_course(row.campaign.name)
            if course:
                course_spend[course] += cost
        return dict(course_spend)
    except Exception as e:
        logger.warning(f"DSU Google Ads spend fetch skipped or failed: {e}")
        return {}


def _fetch_lsq_leads(start_date: str, end_date: str, account_id: int = None) -> Dict[str, int]:
    """Fetch leads from the local LeadSquared mirror.

    The local mirror is kept up-to-date by the scheduler. This makes InsightDesk
    reports load in milliseconds instead of waiting for the slow LSQ API.
    Falls back to direct API only if the mirror is empty or account_id is missing.
    """
    if account_id is None:
        return {}

    from backend.db.database import SessionLocal
    from backend.db.models import LeadSquaredLead
    from backend.services.lsq_mirror import count_leads_by_course

    db = SessionLocal()
    try:
        # Trigger a lightweight incremental sync if mirror is empty
        existing = db.query(LeadSquaredLead).filter(LeadSquaredLead.account_id == account_id).first()
        if not existing:
            logger.warning(f"LSQ mirror empty for account {account_id}, falling back to direct API")
            return _fetch_lsq_leads_direct(start_date, end_date, account_id)

        # Use the local mirror for fast filtering by CreatedOn date
        counts = count_leads_by_course(db, account_id, start_date, end_date)
        logger.info(f"LSQ mirror query for account {account_id} {start_date}-{end_date}: {counts}")
        return counts
    except Exception as e:
        logger.exception(f"LSQ mirror query failed for account {account_id}: {e}")
        return _fetch_lsq_leads_direct(start_date, end_date, account_id)
    finally:
        db.close()


def _fetch_lsq_leads_direct(start_date: str, end_date: str, account_id: int = None) -> Dict[str, int]:
    """Direct API fallback. Kept for cold-start / mirror rebuild scenarios."""
    cache_key = f"{account_id}_{start_date}_{end_date}"
    import time
    if cache_key in _LSQ_CACHE and (time.time() - _LSQ_CACHE_TIME.get(cache_key, 0)) < _LSQ_CACHE_TTL:
        logger.info(f"LSQ leads cache hit for {cache_key}")
        return _LSQ_CACHE[cache_key]

    import requests
    import sqlite3
    from datetime import date, timedelta
    from backend.services.config import load_config

    access_key = ""
    secret_key = ""
    base_url = ""

    if account_id:
        c = _db_connect()
        cur = c.cursor()
        cur.execute("SELECT lsq_access_key, lsq_secret_key, lsq_base_url FROM accounts WHERE id=%s", (account_id,))
        row = cur.fetchone()
        c.close()
        if row and row[0] and row[1]:
            access_key = row[0]
            secret_key = row[1]
            base_url = row[2] or ""
            logger.info(f"Using per-account LSQ creds for account_id={account_id}")

    if not access_key:
        cfg = load_config()
        access_key = cfg.get("leadsquared_access_key", "")
        secret_key = cfg.get("leadsquared_secret_key", "")
        base_url = cfg.get("leadsquared_base_url", "")
        logger.info(f"Using global LSQ creds for account_id={account_id}")

    if not base_url or not access_key or not secret_key:
        logger.warning(f"LeadSquared credentials not configured for account_id={account_id}; leads will be 0.")
        return {}
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v2"):
        base_url = base_url + "/v2"

    today_str = (date.today() + timedelta(days=1)).isoformat()
    search_from = start_date
    search_to = today_str

    url = f"{base_url}/LeadManagement.svc/Leads.RecentlyModified"
    course_leads = defaultdict(int)
    page = 1
    max_pages = 100
    total_records = None
    total_fetched = 0

    while page <= max_pages:
        payload = {
            "Parameter": {
                "FromDate": f"{search_from} 00:00:00",
                "ToDate": f"{search_to} 23:59:59",
            },
            "Columns": {
                "Include_CSV": "ProspectID,Source,SourceCampaign,CreatedOn,mx_Student_Source,mx_Student_Stage,mx_Application_Status,mx_Latest_Source,mx_Secondary_Source"
            },
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "1"},
        }
        success = False
        for attempt in range(3):
            try:
                r = requests.post(url, params={"accessKey": access_key, "secretKey": secret_key}, json=payload, timeout=120)
                r.raise_for_status()
                resp = r.json()
                success = True
                break
            except Exception as e:
                logger.warning(f"LeadSquared page {page} attempt {attempt+1} failed: {e}")
                time.sleep(5)
        if not success:
            logger.error(f"LeadSquared page {page} failed after 3 retries, stopping")
            break

        if total_records is None:
            total_records = resp.get("RecordCount", 0)
            logger.info(f"LSQ total modified leads {search_from} to {search_to}: {total_records}")
        records = resp.get("Leads", [])
        if not records:
            break

        for rec in records:
            props = {}
            for item in rec.get("LeadPropertyList", []):
                props[item.get("Attribute", "")] = item.get("Value", "")
            created = (props.get("CreatedOn") or "")
            source = (props.get("Source") or "")

            created_day = created[:10]
            if not (start_date <= created_day <= end_date):
                continue

            student_source = (props.get("mx_Student_Source") or "")
            latest_source = (props.get("mx_Latest_Source") or "")
            secondary_source = (props.get("mx_Secondary_Source") or "")
            source_combined = " ".join([source, student_source, latest_source, secondary_source]).upper()
            if "GGL" not in source_combined and "PROGRAMMATIC" not in source_combined:
                continue

            course = SOURCE_TO_COURSE.get(source)
            if not course:
                course = _map_campaign_to_course(source)
            if course:
                course_leads[course] += 1
            else:
                course_leads[source] += 1

        total_fetched += len(records)
        logger.info(f"LSQ page {page}: fetched {len(records)} (total: {total_fetched}/{total_records}), ggl/prog leads: {sum(course_leads.values())}")
        if len(records) < 1000:
            break
        page += 1

    result = dict(course_leads)
    _LSQ_CACHE[cache_key] = result
    _LSQ_CACHE_TIME[cache_key] = time.time()
    logger.info(f"LSQ leads cached for {cache_key}: {sum(result.values())} total GGL/Programmatic leads across {len(result)} courses")
    return result


def fetch_dsu_daily(report_date: str) -> List[Dict[str, Any]]:
    """Fetch daily course performance for a given date.
    Returns list of {course, leads, cpl, spend} sorted by spend desc.
    Only courses with spend > 0 or leads > 0 are included."""
    spend_data = _fetch_google_ads_spend(report_date, report_date)
    lead_data = _fetch_lsq_leads(report_date, report_date, account_id=1)

    rows = []
    for course in DSU_COURSES:
        spend = round(spend_data.get(course, 0))
        leads = lead_data.get(course, 0)
        if spend <= 0 and leads <= 0:
            continue
        cpl = round(spend / leads) if leads else None
        rows.append({"course": course, "leads": leads, "cpl": cpl, "spend": spend})

    rows.sort(key=lambda r: -r["spend"])
    return rows


def fetch_dsu_cumulative(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch cumulative course performance from start_date to end_date.
    Returns list of {course, leads, cpl, spend} sorted by spend desc.
    Only courses with spend > 0 or leads > 0 are included."""
    spend_data = _fetch_google_ads_spend(start_date, end_date)
    lead_data = _fetch_lsq_leads(start_date, end_date, account_id=1)

    rows = []
    all_courses = set(DSU_COURSES) | set(spend_data.keys()) | set(lead_data.keys())
    for course in all_courses:
        spend = round(spend_data.get(course, 0))
        leads = lead_data.get(course, 0)
        if spend <= 0 and leads <= 0:
            continue
        cpl = round(spend / leads) if leads else None
        rows.append({"course": course, "leads": leads, "cpl": cpl, "spend": spend})

    rows.sort(key=lambda r: -r["spend"])
    return rows


def fetch_dsu_daily_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch course performance for a date range (Table 1).
    All campaigns (enabled, paused, or otherwise) are included in spend.
    Returns list of {course, leads, cpl, spend} sorted by spend desc.
    Only courses with leads > 0 or spend > 0 are included."""
    spend_data = _fetch_google_ads_spend(start_date, end_date, live_only=False)
    lead_data = _fetch_lsq_leads(start_date, end_date, account_id=1)

    rows = []
    for course in DSU_COURSES:
        spend = round(spend_data.get(course, 0))
        leads = lead_data.get(course, 0)
        if spend <= 0 and leads <= 0:
            continue
        cpl = round(spend / leads) if leads else None
        rows.append({"course": course, "leads": leads, "cpl": cpl, "spend": spend})

    rows.sort(key=lambda r: -r["spend"])
    return rows


# ============================================================================
# Table 3: Lead Attribution Pivot (Student Source × Student Stage)
# ============================================================================

def _fetch_lsq_lead_details(start_date: str, end_date: str, account_id: int = 1) -> List[Dict[str, Any]]:
    """Fetch full lead details from the local LeadSquared mirror.

    Tables 3, 4, 6 need stage and application_status. The mirror stores these,
    so we query locally instead of hitting the slow LSQ API each time.
    """
    from backend.db.database import SessionLocal
    from backend.db.models import LeadSquaredLead
    from backend.services.lsq_mirror import get_lead_details

    db = SessionLocal()
    try:
        existing = db.query(LeadSquaredLead).filter(LeadSquaredLead.account_id == account_id).first()
        if not existing:
            logger.warning(f"LSQ mirror empty for account {account_id}, falling back to direct API for lead details")
            return _fetch_lsq_lead_details_direct(start_date, end_date, account_id)

        leads = get_lead_details(db, account_id, start_date, end_date)
        logger.info(f"LSQ mirror detail query for account {account_id} {start_date}-{end_date}: {len(leads)} leads")
        return leads
    except Exception as e:
        logger.exception(f"LSQ mirror detail query failed for account {account_id}: {e}")
        return _fetch_lsq_lead_details_direct(start_date, end_date, account_id)
    finally:
        db.close()


def _fetch_lsq_lead_details_direct(start_date: str, end_date: str, account_id: int = 1) -> List[Dict[str, Any]]:
    """Direct API fallback for lead details (cold-start / mirror rebuild)."""
    import requests
    import sqlite3
    import time as time_module
    from datetime import date, timedelta
    from backend.services.config import load_config

    access_key = ""
    secret_key = ""
    base_url = ""

    if account_id:
        c = _db_connect()
        cur = c.cursor()
        cur.execute("SELECT lsq_access_key, lsq_secret_key, lsq_base_url FROM accounts WHERE id=%s", (account_id,))
        row = cur.fetchone()
        c.close()
        if row and row[0] and row[1]:
            access_key = row[0]
            secret_key = row[1]
            base_url = row[2] or ""

    if not access_key:
        cfg = load_config()
        access_key = cfg.get("leadsquared_access_key", "")
        secret_key = cfg.get("leadsquared_secret_key", "")
        base_url = cfg.get("leadsquared_base_url", "")

    if not base_url or not access_key or not secret_key:
        logger.warning(f"LeadSquared credentials not configured for account_id={account_id}; returning empty list.")
        return []

    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v2"):
        base_url = base_url + "/v2"

    today_str = (date.today() + timedelta(days=1)).isoformat()
    search_from = start_date
    search_to = today_str

    url = f"{base_url}/LeadManagement.svc/Leads.RecentlyModified"
    all_leads = []
    page = 1
    max_pages = 100
    total_records = None
    total_fetched = 0

    while page <= max_pages:
        payload = {
            "Parameter": {
                "FromDate": f"{search_from} 00:00:00",
                "ToDate": f"{search_to} 23:59:59",
            },
            "Columns": {
                "Include_CSV": "ProspectID,Source,SourceCampaign,CreatedOn,mx_Student_Source,mx_Student_Stage,mx_Application_Status,mx_Latest_Source,mx_Secondary_Source"
            },
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "1"},
        }
        success = False
        for attempt in range(3):
            try:
                r = requests.post(url, params={"accessKey": access_key, "secretKey": secret_key}, json=payload, timeout=120)
                r.raise_for_status()
                resp = r.json()
                success = True
                break
            except Exception as e:
                logger.warning(f"LeadSquared detail page {page} attempt {attempt+1} failed: {e}")
                time_module.sleep(5)
        if not success:
            logger.error(f"LeadSquared detail page {page} failed after 3 retries, stopping")
            break

        if total_records is None:
            total_records = resp.get("RecordCount", 0)
            logger.info(f"LSQ detail total modified leads {search_from} to {search_to}: {total_records}")
        records = resp.get("Leads", [])
        if not records:
            break

        for rec in records:
            props = {}
            for item in rec.get("LeadPropertyList", []):
                props[item.get("Attribute", "")] = item.get("Value", "")
            created = (props.get("CreatedOn") or "")
            source = (props.get("Source") or "")
            created_day = created[:10]
            if not (start_date <= created_day <= end_date):
                continue

            student_source = (props.get("mx_Student_Source") or "")
            latest_source = (props.get("mx_Latest_Source") or "")
            secondary_source = (props.get("mx_Secondary_Source") or "")
            source_combined = " ".join([source, student_source, latest_source, secondary_source]).upper()
            if "GGL" not in source_combined and "PROGRAMMATIC" not in source_combined:
                continue

            stage = (props.get("mx_Student_Stage") or "")
            application_status = (props.get("mx_Application_Status") or "")
            course = SOURCE_TO_COURSE.get(source)
            if not course:
                course = _map_campaign_to_course(source)

            all_leads.append({
                "source": source,
                "student_source": student_source,
                "stage": stage,
                "application_status": application_status,
                "created_on": created_day,
                "course": course or source,
            })

        total_fetched += len(records)
        logger.info(f"LSQ detail page {page}: fetched {len(records)} (total: {total_fetched}/{total_records}), filtered leads: {len(all_leads)}")
        if len(records) < 1000:
            break
        page += 1

    logger.info(f"LSQ detail fetch complete: {len(all_leads)} GGL/Programmatic leads for {start_date} to {end_date}")
    return all_leads


def fetch_dsu_lead_pivot(start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch lead attribution pivot: Student Source × Student Stage (Table 3).
    Returns {stages: [...], rows: [{source, stage1: count, ...}], column_totals: {...}, grand_total: int}"""
    leads = _fetch_lsq_lead_details(start_date, end_date, account_id=1)

    matrix_map = defaultdict(lambda: defaultdict(int))
    unique_stages = set()

    for lead in leads:
        source = lead["source"]
        stage = lead["stage"]
        if source and stage:
            matrix_map[source][stage] += 1
            unique_stages.add(stage)

    all_stages = sorted(unique_stages)

    rows = []
    for source in sorted(matrix_map.keys()):
        row = {"source": source}
        for stage in all_stages:
            row[stage] = matrix_map[source].get(stage, 0)
        rows.append(row)

    column_totals = {}
    grand_total = 0
    for stage in all_stages:
        col_sum = sum(matrix_map[source].get(stage, 0) for source in matrix_map)
        column_totals[stage] = col_sum
        grand_total += col_sum

    return {
        "stages": all_stages,
        "rows": rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
    }


# ============================================================================
# Table 4: Application Submitted and CPA by Campus/Program
# ============================================================================

APPLICATION_SUBMITTED_STATUSES = {
    "application fee paid",
    "application submitted",
    "enrolled",
    "partially paid",
}

# Campus mapping: Campus 4 = B.Tech; Campus 3 = all others
CAMPUS4_COURSES = [
    {"key": "B.Tech", "display": "B.Tech", "target": 522},
]
CAMPUS3_COURSES = [
    {"key": "B.Sc Data Science", "display": "BSc Data Science", "target": 7},
    {"key": "M.Sc Data Science", "display": "MSc Data Science", "target": 6},
    {"key": "School of Law", "display": "LAW", "target": 10},
    {"key": "MBA", "display": "MBA", "target": 39},
    {"key": "BBA", "display": "BBA", "target": 12},
    {"key": "BCA", "display": "BCA", "target": 30},
    {"key": "JMC", "display": "JMC", "target": 7},
    {"key": "B.Sc Biological Sciences", "display": "B.Sc. Biological Science", "target": 9},
    {"key": "M.Sc Biological Sciences", "display": "M.Sc. Biological Science", "target": 6},
    {"key": "B.Sc Cyber Security", "display": "B.Sc. Cyber security", "target": 0},
    {"key": "M.Sc Cyber Security", "display": "M.Sc. Cyber security", "target": 0},
    {"key": "B.Com", "display": "B Com", "target": 5},
    {"key": "B.Design", "display": "B Design", "target": 13},
    {"key": "MCA", "display": "MCA", "target": 29},
]


def fetch_dsu_application_mis(start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch Application Submitted and CPA by Campus/Program (Table 4).
    Uses LeadSquared for submitted counts + cumulative spend (legacy + live API)."""
    leads = _fetch_lsq_lead_details(start_date, end_date, account_id=1)

    submitted_counts = defaultdict(int)
    for lead in leads:
        status_lower = (lead["application_status"] or "").lower().strip()
        if status_lower in APPLICATION_SUBMITTED_STATUSES:
            course = lead["course"]
            if course in ("Direct Traffic", "GGL-DSAT"):
                course = "B.Tech"
            submitted_counts[course] += 1

    # Use cumulative spend (legacy + live API) for consistency with Table 2
    cumulative_rows = fetch_dsu_cumulative_range(start_date, end_date)
    spend_data = {r["course"]: r["spend"] for r in cumulative_rows}

    def get_spend(key):
        return round(spend_data.get(key, 0))

    def build_rows(course_list):
        rows = []
        for c in course_list:
            submitted = submitted_counts.get(c["key"], 0)
            spend = get_spend(c["key"])
            cpa = round(spend / submitted) if submitted > 0 else 0
            rows.append({
                "course": c["display"],
                "submitted": submitted,
                "spend": spend,
                "cpa": cpa,
                "target": c["target"],
            })
        return rows

    def build_total(rows):
        total_submitted = sum(r["submitted"] for r in rows)
        total_spend = sum(r["spend"] for r in rows)
        total_cpa = round(total_spend / total_submitted) if total_submitted > 0 else 0
        total_target = sum(r["target"] for r in rows)
        return {
            "course": "Total",
            "submitted": total_submitted,
            "spend": total_spend,
            "cpa": total_cpa,
            "target": total_target,
        }

    campus4_rows = build_rows(CAMPUS4_COURSES)
    campus3_rows = build_rows(CAMPUS3_COURSES)
    campus4_total = build_total(campus4_rows)
    campus3_total = build_total(campus3_rows)

    grand_submitted = campus4_total["submitted"] + campus3_total["submitted"]
    grand_spend = campus4_total["spend"] + campus3_total["spend"]
    grand_cpa = round(grand_spend / grand_submitted) if grand_submitted > 0 else 0
    grand_target = campus4_total["target"] + campus3_total["target"]

    grand_total = {
        "course": "GRAND TOTAL",
        "submitted": grand_submitted,
        "spend": grand_spend,
        "cpa": grand_cpa,
        "target": grand_target,
    }

    return {
        "campus4_rows": campus4_rows,
        "campus3_rows": campus3_rows,
        "campus4_total": campus4_total,
        "campus3_total": campus3_total,
        "grand_total": grand_total,
    }


# ============================================================================
# Table 5: Budget MIS by Campus and Program
# ============================================================================

def fetch_dsu_budget_mis(start_date: str, end_date: str, db_session=None) -> Dict[str, Any]:
    """Fetch Budget MIS by Campus/Program (Table 5).
    Uses cumulative spend (legacy + live API) + dsu_budget_entries for campus budgets.
    Program status is based on actual Google Ads campaign status."""
    # Use cumulative spend for consistency with Table 2 and Table 4
    cumulative_rows = fetch_dsu_cumulative_range(start_date, end_date)
    spend_data = {r["course"]: r["spend"] for r in cumulative_rows}

    # Fetch live campaign status from Google Ads for each course
    live_status_by_course = _fetch_google_ads_campaign_status()

    # Fetch campus budgets from DB
    campus4_budget = 0.0
    campus3_budget = 0.0
    if db_session:
        from backend.db.models import DsuBudgetEntry
        entries = db_session.query(DsuBudgetEntry).all()
        for entry in entries:
            if entry.campus == "Campus 4":
                campus4_budget += entry.amount
            elif entry.campus == "Campus 3":
                campus3_budget += entry.amount

    def get_spend(key):
        return round(spend_data.get(key, 0))

    campus4_courses = [{"key": "B.Tech", "display": "B.Tech"}]
    campus3_courses = [{"key": c["key"], "display": c["display"]} for c in CAMPUS3_COURSES]

    def build_rows(course_list):
        rows = []
        for c in course_list:
            spend = get_spend(c["key"])
            # Prefer live Google Ads status; fall back to spend-based heuristic if API fails
            is_live = live_status_by_course.get(c["key"])
            if is_live is None:
                is_live = spend > 0
            status = "Live" if is_live else "Paused"
            rows.append({
                "course": c["display"],
                "status": status,
                "spend": spend,
            })
        return rows

    campus4_rows = build_rows(campus4_courses)
    campus3_rows = build_rows(campus3_courses)

    campus4_spend = sum(r["spend"] for r in campus4_rows)
    campus3_spend = sum(r["spend"] for r in campus3_rows)

    return {
        "campus4_rows": campus4_rows,
        "campus3_rows": campus3_rows,
        "campus4_total": {
            "course": "Total",
            "budget": round(campus4_budget),
            "spend": campus4_spend,
            "remaining": round(campus4_budget - campus4_spend),
        },
        "campus3_total": {
            "course": "Total",
            "budget": round(campus3_budget),
            "spend": campus3_spend,
            "remaining": round(campus3_budget - campus3_spend),
        },
        "grand_total": {
            "course": "GRAND TOTAL",
            "budget": round(campus4_budget + campus3_budget),
            "spend": campus4_spend + campus3_spend,
            "remaining": round((campus4_budget + campus3_budget) - (campus4_spend + campus3_spend)),
        },
    }


# ============================================================================
# Table 6: Lead Stage Summary
# ============================================================================

def fetch_dsu_lead_stages(start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch Lead Stage Summary (Table 6).
    Returns {rows: [{stage, count, percentage}], total: int}"""
    leads = _fetch_lsq_lead_details(start_date, end_date, account_id=1)

    counts_map = defaultdict(int)
    total = 0

    for lead in leads:
        stage = lead["stage"]
        if stage:
            counts_map[stage] += 1
            total += 1

    rows = []
    for stage, count in counts_map.items():
        percentage = round((count / total) * 100) if total > 0 else 0
        rows.append({"stage": stage, "count": count, "percentage": percentage})

    rows.sort(key=lambda r: (-r["count"], r["stage"]))

    return {
        "rows": rows,
        "total": total,
    }



def _month_key_lt(a: str, b: str) -> bool:
    """Return True if month key a is earlier than month key b (both 'MMM-YYYY')."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        ma, ya = a.split("-")
        mb, yb = b.split("-")
        ia = months.index(ma)
        ib = months.index(mb)
        return (int(ya), ia) < (int(yb), ib)
    except (ValueError, IndexError):
        return False

# ============================================================================
# Table 7: Monthly Spend Summary and Balance
# ============================================================================

DSU_INCEPTION_DATE = "2025-11-28"
DSU_NEW_ACCOUNT_START = "2026-04-01"  # new Google Ads account goes live
DSU_GST_TRANSITION_DATE = "2026-06-19"
GST_MULTIPLIER = 1.18


def _apply_gst_to_cost(raw_cost: float, date_str: str) -> float:
    """Apply GST rule: no GST till 18-Jun-2026 inclusive, 18% GST from 19-Jun-2026 onwards."""
    if not raw_cost or raw_cost <= 0:
        return 0.0
    if not date_str:
        return raw_cost
    from datetime import date as date_type
    try:
        spend_day = date_type.fromisoformat(date_str[:10])
        cutoff = date_type(2026, 6, 18)
        if spend_day <= cutoff:
            return raw_cost
        return raw_cost * GST_MULTIPLIER
    except (ValueError, TypeError):
        return raw_cost


def _fetch_google_ads_monthly_spend(start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch Google Ads spend for DSU grouped by month using segments.date.
    Applies per-day GST (no GST till 18-Jun-2026, 18% from 19-Jun-2026).
    Aggregates daily spend into monthly totals.
    Returns {month_key: spend_float} where month_key is 'MMM-YYYY' e.g. 'Jun-2026'."""
    creds = _get_dsu_account_creds()
    from google.ads.googleads.client import GoogleAdsClient
    from collections import defaultdict as dd

    client_dict = {
        "developer_token": creds["developer_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "use_proto_plus": True,
    }
    if creds.get("login_customer_id"):
        client_dict["login_customer_id"] = str(creds["login_customer_id"]).replace("-", "")

    gclient = GoogleAdsClient.load_from_dict(client_dict)
    service = gclient.get_service("GoogleAdsService")
    customer_id = "2909919094"

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          segments.date,
          metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
    """
    response = service.search(customer_id=customer_id, query=query)

    month_spend = dd(float)
    months_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    for row in response:
        cost = (row.metrics.cost_micros or 0) / 1_000_000.0
        if cost == 0:
            continue
        day_str = str(row.segments.date) if row.segments.date else ""
        if not day_str:
            continue

        # Apply per-day GST
        cost_with_gst = _apply_gst_to_cost(cost, day_str)

        # Normalize to month key
        try:
            d = date.fromisoformat(day_str)
            mk = f"{months_order[d.month - 1]}-{d.year}"
        except (ValueError, TypeError):
            continue

        month_spend[mk] += cost_with_gst

    return dict(month_spend)


def _normalize_month_key(month_str: str) -> str:
    """Normalize various month formats to 'MMM-YYYY' e.g. 'Jun-2026'."""
    if not month_str:
        return ""
    from datetime import date as date_type
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    s = month_str.strip()

    try:
        if "-" in s and len(s.split("-")) == 2:
            parts = s.split("-")
            month_name = parts[0]
            year = parts[1]
            if len(year) == 2:
                year = "20" + year
            for i, m in enumerate(months):
                if month_name.lower() == m.lower():
                    return f"{m}-{year}"
            return s

        if "-" in s and len(s.split("-")) == 3:
            d = date_type.fromisoformat(s)
            return f"{months[d.month - 1]}-{d.year}"

        d = date_type.fromisoformat(s)
        return f"{months[d.month - 1]}-{d.year}"
    except (ValueError, TypeError):
        return s


def fetch_dsu_monthly_summary(db_session=None) -> Dict[str, Any]:
    """Fetch Monthly Spend Summary and Balance (Table 7).
    - Nov-2025 to May-2026: uses fixed spend values from dsu_monthly_spend_fixed DB table (no GST)
    - Jun-2026 onwards: fetches from Google Ads API with per-day GST applied
    Uses dsu_budget_entries for received amounts.
    Returns monthly summaries with manual entries, grand totals."""
    from datetime import date as date_type
    from collections import OrderedDict

    # Step 1: Build google_spend_map
    # Fixed months: Nov-25 to May-26 (no GST, from DB)
    # API months: Jun-26 onwards (per-day GST, from Google Ads)
    google_spend_map = {}

    # Load fixed historical values from DB
    fixed_months = {}
    if db_session:
        from backend.db.models import DsuMonthlySpendFixed
        fixed_rows = db_session.query(DsuMonthlySpendFixed).all()
        for fr in fixed_rows:
            fixed_months[fr.month_key] = fr.google_spend
            # Normalize to MMM-YYYY format
            normalized = _normalize_month_key(fr.month_key)
            google_spend_map[normalized] = fr.google_spend

    # Fetch from Google Ads API for Apr-2026 onwards (new account start)
    # Use yesterday as end date to match Table 2/4/5 historical range
    from datetime import timedelta
    api_end = (date_type.today() - timedelta(days=1)).isoformat()
    api_start = "2026-04-01"
    api_spend = _fetch_google_ads_monthly_spend(api_start, api_end)

    # Step 1b: Auto-freeze completed months so historical values stop changing.
    # Any month fully in the past (before current month) is written to
    # dsu_monthly_spend_fixed. The current month stays on live API.
    if db_session:
        from backend.db.models import DsuMonthlySpendFixed
        current_month_key = _normalize_month_key(date_type.today().isoformat())
        for mk, spend in api_spend.items():
            # Only freeze months that are before the current month
            if _month_key_lt(mk, current_month_key):
                # If not already fixed, store it; if already fixed, keep the stored value
                if mk not in fixed_months:
                    db_session.add(DsuMonthlySpendFixed(month_key=mk, google_spend=round(spend, 2)))
                    fixed_months[mk] = spend
                    google_spend_map[mk] = spend
                else:
                    google_spend_map[mk] = fixed_months[mk]
            else:
                google_spend_map[mk] = spend
        db_session.commit()
    else:
        for mk, spend in api_spend.items():
            google_spend_map[mk] = spend

    # Merge API spend (overrides fixed if any overlap, but shouldn't overlap)

    # Step 2: Load budget entries
    budget_entries = []
    if db_session:
        from backend.db.models import DsuBudgetEntry
        db_entries = db_session.query(DsuBudgetEntry).order_by(DsuBudgetEntry.entry_date).all()
        for e in db_entries:
            campus_val = (e.campus or "").strip()
            campus_lower = campus_val.lower()
            if "campus 3" in campus_lower or campus_lower == "3":
                campus_val = "Campus 3"
            elif "campus 4" in campus_lower or campus_lower == "4":
                campus_val = "Campus 4"
            else:
                campus_val = "Campus 3"
            budget_entries.append({
                "id": e.id,
                "date": e.entry_date,
                "amount": e.amount,
                "invoice": e.invoice or "",
                "campus": campus_val,
            })

    # Step 3: Collect all month keys
    all_month_keys = set(google_spend_map.keys())
    for entry in budget_entries:
        mk = _normalize_month_key(entry["date"])
        if mk:
            all_month_keys.add(mk)

    months_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def sort_key(mk):
        try:
            parts = mk.split("-")
            m_name = parts[0]
            year = int(parts[1])
            m_idx = months_order.index(m_name) if m_name in months_order else 0
            return (year, m_idx)
        except (ValueError, IndexError):
            return (9999, 0)

    sorted_months = sorted(all_month_keys, key=sort_key)

    # Step 4: Build monthly summaries
    monthly_summaries = {}
    for mk in sorted_months:
        received_monthly = sum(
            e["amount"] for e in budget_entries
            if _normalize_month_key(e["date"]) == mk
        )
        google_spend = google_spend_map.get(mk, 0.0)
        meta_spend = 0.0
        total_spend = google_spend + meta_spend
        available_balance = received_monthly - total_spend

        monthly_summaries[mk] = {
            "month_key": mk,
            "received_monthly": round(received_monthly),
            "google_spend": round(google_spend),
            "meta_spend": round(meta_spend),
            "total_spend": round(total_spend),
            "available_balance": round(available_balance),
        }

    # Step 5: Build table rows (one row per budget entry, virtual rows for months with no entries)
    table7_rows = []
    for mk in sorted_months:
        summary = monthly_summaries[mk]
        entries_for_month = [e for e in budget_entries if _normalize_month_key(e["date"]) == mk]
        entries_for_month.sort(key=lambda e: (e["date"], 0 if e["campus"] == "Campus 4" else 1))

        if not entries_for_month:
            year_part = mk.split("-")[1] if "-" in mk else "2026"
            m_name = mk.split("-")[0] if "-" in mk else "Jan"
            m_idx = months_order.index(m_name) if m_name in months_order else 0
            virtual_date = f"{year_part}-{m_idx + 1:02d}-01"
            table7_rows.append({
                "id": f"virtual_{mk}",
                "date": virtual_date,
                "amount": 0,
                "invoice": "-",
                "campus": "-",
                "is_virtual": True,
                "is_first_in_month": True,
                "month_rowspan": 1,
                "month_key": mk,
                **summary,
            })
        else:
            for idx, entry in enumerate(entries_for_month):
                table7_rows.append({
                    "id": entry["id"],
                    "date": entry["date"],
                    "amount": entry["amount"],
                    "invoice": entry["invoice"] or "-",
                    "campus": entry["campus"],
                    "is_virtual": False,
                    "is_first_in_month": (idx == 0),
                    "month_rowspan": len(entries_for_month),
                    "month_key": mk,
                    **summary,
                })

    grand_total_amount = sum(e["amount"] for e in budget_entries)
    grand_total_google = sum(s["google_spend"] for s in monthly_summaries.values())
    grand_total_meta = sum(s["meta_spend"] for s in monthly_summaries.values())
    grand_total_total_spend = sum(s["total_spend"] for s in monthly_summaries.values())
    grand_total_balance = grand_total_amount - grand_total_total_spend

    return {
        "rows": table7_rows,
        "grand_total": {
            "amount": round(grand_total_amount),
            "google_spend": round(grand_total_google),
            "meta_spend": round(grand_total_meta),
            "total_spend": round(grand_total_total_spend),
            "available_balance": round(grand_total_balance),
        },
    }


def _fetch_legacy_spend(start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch hardcoded legacy spend (old Google Ads account, Nov-25 to Mar-26).
    Returns {course: spend_float}."""
    from backend.db.database import SessionLocal
    from backend.db.models import DsuLegacySpend

    db = SessionLocal()
    try:
        entries = db.query(DsuLegacySpend).all()
        course_spend = defaultdict(float)
        for entry in entries:
            # Month is stored as "2025-11" format; check if it overlaps with requested range
            entry_month_start = entry.month + "-01"
            # Simple check: if the month falls within [start_date, end_date]
            if entry.month >= start_date[:7] and entry.month <= end_date[:7]:
                course_spend[entry.course] += entry.spend
        return dict(course_spend)
    except Exception as e:
        logger.warning(f"Legacy spend fetch failed: {e}")
        return {}
    finally:
        db.close()


def fetch_dsu_cumulative_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch cumulative course performance for a date range (Table 2).

    For the default "from inception to yesterday" range, the exact historical
    raw data is used so the report matches the client's shared report.

    For custom ranges, spend = legacy spend (old account, Nov-25 to Mar-26) +
    live Google Ads spend (current account, Apr-26 onwards) with GST applied
    from 19-Jun-2026.

    Returns list of {course, leads, cpl, spend} sorted by spend desc.
    Only courses with spend > 0 or leads > 0 are included."""
    from datetime import date as date_type

    yesterday = (date_type.today() - __import__('datetime').timedelta(days=1)).isoformat()
    is_default_range = (start_date == DSU_INCEPTION_DATE and end_date == yesterday)

    if is_default_range:
        # Default range: merge old-account historical spend (from Excel) +
        # new-account live API spend + leads from LSQ mirror.
        from backend.db.database import SessionLocal
        from backend.db.models import DsuTable2Historical

        # Live API spend (new account, Apr-26 onwards, GST-adjusted)
        live_spend = _fetch_google_ads_spend(DSU_NEW_ACCOUNT_START, end_date)

        db = SessionLocal()
        try:
            historical = db.query(DsuTable2Historical).all()
            # old-account cumulative spend per course
            old_spend_by_course = {h.course: h.spend for h in historical}
        finally:
            db.close()

        # Leads from LSQ mirror (inception to yesterday)
        lead_data = _fetch_lsq_leads(start_date, end_date, account_id=1)

        # Merge spend: old-account historical + new-account live
        merged_spend = defaultdict(float)
        for course, spend in old_spend_by_course.items():
            merged_spend[course] += spend
        for course, spend in live_spend.items():
            merged_spend[course] += spend

        # Build rows
        rows = []
        all_courses = set(DSU_COURSES) | set(merged_spend.keys()) | set(lead_data.keys())
        for course in all_courses:
            spend = round(merged_spend.get(course, 0))
            leads = lead_data.get(course, 0)
            if spend <= 0 and leads <= 0:
                continue
            cpl = round(spend / leads) if leads else None
            rows.append({"course": course, "leads": leads, "cpl": cpl, "spend": spend})

        rows.sort(key=lambda r: -r["spend"])
        return rows

    # Custom range: compute from legacy + live API
    live_spend = _fetch_google_ads_spend(start_date, end_date)
    legacy_spend = _fetch_legacy_spend(start_date, end_date)
    
    merged_spend = defaultdict(float)
    for course, spend in legacy_spend.items():
        merged_spend[course] += spend
    for course, spend in live_spend.items():
        merged_spend[course] += spend
    
    lead_data = _fetch_lsq_leads(start_date, end_date, account_id=1)

    rows = []
    all_courses = set(DSU_COURSES) | set(merged_spend.keys()) | set(lead_data.keys())
    for course in all_courses:
        spend = round(merged_spend.get(course, 0))
        leads = lead_data.get(course, 0)
        if spend <= 0 and leads <= 0:
            continue
        cpl = round(spend / leads) if leads else None
        rows.append({"course": course, "leads": leads, "cpl": cpl, "spend": spend})

    rows.sort(key=lambda r: -r["spend"])
    return rows