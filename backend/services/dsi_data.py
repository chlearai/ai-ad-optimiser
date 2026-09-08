"""
Live data fetcher for DSI course performance report.
Pulls campaign spend from Google Ads API and leads from LeadSquared.
Maps both to the DSI department/course taxonomy with department rollup.
"""
import json
import logging
from datetime import date, timedelta, datetime
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

from backend.services.crypto import decrypt
from backend.db.database import get_raw_connection

logger = logging.getLogger("AdOptima")


def _db_connect():
    """Return a DB-API connection. Works for SQLite and PostgreSQL."""
    return get_raw_connection()

DSI_ACCOUNT_ID = 2
DSI_CUSTOMER_ID = "1917462211"
DSI_INCEPTION = "2026-01-08"
DSI_GST_TRANSITION = "2026-06-19"
DSI_NEW_ACCOUNT_START = "2026-04-01"  # new Google Ads account goes live
GST_MULTIPLIER = 1.18

# DSI Department Sort Order
DEPT_ORDER = {
    "dsit": 2,
    "dsce": 3,
    "dsca": 4,
    "dscasc - ug": 5,
    "dscasc - masters": 6,
}

# DSI Course list (after department rollup, these are the row labels)
DSI_COURSES = [
    "DSCE", "DSIT",
    "B. Arch", "Bachelor of Architecture Campus 1", "M.Arch",
    "B.Com", "B.Com Evening Programs", "B.Sc (PCM)", "BBA", "BCA", "BCA Evening Programs",
    "MBA", "MCA", "M.Com",
]

# Keyword -> course mapping for DSI (order matters: more specific first)
DSI_CAMPAIGN_KEYWORDS = [
    # Engineering specific (before rollup)
    ("medical", "Medical Electronics Engineering"),
    ("ece", "Electronics and Communications Engineering"),
    ("telecom", "Electronics and Communications Engineering"),
    ("electronicsandcommunication", "Electronics and Communications Engineering"),
    ("eee", "Electrical and Electronics Engineering"),
    ("electrical", "Electrical and Electronics Engineering"),
    ("info", "Information Science"),
    ("ise", "Information Science"),
    ("automobile", "Automotive Engineering"),
    ("automotive", "Automotive Engineering"),
    ("mech", "Mechanical Engineering"),
    ("cse", "Computer Science"),
    ("computer", "Computer Science"),
    ("civil", "Civil Engineering"),
    ("chemical", "Chemical Engineering"),
    ("biotech", "Biotechnology"),
    # Architecture (before generic dsca/dsce/dsit fallbacks)
    ("b.arch", "B. Arch"),
    ("barch", "B. Arch"),
    ("arch", "B. Arch"),
    ("m.arch", "M.Arch"),
    ("march", "M.Arch"),
    # Degree courses
    ("bcaeve", "BCA Evening"),
    ("bcaevening", "BCA Evening"),
    ("bca", "BCA"),
    ("bcomeve", "B.Com Evening"),
    ("bcom", "B.Com"),
    ("b.com", "B.Com"),
    ("b.com evening", "B.Com Evening"),
    ("bba", "BBA"),
    ("bba1", "BBA"),
    ("bba2", "BBA"),
    ("bba3", "BBA"),
    ("mba", "MBA"),
    ("mca", "MCA"),
    ("mcom", "M.Com"),
    ("m.com", "M.Com"),
    ("bsc(pcm)", "B.Sc (PCM)"),
    ("pcm", "B.Sc (PCM)"),
    ("bsc", "B.Sc"),
    # DSIT Diploma must be detected before generic DSIT
    ("dsit-diploma", "DSIT - Diploma"),
    ("dsit diploma", "DSIT - Diploma"),
    ("diploma", "DSIT - Diploma"),
    # Department-level fallbacks
    ("dscasc", "DSCASC - UG"),
    ("dscads", "DSCA"),
    ("dscds", "DSCA"),
    ("dsat", "DSCA"),
    ("dsca", "DSCA"),
    ("dsce", "DSCE"),
    ("engg", "DSCE"),
    ("dsit", "DSIT"),
]

# DSI Source -> course mapping for LeadSquared Student Source
DSI_SOURCE_TO_COURSE = {
    "GGL-BTech": "DSIT",
    "GGL-MBA": "MBA",
    "GGL-BCA": "BCA",
    "GGL-Law": "DSIT",
    "GGL_BBA": "BBA",
    "GGL-DSAT": "DSCA",
    "GGL-B.Pharm": "DSCE",
    "GGL-B.Design": "DSCA",
    "GGL-JMC": "DSCASC - UG",
    "GGL-MCA": "MCA",
    "GGL_B.Com": "B.Com",
}

