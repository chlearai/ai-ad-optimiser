"""
Billing and Razorpay Payment Gateway Service for AdGuard.
Supports both live Razorpay API and zero-friction Test/Sandbox Mode with Cards, UPI QR code, and Netbanking simulation.
"""
import os
import json
import hmac
import hashlib
import time
import logging
from typing import Dict, Any, Optional
import requests

from backend.db.models import AdGuardAccount, User
from backend.services.activity_log import log_activity

logger = logging.getLogger("AdOptima")

# Plan pricing catalog (amounts in INR and paise)
PLAN_CATALOG = {
    "starter": {
        "name": "Starter Plan",
        "price_inr": 4999,
        "amount_paise": 499900,
        "lead_quota": 1000,
        "description": "1,000 leads / month with Disposable Email & Phone Validation",
    },
    "pro": {
        "name": "Pro Plan",
        "price_inr": 14999,
        "amount_paise": 1499900,
        "lead_quota": 5000,
        "description": "5,000 leads / month with Gemini AI Deep Intent & FraudGraph",
    },
    "agency": {
        "name": "Agency Plan",
        "price_inr": 39999,
        "amount_paise": 3999900,
        "lead_quota": -1,  # Unlimited
        "description": "Unlimited leads volume & multi-workspace agency management",
    },
}

def _get_razorpay_credentials() -> Dict[str, str]:
    """Retrieve Razorpay keys from environment or config.json."""
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

    if not key_id:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    key_id = cfg.get("razorpay_key_id", "").strip()
                    key_secret = cfg.get("razorpay_key_secret", "").strip()
            except Exception:
                pass

    # Default to standard test key if none specified, enabling full Sandbox test UI
    if not key_id:
        key_id = "rzp_test_51SandboxAdoptima"
        key_secret = "sandbox_secret_placeholder"

    is_sandbox = key_id.startswith("rzp_test_") or "sandbox" in key_id.lower()
    return {
        "key_id": key_id,
        "key_secret": key_secret,
        "is_sandbox": is_sandbox,
        "mode": "test" if is_sandbox else "live",
    }


def get_billing_config() -> Dict[str, Any]:
    """Public billing configuration for frontend checkout initialization."""
    creds = _get_razorpay_credentials()
    return {
        "key_id": creds["key_id"],
        "mode": creds["mode"],
        "is_sandbox": creds["is_sandbox"],
        "currency": "INR",
        "plans": PLAN_CATALOG,
    }


def create_razorpay_order(plan_code: str, workspace_id: int, user: User, db_session) -> Dict[str, Any]:
    """Create a Razorpay order in INR (or generate a valid sandbox order)."""
    plan_code = (plan_code or "starter").lower().strip()
    if plan_code not in PLAN_CATALOG:
        raise ValueError(f"Invalid plan '{plan_code}'. Must be one of: {list(PLAN_CATALOG.keys())}")

    plan_info = PLAN_CATALOG[plan_code]
    creds = _get_razorpay_credentials()
    key_id = creds["key_id"]
    key_secret = creds["key_secret"]
    is_sandbox = creds["is_sandbox"]

    receipt = f"rcpt_ws{workspace_id}_{int(time.time())}"
    amount_paise = plan_info["amount_paise"]

    # If live/configured Razorpay credentials exist, invoke real API
    if key_secret and key_secret != "sandbox_secret_placeholder":
        try:
            resp = requests.post(
                "https://api.razorpay.com/v1/orders",
                auth=(key_id, key_secret),
                json={
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {
                        "plan": plan_code,
                        "workspace_id": str(workspace_id),
                        "user_email": user.email,
                    }
                },
                timeout=10,
            )
            if resp.ok:
                order_data = resp.json()
                order_data["plan"] = plan_code
                order_data["plan_name"] = plan_info["name"]
                order_data["key_id"] = key_id
                order_data["mode"] = creds["mode"]
                return order_data
            else:
                logger.warning(f"Razorpay API order error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Failed contacting Razorpay API: {e}")

    # Fallback to authentic sandbox order descriptor
    sandbox_order_id = f"order_sbx_{plan_code}_{int(time.time())}"
    return {
        "id": sandbox_order_id,
        "entity": "order",
        "amount": amount_paise,
        "amount_paid": 0,
        "amount_due": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "status": "created",
        "plan": plan_code,
        "plan_name": plan_info["name"],
        "key_id": key_id,
        "mode": "test",
        "notes": {
            "plan": plan_code,
            "workspace_id": str(workspace_id),
            "user_email": user.email,
            "sandbox_simulated": True,
        }
    }


