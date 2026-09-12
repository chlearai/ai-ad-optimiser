"""
Safe additive migration: adguard_accounts.crm_preference

Lets each subscriber choose their own CRM delivery target
(leadsquared | zoho | salesforce | hubspot | webhook | none).
Idempotent. Works on PostgreSQL and SQLite.
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard crm_preference migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    if "crm_preference" in cols:
        logger.info("adguard_accounts.crm_preference already present")
        return
    try:
        with engine.begin() as conn:
            sql = "ALTER TABLE adguard_accounts ADD COLUMN crm_preference VARCHAR(30) NULL"
            conn.execute(text(sql))
        logger.info("Added column adguard_accounts.crm_preference")
    except Exception as e:
        logger.warning(f"Add crm_preference failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()