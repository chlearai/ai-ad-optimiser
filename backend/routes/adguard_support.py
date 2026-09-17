"""
AdGuard SaaS — Support Tickets, Connection Manager, Reports & Settings routes.

Customer (workspace owner) endpoints:
  POST /api/adguard/support/tickets              create ticket
  GET  /api/adguard/support/tickets              my tickets
  GET  /api/adguard/support/tickets/{id}         ticket thread
  POST /api/adguard/support/tickets/{id}/reply   customer reply
  POST /api/adguard/support/tickets/{id}/close   customer closes own ticket
  GET  /api/adguard/connections                  connected accounts + status + last sync
  GET  /api/adguard/reports/campaigns            per-campaign junk report (date range)
  GET  /api/adguard/reports/flags                waste-type breakdown (date range)
  GET  /api/adguard/reports/export               CSV with column selection
  GET  /api/adguard/settings                     my workspace settings
  PUT  /api/adguard/settings                     update timezone/alert emails/protection mode

Owner (admin):
  GET  /api/adguard/support/admin/inbox          all tickets (filter by status)
  POST /api/adguard/support/admin/tickets/{id}/reply   owner reply (-> status answered)
  POST /api/adguard/support/admin/tickets/{id}/close
"""
import csv
import io
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import AdGuardAccount, AdGuardLead, AdGuardSupportTicket, AdGuardTicketMessage, User
from backend.routes.adguard import _require_adguard_access
from backend.routes.auth import get_current_user_required
from backend.services.activity_log import log_activity

logger = logging.getLogger("AdOptima")

router = APIRouter(prefix="/api/adguard", tags=["adguard-support"])

VALID_CATEGORIES = {"general", "connection", "billing", "bug", "feature"}


def _ws_scope(db: Session, user: User, workspace_id: Optional[int] = None):
    """Return workspaces the user may see (admin=all, customer=own)."""
    q = db.query(AdGuardAccount)
    if workspace_id:
        q = q.filter(AdGuardAccount.id == workspace_id)
    if user.role not in ("admin", "superadmin"):
        q = q.filter(AdGuardAccount.owner_email == user.email)
    return q.all()


# ---------------------------------------------------------------------------
# SUPPORT TICKETS — customer side
# ---------------------------------------------------------------------------

class TicketCreateRequest(BaseModel):
    subject: str
    body: str
    category: str = "general"
    priority: str = "normal"
    workspace_id: Optional[int] = None


@router.post("/support/tickets")
def create_ticket(req: TicketCreateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    if not req.subject.strip() or not req.body.strip():
        raise HTTPException(status_code=400, detail="Subject and message required")
    if req.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    ws = None
    if req.workspace_id:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
        if ws and ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Not your workspace")
    else:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == user.email).first()

    ticket = AdGuardSupportTicket(
        workspace_id=ws.id if ws else None,
        requester_email=user.email,
        requester_name=user.full_name or user.email,
        subject=req.subject.strip()[:200],
        category=req.category,
        priority=req.priority if req.priority in ("low", "normal", "high") else "normal",
        status="open",
    )
    db.add(ticket)
    db.flush()
    db.add(AdGuardTicketMessage(
        ticket_id=ticket.id,
        sender="customer",
        sender_name=user.full_name or user.email,
        body=req.body.strip(),
    ))
    db.commit()
    db.refresh(ticket)
    log_activity(module="AdGuard", action="Ticket Created",
                 description=f"Ticket #{ticket.id}: {ticket.subject}",
                 user_id=user.id, user_name=user.email, entity_type="adguard_ticket", entity_id=str(ticket.id), db=db)
    return ticket.to_dict(include_messages=True)


