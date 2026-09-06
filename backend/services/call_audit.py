"""
DSU/DSI Call Audit Report service (InsightDesk Table 8).

Reusable, account-driven implementation:
  - fetch_call_audit(account_id, start_date, end_date) builds:
      Table 1: Audit Summary (total/audited/not audited/good/bad/unclear)
      Table 2: Geography (city/state lead counts, all leads in range)
  - export_call_audit_xlsx(...) writes the same tables to Excel.

Data sources (LeadSquared API, per-tenant):
  - Leads.Get (CreatedOn >= start, UTC) paginated ASC  -> all leads created in range
  - ProspectActivity.svc/RetrieveRecentlyModified (EventCode 210) -> Call Feedback
  - Gemini (via existing configured key) classifies feedback text: Good/Bad/Unclear

Classification criteria (per DSU requirement):
  Good: real interest / live DSU prospect (interested, parents will decide, need time,
        checking other colleges, will take time, etc.)
  Bad:  wrong number, applied by mistake, don't know who submitted, unreachable,
        no genuine DSU admission connection.
  Unclear: missing/vague feedback that fits neither bucket confidently.
"""
import io
import logging
import time as time_module
from collections import Counter, defaultdict
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional

import requests

from backend.db.database import SessionLocal
from backend.db.models import Account, CallFeedbackActivity, LeadSquaredLead
from backend.services.lsq_mirror import _get_lsq_credentials
from backend.services.lsq_call_feedback import sync_call_feedback

logger = logging.getLogger("AdOptima")

CALL_FEEDBACK_EVENT_CODE = 210
_MAX_PAGES_LEADS = 200
_MAX_PAGES_ACTS = 60

# In-memory report cache: {account_start_end: {ts, data}}
_REPORT_CACHE: Dict[str, Dict[str, Any]] = {}

# LSQ mx_State spellings -> amCharts India map titles
_STATE_NAME_MAP = {
    "orissa": "Odisha",
    "pondicherry": "Puducherry",
    "uttaranchal": "Uttarakhand",
    "jammu & kashmir": "Jammu and Kashmir",
    "jammu and kashmir": "Jammu and Kashmir",
    "nct of delhi": "Delhi",
    "new delhi": "Delhi",
    "delhi ncr": "Delhi",
    "andaman & nicobar": "Andaman and Nicobar Islands",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "dadra & nagar haveli": "Dadra and Nagar Haveli",
    "daman & diu": "Daman and Diu",
    "telengana": "Telangana",
    "chattisgarh": "Chhattisgarh",
}


def _norm_state(raw: str) -> str:
    t = (raw or "").strip().lower()
    return _STATE_NAME_MAP.get(t, (raw or "").strip())


