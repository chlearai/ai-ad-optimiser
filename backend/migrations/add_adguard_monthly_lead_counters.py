"""
Additive migration: adguard_accounts leads_this_month (INT, default 0) + leads_month_reset (DATETIME).
Enforces monthly plan quotas (hard stop at plan limit, reset on billing-day anniversary).
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard monthly lead counters migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    try:
        with engine.begin() as conn:
            if "leads_this_month" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN leads_this_month INT DEFAULT 0 NOT NULL"))
                logger.info("Added column adguard_accounts.leads_this_month")
            if "leads_month_reset" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN leads_month_reset DATETIME NULL"))
                logger.info("Added column adguard_accounts.leads_month_reset")
    except Exception as e:
        logger.warning(f"Add monthly lead counters failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()