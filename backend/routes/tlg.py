from datetime import date, datetime, timedelta
import hashlib
import secrets
from pathlib import Path
import json
import requests
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, TlgCentre, TlgSheetReport, UserAccountAssignment
from backend.routes.auth import get_current_user_required
from backend.services.tlg import CENTRES, ACTIVE_CENTRES, META_AD_ACCOUNT_ID, sheet_ref

router = APIRouter(prefix="/api/tlg", tags=["TLG"])

def access(account_id, db, user, edit=False):
    admin = user.role in ("admin", "superadmin")
    if not admin and (edit or not user.access_insightdesk or not db.query(UserAccountAssignment).filter_by(user_id=user.id, account_id=account_id).first()):
        raise HTTPException(403, "Not authorized")
    account = db.query(Account).filter_by(id=account_id).first()
    if not account or "little gym" not in account.name.lower():
        raise HTTPException(404, "TLG account not found")
    return account

def serial(row):
    return {k: (getattr(row, k).isoformat() + "Z" if k == "last_sync" and row.last_sync else getattr(row, k)) for k in ("id", "name", "source_url", "destination_url", "enabled", "last_sync", "copied", "error")}

class SheetCounts(BaseModel):
    daily_counts: dict[str, int]

@router.post("/{account_id}/centres/{centre_id}/reporting-script")
def reporting_script(account_id: int, centre_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user, edit=True)
    centre = db.query(TlgCentre).filter_by(id=centre_id, account_id=account_id).first()
    if not centre or not centre.source_url:
        raise HTTPException(400, "Save the master sheet link first")
    sid, gid = sheet_ref(centre.source_url)
    token = secrets.token_urlsafe(32)
    row = db.query(TlgSheetReport).filter_by(centre_id=centre_id).first()
    if not row:
        row = TlgSheetReport(centre_id=centre_id)
        db.add(row)
    row.token_hash = hashlib.sha256(token.encode()).hexdigest()
    row.source_url = centre.source_url
    row.daily_counts = "{}"
    row.updated_at = None
    db.commit()
    template = (Path(__file__).parents[1] / "integrations/google_apps_script/tlg_report_counts.gs").read_text()
    config = {"sheetId":sid, "gid":gid, "token":token, "url":f"https://ai-ad-optimiser-production-dd12.up.railway.app/api/tlg/{account_id}/centres/{centre_id}/sheet-counts"}
    return {"script": template.replace("__TLG_REPORT_CONFIG__", json.dumps(config))}

@router.post("/{account_id}/centres/{centre_id}/sheet-counts")
def receive_sheet_counts(account_id: int, centre_id: int, body: SheetCounts, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    row = db.query(TlgSheetReport).filter_by(centre_id=centre_id).first()
    centre = db.query(TlgCentre).filter_by(id=centre_id, account_id=account_id).first()
    token = authorization.removeprefix("Bearer ")
    if not row or not centre or not authorization.startswith("Bearer ") or not secrets.compare_digest(row.token_hash, hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(401, "Invalid reporting connection")
    if row.source_url != centre.source_url:
        raise HTTPException(409, "Master sheet changed; reconnect lead counts")
    if len(body.daily_counts) > 10000:
        raise HTTPException(400, "Too many dates")
    for day, count in body.daily_counts.items():
        try:
            if date.fromisoformat(day).isoformat() != day or count < 0:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "Invalid daily lead counts")
    row.daily_counts = json.dumps(body.daily_counts)
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"saved":True}

def report_lead_count(report, source_url, start, end):
    if not report or report.source_url != source_url or not report.updated_at or report.updated_at < datetime.utcnow() - timedelta(minutes=15):
        return None
    return sum(count for day, count in json.loads(report.daily_counts).items() if str(start) <= day <= str(end))

