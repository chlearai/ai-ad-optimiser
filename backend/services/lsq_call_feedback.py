"""
LeadSquared Call Feedback service for InsightDesk Table 8 (Call Quality).

Syncs "Call Feedback" custom activities (EventCode 210) from LeadSquared's
ProspectActivity.svc/RetrieveRecentlyModified endpoint into a local mirror
table (lsq_call_feedback), then audits lead quality by reviewing the last
5 call remarks per lead.

Call Feedback field mapping (discovered from the DSU LSQ tenant):
  mx_Custom_1 = next follow-up datetime
  mx_Custom_2 = call status when not connected (Ringing No Answer / Number Busy / ...)
  mx_Custom_3 = connected outcome (Interested / Not Interested / Not Eligible / Not Yet Decided)
  mx_Custom_6 = not-interested reason (Fees is Too High / ...)
  mx_Custom_7 = not-eligible reason (Wrong Number / By Mistake Enquired / ...)
"""
import logging
from datetime import datetime, date, timedelta
from collections import Counter, defaultdict
from typing import Dict, Any, List

import requests
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.db.models import Account, CallFeedbackActivity, LeadSquaredLead
from backend.services.lsq_mirror import _get_lsq_credentials

logger = logging.getLogger("AdOptima")

CALL_FEEDBACK_EVENT_CODE = 210


def _ensure_v2(base_url: str) -> str:
    base_url = (base_url or "").rstrip("/")
    if not base_url.endswith("/v2"):
        base_url += "/v2"
    return base_url


