"""
AdGuard Inbuilt AI Bot-Caller Loop (Module 2).

Orchestrates voice verification for Green submitted leads:
  1. Enqueues lead for dialing within 5 minutes.
  2. Confirms name, enquiry submission, and intent.
  3. Processes outcomes:
     - verified: upgrades lead stage, triggers Conversion Firewall to fire Meta/Google conversion.
     - not_interested / wrong_person / unreachable: marks lead rejected, prevents conversion, feeds exclusion list.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.db.models import (
    AdGuardAccount,
    AdGuardExclusionMember,
    AdGuardLead,
)
from backend.services.adguard_firewall import queue_conversion_event
from backend.services.adguard_shared_network import hash_entity

logger = logging.getLogger("AdOptima")


def enqueue_bot_call(db: Session, lead: AdGuardLead) -> bool:
    """Queue a newly submitted Green lead for AI bot-caller verification."""
    if lead.verdict != "green" or not lead.phone:
        return False

    lead.bot_call_status = "queued"
    lead.bot_call_attempts = 0
    try:
        db.commit()
        logger.info(f"[BotCaller] Lead {lead.id} ({lead.phone}) queued for AI verification")
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"[BotCaller] enqueue failed: {e}")
        return False


def process_bot_caller_result(
    db: Session,
    lead_id: int,
    outcome: str,  # verified | not_interested | wrong_person | unreachable
    summary: Optional[str] = None,
    intent_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process callback outcome from AI Bot-Caller."""
    lead = db.query(AdGuardLead).filter(AdGuardLead.id == lead_id).first()
    if not lead:
        return {"status": "error", "message": "lead_not_found"}

    outcome_clean = (outcome or "").strip().lower()
    lead.bot_call_status = outcome_clean
    lead.bot_call_attempts = (lead.bot_call_attempts or 0) + 1
    lead.bot_call_summary = summary or f"Outcome: {outcome_clean}"

    ws = db.query(AdGuardAccount).filter(AdGuardAccount.id == lead.adguard_account_id).first() if lead.adguard_account_id else None

    if outcome_clean == "verified":
        lead.stage = "verified"
        lead.verified_at = datetime.utcnow()
        # Fire conversion signal via Conversion Firewall!
        queue_conversion_event(db, lead, "verified")
        logger.info(f"[BotCaller] Lead {lead.id} confirmed VERIFIED -> Conversion event queued")
    else:
        # Not interested / wrong person / unreachable -> soft exclusion
        lead.stage = "rejected"
        if ws and lead.phone:
            phone_h = hash_entity("phone", lead.phone)
            if phone_h:
                db.add(AdGuardExclusionMember(
                    adguard_account_id=ws.id,
                    list_type="meta_custom_audience",
                    entity_hash=phone_h,
                    entity_type="phone",
                    source=f"botcaller_{outcome_clean}",
                ))
                db.add(AdGuardExclusionMember(
                    adguard_account_id=ws.id,
                    list_type="google_customer_match",
                    entity_hash=phone_h,
                    entity_type="phone",
                    source=f"botcaller_{outcome_clean}",
                ))

    try:
        db.commit()
        db.refresh(lead)
        return {
            "status": "ok",
            "lead_id": lead.id,
            "stage": lead.stage,
            "bot_call_status": lead.bot_call_status,
        }
    except Exception as e:
        db.rollback()
        logger.warning(f"[BotCaller] process outcome error: {e}")
        return {"status": "error", "message": str(e)}
