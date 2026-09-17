"""
Billing and Razorpay Payment Routes for AdGuard Subscriptions.
Provides order creation, signature verification, and instant sandbox simulation.
"""
import logging
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import User, AdGuardAccount
from backend.routes.auth import get_current_user_required
from backend.services.billing import (
    get_billing_config,
    create_razorpay_order,
    verify_payment_and_upgrade,
    PLAN_CATALOG,
)

logger = logging.getLogger("AdOptima")

router = APIRouter(prefix="/api/billing", tags=["billing"])


class CreateOrderRequest(BaseModel):
    plan: str
    workspace_id: Optional[int] = None


class VerifyPaymentRequest(BaseModel):
    workspace_id: int
    plan: str
    razorpay_payment_id: str
    razorpay_order_id: Optional[str] = None
    razorpay_signature: Optional[str] = None


@router.get("/config")
def billing_config():
    """Returns Razorpay public key and supported plans."""
    return get_billing_config()


@router.post("/create-order")
def create_order(
    req: CreateOrderRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required)
):
    """Generate a Razorpay payment order for the chosen subscription plan."""
    # If workspace_id not specified, find user's workspace
    ws_id = req.workspace_id
    if not ws_id:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == user.email).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found for this user")
        ws_id = ws.id
    else:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == ws_id).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="Not authorized to upgrade this workspace")

    try:
        order = create_razorpay_order(req.plan, ws_id, user, db)
        return {
            "ok": True,
            "order": order,
            "workspace_id": ws_id,
        }
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify-payment")
def verify_payment(
    req: VerifyPaymentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required)
):
    """Verify payment and upgrade workspace subscription tier."""
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == req.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_email != user.email and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Not authorized for this workspace")

    try:
        result = verify_payment_and_upgrade(
            workspace_id=req.workspace_id,
            plan_code=req.plan,
            payment_id=req.razorpay_payment_id,
            order_id=req.razorpay_order_id,
            signature=req.razorpay_signature,
            user=user,
            db=db
        )
        return result
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sandbox-simulate-success")
def sandbox_simulate(
    req: CreateOrderRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_required)
):
    """Convenience endpoint to simulate successful payment in test/sandbox mode."""
    cfg = get_billing_config()
    if not cfg["is_sandbox"] and user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Instant simulation only available in sandbox mode")

    ws_id = req.workspace_id
    if not ws_id:
        ws = db.query(AdGuardAccount).filter(AdGuardAccount.owner_email == user.email).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        ws_id = ws.id

    mock_pay_id = f"pay_sbx_{int(time.time())}"
    mock_order_id = f"order_sbx_{req.plan}_{int(time.time())}"

    result = verify_payment_and_upgrade(
        workspace_id=ws_id,
        plan_code=req.plan,
        payment_id=mock_pay_id,
        order_id=mock_order_id,
        signature=None,
        user=user,
        db=db
    )
    return result
