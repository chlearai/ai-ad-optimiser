from datetime import date
import json
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account, TlgCentre, UserAccountAssignment
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
    saved_names = [r.name for r in db.query(TlgCentre).filter_by(account_id=account_id).all()]
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
                        leads = sum(float(a["value"]) for i in insights for a in i.get("actions", []) if a["action_type"] == "lead")
                        campaigns.append({"name": c["name"], "status": c["effective_status"], "spend": spend, "results": leads, "result_type": "Leads (Meta)"})
                    url = payload.get("paging", {}).get("next")
                    params = None
            for centre in names:
                linked = [c for c in campaigns if centre_name(c["name"], names) == centre]
                statuses[centre][platform] = "Live" if any(c["status"] in ("ACTIVE", "ENABLED") for c in linked) else "Paused" if linked else "Not started"
            for c in campaigns:
                if centre_name(c["name"], names) == "Unmapped":
                    continue
                rows.append({**c, "platform": platform, "centre": centre_name(c["name"], names), "cost_per_result": c["spend"] / c["results"] if c["results"] else None})
        except Exception:
            errors[platform] = "Connection or API access failed. Check the account integration and permissions."
            for centre in names:
                statuses[centre][platform] = "Unavailable"
    return {"rows": rows, "statuses": statuses, "errors": errors, "currency": account.currency, "checked_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
