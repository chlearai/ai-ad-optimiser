"""
Idempotent migration: add crm_leads column to mis_daily_snapshots.

Stores per-day lead counts from the uploaded Salesforce export (CRM = source
of truth) so the Daily reports can show CRM lead counts alongside/beside
platform-reported conversions (which can under-report when tracking breaks).
"""
import logging
from sqlalchemy import inspect, text

logger = logging.getLogger("AdOptima")


def run_migration():
    from backend.db.database import engine, get_active_db

    try:
        inspector = inspect(engine)
        try:
            columns = {c["name"] for c in inspector.get_columns("mis_daily_snapshots")}
        except Exception:
            columns = set()

        if not columns:
            logger.warning("mis_daily_snapshots table not found; skipping migration")
            return

        if "crm_leads" in columns:
            logger.info("mis_daily_snapshots.crm_leads already present")
            return

        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE mis_daily_snapshots ADD COLUMN crm_leads FLOAT DEFAULT 0.0"))
        logger.info("Added crm_leads column to mis_daily_snapshots")
    except Exception as e:
        logger.error(f"crm_leads migration failed: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()