# City -> State inference map (LSQ mx_State is empty tenant-wide, so state is
# derived from the lead's mx_City for the state table and India map).
_CITY_STATE_MAP = {
    # Karnataka
    "bangalore": "Karnataka", "bengaluru": "Karnataka", "bangalore rural": "Karnataka",
    "bangalore urban": "Karnataka", "mysore": "Karnataka", "mangalore": "Karnataka",
    "hubli": "Karnataka", "dharwad": "Karnataka", "belagavi": "Karnataka", "belgaum": "Karnataka",
    "tumkur": "Karnataka", "ballari": "Karnataka", "bellary": "Karnataka", "raichur": "Karnataka",
    "bidar": "Karnataka", "kalaburagi": "Karnataka", "gulbarga": "Karnataka", "shivamogga": "Karnataka",
    "shimoga": "Karnataka", "chikkamagaluru": "Karnataka", "ramanagara": "Karnataka",
    "haveri": "Karnataka", "koppal": "Karnataka", "davanagere": "Karnataka", "mandya": "Karnataka",
    "hassan": "Karnataka", "udupi": "Karnataka", "bagalkot": "Karnataka", "gadag": "Karnataka",
    "kolar": "Karnataka", "chitradurga": "Karnataka", "kodagu": "Karnataka", "coorg": "Karnataka",
    "karwar": "Karnataka", "chikkaballapur": "Karnataka", "kolar gold fields": "Karnataka",
    "kolar gold field": "Karnataka", "hosur": "Karnataka",
    # Tamil Nadu
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "trichy": "Tamil Nadu", "tiruchirappalli": "Tamil Nadu", "salem": "Tamil Nadu",
    "tiruchirapalli": "Tamil Nadu", "vellore": "Tamil Nadu", "erode": "Tamil Nadu",
    "tirunelveli": "Tamil Nadu", "thiruvallur": "Tamil Nadu", "kanchipuram": "Tamil Nadu",
    "chengalpattu": "Tamil Nadu", "hosur krishnagiri": "Tamil Nadu", "krishnagiri": "Tamil Nadu",
    # Telangana / Andhra
    "hyderabad": "Telangana", "secunderabad": "Telangana", "warangal": "Telangana",
    "nizamabad": "Telangana", "vijayawada": "Andhra Pradesh", "visakhapatnam": "Andhra Pradesh",
    "vizag": "Andhra Pradesh", "guntur": "Andhra Pradesh", "tirupati": "Andhra Pradesh",
    "nellore": "Andhra Pradesh", "kurnool": "Andhra Pradesh", "kakinada": "Andhra Pradesh",
    # Kerala
    "kochi": "Kerala", "cochin": "Kerala", "trivandrum": "Kerala",
    "thiruvananthapuram": "Kerala", "kozhikode": "Kerala", "calicut": "Kerala",
    "thrissur": "Kerala", "kottayam": "Kerala", "palakkad": "Kerala",
    # Maharashtra
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "nashik": "Maharashtra", "aurangabad": "Maharashtra", "solapur": "Maharashtra",
    "kolhapur": "Maharashtra", "thane": "Maharashtra", "amravati": "Maharashtra",
    # UP
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "varanasi": "Uttar Pradesh",
    "agra": "Uttar Pradesh", "meerut": "Uttar Pradesh", "allahabad": "Uttar Pradesh",
    "prayagraj": "Uttar Pradesh", "gorakhpur": "Uttar Pradesh", "noida": "Uttar Pradesh",
    "greater noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh", "bareilly": "Uttar Pradesh",
    "aligarh": "Uttar Pradesh", "moradabad": "Uttar Pradesh", "jhansi": "Uttar Pradesh",
    "faizabad": "Uttar Pradesh", "ayodhya": "Uttar Pradesh", "mathura": "Uttar Pradesh",
    "rampur": "Uttar Pradesh", "shahjahanpur": "Uttar Pradesh", "farrukhabad": "Uttar Pradesh",
    "muzaffarnagar": "Uttar Pradesh", "saharanpur": "Uttar Pradesh", "ballia": "Uttar Pradesh",
    "lko": "Uttar Pradesh",
    # Bihar / Jharkhand
    "patna": "Bihar", "gaya": "Bihar", "bhagalpur": "Bihar", "muzaffarpur": "Bihar",
    "darbhanga": "Bihar", "banka": "Bihar", "purnia": "Bihar", "arran": "Bihar",
    "ara": "Bihar", "bhojpur": "Bihar", "siwan": "Bihar", "chapra": "Bihar",
    "ranchi": "Jharkhand", "jamshedpur": "Jharkhand", "dhanbad": "Jharkhand",
    "bokaro": "Jharkhand", "deoghar": "Jharkhand", "hazaribagh": "Jharkhand",
    # MP / Chhattisgarh
    "bhopal": "Madhya Pradesh", "indore": "Madhya Pradesh", "gwalior": "Madhya Pradesh",
    "jabalpur": "Madhya Pradesh", "ujjain": "Madhya Pradesh", "sagar": "Madhya Pradesh",
    "raipur": "Chhattisgarh", "bhilai": "Chhattisgarh", "bilaspur": "Chhattisgarh",
    "korba": "Chhattisgarh", "durg": "Chhattisgarh",
    # West Bengal / Odisha / Northeast
    "kolkata": "West Bengal", "howrah": "West Bengal", "durgapur": "West Bengal",
    "siliguri": "West Bengal", "asansol": "West Bengal", "bhubaneswar": "Odisha",
    "cuttack": "Odisha", "rourkela": "Odisha", "berhampur": "Odisha",
    "guwahati": "Assam", "silchar": "Assam", "dibrugarh": "Assam",
    "shillong": "Meghalaya", "imphal": "Manipur", "aizawl": "Mizoram",
    "agartala": "Tripura", "kohima": "Nagaland", "itanagar": "Arunachal Pradesh",
    # North
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan",
    "kota": "Rajasthan", "ajmer": "Rajasthan", "bikaner": "Rajasthan",
    "delhi": "Delhi", "new delhi": "Delhi", "gurugram": "Haryana", "gurgaon": "Haryana",
    "faridabad": "Haryana", "panipat": "Haryana", "karnal": "Haryana", "hisar": "Haryana",
    "ambala": "Haryana", "rohtak": "Haryana", "chandigarh": "Chandigarh",
    "chandigarh (ut)": "Chandigarh", "mohali": "Chandigarh", "panchkula": "Chandigarh",
    "ludhiana": "Punjab", "amritsar": "Punjab", "jalandhar": "Punjab", "patiala": "Punjab",
    "mohali": "Punjab", "bathinda": "Punjab", "dehradun": "Uttarakhand",
    "haridwar": "Uttarakhand", "roorkee": "Uttarakhand", "haldwani": "Uttarakhand",
    "shimla": "Himachal Pradesh", "solan": "Himachal Pradesh", "dharamshala": "Himachal Pradesh",
    # West / Central
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat", "rajkot": "Gujarat",
    "gandhinagar": "Gujarat", "bhavnagar": "Gujarat", "jamnagar": "Gujarat",
    "goa": "Goa", "panaji": "Goa", "panjim": "Goa", "margao": "Goa",
    "bhubaneswar": "Odisha",
    # J&K / Ladakh
    "srinagar": "Jammu and Kashmir", "jammu": "Jammu and Kashmir",
}


def _derive_state(city: str, state: str) -> str:
    """Return the best-known state for a lead: mx_State first, then city inference."""
    if (state or "").strip():
        return _norm_state(state)
    c = (city or "").strip().lower()
    return _CITY_STATE_MAP.get(c, "")


def _ensure_v2(base_url: str) -> str:
    base_url = (base_url or "").rstrip("/")
    if not base_url.endswith("/v2"):
        base_url += "/v2"
    return base_url


# ---------------------------------------------------------------------------
# LSQ fetchers
# ---------------------------------------------------------------------------

