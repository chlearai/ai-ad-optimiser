"""
Safe additive migration to add adguard_leads.adguard_account_id column.
Idempotent. Works on PostgreSQL and SQLite.

Needed because Base.metadata.create_all does not alter existing tables.

Run manually after deployment:
    python -m backend.migrations.add_adguard_lead_workspace_column
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def _existing_columns(table):
    inspector = inspect(engine)
    try:
        return {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return set()


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard_leads workspace column migration on {active}")

    try:
        existing_cols = _existing_columns("adguard_leads")
    except Exception as e:
        logger.warning(f"Migration skipped (inspect adguard_leads failed): {e}")
        return

    if "adguard_account_id" not in existing_cols:
        with engine.begin() as conn:
            if active.startswith("postgresql"):
                sql = "ALTER TABLE adguard_leads ADD COLUMN IF NOT EXISTS adguard_account_id INTEGER"
            else:
                sql = "ALTER TABLE adguard_leads ADD COLUMN adguard_account_id INTEGER"
            logger.info("Adding column adguard_leads.adguard_account_id")
            conn.execute(text(sql))
    else:
        logger.info("adguard_leads.adguard_account_id already present")

    logger.info("AdGuard lead workspace column migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()