def _utc_day_window(day: str) -> (str, str):
    """Convert an IST report date to a UTC window (LSQ stores activity times in UTC).

    IST is UTC+5:30, so the IST day [D 00:00, D 23:59:59] maps to
    UTC [D-1 18:30, D+1 18:29]. We use a slightly padded window.
    """
    d = date.fromisoformat(day)
    start = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def sync_call_feedback(account_id: int, report_date: str, db: Session = None) -> Dict[str, Any]:
    """Sync Call Feedback activities (EventCode 210) for one account into the mirror.

    Fetches all activities modified/created within the UTC window covering the
    IST report date, then upserts them (dedup on LSQ activity id).
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"error": f"Account {account_id} not found"}

        access_key, secret_key, base_url = _get_lsq_credentials(account)
        if not access_key or not secret_key or not base_url:
            return {"error": "LeadSquared credentials not configured"}
        base_url = _ensure_v2(base_url)

        from_date, to_date = _utc_day_window(report_date)

        # LSQ 500s on wide windows with IncludeCustomFields; fetch in 2-hour
        # UTC chunks to keep each call light.
        from_dt = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d %H:%M:%S")
        chunk_hours = 2
        windows = []
        cur_dt = from_dt
        while cur_dt < to_dt:
            nxt = min(cur_dt + timedelta(hours=chunk_hours), to_dt)
            windows.append((cur_dt.strftime("%Y-%m-%d %H:%M:%S"), nxt.strftime("%Y-%m-%d %H:%M:%S")))
            cur_dt = nxt

        all_activities: List[Dict[str, Any]] = []
        total_records = 0
        for win_from, win_to in windows:
            page = 1
            while page <= 50:
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
                        logger.warning(f"Call Feedback fetch window {win_from}-{win_to} page {page} attempt {attempt + 1} failed: {e}")
                        import time as _time
                        _time.sleep(min(2 ** attempt * 5, 30))
                if not success:
                    return {"error": f"LSQ activity fetch failed after retries: {last_err}"}

                records = resp.get("ProspectActivities", [])
                if not records:
                    break
                all_activities.extend(records)
                if len(records) < 200:
                    break
                page += 1
            logger.info(f"Call Feedback chunk {win_from} to {win_to}: cumulative {len(all_activities)}")

        # Upsert into the mirror (dedup on activity_id)
        existing_ids = {
            a.activity_id
            for a in db.query(CallFeedbackActivity)
            .filter(CallFeedbackActivity.account_id == account_id)
            .all()
        }

        # Filter to activities whose UTC datetime falls in the report day (IST) window.
        # We fetch a padded window; keep only rows whose IST day == report_date when
        # the window is a single-day sync, otherwise keep all.
        new_rows = []
        updated = 0
        for rec in all_activities:
            fields = {f.get("Key"): f.get("Value") for f in (rec.get("Fields") or []) if isinstance(f, dict)}
            activity_id = fields.get("ProspectActivityId") or rec.get("Id")
            if not activity_id:
                continue
            if activity_id in existing_ids:
                updated += 1
                continue
            created_raw = (rec.get("CreatedOn") or "").strip()
            try:
                created_dt = datetime.strptime(created_raw[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            status = (fields.get("mx_Custom_2") or "").strip()
            outcome = (fields.get("mx_Custom_3") or "").strip()
            reason = (fields.get("mx_Custom_6") or fields.get("mx_Custom_7") or "").strip()
            # Fallback reason from notable description for non-connected calls
            data_map = {d.get("Key"): d.get("Value") for d in (rec.get("Data") or []) if isinstance(d, dict)}
            created_by = (data_map.get("CreatedByName") or "").strip()
            next_followup = (fields.get("mx_Custom_1") or "").strip()

            new_rows.append(
                CallFeedbackActivity(
                    account_id=account_id,
                    activity_id=activity_id,
                    prospect_id=rec.get("RelatedProspectId") or "",
                    call_status=status,
                    outcome=outcome,
                    reason=reason,
                    next_followup=next_followup,
                    created_by_name=created_by,
                    created_on=created_dt,
                )
            )
            existing_ids.add(activity_id)

        if new_rows:
            db.add_all(new_rows)
        db.commit()

        return {
            "synced": len(new_rows),
            "skipped_existing": updated,
            "total_in_window": len(all_activities),
        }
    except Exception as e:
        logger.exception(f"Call Feedback sync failed for account {account_id}: {e}")
        if db:
            db.rollback()
        return {"error": str(e)}
    finally:
        if close_db and db:
            db.close()


def _classify_quality(remarks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify lead quality from the last N call remarks (standard criteria).

    Evaluates: reachability, interest level, requirement clarity, next-step commitment.
    """
    if not remarks:
        return {"quality": "No Calls", "flag": "grey", "summary": "No call remarks found"}

    total = len(remarks)
    connected = [r for r in remarks if r["call_status"] == ""]
    connected_count = len(connected)
    outcomes = [r["outcome"] for r in remarks if r["outcome"]]
    interested = sum(1 for o in outcomes if o.lower() == "interested")
    not_interested = sum(1 for o in outcomes if o.lower() == "not interested")
    not_eligible = sum(1 for o in outcomes if o.lower() == "not eligible")
    undecided = sum(1 for o in outcomes if o.lower() in ("not yet decided", "undecided"))
    has_followup = any(r["next_followup"] for r in remarks)
    reasons = [r["reason"] for r in remarks if r["reason"]]

    # Quality classification logic
    if interested >= 1:
        quality = "Good"
        flag = "green"
        summary = "Interested; " + ("follow-up scheduled" if has_followup else "no follow-up logged")
        if not has_followup:
            quality = "Review"
            flag = "amber"
    elif not_interested > 0:
        quality = "Good"
        flag = "green"
        summary = "Not interested (" + (reasons[0] if reasons else "reason logged") + ")"
    elif not_eligible > 0:
        quality = "Good"
        flag = "green"
        summary = "Not eligible (" + (reasons[0] if reasons else "reason logged") + ")"
    elif undecided > 0:
        quality = "Review"
        flag = "amber"
        summary = "Undecided; needs nurturing" + ("; follow-up set" if has_followup else "; no follow-up logged")
    elif connected_count > 0:
        quality = "Review"
        flag = "amber"
        summary = f"{connected_count} connected call(s) but no outcome logged"
    elif total >= 3:
        quality = "Suspect"
        flag = "red"
        summary = f"Unreachable across {total} attempts"
    else:
        quality = "Review"
        flag = "amber"
        summary = f"Only {total} attempt(s), all unanswered"

    return {
        "quality": quality,
        "flag": flag,
        "summary": summary,
        "connected_count": connected_count,
        "total_remarks": total,
        "interested": interested,
        "not_interested": not_interested,
        "not_eligible": not_eligible,
        "undecided": undecided,
    }


