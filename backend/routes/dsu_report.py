"""
DSU course performance report: live data from Google Ads + LeadSquared.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, DsuBudgetEntry
from backend.routes.auth import get_current_user_required
from backend.services.activity_log import log_activity
from backend.services.dsu_data import (
    fetch_dsu_daily, fetch_dsu_cumulative,
    fetch_dsu_daily_range, fetch_dsu_cumulative_range,
    _fetch_google_ads_spend, _fetch_lsq_leads, DSU_COURSES,
    fetch_dsu_lead_pivot, fetch_dsu_application_mis,
    fetch_dsu_budget_mis, fetch_dsu_lead_stages,
    fetch_dsu_monthly_summary,
)
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Inception and GST transition dates for DSU
DSU_INCEPTION = "2025-11-28"
DSU_GST_TRANSITION = "2026-06-19"
GST_MULTIPLIER = 1.18


def _fmt_inr(n):
    if n is None:
        return "No Leads"
    return "₹" + str(int(round(n))).replace(",", "_").replace("_", ",")


def _pdf_in(n):
    if n is None:
        return "No Leads"
    return "₹" + str(int(round(n)))


def _pdf_num(n):
    return str(int(n or 0))


def _apply_gst(rows, report_date):
    import datetime

    gst_start = datetime.date.fromisoformat(DSU_GST_TRANSITION)
    report = datetime.date.fromisoformat(report_date)
    include_gst = report >= gst_start
    out = []
    for r in rows:
        spend = r["spend"]
        if include_gst:
            spend = round(spend * GST_MULTIPLIER)
        leads = r["leads"]
        cpl = round(spend / leads) if leads else (r["cpl"] if r["cpl"] is not None else None)
        out.append({"course": r["course"], "leads": leads, "cpl": cpl, "spend": spend, "has_gst": include_gst})
    return out


@router.get("/dsu-performance")
def dsu_performance(
    t1_start: str = Query(None, description="Table 1 start date (YYYY-MM-DD)"),
    t1_end: str = Query(None, description="Table 1 end date (YYYY-MM-DD)"),
    t2_start: str = Query(None, description="Table 2 start date (YYYY-MM-DD)"),
    t2_end: str = Query(None, description="Table 2 end date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    from datetime import date, timedelta

    account = db.query(Account).filter(Account.name == "DSU").first()
    if not account:
        raise HTTPException(status_code=404, detail="DSU account not found")

    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Table 1 date range — default to yesterday
    t1_start_date = t1_start or yesterday
    t1_end_date = t1_end or yesterday

    # Table 2 date range — default to inception to yesterday
    t2_start_date = t2_start or DSU_INCEPTION
    t2_end_date = t2_end or yesterday

    daily_raw = fetch_dsu_daily_range(t1_start_date, t1_end_date)
    # GST is already applied per-day inside _fetch_google_ads_spend.
    # No additional GST application needed here.
    daily = [
        {
            "course": r["course"],
            "leads": r["leads"],
            "cpl": r["cpl"],
            "spend": r["spend"],
            "has_gst": t1_end_date >= DSU_GST_TRANSITION,
        }
        for r in daily_raw
    ]

    cumulative_raw = fetch_dsu_cumulative_range(t2_start_date, t2_end_date)
    # GST is already applied per-day inside _fetch_google_ads_spend for the live
    # portion, and legacy spend has no GST. So we do NOT apply GST again here.
    cumulative = [
        {
            "course": r["course"],
            "leads": r["leads"],
            "cpl": r["cpl"],
            "spend": r["spend"],
        }
        for r in cumulative_raw
    ]

    # Surface Google Ads spend fetch errors to the UI instead of failing silently
    from backend.services.dsu_data import _spend_error_holder
    spend_error = _spend_error_holder.get("message") if _spend_error_holder.get("message") else None

    daily_total = {
        "leads": sum(r["leads"] for r in daily),
        "spend": sum(r["spend"] for r in daily),
    }
    daily_total["cpl"] = round(daily_total["spend"] / daily_total["leads"]) if daily_total["leads"] else 0

    cum_total = {
        "leads": sum(r["leads"] for r in cumulative),
        "spend": sum(r["spend"] for r in cumulative),
    }
    cum_total["cpl"] = round(cum_total["spend"] / cum_total["leads"]) if cum_total["leads"] else 0

    # Data freshness for the lead mirror so users can see when counts were last synced.
    from backend.db.models import LeadSquaredLead
    freshness = (
        db.query(LeadSquaredLead.synced_at)
        .filter(LeadSquaredLead.account_id == account.id)
        .order_by(LeadSquaredLead.synced_at.desc())
        .first()
    )
    freshness_iso = freshness[0].isoformat() + "Z" if freshness and freshness[0] else None

    return {
        "account": account.to_dict() if account else None,
        "t1_start": t1_start_date,
        "t1_end": t1_end_date,
        "t2_start": t2_start_date,
        "t2_end": t2_end_date,
        "inception_date": DSU_INCEPTION,
        "gst_transition_date": DSU_GST_TRANSITION,
        "gst_note": "Spend values before 19-Jun-2026 are without GST. From 19-Jun-2026 onwards, platform cost is multiplied by 1.18.",
        "spend_fetch_error": spend_error,
        "data_freshness": {
            "leads_last_synced_at": freshness_iso,
            "report_generated_at": datetime.utcnow().isoformat() + "Z",
        },
        "daily": {
            "title": f"TABLE 1: {t1_start_date}" + (f" to {t1_end_date}" if t1_start_date != t1_end_date else ""),
            "subtitle": "WITHOUT GST (BASE CAMPAIGN METRICS)" if t1_end_date < DSU_GST_TRANSITION else "WITH GST (18% APPLIED)",
            "rows": daily,
            "total": daily_total,
        },
        "cumulative": {
            "title": f"TABLE 2: {t2_start_date} to {t2_end_date}",
            "subtitle": "WITHOUT GST (BASE CAMPAIGN METRICS)" if t2_end_date < DSU_GST_TRANSITION else "WITH GST (18% APPLIED)",
            "rows": cumulative,
            "total": cum_total,
        },
    }


@router.get("/dsu-performance/pdf")
def dsu_performance_pdf(
    table: str = Query("daily", enum=["daily", "cumulative"]),
    t1_start: str = Query(None),
    t1_end: str = Query(None),
    t2_start: str = Query(None),
    t2_end: str = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    from datetime import date, timedelta
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm

    account = db.query(Account).filter(Account.name == "DSU").first()
    if not account:
        raise HTTPException(status_code=404, detail="DSU account not found")

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    data = dsu_performance(db, user)
    section = data["daily" if table == "daily" else "cumulative"]
    rows = section["rows"]
    total = section["total"]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "dsuTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        alignment=1,
        textColor=colors.HexColor("#1e3a8a"),
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "dsuSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        alignment=1,
        textColor=colors.HexColor("#4338ca"),
        spaceAfter=6,
    )
    note_style = ParagraphStyle(
        "dsuNote",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=12,
    )

    elements = []
    elements.append(Paragraph("DSU Course Performance Report", title_style))
    elements.append(Paragraph(section["subtitle"], sub_style))
    elements.append(Paragraph(section["title"], sub_style))
    elements.append(Paragraph("MIS", sub_style))
    elements.append(Paragraph(data["gst_note"], note_style))
    elements.append(Spacer(1, 0.3 * cm))

    table_data = [["Course", "Leads", "CPL", "Spend"]]
    for r in rows:
        cpl = _pdf_in(r["cpl"]) if r["leads"] else "No Leads"
        table_data.append([r["course"], _pdf_num(r["leads"]), cpl, _pdf_in(r["spend"])])
    total_cpl = _pdf_in(total["cpl"]) if total["leads"] else "No Leads"
    table_data.append(["TOTAL", _pdf_num(total["leads"]), total_cpl, _pdf_in(total["spend"])])

    t = Table(table_data, colWidths=[9 * cm, 3 * cm, 3.5 * cm, 4 * cm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4338ca")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#c7d2fe")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#a5b4fc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#eef2ff")]),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
            ]
        )
    )
    elements.append(t)
    elements.append(Spacer(1, 0.5 * cm))
    elements.append(
        Paragraph(
            f"Inception date: {DSU_INCEPTION} | GST transition: {DSU_GST_TRANSITION}",
            note_style,
        )
    )

    doc.build(elements)
    buffer.seek(0)
    filename = f"DSU_{table}_{yesterday}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================================
# LeadSquared mirror sync (manual trigger for InsightDesk)
# ============================================================================

@router.post("/lsq-sync")
def trigger_lsq_sync(
    account_id: int = Query(..., description="Account ID to sync"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """Manually sync LeadSquared leads for one account into the local mirror.

    InsightDesk reads lead counts from the local mirror. Use this endpoint
    before viewing reports if the mirror is stale (e.g. server was down when
    the scheduled sync ran)."""
    from backend.services.lsq_mirror import sync_account_leads
    result = sync_account_leads(account_id, db=db)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    account = db.query(Account).filter(Account.id == account_id).first()
    log_activity(module="InsightDesk", action="Leads Synced", description=f"LeadSquared leads synced for {account.name if account else 'Client/Brand #'+str(account_id)}: {result.get('synced', result.get('leads', 0))} leads",
                 user_id=getattr(user, "id", None), user_name=getattr(user, "full_name", None) or getattr(user, "email", None),
                 account_id=account_id, account_name=account.name if account else None, db=db)
    return result


# ============================================================================
# DSU Table 3: Lead Attribution Pivot
# ============================================================================

@router.get("/dsu/lead-pivot")
def dsu_lead_pivot(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """DSU Table 3: Lead attribution pivot (Student Source × Student Stage)."""
    return fetch_dsu_lead_pivot(start_date, end_date)


# ============================================================================
# DSU Table 4: Application Submitted and CPA
# ============================================================================

@router.get("/dsu/application-mis")
def dsu_application_mis(
    start_date: str = Query(None, description="Start date (default: inception)"),
    end_date: str = Query(None, description="End date (default: yesterday)"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """DSU Table 4: Application Submitted and CPA by Campus/Program."""
    from datetime import date, timedelta
    s = start_date or DSU_INCEPTION
    e = end_date or (date.today() - timedelta(days=1)).isoformat()
    return fetch_dsu_application_mis(s, e)


# ============================================================================
# DSU Table 5: Budget MIS
# ============================================================================

@router.get("/dsu/budget-mis")
def dsu_budget_mis(
    start_date: str = Query(None, description="Start date (default: inception)"),
    end_date: str = Query(None, description="End date (default: yesterday)"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """DSU Table 5: Budget MIS by Campus/Program."""
    from datetime import date, timedelta
    s = start_date or DSU_INCEPTION
    e = end_date or (date.today() - timedelta(days=1)).isoformat()
    return fetch_dsu_budget_mis(s, e, db_session=db)


# ============================================================================
# DSU Table 6: Lead Stage Summary
# ============================================================================

@router.get("/dsu/lead-stages")
def dsu_lead_stages(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """DSU Table 6: Lead Stage Summary."""
    return fetch_dsu_lead_stages(start_date, end_date)


# ============================================================================
# DSU Table 7: Monthly Spend Summary and Balance
# ============================================================================

@router.get("/dsu/monthly-summary")
def dsu_monthly_summary(
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """DSU Table 7: Monthly spend summary and balance."""
    return fetch_dsu_monthly_summary(db_session=db)


# --- Budget entry CRUD for Table 7 ---

class BudgetEntryCreate(BaseModel):
    date: str
    amount: float
    invoice: Optional[str] = ""
    campus: Optional[str] = ""


class BudgetEntryUpdate(BaseModel):
    date: Optional[str] = None
    amount: Optional[float] = None
    invoice: Optional[str] = None
    campus: Optional[str] = None


@router.get("/dsu/budget-entries")
def list_dsu_budget_entries(
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """List all DSU budget entries."""
    entries = db.query(DsuBudgetEntry).order_by(DsuBudgetEntry.entry_date).all()
    return [e.to_dict() for e in entries]


@router.post("/dsu/budget-entries")
def create_dsu_budget_entry(
    entry: BudgetEntryCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """Create a new DSU budget entry."""
    new_entry = DsuBudgetEntry(
        entry_date=entry.date,
        amount=entry.amount,
        invoice=entry.invoice or "",
        campus=entry.campus or "",
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry.to_dict()


@router.put("/dsu/budget-entries/{entry_id}")
def update_dsu_budget_entry(
    entry_id: int,
    entry: BudgetEntryUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """Update a DSU budget entry."""
    existing = db.query(DsuBudgetEntry).filter(DsuBudgetEntry.id == entry_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Budget entry not found")
    if entry.date is not None:
        existing.entry_date = entry.date
    if entry.amount is not None:
        existing.amount = entry.amount
    if entry.invoice is not None:
        existing.invoice = entry.invoice
    if entry.campus is not None:
        existing.campus = entry.campus
    db.commit()
    db.refresh(existing)
    return existing.to_dict()


@router.delete("/dsu/budget-entries/{entry_id}")
def delete_dsu_budget_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user_required),
):
    """Delete a DSU budget entry."""
    existing = db.query(DsuBudgetEntry).filter(DsuBudgetEntry.id == entry_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Budget entry not found")
    db.delete(existing)
    db.commit()
    return {"deleted": True, "id": entry_id}