# DSI fallback department mapping
DSI_FALLBACK_DEPT = {
    "automobile engineering": "DSCE",
    "biotechnology": "DSCE",
    "chemical engineering": "DSCE",
    "electronics and telecommunication engineering": "DSCE",
    "electronics and telecom engineering": "DSCE",
    "mechanical engineering": "DSCE",
    "eee engineering": "DSCE",
    "medical electronics engineering": "DSCE",
    "electronics and instrumentation engineering": "DSCE",
    "automotive engineering": "DSCE",
    "information science": "DSCE",
    "civil engineering": "DSIT",
    "computer science": "DSIT",
    "computer science and engineering - cyber security": "DSIT",
    "electrical and electronics": "DSIT",
    "electrical and electronics engineering": "DSIT",
    "electronics and communication": "DSIT",
    "electronics and communications engineering": "DSIT",
    "mechanical": "DSIT",
    "diploma": "DSIT",
    "dsit": "DSIT",
    "dsit - diploma": "DSIT",
    "electrical & electronics engineering": "DSIT",
    "bachelor of architecture campus 1": "DSCA",
    "b. arch": "DSCA",
    "m.arch": "DSCA",
    "arch": "DSCA",
    "b.com": "DSCASC - UG",
    "b.com evening": "DSCASC - UG",
    "b.com evening programs": "DSCASC - UG",
    "b.sc (pcm)": "DSCASC - UG",
    "bba": "DSCASC - UG",
    "bca": "DSCASC - UG",
    "bca evening": "DSCASC - UG",
    "bca evening programs": "DSCASC - UG",
    "b.sc": "DSCASC - UG",
    "bca eve": "DSCASC - UG",
    "b.com eve": "DSCASC - UG",
    "mba": "DSCASC - Masters",
    "mca": "DSCASC - Masters",
    "m.com": "DSCASC - Masters",
    "dscasc - ug": "DSCASC - UG",
    "dscasc - masters": "DSCASC - Masters",
}

# DSI course normalization
DSI_COURSE_NORMALISE = {
    "b.com": "B.Com",
    "bcom": "B.Com",
    "b.com evening": "B.Com Evening",
    "b.sc(pcm)": "B.Sc (PCM)",
    "b.sc (pcm)": "B.Sc (PCM)",
    "b. arch": "B. Arch",
    "b.arch": "B. Arch",
    "eee": "Electrical and Electronics Engineering",
    "ece": "Electronics and Communications Engineering",
    "cse": "Computer Science",
    "ise": "Information Science",
}


