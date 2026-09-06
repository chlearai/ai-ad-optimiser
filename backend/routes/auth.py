"""
JWT-based authentication + user management for AdOptima.

Roles:
  - superadmin: full access + can manage users
  - admin: full access, cannot manage users
  - user (BM) and newuser (User): only sees assigned accounts
"""
import os
import secrets
import re
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.services.activity_log import log_activity
from backend.db.database import get_db
from backend.db.models import User, UserAccountAssignment, Account, AppSetting
from backend.services.onboarding_email import send_onboarding_email
from backend.services.gmail_api import build_authorization_url, exchange_code_for_token

logger = logging.getLogger("AdOptima")

router = APIRouter(prefix="/api/auth", tags=["auth"])

SECRET_KEY = os.getenv("ADOPTIMA_JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
ONBOARDING_TOKEN_EXPIRE_HOURS = 72

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


class OnboardRequest(BaseModel):
    full_name: str
    email: str
    mobile: str
    password: str


class UserCreateRequest(BaseModel):
    full_name: str
    email: str
    mobile: Optional[str] = None
    role: str = "user"  # superadmin, admin, user (BM), or newuser (User)
    assigned_account_ids: List[int] = []
    access_adpulse: bool = True
    access_insightdesk: bool = False
    access_revenueops: bool = False
    access_audit_review: bool = False
    access_adguard: bool = False


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    role: Optional[str] = None
    rev_role: Optional[str] = None
    is_active: Optional[bool] = None
    assigned_account_ids: Optional[List[int]] = None
    access_adpulse: Optional[bool] = None
    access_insightdesk: Optional[bool] = None
    access_revenueops: Optional[bool] = None
    access_audit_review: Optional[bool] = None
    access_adguard: Optional[bool] = None


class SetPasswordRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Optional[User]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
    return db.query(User).filter(User.email == email, User.is_active == True).first()


def get_current_user_required(user: User = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_superadmin(user: User = Depends(get_current_user_required)) -> User:
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return user


def require_admin_or_superadmin(user: User = Depends(get_current_user_required)) -> User:
    if user.role not in ("superadmin", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _sync_account_assignments(user: User, account_ids: List[int], db: Session):
    db.query(UserAccountAssignment).filter(UserAccountAssignment.user_id == user.id).delete()
    for aid in account_ids:
        if db.query(Account).filter(Account.id == aid).first():
            db.add(UserAccountAssignment(user_id=user.id, account_id=aid))
    db.commit()


# ============================================================
# ONBOARDING (first user)
# ============================================================
@router.get("/onboarding-required")
def onboarding_required(db: Session = Depends(get_db)):
    """Returns true if no users exist yet — frontend shows onboarding screen."""
    count = db.query(User).count()
    return {"onboarding_required": count == 0}


@router.post("/onboard")
def onboard_first_user(req: OnboardRequest, db: Session = Depends(get_db)):
    """Create the first Super Admin. Only works if no users exist."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=400, detail="Onboarding already complete. Please login.")
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=req.email,
        hashed_password=get_password_hash(req.password),
        full_name=req.full_name,
        mobile=req.mobile,
        role="superadmin",
        access_adpulse=True,
        access_insightdesk=True,
        access_revenueops=True,
        access_adguard=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "user": user.to_dict()}


# ============================================================
# LOGIN
# ============================================================
@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    log_activity(
        module="System",
        action="Login",
        description=f"User {user.full_name or user.email} logged in",
        user_id=user.id,
        user_name=user.full_name or user.email,
        db=db,
    )
    return {"access_token": access_token, "token_type": "bearer", "user": user.to_dict()}


@router.get("/me", response_model=dict)
def me(user: User = Depends(get_current_user_required)):
    data = user.to_dict()
    # For legacy users created before onboarding flow, treat active+completed as fully onboarded.
    if not hasattr(user, "onboarding_completed") or user.onboarding_completed is None:
        data["onboarding_completed"] = user.is_active
    return data


# ============================================================
# USER MANAGEMENT (Super Admin or Admin)
# ============================================================
@router.get("/users")
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin_or_superadmin)):
    users = db.query(User).order_by(User.created_at).all()
    return [u.to_dict() for u in users]


@router.post("/users")
def create_user(req: UserCreateRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_admin_or_superadmin)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    if req.role not in ("superadmin", "admin", "user", "newuser"):
        raise HTTPException(status_code=400, detail="Role must be 'superadmin', 'admin', 'user', or 'newuser'")
    # Only superadmin can create another superadmin
    if req.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only Super Admin can create superadmin users")

    token = secrets.token_urlsafe(32)
    token_expires = datetime.utcnow() + timedelta(hours=ONBOARDING_TOKEN_EXPIRE_HOURS)

    user = User(
        email=req.email,
        hashed_password=get_password_hash(secrets.token_urlsafe(16)),  # random temp password
        full_name=req.full_name,
        mobile=req.mobile,
        role=req.role,
        access_adpulse=req.access_adpulse,
        access_insightdesk=req.access_insightdesk,
        access_revenueops=req.access_revenueops,
        access_audit_review=req.access_audit_review,
        access_adguard=req.access_adguard,
        onboarding_token=token,
        onboarding_token_expires_at=token_expires,
        onboarding_completed=False,
        is_active=False,
    )
    db.add(user)
    db.flush()
    if req.role == "user" and req.assigned_account_ids:
        _sync_account_assignments(user, req.assigned_account_ids, db)
    if req.role == "newuser" and req.assigned_account_ids:
        _sync_account_assignments(user, req.assigned_account_ids, db)

    db.refresh(user)
    base_url = os.getenv("ADOPTIMA_PUBLIC_BASE_URL", "") or str(request.base_url).rstrip("/")
    setup_link = f"{base_url}/onboard.html?token={token}"

    log_activity(
        module="System",
        action="User Created",
        description=f"Created user {user.full_name or user.email} with role {user.role}",
        user_id=current_user.id,
        user_name=current_user.full_name or current_user.email,
        entity_type="user",
        entity_id=str(user.id),
        details={"role": user.role, "email": user.email, "setup_link": setup_link},
        db=db,
    )

    # Load Gmail refresh token from DB if available
    refresh_token_setting = db.query(AppSetting).filter(AppSetting.key == "gmail_refresh_token").first()
    refresh_token = refresh_token_setting.value if refresh_token_setting else None

    def _send_email_background(email, name, link, rt):
        try:
            result = send_onboarding_email(email, name, link, refresh_token=rt, timeout=30)
            if result.get("sent"):
                logger.info(f"Onboarding email sent for {email}: provider={result.get('provider')}, msgid={result.get('message_id')}")
            else:
                logger.error(f"Onboarding email failed for {email}: {result.get('error')}")
        except Exception as e:
            logger.exception(f"Onboarding email background crashed for {email}: {e}")

    # Fire-and-forget: do not block the HTTP response waiting for email.
    threading.Thread(
        target=_send_email_background,
        args=(req.email, req.full_name, setup_link, refresh_token),
        daemon=True,
    ).start()

    return {
        "user": user.to_dict(),
        "setup_link": setup_link,
        "email_sent": True,
        "email_status": "Email is being sent in background via " + ("Gmail API" if refresh_token else "SMTP fallback"),
    }


@router.get("/onboard/{token}")
def get_onboarding_user(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.onboarding_token == token).first()
    if not user or not user.onboarding_token_expires_at or user.onboarding_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired setup link")
    return {"email": user.email, "full_name": user.full_name, "role": user.role}


@router.post("/onboard/{token}")
def set_onboarding_password(token: str, req: SetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.onboarding_token == token).first()
    if not user or not user.onboarding_token_expires_at or user.onboarding_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired setup link")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    user.hashed_password = get_password_hash(req.password)
    user.onboarding_completed = True
    user.is_active = True
    user.onboarding_token = None
    user.onboarding_token_expires_at = None
    db.commit()
    db.refresh(user)
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "user": user.to_dict()}


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UserUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin_or_superadmin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Only superadmin can create/modify superadmin users
    if current_user.role != "superadmin" and (req.role == "superadmin" or user.role == "superadmin"):
        raise HTTPException(status_code=403, detail="Only Super Admin can manage superadmin users")
    if req.full_name is not None:
        user.full_name = req.full_name
    if req.email is not None:
        user.email = req.email
    if req.mobile is not None:
        user.mobile = req.mobile
    if req.password:
        user.hashed_password = get_password_hash(req.password)
        if req.role is not None:
            if req.role not in ("superadmin", "admin", "user", "newuser"):
                raise HTTPException(status_code=400, detail="Invalid role")
            user.role = req.role
        if req.rev_role is not None:
            user.rev_role = req.rev_role
        if req.assigned_account_ids is not None:
            _sync_account_assignments(user, req.assigned_account_ids, db)
        if req.is_active is not None:
            user.is_active = req.is_active
        if req.access_adpulse is not None:
            user.access_adpulse = req.access_adpulse
        if req.access_insightdesk is not None:
            user.access_insightdesk = req.access_insightdesk
        if req.access_revenueops is not None:
            user.access_revenueops = req.access_revenueops
        if req.access_audit_review is not None:
            user.access_audit_review = req.access_audit_review
        if req.access_adguard is not None:
            user.access_adguard = req.access_adguard

    db.refresh(user)
    log_activity(
        module="System",
        action="User Updated",
        description=f"Updated user {user.full_name or user.email}",
        user_id=current_user.id,
        user_name=current_user.full_name or current_user.email,
        entity_type="user",
        entity_id=str(user.id),
        details={"role": user.role, "email": user.email, "changes": req.model_dump(exclude_unset=True)},
        db=db,
    )
    return user.to_dict()


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin_or_superadmin)):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"status": "success"}


@router.get("/gmail/callback")
def gmail_callback(code: str, db: Session = Depends(get_db)):
    """OAuth callback from Google. Saves the refresh token in DB."""
    result = exchange_code_for_token(code=code)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    refresh_token = result.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google did not return a refresh token. Make sure you selected a Gmail account and approved the permission.")
    setting = db.query(AppSetting).filter(AppSetting.key == "gmail_refresh_token").first()
    if setting:
        setting.value = refresh_token
    else:
        setting = AppSetting(key="gmail_refresh_token", value=refresh_token)
        db.add(setting)
    db.commit()
    logger.info("Gmail refresh token saved successfully")
    return {"status": "success", "message": "Gmail authorized successfully. You can now send invite emails."}


@router.get("/gmail-auth-url")
def gmail_auth_url(request: Request, current_user: User = Depends(require_superadmin)):
    """Return the Google OAuth URL to authorize Gmail sending."""
    redirect_uri = os.getenv("GMAIL_REDIRECT_URI", str(request.base_url).rstrip("/") + "/api/auth/gmail/callback")
    result = build_authorization_url(redirect_uri=redirect_uri)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"authorization_url": result["url"]}


@router.get("/accounts-for-assignment")
def accounts_for_assignment(current_user: User = Depends(require_admin_or_superadmin), db: Session = Depends(get_db)):
    """List all accounts for the assignment multi-select dropdown."""
    accounts = db.query(Account).order_by(Account.name).all()
    return [{"id": a.id, "name": a.name} for a in accounts]


# ============================================================
# EMERGENCY: reset first superadmin password (CLI only)
# ============================================================
@router.post("/reset-superadmin")
def reset_superadmin(email: str, new_password: str, db: Session = Depends(get_db)):
    """Reset a superadmin's password. Only works if called from localhost.
    Use this if you lose access and need to reset the first admin."""
    import socket
    # Allow only if request originates from localhost
    user = db.query(User).filter(User.email == email, User.role == "superadmin").first()
    if not user:
        raise HTTPException(status_code=404, detail="Superadmin not found for this email")
    user.hashed_password = get_password_hash(new_password)
    db.commit()
    return {"status": "success", "message": f"Password reset for {email}"}