def _fetch_leads_created_between(
    account: Account,
    start_date_utc: str,
    end_date_utc: str,
) -> List[Dict[str, Any]]:
    """All leads created in [start_date_utc, end_date_utc) via Leads.Get.

    Uses a single CreatedOn >= condition (verified to work on this tenant)
    plus a client-side upper-bound cut. Returns flat dicts.
    """
    access_key, secret_key, base_url = _get_lsq_credentials(account)
    base_url = _ensure_v2(base_url)
    include_csv = (
        "ProspectID,FirstName,LastName,Stage,Source,SourceCampaign,"
        "mx_City,mx_State,mx_Application_Course,CreatedOn"
    )

    out: List[Dict[str, Any]] = []
    page = 1
    upper_dt = datetime.fromisoformat(end_date_utc)
    while page <= _MAX_PAGES_LEADS:
        payload = {
            "Parameter": {"LookupName": "CreatedOn", "LookupValue": start_date_utc, "SqlOperator": ">="},
            "Columns": {"Include_CSV": include_csv},
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "0"},  # ASC
        }
        success = False
        last_err = None
        for attempt in range(4):
            try:
                r = requests.post(
                    f"{base_url}/LeadManagement.svc/Leads.Get",
                    params={"accessKey": access_key, "secretKey": secret_key},
                    json=payload,
                    timeout=180,
                )
                r.raise_for_status()
                rows = r.json()
                success = True
                break
            except Exception as e:
                last_err = e
                logger.warning(f"Call audit leads fetch page {page} attempt {attempt + 1} failed: {e}")
                time_module.sleep(min(2 ** attempt * 3, 20))
        if not success:
            raise RuntimeError(f"LSQ Leads.Get failed after retries: {last_err}")

        if not isinstance(rows, list) or not rows:
            break
        stop = False
        for rec in rows:
            created_raw = (rec.get("CreatedOn") or "").strip()
            try:
                created = datetime.strptime(created_raw[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            if created >= upper_dt:
                stop = True
                break
            out.append(rec)
        if stop or len(rows) < 1000:
            break
        page += 1
    return out


def _utc_window_for_ist_days(start_date: str, end_date: str) -> (str, str):
    """IST [start 00:00, end 23:59:59] -> UTC window [start-1d 18:30, end+1d 18:30)."""
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    start_utc = datetime.combine(s - timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    end_utc = datetime.combine(e + timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    return start_utc.strftime("%Y-%m-%d %H:%M:%S"), end_utc.strftime("%Y-%m-%d %H:%M:%S")


def _fetch_call_feedback(
    account: Account,
    start_date_utc: str,
    end_date_utc: str,
) -> List[Dict[str, Any]]:
    """Call Feedback activities (210) created in UTC window, fetched in 2h chunks."""
    access_key, secret_key, base_url = _get_lsq_credentials(account)
    base_url = _ensure_v2(base_url)

    start_dt = datetime.strptime(start_date_utc, "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(end_date_utc, "%Y-%m-%d %H:%M:%S")

    all_acts: List[Dict[str, Any]] = []
    chunk = timedelta(hours=2)
    cur = start_dt
    while cur < end_dt:
        nxt = min(cur + chunk, end_dt)
        win_from = cur.strftime("%Y-%m-%d %H:%M:%S")
        win_to = nxt.strftime("%Y-%m-%d %H:%M:%S")
        page = 1
        while page <= _MAX_PAGES_ACTS:
            payload = {
                "Parameter": {
                    "FromDate": win_from,
                    "ToDate": win_to,
                    "IncludeCustomFields": 1,
                    "ActivityEvent": CALL_FEEDBACK_EVENT_CODE,
                },
                "Paging": {"PageIndex": page, "PageSize": 200},
                "Sorting": {"ColumnName": "CreatedOn", "Direction": 1},
            }
            success = False
            last_err = None
            for attempt in range(4):
                try:
                    r = requests.post(
                        f"{base_url}/ProspectActivity.svc/RetrieveRecentlyModified",
                        params={"accessKey": access_key, "secretKey": secret_key},
                        json=payload,
                        timeout=180,
                    )
                    r.raise_for_status()
                    resp = r.json()
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    logger.warning(f"Call audit activities fetch {win_from}-{win_to} p{page} attempt {attempt + 1} failed: {e}")
                    time_module.sleep(min(2 ** attempt * 5, 30))
            if not success:
                logger.error(f"Call audit: activity chunk {win_from}-{win_to} failed: {last_err}")
                break  # skip chunk rather than failing whole report

            records = resp.get("ProspectActivities", [])
            if not records:
                break
            all_acts.extend(records)
            if len(records) < 200:
                break
            page += 1
        cur = nxt
    return all_acts


# ---------------------------------------------------------------------------
# Classification — fully rule-based, zero AI dependency.
#
# Two passes:
#   1. Structured dropdown outcomes (deterministic keyword rules)
#   2. Free-text remarks — keyword heuristics over the agents' typed feedback
#
# Attempt counting handles reachability: 3+ failed attempts = Bad (unreachable).
# ---------------------------------------------------------------------------

# Substring -> classification for structured outcomes (case-insensitive)
_GOOD_PATTERNS = (
    "interested", "parents will decide", "parents agreed", "need some more time",
    "need time", "will take time", "checking", "other colleges", "will decide",
    "asked for", "callback", "campus visit", "scholarship", "next session",
    "follow up", "follow-up",
)
_BAD_PATTERNS = (
    "by mistake", "wrong number", "does not exist", "not exist", "looking for a job",
    "job",
    "don't know", "do not know", "no one home", "unreachable", "prank",
    "not reachable", "fake",
)

# Pure call-status texts (not connected, no conversation happened)
_NOT_CONNECTED = ("ringing no answer", "number busy", "switched off", "out of coverage",
                  "no answer", "not reachable", "not picked")

# NOTE: admission-taken phrases are GOOD (education-seeker reached late).
_FREE_TEXT_BAD = (
    "not interested", "not intrested", "not intrest", "no interest",
    "don't want", "do not want", "dont want", "not willing", "not looking",
    "no requirement", "not require",
    "enrolled in",
    "joined elsewhere", "joined other", "wrong number", "wrong no",
    "number does not exist", "does not exist", "no such number",
    "applied by mistake", "by mistake", "mistakenly", "bymistake",
    "don't know", "do not know", "dont know", "who submitted",
    "no one home", "nobody home", "not reachable", "unreachable",
    "switched off", "out of coverage", "not picking", "not attending",
    "abusive", "abuse", "prank", "fake", "fraud", "hang up", "hung up",
    "job", "working", "working professional already", "married", "shifted",
    "abroad", "dropped out", "discontinued", "backlog", "year gap",
    "fees too high", "fees is too high", "fee is too high", "expensive",
    "cannot afford", "can't afford", "cant afford", "no money", "financial",
)
# NOTE: fee-related remarks are ambiguous — "fees too high" usually closes the
# conversation, but the student genuinely considered DSU. We classify them Good
# below via an explicit override before the bad list is applied.
_FREE_TEXT_FEE_GOOD = (
    "fees too high", "fees is too high", "fee is too high", "expensive",
    "cannot afford", "can't afford", "cant afford", "no money",
    "scholarship", "discount", "concession", "emi", "installment",
)
_FREE_TEXT_GOOD = (
    "interested", "intrested", "yes", "willing", "want to", "wants to",
    "plan to", "planning", "thinking about", "asking about", "enquired about",
    "asked about", "asked for", "details shared", "shared details",
    "sent details", "mailed", "whatsapp", "visited", "campus visit",
    "visit campus", "come to college", "will come", "coming",
    "callback", "call back", "call later", "call after", "busy right now",
    "busy now", "call in evening", "call tomorrow", "call monday",
    "rescheduled", "scheduled", "appointment", "follow up", "follow-up",
    "followup", "parents will decide", "parent will decide", "talk to parents",
    "discuss with parents", "parents agreed", "parents said", "father said",
    "mother said", "family will decide", "need time", "need some time",
    "need some more time", "some time", "take time", "taking time",
    "will take time", "will decide", "deciding", "confused", "comparing",
    "checking", "other colleges", "other college", "other university",
    "other options", "exploring", "next session", "next year", "next batch",
    "not eligible this year", "next intake", "waiting for result",
    "waiting for results", "result waiting", "12th result", "board exam",
    "entrance", "scholarship test", "dsat", "admission open", "process",
    "application", "apply", "documents", "fees structure", "fee structure",
    "hostel", "transport", "course details", "branch", "cse", "placement",
    # Admission-taken-elsewhere = genuine education-seeker reached late (Good per DSU)
    "already taken admission", "took admission", "taking admission",
    "admission done", "admission somewhere else", "already admitted",
    "admission taken", "got admitted", "taken admission",
)

# Strong negatives for free-text (checked per-segment, after structured outcomes
# so "Not Interested - Parents did not Agree" is judged by its reason, not the prefix)
# NOTE: admission-taken-elsewhere phrases are intentionally NOT bad — a student
# who took admission anywhere is a genuine education-seeker (Good per DSU).
_FREE_TEXT_STRONG_BAD = (
    "wrong number", "number does not exist", "does not exist", "no such number",
    "by mistake", "applied by mistake", "bymistake", "mistakenly",
    "joined elsewhere", "joined other", "enrolled in",
    "don't know", "do not know", "dont know", "who submitted",
    "prank", "fake", "fraud", "abusive", "abuse",
    "looking for a job", "not eligible for any",
    "no one home", "nobody home", "unreachable", "not reachable",
    "discontinued", "dropped out", "year gap", "backlogs",
)

# Bad reasons inside structured "Not Interested - <reason>" / "Not Eligible - <reason>"
# NOTE per DSU definition: any admission-seeker is GOOD — "taking admission somewhere
# else" / "already taken admission" mean the student was genuinely looking for further
# education; we just reached them late. Only non-students are Bad.
_STRUCT_BAD_REASONS = (
    "by mistake", "wrong number", "does not exist", "don't know", "do not know",
)

# Good reasons inside structured outcomes (genuine DSU conversation happened)
_STRUCT_GOOD_REASONS = (
    "parents", "guardian", "fees", "fee", "scholarship", "relocate", "bangalore",
    "distance", "next session", "next year", "program not available",
    "not available", "need time", "comparing", "checking", "other colleges",
    "decide", "time", "admission somewhere else", "already taken admission",
)


def _split_remarks(text: str) -> List[str]:
    """Split the compiled corpus back into individual remark segments (most recent first)."""
    return [s.strip() for s in (text or "").split("|") if s.strip()]


def _classify_remark(remark: str) -> Optional[str]:
    """Classify one remark segment. Returns Good/Bad/Unclear, or None if undecided."""
    t = (remark or "").lower().strip()
    if not t or t in ("(no feedback text logged)", "connected, no outcome logged"):
        return "Unclear"

    # Pure call-status (no conversation happened)
    if t in _NOT_CONNECTED or t.startswith(("ringing", "number busy", "switched off", "out of coverage")):
        return "Unclear"

    # ---- Structured outcomes (dropdown prefixes) ----
    if "not interested" in t or "not yet decided" in t:
        for kw in _STRUCT_BAD_REASONS:
            if kw in t:
                return "Bad"
        # A reason implies a genuine conversation about DSU -> Good per spec;
        # a bare "Not Interested" with no reason is an explicit rejection -> Bad
        if " - " in t or "-" in t.replace("not interested", "").replace("not yet decided", ""):
            return "Good"
        return "Bad" if "not interested" in t else "Good"

    if "not eligible" in t:
        for kw in _STRUCT_BAD_REASONS:
            if kw in t:
                return "Bad"
        for kw in _STRUCT_GOOD_REASONS:
            if kw in t:
                return "Good"
        # e.g. "Not Eligible - 12th not completed": genuine prospect, timing issue
        return "Good"

    # ---- Free-text keyword heuristics ----
    for kw in _FREE_TEXT_STRONG_BAD:
        if kw in t:
            return "Bad"
    for kw in _FREE_TEXT_GOOD:
        if kw in t:
            return "Good"
    for kw in _FREE_TEXT_FEE_GOOD:
        if kw in t:
            return "Good"
    return None


def _classify_free_text(text: str, failed_attempts: int) -> Optional[str]:
    """Classify a compiled remark corpus (segments joined by ' | ', most recent first).

    Priority: latest remark's verdict wins; 3+ failed attempts with no
    conversation anywhere -> Bad (unreachable); nothing matched -> Unclear.
    """
    if not text or not text.strip():
        return "Unclear"
    segments = _split_remarks(text.lower())

    # Reachability: 3+ failed attempts and no connected conversation in any segment
    fail_count = sum(
        1 for s in segments
        if s in _NOT_CONNECTED or s.startswith(("ringing", "number busy", "switched off", "out of coverage"))
    )
    if fail_count >= 3 and fail_count == len(segments):
        return "Bad"

    # Latest non-Unclear verdict wins (segments are most-recent-first)
    for s in segments:
        verdict = _classify_remark(s)
        if verdict in ("Good", "Bad"):
            return verdict

    # No conversation verdicts at all
    if fail_count >= 3:
        return "Bad"
    return "Unclear"


def classify_feedback_batch(items: List[Dict[str, Any]], batch_size: int = 15) -> Dict[Any, Dict[str, str]]:
    """Classify all feedback items with pure rules. Zero AI/API dependency.

    items: [{id, text, total_attempts}] — text is the compiled last-5-remarks corpus.
    Returns {id: {classification, reason}}.
    """
    results: Dict[Any, Dict[str, str]] = {}
    for it in items:
        text = (it.get("text") or "").strip()
        attempts = int(it.get("total_attempts") or 0)
        if not text:
            cls = "Unclear"
            reason = "No feedback text logged"
        else:
            cls = _classify_free_text(text, attempts) or "Unclear"
            reason = "Rule-based classification"
        results[it["id"]] = {"classification": cls, "reason": reason}
    return results


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def _lead_day_ist(created_raw: str) -> str:
    try:
        dt = datetime.strptime((created_raw or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
        return (dt + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _fmt_ist(created_raw: str) -> str:
    try:
        dt = datetime.strptime((created_raw or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
        return (dt + timedelta(hours=5, minutes=30)).strftime("%d-%b %Y %H:%M")
    except (ValueError, TypeError):
        return ""


def _backfill_geo(account: Account, start_date: str, end_date: str) -> Dict[str, Dict[str, str]]:
    """Bulk-fetch mx_City/mx_State for the range's leads from LSQ.

    Uses Leads.RecentlyModified over the UTC window (fast, paginated) and
    returns {prospect_id: {city, state}} for leads whose IST created date
    falls in [start_date, end_date].
    """
    access_key, secret_key, base_url = _get_lsq_credentials(account)
    base_url = _ensure_v2(base_url)
    start_utc, end_utc = _utc_window_for_ist_days(start_date, end_date)

    out: Dict[str, Dict[str, str]] = {}
    page = 1
    while page <= 100:
        payload = {
            "Parameter": {"FromDate": start_utc, "ToDate": end_utc},
            "Columns": {"Include_CSV": "ProspectID,mx_City,mx_State,CreatedOn"},
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "1"},
        }
        success = False
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{base_url}/LeadManagement.svc/Leads.RecentlyModified",
                    params={"accessKey": access_key, "secretKey": secret_key},
                    json=payload,
                    timeout=180,
                )
                r.raise_for_status()
                resp = r.json()
                success = True
                break
            except Exception as e:
                last_err = e
                logger.warning(f"Geo backfill page {page} attempt {attempt + 1} failed: {e}")
                time_module.sleep(min(2 ** attempt * 3, 15))
        if not success:
            raise RuntimeError(f"Geo backfill failed: {last_err}")

        records = resp.get("Leads", [])
        if not records:
            break
        for rec in records:
            props = {i.get("Attribute"): i.get("Value") for i in rec.get("LeadPropertyList", []) if isinstance(i, dict)}
            pid = props.get("ProspectID")
            if not pid:
                continue
            created_ist = _lead_day_ist(props.get("CreatedOn"))
            if not (start_date <= created_ist <= end_date):
                continue
            out[pid] = {
                "city": (props.get("mx_City") or "").strip(),
                "state": (props.get("mx_State") or "").strip(),
            }
        if len(records) < 1000:
            break
        page += 1
    return out


def _fetch_call_feedback_for_leads(
    account: Account,
    prospect_ids: List[str],
) -> List[Dict[str, Any]]:
    """Fetch Call Feedback activities for specific leads — fast, targeted.

    Strategy:
      1. Serve everything already in the local mirror (lsq_call_feedback).
      2. Leads missing from the mirror (never synced) are fetched individually
         from LSQ (ProspectActivity.svc/Retrieve) and added to the mirror.
    Returns raw activity dicts compatible with the previous range-based fetch.
    """
    from backend.db.models import CallFeedbackActivity

    db = SessionLocal()
    try:
        id_set = set(prospect_ids)
        # 1. Mirror hits
        mirror_rows = (
            db.query(CallFeedbackActivity)
            .filter(
                CallFeedbackActivity.account_id == account.id,
                CallFeedbackActivity.prospect_id.in_(id_set),
            )
            .all()
        )
        seen_pids = {r.prospect_id for r in mirror_rows}
        results: List[Dict[str, Any]] = []
        for r in mirror_rows:
            results.append({
                "RelatedProspectId": r.prospect_id,
                "CreatedOn": r.created_on.strftime("%Y-%m-%d %H:%M:%S") if r.created_on else "",
                "Fields": [
                    {"Key": "mx_Custom_2", "Value": r.call_status},
                    {"Key": "mx_Custom_3", "Value": r.outcome},
                    {"Key": "mx_Custom_6", "Value": r.reason if (r.reason or "").lower() in _reason_field6 else None},
                    {"Key": "mx_Custom_7", "Value": r.reason if (r.reason or "").lower() not in _reason_field6 else None},
                ],
                "Data": [{"Key": "CreatedByName", "Value": r.created_by_name}],
                "_from_mirror": True,
            })

        # 2. Leads with no mirror rows -> fetch from LSQ in parallel
        missing = [p for p in prospect_ids if p not in seen_pids]
        access_key, secret_key, base_url = _get_lsq_credentials(account)
        if base_url:
            base_url = _ensure_v2(base_url)

        new_mirror_rows = []

        def _fetch_one(pid: str):
            """Fetch Call Feedback activities for one lead from LSQ. Returns (pid, activities, mirror_row_fields)."""
            try:
                r = requests.post(
                    f"{base_url}/ProspectActivity.svc/Retrieve",
                    params={"accessKey": access_key, "secretKey": secret_key, "leadId": pid},
                    json={},
                    timeout=45,
                )
                if r.status_code != 200:
                    return pid, [], None
                acts = r.json().get("ProspectActivities", []) or []
            except Exception as e:
                logger.warning(f"Per-lead activity fetch failed for {pid[:8]}: {e}")
                return pid, [], None

            parsed = []
            mirror_fields = None
            for a in acts:
                if a.get("EventCode") != CALL_FEEDBACK_EVENT_CODE:
                    continue
                fields = {
                    f.get("Key"): f.get("Value")
                    for f in (a.get("Fields") or [])
                    if isinstance(f, dict) and f.get("Value")
                }
                data_map = {
                    d.get("Key"): d.get("Value")
                    for d in (a.get("Data") or [])
                    if isinstance(d, dict)
                }
                parsed.append({
                    "RelatedProspectId": pid,
                    "CreatedOn": (a.get("CreatedOn") or "").strip(),
                    "Fields": [
                        {"Key": "mx_Custom_2", "Value": fields.get("mx_Custom_2")},
                        {"Key": "mx_Custom_3", "Value": fields.get("mx_Custom_3")},
                        {"Key": "mx_Custom_6", "Value": fields.get("mx_Custom_6")},
                        {"Key": "mx_Custom_7", "Value": fields.get("mx_Custom_7")},
                    ],
                    "Data": [{"Key": "CreatedByName", "Value": data_map.get("CreatedByName")}],
                })
                activity_id = fields.get("ProspectActivityId") or a.get("Id")
                status = (fields.get("mx_Custom_2") or "").strip()
                outcome = (fields.get("mx_Custom_3") or "").strip()
                reason = (fields.get("mx_Custom_6") or fields.get("mx_Custom_7") or "").strip()
                created_raw = (a.get("CreatedOn") or "").strip()[:19]
                try:
                    created_dt = datetime.strptime(created_raw, "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue
                mirror_fields = (activity_id, status, outcome, reason,
                                 (fields.get("mx_Custom_1") or "").strip(),
                                 (data_map.get("CreatedByName") or "").strip(), created_dt)
            return pid, parsed, mirror_fields

        if missing and access_key and base_url:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            fetched_count = 0
            with ThreadPoolExecutor(max_workers=12) as pool:
                futures = {pool.submit(_fetch_one, pid): pid for pid in missing}
                for fut in as_completed(futures):
                    try:
                        pid, parsed, mirror_fields = fut.result()
                    except Exception as e:
                        logger.warning(f"Parallel activity fetch error: {e}")
                        continue
                    results.extend(parsed)
                    if mirror_fields:
                        activity_id, status, outcome, reason, followup, agent, created_dt = mirror_fields
                        new_mirror_rows.append(CallFeedbackActivity(
                            account_id=account.id,
                            activity_id=activity_id,
                            prospect_id=pid,
                            call_status=status,
                            outcome=outcome,
                            reason=reason,
                            next_followup=followup,
                            created_by_name=agent,
                            created_on=created_dt,
                        ))
        if new_mirror_rows:
            db.add_all(new_mirror_rows)
            db.commit()
            logger.info(f"Call audit: fetched + mirrored {len(new_mirror_rows)} feedback activities for {len(missing)} leads (parallel)")

        return results
    finally:
        db.close()


# Reasons that live in mx_Custom_6 (not-interested reason) vs mx_Custom_7 (not-eligible)
_reason_field6 = {
    "fees is too high", "fees too high", "parents/guardian did not agree",
    "taking admission somewhere else", "already taken admission somewhere else",
    "not ready to relocate to bangalore", "distance issue within bangalore",
    "looking for a job", "eligible for next session", "program not available",
    "not interested", "low academics", "year gap", "dropout",
}


def fetch_call_audit(
    account_id: int,
    start_date: str,
    end_date: str,
    use_cache: bool = True,
    cache_ttl_seconds: int = 300,
) -> Dict[str, Any]:
    """Build the Call Audit Report for [start_date, end_date] (IST inclusive).

    Lead population = "our leads" only — the same GGL/Programmatic mirror used
    by InsightDesk Tables 1 & 2 (lead counts match those tables exactly).
    Results are cached in memory for cache_ttl_seconds (5 min default) so
    repeated Generate clicks / exports are instant.
    """
    cache_key = f"{account_id}_{start_date}_{end_date}"
    if use_cache:
        cached = _REPORT_CACHE.get(cache_key)
        if cached and (time_module.time() - cached["ts"]) < cache_ttl_seconds:
            logger.info(f"Call audit cache hit for {cache_key}")
            return cached["data"]

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"error": f"Account {account_id} not found"}

        # --- 1. "Our leads" from the local mirror (same base as Tables 1 & 2) ---
        mirror_rows = (
            db.query(LeadSquaredLead)
            .filter(
                LeadSquaredLead.account_id == account_id,
                LeadSquaredLead.created_on >= start_date,
                LeadSquaredLead.created_on <= end_date,
            )
            .all()
        )
        from backend.services.dsu_data import EXCLUDED_SOURCES

        leads: Dict[str, Dict[str, Any]] = {}
        missing_geo_ids: List[str] = []
        lead_row_by_pid: Dict[str, LeadSquaredLead] = {}
        for row in mirror_rows:
            if row.source in EXCLUDED_SOURCES:
                continue
            pid = row.prospect_id
            if not pid or pid in leads:
                continue
            city = (getattr(row, "city", "") or "").strip()
            state = (getattr(row, "state", "") or "").strip()
            if not city and not state:
                missing_geo_ids.append(pid)
            first = ""
            last = ""
            try:
                import json as _json
                raw = _json.loads(row.raw_json or "{}")
                first = (raw.get("FirstName") or "").strip()
                last = (raw.get("LastName") or "").strip()
            except Exception:
                pass
            geo = (city.title() if city else "") or (state.title() if state else "") or "Unknown"
            lead_row_by_pid[pid] = row
            leads[pid] = {
                "prospect_id": pid,
                "name": (first + " " + last).strip() or "Unnamed",
                "stage": row.student_stage or "",
                "source": row.source or "",
                "course": row.course or "",
                "city": city,
                "state": state,
                "geo": geo,
                "created_on": row.created_on or "",
            }

        # Backfill missing city/state from LSQ in bulk (paginated RecentlyModified)
        # and persist to the mirror so future runs are instant.
        if missing_geo_ids:
            try:
                geo_map = _backfill_geo(account, start_date, end_date)
                updated = 0
                for pid in missing_geo_ids:
                    info = geo_map.get(pid)
                    if not info:
                        continue
                    leads[pid]["city"] = info["city"]
                    leads[pid]["state"] = info["state"]
                    if info["city"] or info["state"]:
                        leads[pid]["geo"] = (info["city"].title() if info["city"] else "") or (
                            info["state"].title() if info["state"] else ""
                        ) or "Unknown"
                    row_obj = lead_row_by_pid.get(pid)
                    if row_obj is not None:
                        row_obj.city = info["city"]
                        row_obj.state = info["state"]
                        updated += 1
                if updated:
                    db.commit()
                    logger.info(f"Call audit geo backfill: enriched {updated}/{len(missing_geo_ids)} leads")
            except Exception as e:
                logger.warning(f"Call audit geo backfill failed (continuing with Unknown): {e}")

        total_leads = len(leads)

        # --- 2. Call Feedback activities (audited = >=1 attempt) ---
        # Fast path: if the local mirror already has these activities, use it.
        # Slow path (first run / mirror stale): per-lead LSQ activity fetch.
        start_utc, end_utc = _utc_window_for_ist_days(start_date, end_date)
        act_rows = _fetch_call_feedback_for_leads(account, list(leads.keys()))
        feedback_by_lead: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for a in act_rows:
            pid = a.get("RelatedProspectId")
            if pid not in leads:
                continue
            fields = {
                f.get("Key"): f.get("Value")
                for f in (a.get("Fields") or [])
                if isinstance(f, dict) and f.get("Value")
            }
            data_map = {
                d.get("Key"): d.get("Value")
                for d in (a.get("Data") or [])
                if isinstance(d, dict)
            }
            feedback_by_lead[pid].append({
                "status": (fields.get("mx_Custom_2") or "").strip(),          # empty = connected
                "outcome": (fields.get("mx_Custom_3") or "").strip(),
                "reason": (fields.get("mx_Custom_6") or fields.get("mx_Custom_7") or "").strip(),
                "agent": (data_map.get("CreatedByName") or "").strip(),
                "at": _fmt_ist(a.get("CreatedOn")),
            })

        # Sort each lead's feedback most-recent-first and build text corpus
        feedback_texts: Dict[str, str] = {}
        for pid, remarks in feedback_by_lead.items():
            remarks.sort(key=lambda r: r["at"], reverse=True)
            parts = []
            for r in remarks[:5]:  # last 5 remarks
                if r["outcome"]:
                    seg = r["outcome"] + (f" - {r['reason']}" if r["reason"] else "")
                elif r["status"]:
                    seg = r["status"]
                else:
                    seg = "Connected, no outcome logged"
                parts.append(seg)
            feedback_texts[pid] = " | ".join(parts)

        audited_ids = sorted(feedback_texts.keys())
        not_audited = total_leads - len(audited_ids)

        # --- 3. Classification (pure rules, zero AI) ---
        classification: Dict[str, Dict[str, str]] = {}
        if audited_ids:
            items = [
                {"id": pid, "text": feedback_texts[pid], "total_attempts": len(feedback_by_lead.get(pid, []))}
                for pid in audited_ids
            ]
            classification = classify_feedback_batch(items)

        good = sum(1 for c in classification.values() if c["classification"] == "Good")
        bad = sum(1 for c in classification.values() if c["classification"] == "Bad")
        unclear = sum(1 for c in classification.values() if c["classification"] == "Unclear")

        # --- 4. Geography (ALL leads, incl. not-audited) ---
        geo_counter = Counter(l["geo"] for l in leads.values())
        geography = [
            {"city_state": g, "count": c}
            for g, c in geo_counter.most_common()
        ]
        geo_total = sum(g["count"] for g in geography)

        # State-wise breakdown for the third column + India map.
        # mx_State is empty tenant-wide, so state is derived from mx_City.
        state_counter = Counter()
        for l in leads.values():
            st = _derive_state(l["city"], l["state"])
            state_counter[st.title() if st else "Unknown"] += 1
        state_geo = [
            {"state": st, "count": c}
            for st, c in state_counter.most_common()
        ]
        state_total = sum(g["count"] for g in state_geo)

        integrity_ok = (geo_total == total_leads and state_total == total_leads)
        summary = {
            "total_leads": total_leads,
            "audited": len(audited_ids),
            "not_audited": not_audited,
            "good": good,
            "bad": bad,
            "unclear": unclear,
        }

        result = {
            "account": account.name,
            "start_date": start_date,
            "end_date": end_date,
            "summary": summary,
            "geography": geography,
            "geo_total": geo_total,
            "state_geo": state_geo,
            "state_total": state_total,
            "integrity_ok": integrity_ok,
            "reconciliation": {
                "table1_total_leads": total_leads,
                "table2_total_leads": geo_total,
                "state_total_leads": state_total,
                "match": integrity_ok,
            },
            "classification_detail": classification,  # pid -> {classification, reason}
            "feedback_texts": feedback_texts,          # pid -> corpus used
        }
        _REPORT_CACHE[cache_key] = {"ts": time_module.time(), "data": result}
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def export_call_audit_xlsx(report: Dict[str, Any]) -> bytes:
    """Build .xlsx with Sheet1=Audit Summary, Sheet2=Geography. Returns bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="B45309")
    header_font = Font(color="FFFFFF", bold=True)
    total_fill = PatternFill("solid", fgColor="FEF3C7")

    # --- Sheet 1: Audit Summary ---
    ws1 = wb.active
    ws1.title = "Audit Summary"
    ws1["A1"] = f"DSU Call Audit Report — {report['start_date']} to {report['end_date']}"
    ws1["A1"].font = Font(bold=True, size=13)
    ws1.append([])
    ws1.append(["Metric", "Count"])
    for c in ws1[3]:
        c.fill, c.font = header_fill, header_font
    s = report["summary"]
    rows1 = [
        ("Total leads in date range", s["total_leads"]),
        ("Calls Logged", s["audited"]),
        ("Awaiting First Call", s["not_audited"]),
        ("Good quality leads", s["good"]),
        ("Bad quality leads", s["bad"]),
        ("Needs Re-call", s["unclear"]),
    ]
    for label, count in rows1:
        ws1.append([label, count])
    ws1.append([])
    if not report.get("integrity_ok", True):
        ws1.append(["DATA INTEGRITY WARNING: Table 1 total != Table 2 total"])
    ws1.column_dimensions["A"].width = 34
    ws1.column_dimensions["B"].width = 12

    # --- Sheet 2: Geography (City) ---
    ws2 = wb.create_sheet("Geography-City")
    ws2["A1"] = "Geography (City) — all leads in date range"
    ws2["A1"].font = Font(bold=True, size=12)
    ws2.append([])
    ws2.append(["City", "Lead Count"])
    for c in ws2[3]:
        c.fill, c.font = header_fill, header_font
    for g in report.get("geography", []):
        ws2.append([g["city_state"], g["count"]])
    ws2.append(["TOTAL", report.get("geo_total", 0)])
    for c in ws2[ws2.max_row]:
        c.fill = total_fill
        c.font = Font(bold=True)
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 12

    # --- Sheet 3: States ---
    ws3 = wb.create_sheet("Geography-State")
    ws3["A1"] = "Geography (State) — all leads in date range"
    ws3["A1"].font = Font(bold=True, size=12)
    ws3.append([])
    ws3.append(["State", "Lead Count"])
    for c in ws3[3]:
        c.fill, c.font = header_fill, header_font
    for g in report.get("state_geo", []):
        ws3.append([g["state"], g["count"]])
    ws3.append(["TOTAL", report.get("state_total", 0)])
    for c in ws3[ws3.max_row]:
        c.fill = total_fill
        c.font = Font(bold=True)
    ws3.column_dimensions["A"].width = 28
    ws3.column_dimensions["B"].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()