def _map_dsi_campaign_to_course(name: str) -> Optional[str]:
    """Map a DSI campaign name to a course using word-aware keyword matching.

    Campaign names are tokenized (split on |, -, _, spaces, dots where they act
    as separators) so that a keyword like 'arch' does not match inside the word
    'search'.  Multi-word keywords (e.g. 'b.com evening', 'dsit diploma') are
    matched as contiguous token sequences.
    """
    name = (name or "").strip()
    if not name:
        return None

    # Check exact source mapping first
    if name in DSI_SOURCE_TO_COURSE:
        return DSI_SOURCE_TO_COURSE[name]

    # Normalize to tokens: lowercase, treat separators as spaces, strip
    # surrounding punctuation like parentheses and quotes.
    import re
    norm = re.sub(r"[\|\-_\s]+", " ", name.lower())
    tokens = [re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", t) for t in norm.split() if t]

    def _tokens_for_kw(kw: str) -> List[str]:
        return [re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", t)
                for t in re.sub(r"[\|\-_\s]+", " ", kw.lower()).split() if t]

    # Try each keyword as a contiguous token sequence
    for kw, course in DSI_CAMPAIGN_KEYWORDS:
        kw_tokens = _tokens_for_kw(kw)
        if not kw_tokens:
            continue
        for i in range(len(tokens) - len(kw_tokens) + 1):
            if tokens[i:i + len(kw_tokens)] == kw_tokens:
                return course

    # Final fallback: standalone 'arch' token
    if "arch" in tokens:
        return "B. Arch"

    return None


def _get_dsi_dept(course: str) -> str:
    """Get the department for a DSI course name.

    Department-level course labels (DSCE, DSIT) return themselves.
    """
    if not course:
        return ""
    low = course.lower().strip()
    if low in ("dsce", "dsit"):
        return course.upper()
    if low in DSI_FALLBACK_DEPT:
        return DSI_FALLBACK_DEPT[low]
    return ""


def _rollup_to_dept(course: str) -> str:
    """For DSI, roll up generic DSCE courses to the department label.

    DSIT generic and diploma campaigns all roll up to the course label
    'DSIT - Diploma' while keeping department DSIT.
    """
    if not course:
        return course
    dept = _get_dsi_dept(course)
    if dept == "DSCE":
        return "DSCE"
    if dept == "DSIT":
        return "DSIT - Diploma"
    return course


def _get_dsi_account_creds() -> Dict[str, Any]:
    """Load DSI's Google Ads credentials from the DB."""
    c = _db_connect()
    cur = c.cursor()
    cur.execute("SELECT google_credentials FROM accounts WHERE name='DSI'")
    row = cur.fetchone()
    c.close()
    if not row or not row[0]:
        raise ValueError("DSI has no stored google_credentials")
    return json.loads(decrypt(row[0]))


def _apply_gst(raw_cost: float, date_str: str) -> float:
    """Apply GST rule: no GST till 18-Jun-2026 inclusive, 18% from 19-Jun-2026 onwards."""
    if not raw_cost or raw_cost <= 0:
        return 0.0
    if not date_str:
        return raw_cost
    try:
        spend_day = date.fromisoformat(date_str[:10])
        cutoff = date(2026, 6, 18)
        if spend_day <= cutoff:
            return raw_cost
        return raw_cost * GST_MULTIPLIER
    except (ValueError, TypeError):
        return raw_cost


# ============================================================================
# Google Ads spend fetch
# ============================================================================

def _fetch_dsi_google_ads_campaign_status() -> Dict[str, bool]:
    """Return {course: is_live} based on actual Google Ads campaign statuses for DSI.

    A course is considered live if it has at least one ENABLED campaign.
    Mapping uses the same keyword-based rules as spend reporting.
    """
    try:
        creds = _get_dsi_account_creds()
        from google.ads.googleads.client import GoogleAdsClient

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
        customer_id = DSI_CUSTOMER_ID

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
            course = _map_dsi_campaign_to_course(row.campaign.name)
            if not course:
                continue
            rolled_up = _rollup_to_dept(course)
            all_courses.add(rolled_up)
            status_str = str(row.campaign.status).upper() if row.campaign.status else ""
            # GoogleAdsStatus enum: ENABLED, PAUSED, REMOVED, UNKNOWN
            if status_str in ("ENABLED", "CAMPAIGNSTATUS.ENABLED", "2"):
                live_courses.add(rolled_up)
        return {course: course in live_courses for course in all_courses}
    except Exception as e:
        logger.warning(f"DSI Google Ads campaign status fetch skipped or failed: {e}")
        return {}


def _fetch_dsi_google_ads_spend(start_date: str, end_date: str, live_only: bool = False) -> Dict[str, float]:
    """Fetch campaign-level spend from Google Ads for DSI, mapped to courses with dept rollup.

    Args:
        live_only: if True, only include enabled/live/active campaigns (Table 1).
                   if False, include all campaigns regardless of status (Table 2).

    Returns {course: spend_float}."""
    creds = _get_dsi_account_creds()
    from google.ads.googleads.client import GoogleAdsClient

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
    customer_id = DSI_CUSTOMER_ID

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
        day_str = str(row.segments.date) if row.segments.date else ""
        cost_with_gst = _apply_gst(cost, day_str)
        campaign_name = row.campaign.name
        course = _map_dsi_campaign_to_course(campaign_name)
        if course:
            rolled_up = _rollup_to_dept(course)
            course_spend[rolled_up] += cost_with_gst
        else:
            course_spend[campaign_name] += cost_with_gst

    return dict(course_spend)


def _fetch_dsi_google_ads_spend_daily(start_date: str, end_date: str) -> Dict[str, Dict[str, float]]:
    """Fetch campaign-level spend from Google Ads for DSI grouped by date.
    Returns {date_str: {course: spend_float}}."""
    creds = _get_dsi_account_creds()
    from google.ads.googleads.client import GoogleAdsClient

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
    customer_id = DSI_CUSTOMER_ID

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

    daily_spend = defaultdict(lambda: defaultdict(float))
    for row in response:
        cost = (row.metrics.cost_micros or 0) / 1_000_000.0
        if cost == 0:
            continue
        day_str = str(row.segments.date) if row.segments.date else ""
        cost_with_gst = _apply_gst(cost, day_str)
        campaign_name = row.campaign.name
        course = _map_dsi_campaign_to_course(campaign_name)
        if course:
            rolled_up = _rollup_to_dept(course)
            daily_spend[day_str][rolled_up] += cost_with_gst
        else:
            daily_spend[day_str][campaign_name] += cost_with_gst

    return {k: dict(v) for k, v in daily_spend.items()}


def _fetch_dsi_google_ads_monthly_spend(start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch Google Ads spend for DSI grouped by month.
    Returns {month_key: spend_float} where month_key is 'MMM-YYYY'."""
    creds = _get_dsi_account_creds()
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
    customer_id = DSI_CUSTOMER_ID

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          segments.month,
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
        month_str = str(row.segments.month) if row.segments.month else ""
        if not month_str:
            continue
        # Normalize month key
        try:
            d = date.fromisoformat(month_str)
            mk = f"{months_order[d.month - 1]}-{d.year}"
        except (ValueError, TypeError):
            mk = month_str

        # Apply GST: months after Jun-2026 get 18%
        try:
            parts = mk.split("-")
            year = int(parts[1])
            m_idx = months_order.index(parts[0]) if parts[0] in months_order else 0
            if year > 2026 or (year == 2026 and m_idx > 5):
                cost = cost * GST_MULTIPLIER
        except (ValueError, IndexError):
            pass

        month_spend[mk] += cost

    return dict(month_spend)


# ============================================================================
# LeadSquared lead fetch with full details
# ============================================================================

def _count_dsi_override_leads(report_date: str, source_campaigns: List[str]) -> int:
    """Count leads in the DSI mirror matching specific source campaigns on a date.

    Used for temporary business overrides where campaign-level leads are not
    captured by the standard course mapping.
    """
    from backend.db.database import SessionLocal
    from backend.db.models import LeadSquaredLead

    db = SessionLocal()
    try:
        rows = (
            db.query(LeadSquaredLead)
            .filter(
                LeadSquaredLead.account_id == DSI_ACCOUNT_ID,
                LeadSquaredLead.created_on == report_date,
                LeadSquaredLead.source_campaign.in_(source_campaigns),
            )
            .all()
        )
        return len(rows)
    except Exception as e:
        logger.warning(f"DSI override lead count failed: {e}")
        return 0
    finally:
        db.close()


def _fetch_dsi_lsq_leads(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch DSI lead details from the local LeadSquared mirror.

    The mirror stores stage, application_status, resolved course, and department.
    Falls back to direct API only if the mirror is empty.
    """
    from backend.db.database import SessionLocal
    from backend.db.models import LeadSquaredLead
    from backend.services.lsq_mirror import get_lead_details

    db = SessionLocal()
    try:
        existing = (
            db.query(LeadSquaredLead)
            .filter(LeadSquaredLead.account_id == DSI_ACCOUNT_ID)
            .first()
        )
        if not existing:
            logger.warning("DSI LSQ mirror empty, falling back to direct API")
            return _fetch_dsi_lsq_leads_direct(start_date, end_date)

        leads = get_lead_details(db, DSI_ACCOUNT_ID, start_date, end_date)
        # Enrich with department for callers that expect it
        for lead in leads:
            lead["department"] = _get_dsi_dept(lead["course"])
            lead["raw_course"] = lead.get("course", "")
        logger.info(f"DSI LSQ mirror query for {start_date}-{end_date}: {len(leads)} leads")
        return leads
    except Exception as e:
        logger.exception(f"DSI LSQ mirror query failed: {e}")
        return _fetch_dsi_lsq_leads_direct(start_date, end_date)
    finally:
        db.close()


def _fetch_dsi_lsq_leads_direct(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Direct API fallback for DSI leads (cold-start / mirror rebuild)."""
    import requests
    from backend.services.config import load_config

    access_key = ""
    secret_key = ""
    base_url = ""

    c = _db_connect()
    cur = c.cursor()
    cur.execute("SELECT lsq_access_key, lsq_secret_key, lsq_base_url FROM accounts WHERE id=%s", (DSI_ACCOUNT_ID,))
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
        logger.warning("LeadSquared credentials not configured for DSI; returning empty list.")
        return []

    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v2"):
        base_url = base_url + "/v2"

    today_str = (date.today() + timedelta(days=1)).isoformat()
    url = f"{base_url}/LeadManagement.svc/Leads.RecentlyModified"

    all_leads = []
    page = 1
    max_pages = 100
    total_records = None

    while page <= max_pages:
        payload = {
            "Parameter": {
                "FromDate": f"{start_date} 00:00:00",
                "ToDate": f"{today_str} 23:59:59",
            },
            "Columns": {
                "Include_CSV": "ProspectID,Source,SourceCampaign,CreatedOn,ModifiedOn,mx_Student_Source,mx_Student_Stage,mx_Application_Status,mx_Latest_Source,mx_Secondary_Source,mx_Application_Course,mx_Application_Program"
            },
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "1"},
        }
        success = False
        import time as time_module
        for attempt in range(3):
            try:
                r = requests.post(url, params={"accessKey": access_key, "secretKey": secret_key}, json=payload, timeout=120)
                r.raise_for_status()
                resp = r.json()
                success = True
                break
            except Exception as e:
                logger.warning(f"DSI LSQ page {page} attempt {attempt+1} failed: {e}")
                time_module.sleep(5)
        if not success:
            logger.error(f"DSI LSQ page {page} failed after 3 retries, stopping")
            break

        if total_records is None:
            total_records = resp.get("RecordCount", 0)
            logger.info(f"DSI LSQ total modified leads: {total_records}")
        records = resp.get("Leads", [])
        if not records:
            break

        for rec in records:
            props = {}
            for item in rec.get("LeadPropertyList", []):
                props[item.get("Attribute", "")] = item.get("Value", "")
            created = (props.get("CreatedOn") or "")
            source = (props.get("Source") or "")
            # DSI CreatedOn format: DD-MM-YYYY HH:MM
            created_day = ""
            if created:
                try:
                    created_day = datetime.strptime(created.strip(), "%d-%m-%Y %H:%M").strftime("%Y-%m-%d")
                except ValueError:
                    created_day = created[:10]
            if not (start_date <= created_day <= end_date):
                continue

            student_source = (props.get("mx_Student_Source") or "")
            latest_source = (props.get("mx_Latest_Source") or "")
            secondary_source = (props.get("mx_Secondary_Source") or "")
            source_campaign = (props.get("SourceCampaign") or "").strip()
            source_combined = " ".join([source, student_source, latest_source, secondary_source]).upper()
            if "GGL" not in source_combined and "PROGRAMMATIC" not in source_combined:
                continue

            stage = (props.get("mx_Student_Stage") or "")
            application_status = (props.get("mx_Application_Status") or "")
            app_course = (props.get("mx_Application_Course") or "").strip()
            app_program = (props.get("mx_Application_Program") or "").strip()

            # DSI primary classification: Source Campaign encodes the department/course.
            # Fallback to Application Course / Program only when source campaign is absent.
            raw_course = ""
            if source_campaign:
                mapped = _map_dsi_campaign_to_course(source_campaign)
                if mapped:
                    raw_course = mapped

            if not raw_course and app_course and app_course != "--":
                raw_course = app_course
            elif not raw_course and app_program and app_program != "--":
                raw_course = app_program

            if not raw_course:
                mapped = _map_dsi_campaign_to_course(source)
                raw_course = mapped or source

            low_course = raw_course.lower().strip()
            if low_course in DSI_COURSE_NORMALISE:
                raw_course = DSI_COURSE_NORMALISE[low_course]

            clean_course = _rollup_to_dept(raw_course)
            department = _get_dsi_dept(clean_course) or _get_dsi_dept(raw_course)

            all_leads.append({
                "source": source,
                "student_source": student_source,
                "stage": stage,
                "application_status": application_status,
                "created_on": created_day,
                "course": clean_course,
                "department": department,
                "raw_course": raw_course,
            })

        if len(records) < 1000:
            break
        page += 1

    logger.info(f"DSI LSQ direct fetch complete: {len(all_leads)} GGL/Programmatic leads for {start_date} to {end_date}")
    return all_leads


# ============================================================================
# Table 1 & 2: Daily and Cumulative Campaign Metrics
# ============================================================================

def _dept_sort_key(course):
    """Sort key for DSI courses using DEPT_ORDER."""
    low = course.lower().strip()
    # Match department prefix for rolled-up labels like "DSIT - Diploma"
    dept_key = low.split(" - ")[0] if " - " in low else low
    return (DEPT_ORDER.get(dept_key, 99), course)


def fetch_dsi_daily_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch DSI course performance for a date range (Table 1).
    All campaigns (enabled, paused, or otherwise) are included in spend.
    Returns list of {department, course, leads, cpl, spend} sorted by dept order.
    Only rows with spend > 0 or leads > 0 are included."""
    spend_data = _fetch_dsi_google_ads_spend(start_date, end_date, live_only=False)
    leads = _fetch_dsi_lsq_leads(start_date, end_date)

    leads_by_course = defaultdict(int)
    for lead in leads:
        normalized = _rollup_to_dept(lead["course"])
        leads_by_course[normalized] += 1

    # Temporary business override (07-Aug-2026): add 2 leads to MBA sourced from
    # campaigns CHLEAR_DSCASC_PG_MCA_Search_Brand_Generic_20260805 and
    # CHLEAR_DSCASC_PG_MBA_Search_Brand_Generic_20260805.
    # TODO: Remove once LeadSquared source-campaign mapping is aligned.
    if start_date <= "2026-08-07" <= end_date:
        override_count = 2
        mba_label = "MBA"
        leads_by_course[mba_label] = leads_by_course.get(mba_label, 0) + override_count

    normalized_spend = {}
    for course, spend in spend_data.items():
        normalized = _rollup_to_dept(course)
        normalized_spend[normalized] = normalized_spend.get(normalized, 0) + spend

    all_courses = set(normalized_spend.keys()) | set(leads_by_course.keys())
    rows = []
    for course in all_courses:
        spend = round(normalized_spend.get(course, 0))
        leads_count = leads_by_course.get(course, 0)
        cpl = round(spend / leads_count) if leads_count else None
        dept = _get_dsi_dept(course)
        if spend > 0 or leads_count > 0:
            rows.append({
                "department": dept,
                "course": course,
                "leads": leads_count,
                "cpl": cpl,
                "spend": spend,
            })

    rows.sort(key=lambda r: _dept_sort_key(r["course"]))
    _annotate_dept_cpl_spend(rows)
    return rows


def _annotate_dept_cpl_spend(rows: List[Dict[str, Any]]) -> None:
    """Add dept_leads, dept_spend, dept_cpl to each row so the frontend can
    merge CPL/Spend cells for departments that span multiple courses
    (e.g. DSCASC - UG where some courses have 0 spend)."""
    dept_leads = defaultdict(int)
    dept_spend = defaultdict(float)
    for r in rows:
        dept_leads[r["department"]] += r.get("leads", 0)
        dept_spend[r["department"]] += r.get("spend", 0)
    for r in rows:
        d = r["department"]
        d_leads = dept_leads.get(d, 0)
        d_spend = dept_spend.get(d, 0)
        r["dept_leads"] = d_leads
        r["dept_spend"] = round(d_spend)
        r["dept_cpl"] = round(d_spend / d_leads) if d_leads else None


def fetch_dsi_cumulative_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """Fetch DSI cumulative course performance for a date range (Table 2).

    For the default "from inception to yesterday" range, old-account historical
    spend (from Excel-seeded dsi_legacy_spend) is merged with new-account live
    API spend. Leads come from the LSQ mirror.

    Returns list of {department, course, leads, cpl, spend} sorted by dept order.
    Only rows with spend > 0 or leads > 0 are included."""
    from datetime import date as date_type

    yesterday = (date_type.today() - __import__('datetime').timedelta(days=1)).isoformat()
    is_default_range = (start_date == DSI_INCEPTION and end_date == yesterday)

    # Live API spend (new account, Apr-26 onwards, GST-adjusted)
    live_spend = _fetch_dsi_google_ads_spend(DSI_NEW_ACCOUNT_START, end_date)

    # Old-account legacy spend (from Excel, Jan-26 to Mar-26, no GST)
    legacy_spend = _fetch_dsi_legacy_spend(start_date, end_date)

    # Merge spend — normalize all course labels through _rollup_to_dept
    merged_spend = defaultdict(float)
    for course, spend in legacy_spend.items():
        normalized = _rollup_to_dept(course)
        merged_spend[normalized] += spend
    for course, spend in live_spend.items():
        normalized = _rollup_to_dept(course)
        merged_spend[normalized] += spend

    # Leads from LSQ mirror
    leads = _fetch_dsi_lsq_leads(start_date, end_date)
    leads_by_course = defaultdict(int)
    for lead in leads:
        normalized = _rollup_to_dept(lead["course"])
        leads_by_course[normalized] += 1

    # Build rows
    all_courses = set(merged_spend.keys()) | set(leads_by_course.keys())
    rows = []
    for course in all_courses:
        spend = round(merged_spend.get(course, 0))
        leads_count = leads_by_course.get(course, 0)
        if spend <= 0 and leads_count <= 0:
            continue
        cpl = round(spend / leads_count) if leads_count else None
        dept = _get_dsi_dept(course)
        rows.append({
            "department": dept,
            "course": course,
            "leads": leads_count,
            "cpl": cpl,
            "spend": spend,
        })

    rows.sort(key=lambda r: _dept_sort_key(r["course"]))
    _annotate_dept_cpl_spend(rows)
    return rows


def _fetch_dsi_legacy_spend(start_date: str, end_date: str) -> Dict[str, float]:
    """Fetch hardcoded legacy spend (DSI old Google Ads account, Jan-26 to Mar-26).
    Returns {course: spend_float}."""
    from backend.db.database import SessionLocal
    from backend.db.models import DsiLegacySpend

    db = SessionLocal()
    try:
        entries = db.query(DsiLegacySpend).all()
        course_spend = defaultdict(float)
        for entry in entries:
            # Month stored as "2026-01"; check overlap with requested range
            if entry.month >= start_date[:7] and entry.month <= end_date[:7]:
                course_spend[entry.course] += entry.spend
        return dict(course_spend)
    except Exception as e:
        logger.warning(f"DSI legacy spend fetch failed: {e}")
        return {}
    finally:
        db.close()


# ============================================================================
# Table 3: Lead Attribution Pivot (Course × Stage)
# ============================================================================

def fetch_dsi_lead_pivot(start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch DSI lead attribution pivot: Course × Student Stage (Table 3).
    Rows are by course (with department rollup), columns by stage."""
    leads = _fetch_dsi_lsq_leads(start_date, end_date)

    matrix_map = defaultdict(lambda: defaultdict(int))
    unique_stages = set()

    for lead in leads:
        course = lead["course"]
        stage = lead["stage"]
        if course and stage:
            matrix_map[course][stage] += 1
            unique_stages.add(stage)

    all_stages = sorted(unique_stages)

    rows = []
    for course in sorted(matrix_map.keys(), key=lambda c: _dept_sort_key(c)):
        row = {"course": course}
        for stage in all_stages:
            row[stage] = matrix_map[course].get(stage, 0)
        rows.append(row)

    column_totals = {}
    grand_total = 0
    for stage in all_stages:
        col_sum = sum(matrix_map[course].get(stage, 0) for course in matrix_map)
        column_totals[stage] = col_sum
        grand_total += col_sum

    return {
        "stages": all_stages,
        "rows": rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
    }


# ============================================================================
# Table 4: Application MIS (Application Submitted & CPA by Department)
# ============================================================================

APPLICATION_SUBMITTED_STATUSES = {
    "application fee paid",
    "application submitted",
    "enrolled",
    "partially paid",
}

# Target applications per course (can be updated as needed)
DSI_T4_TARGETS = {
    "DSCE": 0,
    "DSIT - Diploma": 0,
    "B. Arch": 0,
    "B.Com": 0,
    "B.Sc (PCM)": 0,
    "BBA": 0,
    "BCA": 0,
}


def fetch_dsi_application_mis(start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch DSI Application MIS (Table 4).
    Mirrors Table 1/2 layout: merged department column, one row per course,
    no per-section subtotals.  Uses cumulative spend for CPA.
    """
    leads = _fetch_dsi_lsq_leads(start_date, end_date)

    submitted_counts = defaultdict(int)
    for lead in leads:
        status_lower = (lead["application_status"] or "").lower().strip()
        if status_lower in APPLICATION_SUBMITTED_STATUSES:
            course = _rollup_to_dept(lead["course"])
            submitted_counts[course] += 1

    # Use cumulative spend (legacy + live API) for consistency with Table 2
    cumulative_rows = fetch_dsi_cumulative_range(start_date, end_date)
    spend_data = {r["course"]: r["spend"] for r in cumulative_rows}

    all_courses = set(submitted_counts.keys()) | set(spend_data.keys())
    rows = []
    dept_submitted = defaultdict(int)
    dept_spend = defaultdict(float)
    grand_submitted = 0
    grand_spend = 0
    grand_target = 0

    for course in all_courses:
        submitted = submitted_counts.get(course, 0)
        spend = round(spend_data.get(course, 0))
        dept = _get_dsi_dept(course)
        dept_submitted[dept] += submitted
        dept_spend[dept] += spend
        target = DSI_T4_TARGETS.get(course, 0)
        rows.append({
            "department": dept,
            "course": course,
            "submitted": submitted,
            "spend": spend,
            "cpa": round(spend / submitted) if submitted > 0 else 0,
            "target": target,
        })
        grand_submitted += submitted
        grand_spend += spend
        grand_target += target

    rows.sort(key=lambda r: _dept_sort_key(r["course"]))

    # Department-level CPA/Spend for DSCASC - UG merging
    for row in rows:
        dept = row["department"]
        d_sub = dept_submitted.get(dept, 0)
        d_spend = round(dept_spend.get(dept, 0))
        row["dept_cpa"] = round(d_spend / d_sub) if d_sub > 0 else 0
        row["dept_spend"] = d_spend
        row["dept_submitted"] = d_sub

    grand_cpa = round(grand_spend / grand_submitted) if grand_submitted > 0 else 0
    grand_total = {
        "course": "TOTAL",
        "submitted": grand_submitted,
        "spend": grand_spend,
        "cpa": grand_cpa,
        "target": grand_target,
    }

    return {
        "rows": rows,
        "grand_total": grand_total,
    }


# ============================================================================
# Table 5: Budget MIS by Department
# ============================================================================

def fetch_dsi_budget_mis(start_date: str, end_date: str, db_session=None) -> Dict[str, Any]:
    """Fetch DSI Budget MIS (Table 5).
    Mirrors Table 1/2/4 course/department layout.  Spend is taken from the
    cumulative Table 2 data and rolled up to the same course labels.
    Budgets are stored per department in DsiBudgetEntry; for DSCASC - UG the
    budget/spend/remaining cells are merged at department level.
    """
    # Use cumulative spend for consistency with Table 2
    cumulative_rows = fetch_dsi_cumulative_range(start_date, end_date)
    spend_data = {r["course"]: r["spend"] for r in cumulative_rows}

    # Fetch live campaign status from Google Ads for each course
    live_status_by_course = _fetch_dsi_google_ads_campaign_status()

    # Fetch budgets from DB (stored per department/section)
    section_budgets = defaultdict(float)
    if db_session:
        from backend.db.models import DsiBudgetEntry
        entries = db_session.query(DsiBudgetEntry).all()
        for entry in entries:
            if entry.section:
                section_budgets[entry.section] += entry.amount

    all_courses = set(spend_data.keys()) | set(live_status_by_course.keys())
    rows = []
    grand_budget = 0.0
    grand_spend = 0.0
    dept_budget_map = {}
    dept_spend_map = {}

    for course in all_courses:
        spend = round(spend_data.get(course, 0))
        dept = _get_dsi_dept(course)
        # Prefer live Google Ads status; fall back to spend-based heuristic if API fails
        is_live = live_status_by_course.get(course)
        if is_live is None:
            is_live = spend > 0
        status = "Live" if is_live else "Paused"
        rows.append({
            "department": dept,
            "course": course,
            "status": status,
            "spend": spend,
        })
        dept_spend_map[dept] = dept_spend_map.get(dept, 0) + spend

    rows.sort(key=lambda r: _dept_sort_key(r["course"]))

    # Department budget / remaining (DSCASC - UG is merged)
    for row in rows:
        dept = row["department"]
        if dept not in dept_budget_map:
            dept_budget_map[dept] = section_budgets.get(dept, 0.0)
        budget = dept_budget_map[dept]
        spend = dept_spend_map.get(dept, 0)
        row["budget"] = round(budget)
        row["remaining"] = round(budget - spend)
        row["dept_budget"] = row["budget"]
        row["dept_spend"] = spend
        row["dept_remaining"] = row["remaining"]

    for dept, budget in dept_budget_map.items():
        grand_budget += budget
    grand_spend = sum(dept_spend_map.values())

    grand_total = {
        "course": "TOTAL",
        "budget": round(grand_budget),
        "spend": grand_spend,
        "remaining": round(grand_budget - grand_spend),
    }

    return {
        "rows": rows,
        "grand_total": grand_total,
    }