@router.get("/support/tickets")
def list_my_tickets(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    q = db.query(AdGuardSupportTicket).filter(AdGuardSupportTicket.requester_email == user.email)
    tickets = q.order_by(AdGuardSupportTicket.updated_at.desc()).all()
    return [t.to_dict() for t in tickets]


def _ticket_visible(ticket: AdGuardSupportTicket, user: User) -> bool:
    return user.role in ("admin", "superadmin") or ticket.requester_email == user.email


@router.get("/support/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    t = db.query(AdGuardSupportTicket).filter(AdGuardSupportTicket.id == ticket_id).first()
    if not t or not _ws_scope_ok_ticket(t, user):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t.to_dict(include_messages=True)


def _ws_scope_ok_ticket(t: AdGuardSupportTicket, user: User) -> bool:
    if user.role in ("admin", "superadmin"):
        return True
    return t.requester_email == user.email


class TicketReplyRequest(BaseModel):
    body: str


@router.post("/support/tickets/{ticket_id}/reply")
def reply_ticket(ticket_id: int, req: TicketReplyRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    t = db.query(AdGuardSupportTicket).filter(AdGuardSupportTicket.id == ticket_id).first()
    if not t or not _ws_scope_ok_ticket(t, user):
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Message required")
    is_owner = user.role in ("admin", "superadmin") and t.requester_email != user.email
    db.add(AdGuardTicketMessage(
        ticket_id=t.id,
        sender="owner" if is_owner else "customer",
        sender_name=user.full_name or user.email,
        body=req.body.strip(),
    ))
    if is_owner:
        t.status = "answered"
    else:
        t.status = "open"
    db.commit()
    db.refresh(t)
    return t.to_dict(include_messages=True)


@router.post("/support/tickets/{ticket_id}/close")
def close_ticket(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    t = db.query(AdGuardSupportTicket).filter(AdGuardSupportTicket.id == ticket_id).first()
    if not t or not _ws_scope_ok_ticket(t, user):
        raise HTTPException(status_code=404, detail="Ticket not found")
    t.status = "closed"
    db.commit()
    return t.to_dict(include_messages=True)


# ---------------------------------------------------------------------------
# SUPPORT TICKETS — owner inbox
# ---------------------------------------------------------------------------

@router.get("/support/admin/inbox")
def admin_inbox(status: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    q = db.query(AdGuardSupportTicket)
    if status in ("open", "answered", "closed"):
        q = q.filter(AdGuardSupportTicket.status == status)
    tickets = q.order_by(AdGuardSupportTicket.updated_at.desc()).limit(200).all()
    open_count = db.query(func.count(AdGuardSupportTicket.id)).filter(AdGuardSupportTicket.status == "open").scalar() or 0
    return {"open_count": open_count, "tickets": [t.to_dict() for t in tickets]}


# ---------------------------------------------------------------------------
# CONNECTION MANAGER
# ---------------------------------------------------------------------------

@router.get("/connections")
def list_connections(db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Connected ad accounts with health status + last sync (customer sees own)."""
    _require_adguard_access(user)
    out = []
    for ws in _ws_scope(db, user):
        def _load(raw):
            try:
                return json.loads(raw) if raw else []
            except Exception:
                return []
        identities = _load(ws.google_identities)
        identity_accounts = {}
        for i in identities:
            if i.get("email"):
                identity_accounts[i["email"]] = i.get("discovered") or []
        meta_identities = _load(ws.meta_identities)
        meta_identity_accounts = {}
        meta_identity_pages = {}
        for i in meta_identities:
            if i.get("label"):
                meta_identity_accounts[i["label"]] = i.get("discovered_accounts") or []
                meta_identity_pages[i["label"]] = i.get("discovered_pages") or []
        out.append({
            "workspace_id": ws.id,
            "name": ws.display_name or ws.owner_email,
            "google": {
                "connected": bool(ws.google_is_live),
                "last_sync_at": ws.google_last_sync_at.isoformat() if ws.google_last_sync_at else None,
                "accounts": _load(ws.discovered_accounts)[:50],
                "identities": [
                    {"email": i.get("email"), "connected_at": i.get("connected_at")}
                    for i in identities
                ],
                "identity_accounts": identity_accounts,
            },
            "meta": {
                "connected": bool(ws.meta_is_live),
                "last_sync_at": ws.meta_last_sync_at.isoformat() if ws.meta_last_sync_at else None,
                "accounts": _load(ws.discovered_meta_accounts)[:50],
                "pages": _load(ws.discovered_meta_pages)[:50],
                "identities": [
                    {"label": i.get("label"), "connected_at": i.get("connected_at")}
                    for i in meta_identities
                ],
                "identity_accounts": meta_identity_accounts,
                "identity_pages": meta_identity_pages,
            },
        })
    return {"connections": out}


def _touch_sync(db: Session, ws: AdGuardAccount, platform: str):
    """Update last-sync timestamp (called from poller/callbacks)."""
    now = datetime.utcnow()
    if platform == "meta":
        ws.meta_last_sync_at = now
    else:
        ws.google_last_sync_at = now
    db.commit()


class ConnectionActionRequest(BaseModel):
    workspace_id: int
    platform: str  # google | meta
    identity_email: Optional[str] = None  # for google: remove one login identity
    identity_label: Optional[str] = None  # for meta: remove one Facebook login identity


@router.post("/connections/rediscover")
def rediscover_accounts(req: ConnectionActionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Re-run ad-account discovery with stored credentials. Surfaces real errors."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    platform = (req.platform or "google").lower()

    if platform == "google":
        if not ws.google_is_live:
            raise HTTPException(status_code=400, detail="Google not connected")
        from backend.services.oauth import discover_google_ads_customers_detailed

        # Scan EVERY connected Gmail identity; store accounts per identity
        identities = []
        try:
            identities = json.loads(ws.google_identities) if ws.google_identities else []
        except Exception:
            identities = []
        all_accounts = []
        warnings = []
        if not identities:
            # legacy single-connection workspace: scan the shared blob
            result = discover_google_ads_customers_detailed(ws.google_credentials)
            discovered = result.get("accounts", [])
            ws.discovered_accounts = json.dumps(discovered) if discovered else "[]"
            ws.google_last_sync_at = datetime.utcnow()
            db.commit()
            out = {"status": "ok", "platform": "google", "accounts_found": len(discovered), "accounts": discovered}
            if result.get("error"):
                out["warning"] = result["error"]
            return out
        for ident in identities:
            enc = ident.get("credentials")
            if not enc:
                continue
            result = discover_google_ads_customers_detailed(enc)
            ident["discovered"] = result.get("accounts", [])
            all_accounts.extend(result.get("accounts", []))
            if result.get("error"):
                warnings.append(f"{ident.get('email')}: {result['error']}")
        ws.discovered_accounts = json.dumps(all_accounts) if all_accounts else "[]"
        ws.google_identities = json.dumps(identities)
        ws.google_last_sync_at = datetime.utcnow()
        db.commit()
        out = {"status": "ok", "platform": "google", "accounts_found": len(all_accounts), "accounts": all_accounts}
        if warnings:
            out["warning"] = " | ".join(warnings[:3])
        return out

    if platform == "meta":
        if not ws.meta_is_live:
            raise HTTPException(status_code=400, detail="Meta not connected")
        from backend.services.adguard_meta import (
            discover_meta_ad_accounts,
            discover_meta_pages,
            get_meta_token_from_credentials,
        )
        identities = []
        try:
            identities = json.loads(ws.meta_identities) if ws.meta_identities else []
        except Exception:
            identities = []
        all_accounts, all_pages = [], []
        warnings = []
        if not identities:
            token = get_meta_token_from_credentials(ws.meta_credentials or "")
            if not token:
                raise HTTPException(status_code=400, detail="Meta token unreadable — reconnect Meta")
            accounts = discover_meta_ad_accounts(token)
            pages = discover_meta_pages(token)
            ws.discovered_meta_accounts = json.dumps(accounts) if accounts else "[]"
            ws.discovered_meta_pages = json.dumps(pages) if pages else "[]"
            ws.meta_last_sync_at = datetime.utcnow()
            db.commit()
            return {"status": "ok", "platform": "meta", "accounts_found": len(accounts or []), "pages_found": len(pages or [])}
        for ident in identities:
            enc = ident.get("credentials")
            if not enc:
                continue
            token = get_meta_token_from_credentials(enc)
            if not token:
                warnings.append(f"{ident.get('label')}: token unreadable")
                continue
            accounts = discover_meta_ad_accounts(token)
            pages = discover_meta_pages(token)
            ident["discovered_accounts"] = accounts or []
            ident["discovered_pages"] = pages or []
            all_accounts.extend(accounts or [])
            all_pages.extend(pages or [])
        ws.discovered_meta_accounts = json.dumps(all_accounts) if all_accounts else "[]"
        ws.discovered_meta_pages = json.dumps(all_pages) if all_pages else "[]"
        ws.meta_identities = json.dumps(identities)
        ws.meta_last_sync_at = datetime.utcnow()
        db.commit()
        out = {"status": "ok", "platform": "meta", "accounts_found": len(all_accounts), "pages_found": len(all_pages)}
        if warnings:
            out["warning"] = " | ".join(warnings[:3])
        return out

    raise HTTPException(status_code=400, detail="platform must be google or meta")


@router.post("/connections/disconnect")
def disconnect_connection(req: ConnectionActionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Disconnect an ad platform (or one Google identity) from a workspace. Keeps lead history."""
    _require_adguard_access(user)
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not your workspace")
    platform = (req.platform or "").lower()

    if platform == "google" and req.identity_email:
        # Remove one Google identity; keep platform live if others remain
        try:
            identities = json.loads(ws.google_identities) if ws.google_identities else []
        except Exception:
            identities = []
        identities = [i for i in identities if i.get("email") != req.identity_email]
        ws.google_identities = json.dumps(identities) if identities else None
        if not identities:
            # last identity removed -> full google disconnect
            ws.google_credentials = None
            ws.google_is_live = False
            ws.discovered_accounts = "[]"
            ws.google_last_sync_at = None
        else:
            merged = []
            seen_ids = set()
            for i in identities:
                for a in i.get("discovered") or []:
                    if a.get("id") not in seen_ids:
                        merged.append(a)
                        seen_ids.add(a.get("id"))
            ws.discovered_accounts = json.dumps(merged)
        db.commit()
        log_activity(module="AdGuard", action="Google Identity Disconnected",
                     description=f"{req.identity_email} disconnected from {ws.display_name or ws.owner_email}",
                     user_id=user.id, user_name=user.full_name or user.email,
                     entity_type="adguard_account", entity_id=str(ws.id), db=db)
        return {"status": "disconnected", "platform": "google", "identity": req.identity_email,
                "google_still_live": ws.google_is_live}

    if platform == "meta" and req.identity_label:
        # Remove one Meta identity; keep platform live if others remain
        try:
            meta_idents = json.loads(ws.meta_identities) if ws.meta_identities else []
        except Exception:
            meta_idents = []
        meta_idents = [i for i in meta_idents if i.get("label") != req.identity_label]
        ws.meta_identities = json.dumps(meta_idents) if meta_idents else None
        if not meta_idents:
            ws.meta_credentials = None
            ws.meta_is_live = False
            ws.discovered_meta_accounts = "[]"
            ws.discovered_meta_pages = "[]"
            ws.meta_last_sync_at = None
        else:
            merged_accs, merged_pages = [], []
            seen_acc_ids, seen_page_ids = set(), set()
            for i in meta_idents:
                for a in i.get("discovered_accounts") or []:
                    if a.get("id") not in seen_acc_ids:
                        merged_accs.append(a)
                        seen_acc_ids.add(a.get("id"))
                for p in i.get("discovered_pages") or []:
                    if p.get("id") not in seen_page_ids:
                        merged_pages.append(p)
                        seen_page_ids.add(p.get("id"))
            ws.discovered_meta_accounts = json.dumps(merged_accs)
            ws.discovered_meta_pages = json.dumps(merged_pages)
        db.commit()
        log_activity(module="AdGuard", action="Meta Identity Disconnected",
                     description=f"{req.identity_label} disconnected from {ws.display_name or ws.owner_email}",
                     user_id=user.id, user_name=user.full_name or user.email,
                     entity_type="adguard_account", entity_id=str(ws.id), db=db)
        return {"status": "disconnected", "platform": "meta", "identity": req.identity_label,
                "meta_still_live": ws.meta_is_live}

    if platform == "meta":
        ws.meta_credentials = None
        ws.meta_is_live = False
        ws.discovered_meta_accounts = "[]"
        ws.discovered_meta_pages = "[]"
        ws.meta_identities = "[]"
        ws.meta_last_sync_at = None
    elif platform == "google":
        ws.google_credentials = None
        ws.google_is_live = False
        ws.discovered_accounts = "[]"
        ws.google_identities = "[]"
        ws.google_last_sync_at = None
    else:
        raise HTTPException(status_code=400, detail="platform must be google or meta")
    db.commit()
    log_activity(module="AdGuard", action="Connection Disconnected",
                 description=f"{platform} disconnected from {ws.display_name or ws.owner_email}",
                 user_id=user.id, user_name=user.full_name or user.email,
                 entity_type="adguard_account", entity_id=str(ws.id), db=db)
    return {"status": "disconnected", "platform": platform}


# ---------------------------------------------------------------------------
# REPORTS
# ---------------------------------------------------------------------------

def _parse_range(days: int):
    days = max(1, min(days, 365))
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    return start, end


@router.get("/reports/campaigns")
def campaign_report(days: int = 7, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Per-campaign junk report: totals, verified, flagged, junk %, estimated recovered spend."""
    _require_adguard_access(user)
    start, end = _parse_range(days)
    q = db.query(AdGuardLead.campaign_name, AdGuardLead.verdict).filter(AdGuardLead.received_at >= start)
    if user.role not in ("admin", "superadmin"):
        ws_ids = [w.id for w in db.query(AdGuardAccount.id).filter(AdGuardAccount.owner_email == user.email).all()]
        q = q.filter(AdGuardLead.adguard_account_id.in_(ws_ids or [0]))
    rows = q.all()
    per: dict = {}
    for name, verdict in rows:
        key = (name or "(unknown)").strip() or "(unknown)"
        b = per.setdefault(key, {"leads": 0, "verified": 0, "flagged": 0})
        b["leads"] += 1
        if verdict == "flagged":
            b["flagged"] += 1
        elif verdict == "verified":
            b["verified"] += 1
    out = []
    for name, b in per.items():
        junk_pct = round(100 * b["flagged"] / b["leads"], 1) if b["leads"] else 0.0
        out.append({
            "campaign": name,
            "leads": b["leads"],
            "verified": b["verified"],
            "flagged": b["flagged"],
            "junk_pct": junk_pct,
            "recovered_spend_inr": b["flagged"] * 350,
        })
    out.sort(key=lambda r: r["leads"], reverse=True)
    return {"days": days, "campaigns": out}


@router.get("/reports/flags")
def flag_breakdown(days: int = 7, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    """Waste-type breakdown: count of each flag reason in the window."""
    _require_adguard_access(user)
    start, end = _parse_range(days)
    q = db.query(AdGuardLead).filter(AdGuardLead.verdict == "flagged", AdGuardLead.received_at >= start)
    if user.role not in ("admin", "superadmin"):
        ws_ids = [w.id for w in db.query(AdGuardAccount.id).filter(AdGuardAccount.owner_email == user.email).all()]
        q = q.filter(AdGuardLead.adguard_account_id.in_(ws_ids or [0]))
    counts: dict = {}
    total_flagged = 0
    for row in q.all():
        try:
            flags = json.loads(row.flags) if row.flags else []
        except Exception:
            flags = []
        for f in flags:
            counts[f] = counts.get(f, 0) + 1
        total_flagged += 1
    breakdown = [{"flag": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return {"days": days, "total_flagged": total_flagged, "breakdown": breakdown}


class ReportExportRequest(BaseModel):
    days: int = 30
    columns: Optional[list] = None  # subset of default columns


REPORT_COLUMNS = [
    "received_at", "full_name", "email", "phone", "city", "campaign_name",
    "lead_type", "integrity_score", "verdict", "lsq_status", "flags",
]


@router.get("/reports/export")
def report_export(
    days: int = 30,
    columns: Optional[str] = Query(default=None, description="comma-separated column names"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required),
):
    """CSV export with date range + column selection (Connection/reports UX standard)."""
    _require_adguard_access(user)
    start, end = _parse_range(days)
    q = db.query(AdGuardLead).filter(AdGuardLead.received_at >= start)
    if user.role not in ("admin", "superadmin"):
        ws_ids = [w.id for w in db.query(AdGuardAccount.id).filter(AdGuardAccount.owner_email == user.email).all()]
        q = q.filter(AdGuardLead.adguard_account_id.in_(ws_ids or [0]))
    cols = [c.strip() for c in columns.split(",")] if columns else REPORT_COLUMNS
    cols = [c for c in cols if c in REPORT_COLUMNS] or REPORT_COLUMNS
    rows = q.order_by(AdGuardLead.received_at.desc()).limit(10000).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for l in rows:
        data = l.to_dict()
        writer.writerow([json.dumps(data.get(c)) if isinstance(data.get(c), list) else (data.get(c) or "") for c in cols])
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=adguard_report_{days}d.csv"},
    )


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

class SettingsUpdateRequest(BaseModel):
    timezone: Optional[str] = None
    alert_emails: Optional[list] = None
    protection_mode: Optional[str] = None  # monitor | protect


@router.get("/settings")
def get_settings(workspace_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    ws_list = _ws_scope(db, user, workspace_id)
    if not ws_list:
        raise HTTPException(status_code=404, detail="No workspace found")
    ws = ws_list[0]
    return {
        "workspace_id": ws.id,
        "timezone": ws.timezone or "Asia/Kolkata",
        "alert_emails": json.loads(ws.alert_emails) if ws.alert_emails else [],
        "protection_mode": ws.protection_mode or "monitor",
        "plan": ws.plan,
        "lead_quota": ws.lead_quota,
    }


@router.put("/settings")
def update_settings(req: SettingsUpdateRequest, workspace_id: Optional[int] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user_required)):
    _require_adguard_access(user)
    ws_list = _ws_scope(db, user, workspace_id)
    if not ws_list:
        raise HTTPException(status_code=404, detail="No workspace found")
    ws = ws_list[0]
    if req.timezone:
        ws.timezone = req.timezone[:50]
    if req.alert_emails is not None:
        clean = [e.strip().lower() for e in req.alert_emails if e.strip() and "@" in e][:5]
        ws.alert_emails = json.dumps(clean)
    if req.protection_mode:
        if req.protection_mode not in ("monitor", "protect"):
            raise HTTPException(status_code=400, detail="protection_mode must be monitor or protect")
        ws.protection_mode = req.protection_mode
        # Mirror into shield_enabled for the governor
        ws.shield_enabled = (req.protection_mode == "protect")
    db.commit()
    return {"status": "ok", "timezone": ws.timezone, "alert_emails": json.loads(ws.alert_emails or "[]"), "protection_mode": ws.protection_mode}


from fastapi.responses import Response  # noqa: E402