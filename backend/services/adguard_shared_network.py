"""
AdGuard Shared Fraud Network service.

Stores privacy-safe SHA-256 peppered hashes of repeat offender fingerprints,
phones, emails, and IP /24 subnets across client accounts.
Computes multi-tenant trust scores with 30-day half-life decay.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.db.models import AdGuardNetworkEntity

logger = logging.getLogger("AdOptima")

PEPPER = os.getenv("ADGUARD_NETWORK_PEPPER", "adguard_shared_threat_network_secret_pepper_2026")


def hash_entity(entity_type: str, raw_val: Optional[str]) -> Optional[str]:
    """Compute peppered SHA-256 hash of a normalized identifier."""
    if not raw_val:
        return None
    val = str(raw_val).strip().lower()
    if entity_type == "phone":
        digits = re.sub(r"\D", "", val)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        val = digits
    elif entity_type == "email":
        val = val.strip().lower()
    elif entity_type == "ip_subnet":
        # Ignore localhost / private IP ranges from shared network threat indexing
        if val in ("127.0.0.1", "localhost", "testclient", "::1", "0.0.0.0") or val.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
            return None
        # /24 subnet for IPv4
        parts = val.split(".")
        if len(parts) == 4:
            val = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        else:
            return None
    elif entity_type == "fingerprint":
        val = val.strip()


    if not val:
        return None

    to_hash = f"{PEPPER}:{entity_type}:{val}".encode("utf-8")
    return hashlib.sha256(to_hash).hexdigest()


def lookup_threat(db: Session, hashes: Dict[str, Optional[str]]) -> Tuple[int, List[str]]:
    """Look up threat score in the shared network.

    hashes: {'fingerprint': '...', 'phone': '...', 'email': '...', 'ip_subnet': '...'}
    Returns (risk_points, matched_reasons)
    """
    valid_hashes = [h for h in hashes.values() if h]
    if not valid_hashes:
        return 0, []

    matched = (
        db.query(AdGuardNetworkEntity)
        .filter(AdGuardNetworkEntity.entity_hash.in_(valid_hashes))
        .all()
    )

    if not matched:
        return 0, []

    max_score = 0
    reasons = []
    for ent in matched:
        # If seen in 3+ accounts, it's a confirmed threat
        if ent.accounts_flagged >= 3:
            pts = min(60, 20 + ent.accounts_flagged * 10)
            max_score = max(max_score, pts)
            reasons.append(f"shared_network_multi_account_{ent.entity_type}")
        elif ent.accounts_flagged >= 1:
            pts = min(35, 15 + ent.offence_count * 5)
            max_score = max(max_score, pts)
            reasons.append(f"shared_network_repeat_{ent.entity_type}")

    return max_score, reasons


def record_offence(
    db: Session,
    entity_type: str,
    raw_val: Optional[str],
    adguard_account_id: Optional[int] = None,
) -> Optional[AdGuardNetworkEntity]:
    """Record an offence in the shared network."""
    h = hash_entity(entity_type, raw_val)
    if not h:
        return None

    ent = db.query(AdGuardNetworkEntity).filter(AdGuardNetworkEntity.entity_hash == h).first()
    now = datetime.utcnow()
    if ent:
        ent.offence_count += 1
        ent.last_offence_at = now
        ent.trust_score = min(100, ent.trust_score + 15)
        ent.accounts_flagged = min(10, ent.accounts_flagged + 1)
    else:
        ent = AdGuardNetworkEntity(
            entity_hash=h,
            entity_type=entity_type,
            trust_score=50,
            accounts_flagged=1,
            offence_count=1,
            last_offence_at=now,
        )
        db.add(ent)

    try:
        db.commit()
        db.refresh(ent)
        return ent
    except Exception as e:
        db.rollback()
        logger.warning(f"[SharedNetwork] record_offence failed: {e}")
        return None


def run_network_score_decay(db: Session) -> int:
    """Decay entity trust scores by half every 30 days of inactivity."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    entities = (
        db.query(AdGuardNetworkEntity)
        .filter(AdGuardNetworkEntity.last_offence_at < cutoff, AdGuardNetworkEntity.trust_score > 10)
        .all()
    )
    count = 0
    for ent in entities:
        ent.trust_score = max(5, int(ent.trust_score * 0.5))
        count += 1
    if count:
        db.commit()
        logger.info(f"[SharedNetwork] decayed threat scores for {count} inactive entities")
    return count
