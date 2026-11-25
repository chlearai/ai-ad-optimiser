"""
Notification settings and dispatch API routes.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.db.database import get_db
from backend.db.models import NotificationSetting, NotificationLog
from backend.services.notification_dispatch import send_email, send_whatsapp

router = APIRouter(prefix="/api", tags=["notifications"])


class NotificationUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    config: Optional[dict] = None
    notify_on_audit_complete: Optional[bool] = None
    notify_on_action_reviewed: Optional[bool] = None
    notify_on_sync_failure: Optional[bool] = None


@router.get("/notification-settings")
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(NotificationSetting).all()
    return [s.to_dict() for s in settings]


@router.put("/notification-settings/{channel}")
def update_setting(channel: str, req: NotificationUpdate, db: Session = Depends(get_db)):
    setting = db.query(NotificationSetting).filter(NotificationSetting.channel == channel).first()
    if not setting:
        setting = NotificationSetting(channel=channel)
        db.add(setting)

    if req.enabled is not None:
        setting.enabled = req.enabled
    if req.provider is not None:
        setting.provider = req.provider
    if req.config is not None:
        setting.config = {**(setting.config or {}), **req.config}
    if req.notify_on_audit_complete is not None:
        setting.notify_on_audit_complete = req.notify_on_audit_complete
    if req.notify_on_action_reviewed is not None:
        setting.notify_on_action_reviewed = req.notify_on_action_reviewed
    if req.notify_on_sync_failure is not None:
        setting.notify_on_sync_failure = req.notify_on_sync_failure

    db.commit()
    db.refresh(setting)
    return setting.to_dict()


@router.post("/notification-settings/{channel}/test")
def test_notification(channel: str, db: Session = Depends(get_db)):
    setting = db.query(NotificationSetting).filter(NotificationSetting.channel == channel).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Notification channel not configured")
    if not setting.enabled:
        raise HTTPException(status_code=400, detail="Channel is disabled")

    if channel == "email":
        result = send_email(setting, "test", "AdOptima Test Email", "This is a test email from AdOptima.", db)
    elif channel == "whatsapp":
        result = send_whatsapp(setting, "test", "AdOptima Test", "This is a test WhatsApp message from AdOptima.", db)
    else:
        raise HTTPException(status_code=400, detail="Unsupported channel")

    if result.get("status") != "sent":
        raise HTTPException(status_code=400, detail="Dispatch failed")
    return result


@router.get("/notification-logs")
def list_logs(limit: int = 100, db: Session = Depends(get_db)):
    logs = db.query(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(limit).all()
    return [l.to_dict() for l in logs]