@router.get("/{account_id}/centres")
def centres(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    existing = db.query(TlgCentre).filter_by(account_id=account_id).all()
    if not existing:
        db.add_all([TlgCentre(account_id=account_id, name=name, enabled=name in ACTIVE_CENTRES) for name in CENTRES])
        db.commit()
        existing = db.query(TlgCentre).filter_by(account_id=account_id).all()
    return [serial(row) for row in sorted(existing, key=lambda r: r.id)]

@router.get("/{account_id}/sheets-connection")
def sheets_connection(account_id: int, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user)
    from backend.services.tlg import sheets_identity
    try:
        info = sheets_identity()
        from google.oauth2.service_account import Credentials
        Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return {"configured": True, "email": info["client_email"], "message": "Share each master sheet as Viewer and each branch sheet as Editor with this email. Sync will retry automatically."}
    except Exception:
        return {"configured": False, "email": None, "message": "Google Sheets credentials are missing or invalid on the server. An administrator needs to configure the service account once."}

class Settings(BaseModel):
    source_url: str = Field(default="", max_length=2048)
    destination_url: str = Field(default="", max_length=2048)
    enabled: bool = True

class BranchName(BaseModel):
    name: str = Field(min_length=1, max_length=120)

def validate_branch_name(name, account_id, db, exclude_id=None):
    name = " ".join(name.split())
    if not name or not normalized(name):
        raise HTTPException(400, "Enter a branch name")
    for other in db.query(TlgCentre).filter_by(account_id=account_id).all():
        if other.id != exclude_id and normalized(other.name) == normalized(name):
            raise HTTPException(409, "A branch with this name already exists")
    return name

@router.post("/{account_id}/centres", status_code=201)
def create_centre(account_id: int, body: BranchName, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user, edit=True)
    row = TlgCentre(account_id=account_id, name=validate_branch_name(body.name, account_id, db), enabled=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return serial(row)

@router.patch("/{account_id}/centres/{centre_id}")
def rename_centre(account_id: int, centre_id: int, body: BranchName, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user, edit=True)
    row = db.query(TlgCentre).filter_by(id=centre_id, account_id=account_id).with_for_update().first()
    if not row:
        raise HTTPException(404, "Branch not found")
    row.name = validate_branch_name(body.name, account_id, db, centre_id)
    db.commit()
    return serial(row)

@router.put("/{account_id}/centres/{centre_id}")
def save(account_id: int, centre_id: int, body: Settings, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    access(account_id, db, user, edit=True)
    row = db.query(TlgCentre).filter_by(id=centre_id, account_id=account_id).with_for_update().first()
    if not row:
        raise HTTPException(404, "Centre not found")
    try:
        for url in (body.source_url, body.destination_url):
            if url:
                sheet_ref(url)
        if body.source_url and body.destination_url and sheet_ref(body.source_url)[0] == sheet_ref(body.destination_url)[0]:
            raise ValueError("Use separate master and branch spreadsheets")
        if body.destination_url:
            for other in db.query(TlgCentre).filter(TlgCentre.id != centre_id).all():
                if other.destination_url and sheet_ref(other.destination_url) == sheet_ref(body.destination_url):
                    raise ValueError("This branch worksheet is already assigned to another centre")
        if row.copied and (body.source_url != row.source_url or body.destination_url != row.destination_url):
            raise ValueError("This route has transferred leads; changing sheets requires a reviewed migration")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    for key in ("source_url", "destination_url", "enabled"):
        setattr(row, key, getattr(body, key))
    db.commit()
    return serial(row)

def normalized(value):
    return "".join(c for c in value.lower() if c.isalnum())

def centre_name(name, names=None):
    matches = [c for c in (CENTRES if names is None else names) if normalized(c) in normalized(name)]
    return matches[0] if len(matches) == 1 else "Unmapped"

@router.get("/{account_id}/performance")
def performance(account_id: int, start: date, end: date, db: Session = Depends(get_db), user=Depends(get_current_user_required)):
    account = access(account_id, db, user)
    if start > end or (end-start).days > 366:
        raise HTTPException(400, "Choose an ordered range up to one year")
    rows, errors = [], {}
    saved_centres = db.query(TlgCentre).filter_by(account_id=account_id).all()
    saved_names = [r.name for r in saved_centres]
    counts = {r.name: report_lead_count(db.query(TlgSheetReport).filter_by(centre_id=r.id).first(), r.source_url, start, end) for r in saved_centres}
    names = saved_names or CENTRES
    statuses = {c: {"meta": "Not connected", "google": "Not connected"} for c in names}
    from backend.services.connectors import GoogleAdsConnector, get_meta_access_token
    for platform in ("google", "meta"):
        try:
            campaigns = []
            if platform == "google":
                connector = GoogleAdsConnector(account, start_date=str(start), end_date=str(end))
                if not connector.is_valid:
                    raise ValueError("Connect Google Ads to load spend")
                cid = (account.google_external_id or account.external_id).replace("-", "")
                query = f"SELECT campaign.name, campaign.status, metrics.cost_micros, metrics.conversions FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}' AND campaign.status != 'REMOVED'"
                for r in connector.client.get_service("GoogleAdsService").search(customer_id=cid, query=query):
                    campaigns.append({"name": r.campaign.name, "status": r.campaign.status.name, "spend": r.metrics.cost_micros / 1e6, "results": float(r.metrics.conversions), "result_type": "Conversions"})
            else:
                token = get_meta_access_token(account)
                if not token:
                    raise ValueError("Connect Meta Ads to load live status")
                aid = META_AD_ACCOUNT_ID
                url = f"https://graph.facebook.com/{__import__('os').getenv('META_API_VERSION', 'v20.0')}/act_{aid}/campaigns"
                params = {"access_token": token, "fields": 'name,effective_status,insights.time_range('+json.dumps({"since":str(start),"until":str(end)},separators=(",", ":"))+'){spend,actions}', "limit": 100}
                while url:
                    response = requests.get(url, params=params, timeout=30)
                    response.raise_for_status()
                    payload = response.json()
                    for c in payload.get("data", []):
                        insights = c.get("insights", {}).get("data", [])
                        spend = sum(float(i.get("spend", 0)) for i in insights)
                        campaigns.append({"name": c["name"], "status": c["effective_status"], "spend": spend, "results": None, "result_type": "Leads (Website)"})
                    url = payload.get("paging", {}).get("next")
                    params = None
            for centre in names:
                linked = [c for c in campaigns if centre_name(c["name"], names) == centre]
                statuses[centre][platform] = "Live" if any(c["status"] in ("ACTIVE", "ENABLED") for c in linked) else "Paused" if linked else "Not started"
            for c in campaigns:
                centre = centre_name(c["name"], names)
                if centre == "Unmapped" or c["status"] not in ("ACTIVE", "ENABLED"):
                    continue
                live_matches = [x for x in campaigns if x["status"] in ("ACTIVE", "ENABLED") and centre_name(x["name"], names) == centre]
                results = counts.get(centre) if platform == "meta" and len(live_matches) == 1 else None
                rows.append({**c, "platform": platform, "centre": centre, "results":results, "result_type":"Leads (Website)", "cost_per_result":c["spend"] / results if results else None, "lead_error":("Multiple live campaigns need a campaign column in the master sheet" if len(live_matches)>1 else "Connect master sheet lead counts") if results is None else None})
        except Exception:
            errors[platform] = "Connection or API access failed. Check the account integration and permissions."
            for centre in names:
                statuses[centre][platform] = "Unavailable"
    return {"rows": rows, "statuses": statuses, "errors": errors, "currency": account.currency, "checked_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
