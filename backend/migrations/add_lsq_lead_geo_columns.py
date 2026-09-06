"""
Idempotent migration: add city/state columns to leadsquared_leads if missing.
Works for both SQLite and PostgreSQL.
"""
import logging
from sqlalchemy import text

logger = logging.getLogger("AdOptima")


def run_migration():
    from backend.db.database import engine

    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM pragma_table_info('leadsquared_leads')")
            )
            columns = {row[0] for row in result.fetchall()}

            if "city" in columns and "state" in columns:
                logger.info("leadsquared_leads city/state columns already exist; skipping migration")
                return

            if "city" not in columns:
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN city VARCHAR DEFAULT ''"))
                logger.info("Migration applied: added leadsquared_leads.city")
            if "state" not in columns:
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN state VARCHAR DEFAULT ''"))
                logger.info("Migration applied: added leadsquared_leads.state")
            conn.commit()
    except Exception as e:
        logger.error(f"Migration add_lsq_lead_geo_columns failed: {e}")
        raise