def verify_payment_and_upgrade(
    workspace_id: int,
    plan_code: str,
    payment_id: str,
    order_id: str,
    signature: Optional[str],
    user: User,
    db
) -> Dict[str, Any]:
    """Verify payment signature and upgrade workspace quota."""
    plan_code = (plan_code or "starter").lower().strip()
    if plan_code not in PLAN_CATALOG:
        raise ValueError(f"Invalid plan '{plan_code}'")

    plan_info = PLAN_CATALOG[plan_code]
    creds = _get_razorpay_credentials()
    key_secret = creds["key_secret"]

    # Signature verification for live/actual keys
    if signature and key_secret and key_secret != "sandbox_secret_placeholder" and order_id and not order_id.startswith("order_sbx_"):
        body = f"{order_id}|{payment_id}".encode("utf-8")
        expected_sig = hmac.new(key_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            raise ValueError("Payment signature verification failed. Please contact support.")

    # Find and update workspace
    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == workspace_id).first()
    if not ws:
        raise ValueError(f"Workspace with ID {workspace_id} not found")

    ws.plan = plan_code
    ws.lead_quota = plan_info["lead_quota"]
    db.commit()
    db.refresh(ws)

    log_activity(
        module="AdGuard Billing",
        action="Plan Upgrade",
        description=f"Workspace '{ws.display_name or ws.id}' upgraded to {plan_info['name']} (Quota: {plan_info['lead_quota']}) via {creds['mode'].upper()} payment {payment_id}",
        user_id=user.id,
        user_name=user.full_name or user.email,
        db=db,
    )

    return {
        "ok": True,
        "message": f"Successfully activated {plan_info['name']}!",
        "workspace_id": ws.id,
        "plan": ws.plan,
        "lead_quota": ws.lead_quota,
        "lead_count": ws.lead_count,
        "payment_id": payment_id,
        "mode": creds["mode"],
    }


# =====================================================================
# Account Card Billing Display (for Connected Ad Platform Accounts)
# =====================================================================

def format_billing_amount(amount: Optional[float]) -> str:
    """Format amount as compact Indian notation: ₹850, ₹12.5K, ₹1.4L, ₹10L."""
    if amount is None:
        return "---"
    amt = float(amount)
    if amt < 0:
        amt = 0.0
    if amt < 1000:
        return f"₹{int(round(amt))}"
    if amt < 100000:
        # Show as K with one decimal (e.g. 12.5K)
        k = amt / 1000.0
        return f"₹{k:.1f}K"
    # 1,00,000+ → L format
    l = amt / 100000.0
    return f"₹{l:.1f}L"


def build_billing_display(billing_cache: Optional[str], fallback_spend: float = 0.0) -> Dict[str, Any]:
    """Build the billing display object from cached billing data."""
    billing_data = None
    if billing_cache:
        try:
            billing_data = json.loads(billing_cache)
        except Exception:
            billing_data = None

    if not billing_data or billing_data.get("status") != "available":
        if billing_data and billing_data.get("billing_type") == "postpaid":
            return {
                "billing_type": "postpaid",
                "billing_display": "SPEND ---",
                "billing_colour": "grey",
                "billing_amount": None,
            }
        if billing_data and billing_data.get("billing_type") == "prepaid":
            return {
                "billing_type": "prepaid",
                "billing_display": "BAL ---",
                "billing_colour": "grey",
                "billing_amount": None,
            }
        return {
            "billing_type": "unknown",
            "billing_display": "BILL ---",
            "billing_colour": "grey",
            "billing_amount": None,
        }

    billing_type = billing_data.get("billing_type", "unknown")
    amount = billing_data.get("amount")

    if billing_type == "prepaid":
        total_budget = billing_data.get("total_budget")
        amount = billing_data.get("amount")
        balance_pct = billing_data.get("balance_pct")
        health = billing_data.get("health")
        lifetime_spend = billing_data.get("lifetime_spend")

        if balance_pct is None and amount is not None and total_budget and total_budget > 0:
            balance_pct = round((amount / total_budget * 100), 1)
        if health is None and balance_pct is not None:
            if balance_pct > 30:
                health = "good"
            elif balance_pct > 10:
                health = "warning"
            else:
                health = "critical"
        if health is None:
            health = "good"

        if health == "good":
            colour = "good"
        elif health == "warning":
            colour = "warning"
        elif health == "critical":
            colour = "critical"
        else:
            colour = "neutral"

        if amount is not None:
            display = f"BAL {format_billing_amount(amount)}"
        else:
            display = "BAL ---"
            colour = "grey"

        return {
            "billing_type": "prepaid",
            "billing_display": display,
            "billing_colour": colour,
            "balance_pct": balance_pct,
            "billing_amount": amount,
            "lifetime_spend": lifetime_spend,
        }

    elif billing_type == "postpaid":
        monthly_spend = billing_data.get("monthly_spend", amount)
        lifetime_spend = billing_data.get("lifetime_spend")
        month_label = billing_data.get("month_label", "")
        if lifetime_spend is not None:
            display = f"SPEND {format_billing_amount(lifetime_spend)}"
        elif monthly_spend is not None:
            suffix = f" this month" if not month_label else f" ({month_label})"
            display = f"USED {format_billing_amount(monthly_spend)}{suffix}"
        else:
            display = "USED ---"
        return {
            "billing_type": "postpaid",
            "billing_display": display,
            "billing_colour": "neutral",
            "billing_amount": monthly_spend,
        }
    else:
        return {
            "billing_type": "unknown",
            "billing_display": "BILL ---",
            "billing_colour": "grey",
            "billing_amount": None,
        }


def get_billing_for_account(account) -> Dict[str, Any]:
    """Get billing display for an account from its cached billing data."""
    billing_cache = getattr(account, "billing_cache", None)
    spend = float(getattr(account, "spend", 0) or 0)
    return build_billing_display(billing_cache, fallback_spend=spend)