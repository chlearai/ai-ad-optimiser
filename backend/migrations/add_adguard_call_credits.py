"""
Additive migration: adguard_accounts call credit columns.
Call verification credits = prepaid bucket (NEVER expire, carry across renewals):
  - call_credits_remaining: current balance
  - call_credits_granted_total: lifetime credits granted (plan grants + top-ups)
  - call_credits_used: lifetime calls consumed
Lead audit quota stays monthly (leads_this_month + leads_month_reset).
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard call credits migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    try:
        with engine.begin() as conn:
            if "call_credits_remaining" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN call_credits_remaining INT DEFAULT 0 NOT NULL"))
                logger.info("Added column adguard_accounts.call_credits_remaining")
            if "call_credits_granted_total" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN call_credits_granted_total INT DEFAULT 0 NOT NULL"))
                logger.info("Added column adguard_accounts.call_credits_granted_total")
            if "call_credits_used" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN call_credits_used INT DEFAULT 0 NOT NULL"))
                logger.info("Added column adguard_accounts.call_credits_used")
    except Exception as e:
        logger.warning(f"Add call credits columns failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()