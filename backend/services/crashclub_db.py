"""
Crash Club — Meta lead store (app database).

Persists every Crash Club Meta lead into the `crashclub_leads` table so the
InsightDesk MIS report (/insightdesk) can count them in the "Meta Ads" row
of the Source-wise Breakdown.

Storage is account-aware: leads are linked via the Account whose
meta_external_id matches the ad account being synced.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint

from backend.db.database import Base

logger = logging.getLogger("AdOptima")


class CrashClubLead(Base):
    __tablename__ = "crashclub_leads"

    id = Column(Integer, primary_key=True)
    lead_id = Column(String, unique=True, index=True, nullable=False)   # Meta leadgen id
    account_id = Column(Integer, index=True, nullable=True)             # -> accounts.id
    campaign = Column(String, nullable=True)
    adset = Column(String, nullable=True)
    ad = Column(String, nullable=True)
    platform = Column(String, nullable=True)                            # fb / ig
    created_time = Column(DateTime, index=True, nullable=True)          # UTC
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    occasion = Column(String, nullable=True)
    budget = Column(String, nullable=True)
    purchase_timeline = Column(String, nullable=True)
    form_type = Column(String, nullable=True)                           # goa / mw
    raw_json = Column(Text, nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("lead_id", name="uq_crashclub_leads_lead_id"),)


def _parse_meta_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("+0000", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def save_leads(items: List[Dict[str, Any]], db=None) -> Dict[str, Any]:
    """
    Persist leads idempotently (dedup by lead_id).
    items: [{lead: <raw meta lead>, campaign: <campaign name>}, ...]
    """
    from backend.db.database import SessionLocal
    from backend.db.models import Account

    own = None
    if db is None:
        own = SessionLocal()
        db = own

    account_id = None
    try:
        act_id = os.getenv("CRASH_CLUB_AD_ACCOUNT", "act_577546498668650").strip()
        acc = db.query(Account).filter(Account.meta_external_id == act_id).first()
        if acc:
            account_id = acc.id
    except Exception:
        account_id = None

    added = skipped = 0
    try:
        for item in items:
            lead = item.get("lead", {}) or {}
            camp = item.get("campaign", "") or ""
            lid = str(lead.get("id") or "")
            if not lid:
                continue
            if db.query(CrashClubLead).filter(CrashClubLead.lead_id == lid).first():
                skipped += 1
                continue

            fields = {f.get("name"): "; ".join(f.get("values") or []) for f in lead.get("field_data", [])}
            def pick(*keys: str) -> str:
                for k in keys:
                    if fields.get(k):
                        return fields[k]
                return ""

            form_type = "mw" if "magnificent" in camp.lower() else ("goa" if "goa" in camp.lower() else "")
            row = CrashClubLead(
                lead_id=lid,
                account_id=account_id,
                campaign=camp,
                adset="",
                ad=lead.get("ad_name") or "",
                platform=(lead.get("platform") or "").lower() or None,
                created_time=_parse_meta_time(lead.get("created_time")),
                name=pick("full_name", "full name", "name"),
                email=pick("email", "email_address"),
                phone=pick("phone_number", "phone"),
                occasion=pick("what's_the_occasion?"),
                budget=pick("what's_your_jewellery_budget?"),
                purchase_timeline=pick("when_are_you_planning_to_purchase?"),
                form_type=form_type,
                raw_json=json.dumps(lead, ensure_ascii=False),
            )
            db.add(row)
            added += 1
        db.commit()
        logger.info(f"[CrashClub] DB store: added={added} skipped={skipped}")
    except Exception as e:
        db.rollback()
        logger.error(f"[CrashClub] DB store failed: {e}")
        raise
    finally:
        if own:
            own.close()
    return {"added": added, "skipped": skipped}


def get_leads_for_account(account_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None, db=None) -> List[Dict[str, Any]]:
    """CRM-shaped lead dicts for the MIS report (source='Meta Ads')."""
    from backend.db.database import SessionLocal

    own = None
    if db is None:
        own = SessionLocal()
        db = own
    try:
        q = db.query(CrashClubLead).filter(CrashClubLead.account_id == account_id)
        if start_date:
            try:
                sd = datetime.fromisoformat(start_date).replace(tzinfo=None)
                q = q.filter(CrashClubLead.created_time >= sd)
            except Exception:
                pass
        if end_date:
            try:
                ed = datetime.fromisoformat(end_date) + timedelta(days=1)
                q = q.filter(CrashClubLead.created_time < ed)
            except Exception:
                pass
        rows = q.order_by(CrashClubLead.created_time.desc()).all()
        out = []
        for r in rows:
            fields = {}
            try:
                fields = json.loads(r.raw_json or "{}").get("field_data", [])
                fields = {f.get("name"): "; ".join(f.get("values") or []) for f in fields}
            except Exception:
                fields = {}

            def pick(*keys: str) -> str:
                for k in keys:
                    if fields.get(k):
                        return fields[k]
                return ""

            if (r.form_type or "") == "mw":
                col_d = pick("do_you_plan_to_make_a_purchase_in_the_near_future_or_before_march_31st,_2027?")
                col_e = pick("have_you_made_a_purchase_from_c._krishniah_chetty_group_of_jewellers_or_crash.club_at_any_time_before_?")
                col_f = pick("tentative_wedding/special_moment/corporate_events_date._*")
            else:
                col_d = pick("what's_the_occasion?")
                col_e = pick("what's_your_jewellery_budget?")
                col_f = pick("when_are_you_planning_to_purchase?")

            out.append({
                "id": r.lead_id,
                "first_name": r.name or "",
                "last_name": "",
                "email": r.email or "",
                "phone": r.phone or "",
                "company": "",
                "status": "New",
                "source": "Meta Ads",
                "source_campaign": r.campaign or "",
                "created_at": r.created_time.strftime("%Y-%m-%d %H:%M:%S") if r.created_time else "",
                "owner": "",
                "platform": "meta",
                "form_type": r.form_type or "",
                "col_d": col_d,
                "col_e": col_e,
                "col_f": col_f,
            })
        return out
    finally:
        if own:
            own.close()