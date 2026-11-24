"""
OAuth connection routes for Google Ads and Meta Marketing API.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from backend.routes.auth import require_admin_or_superadmin
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import Account
from backend.services.oauth import (
    get_google_auth_url,
    get_meta_auth_url,
    exchange_google_code,
    exchange_meta_code,
    build_google_credentials,
    build_meta_credentials,
    parse_state,
    _account_oauth,
)

router = APIRouter(prefix="/api/oauth", tags=["oauth"])


@router.get("/google/{account_id}/connect", dependencies=[Depends(require_admin_or_superadmin)])
def connect_google(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.has_google:
        raise HTTPException(status_code=400, detail="Google platform is not enabled for this account")
    try:
        url = get_google_auth_url(account_id)
        return {"authorization_url": url}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/google/callback")
def google_callback(request: Request, code: str, state: str, error: Optional[str] = None, db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(url=f"/?oauth_error={error}")
    payload = parse_state(state)
    if not payload or payload.get("platform") != "google":
        return RedirectResponse(url="/?oauth_error=invalid_state")
    account_id = payload.get("account_id")
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return RedirectResponse(url="/?oauth_error=account_not_found")

    redirect_uri = f"{_account_oauth(account)['redirect_base_url'].rstrip('/')}/api/oauth/google/callback"
    token_data = exchange_google_code(code, redirect_uri, account_id)
    if not token_data:
        return RedirectResponse(url="/?oauth_error=google_token_exchange_failed")

    encrypted = build_google_credentials(token_data, account_id)
    if not encrypted:
        return RedirectResponse(url="/?oauth_error=missing_refresh_token")

    account.google_credentials = encrypted
    account.google_is_live = True
    account.is_live = account.google_is_live or account.meta_is_live
    db.commit()
    return RedirectResponse(url=f"/?oauth_success=google&account_id={account_id}")


@router.get("/meta/{account_id}/connect", dependencies=[Depends(require_admin_or_superadmin)])
def connect_meta(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not account.has_meta:
        raise HTTPException(status_code=400, detail="Meta platform is not enabled for this account")
    try:
        url = get_meta_auth_url(account_id)
        return {"authorization_url": url}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/meta/callback")
def meta_callback(request: Request, code: str, state: str, error: Optional[str] = None, db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(url=f"/?oauth_error={error}")
    payload = parse_state(state)
    if not payload or payload.get("platform") != "meta":
        return RedirectResponse(url="/?oauth_error=invalid_state")
    account_id = payload.get("account_id")
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return RedirectResponse(url="/?oauth_error=account_not_found")

    redirect_uri = f"{_account_oauth(account)['redirect_base_url'].rstrip('/')}/api/oauth/meta/callback"
    token_data = exchange_meta_code(code, redirect_uri, account_id)
    if not token_data:
        return RedirectResponse(url="/?oauth_error=meta_token_exchange_failed")

    encrypted = build_meta_credentials(token_data, account_id)
    if not encrypted:
        return RedirectResponse(url="/?oauth_error=missing_access_token")

    account.meta_credentials = encrypted
    account.meta_is_live = True
    account.is_live = account.google_is_live or account.meta_is_live
    db.commit()
    return RedirectResponse(url=f"/?oauth_success=meta&account_id={account_id}")


@router.post("/{platform}/{account_id}/disconnect", dependencies=[Depends(require_admin_or_superadmin)])
def disconnect_oauth(platform: str, account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if platform == "google":
        account.google_credentials = None
        account.google_is_live = False
    elif platform == "meta":
        account.meta_credentials = None
        account.meta_is_live = False
    else:
        raise HTTPException(status_code=400, detail="Platform must be 'google' or 'meta'")
    account.is_live = account.google_is_live or account.meta_is_live
    db.commit()
    return {"status": "disconnected", "platform": platform, "account_id": account_id}