def _fetch_lead_names_for_day(account, start_date: str) -> Dict[str, Dict[str, str]]:
    """Bulk-fetch names/stages/courses for leads created on the report day.

    Uses Leads.RecentlyModified (1-3 paginated calls) and filters to CreatedOn
    within the IST report day. Returns {prospect_id: {name, stage, course, source}}.
    """
    access_key, secret_key, base_url = _get_lsq_credentials(account)
    if not access_key or not secret_key or not base_url:
        return {}
    base_url = _ensure_v2(base_url)

    d = date.fromisoformat(start_date)
    # UTC window covering the IST day
    from_dt = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)
    to_dt = datetime.combine(d + timedelta(days=1), datetime.min.time()) + timedelta(hours=18, minutes=30)

    out: Dict[str, Dict[str, str]] = {}
    page = 1
    while page <= 50:
        payload = {
            "Parameter": {
                "FromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ToDate": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "Columns": {
                "Include_CSV": "ProspectID,FirstName,LastName,Stage,mx_Student_Stage,mx_Application_Course,Source,SourceCampaign,CreatedOn"
            },
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "ProspectAutoId", "Direction": "1"},
        }
        try:
            r = requests.post(
                f"{base_url}/LeadManagement.svc/Leads.RecentlyModified",
                params={"accessKey": access_key, "secretKey": secret_key},
                json=payload,
                timeout=120,
            )
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            logger.warning(f"Call Quality lead-name fetch page {page} failed: {e}")
            break
        records = resp.get("Leads", [])
        if not records:
            break
        for rec in records:
            props = {i.get("Attribute"): i.get("Value") for i in rec.get("LeadPropertyList", []) if isinstance(i, dict)}
            pid = props.get("ProspectID") or rec.get("ProspectID")
            if not pid:
                continue
            created = (props.get("CreatedOn") or "")[:10]  # ISO or DD-MM-YYYY head
            # Keep leads created on the IST report day (ISO head match covers ISO format)
            if created != start_date:
                # try DD-MM-YYYY parse
                try:
                    created_ist = (datetime.strptime((props.get("CreatedOn") or "").strip()[:19], "%d-%m-%Y %H:%M:%S") + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    created_ist = ""
                if created_ist != start_date:
                    continue
            first = (props.get("FirstName") or "").strip()
            last = (props.get("LastName") or "").strip()
            out[pid] = {
                "name": (first + " " + last).strip() or pid[:8],
                "stage": (props.get("Stage") or props.get("mx_Student_Stage") or "").strip(),
                "course": (props.get("mx_Application_Course") or "").strip(),
                "source": (props.get("Source") or "").strip(),
            }
        if len(records) < 1000:
            break
        page += 1
    return out


def fetch_call_quality(account_id: int, report_date: str, audit_depth: int = 5) -> Dict[str, Any]:
    """Build Table 8 (Call Quality) data for one report day.

    1. Ensures the mirror has today's Call Feedback activities (syncs if needed).
    2. Gets the leads created on the report date from the LSQ lead mirror.
    3. For each lead, takes the last N remarks (all-time, most recent first)
       and classifies quality on standard criteria.
    4. Returns summary counts + per-lead audit rows.
    """
    db = SessionLocal()
    try:
        # Step 1: ensure mirror is fresh for the report day
        sync_result = sync_call_feedback(account_id, report_date, db=db)
        if "error" in sync_result:
            return {"error": sync_result["error"]}

        # Step 2: leads created on the report date (from lead mirror)
        leads = (
            db.query(LeadSquaredLead)
            .filter(
                LeadSquaredLead.account_id == account_id,
                LeadSquaredLead.created_on == report_date,
            )
            .all()
        )
        lead_by_prospect = {l.prospect_id: l for l in leads if l.prospect_id}
        prospect_ids = set(lead_by_prospect.keys())

        # Step 3: all remarks for these leads (last N per lead, most recent first)
        remarks_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        if prospect_ids:
            rows = (
                db.query(CallFeedbackActivity)
                .filter(
                    CallFeedbackActivity.account_id == account_id,
                    CallFeedbackActivity.prospect_id.in_(prospect_ids),
                )
                .order_by(CallFeedbackActivity.created_on.desc())
                .all()
            )
            for r in rows:
                remarks_map[r.prospect_id].append(
                    {
                        "call_status": r.call_status or "",
                        "outcome": r.outcome or "",
                        "reason": r.reason or "",
                        "next_followup": r.next_followup or "",
                        "agent": r.created_by_name or "",
                        "at": r.created_on.strftime("%d-%b %H:%M"),
                    }
                )

        # Step 4: per-lead audit + roll-up
        # Enrich with names/stages/courses from LSQ (bulk, fast)
        account = db.query(Account).filter(Account.id == account_id).first()
        enriched = _fetch_lead_names_for_day(account, report_date) if account else {}

        outcome_counter: Counter = Counter()
        status_counter: Counter = Counter()
        quality_counter: Counter = Counter()
        audit_rows = []

        for pid, lead in lead_by_prospect.items():
            all_remarks = remarks_map.get(pid, [])
            last5 = all_remarks[:audit_depth]
            verdict = _classify_quality(last5)

            outcome_counter[last5[0]["outcome"] if last5 and last5[0]["outcome"] else (
                last5[0]["call_status"] if last5 else "No Call"
            )] += 1
            for r in last5:
                status_counter[r["call_status"] or "Connected"] += 1
            quality_counter[verdict["quality"]] += 1

            lead_row = lead_by_prospect[pid]
            info = enriched.get(pid, {})
            audit_rows.append(
                {
                    "student": info.get("name") or pid[:8],
                    "prospect_id": pid,
                    "stage": info.get("stage") or lead_row.student_stage or "",
                    "course": info.get("course") or lead_row.course or "",
                    "source": info.get("source") or lead_row.source or "",
                    "owner": "",
                    "total_calls": len(all_remarks),
                    "audited_calls": len(last5),
                    "last_outcome": last5[0]["outcome"] if last5 and last5[0]["outcome"] else (
                        last5[0]["call_status"] if last5 else "No Call Yet"
                    ),
                    "last_reason": last5[0]["reason"] if last5 else "",
                    "last_agent": last5[0]["agent"] if last5 else "",
                    "last_at": last5[0]["at"] if last5 else "",
                    "quality": verdict["quality"],
                    "flag": verdict["flag"],
                    "summary": verdict["summary"],
                    "remarks": last5,
                }
            )

        # Sort audit rows: flagged first (red, amber), then by call count desc
        flag_order = {"red": 0, "amber": 1, "green": 2, "grey": 3}
        audit_rows.sort(key=lambda r: (flag_order.get(r["flag"], 9), -r["total_calls"]))

        total_leads = len(audit_rows)
        audited = sum(1 for r in audit_rows if r["audited_calls"] > 0)
        connected_total = sum(1 for r in audit_rows if r["flag"] in ("green", "amber") and r["last_outcome"] not in ("Ringing No Answer", "Number Busy", "Switched Off", "Number does not exist", "Out of Coverage", "No Call Yet"))

        return {
            "report_date": report_date,
            "audit_depth": audit_depth,
            "sync": sync_result,
            "summary": {
                "leads_created": total_leads,
                "calls_audited": audited,
                "not_audited": total_leads - audited,
                "connected_leads": connected_total,
                "quality_good": quality_counter.get("Good", 0),
                "quality_review": quality_counter.get("Review", 0),
                "quality_suspect": quality_counter.get("Suspect", 0),
                "quality_no_calls": quality_counter.get("No Calls", 0),
            },
            "outcome_breakdown": dict(outcome_counter.most_common()),
            "call_status_breakdown": dict(status_counter.most_common()),
            "rows": audit_rows,
        }
    finally:
        db.close()