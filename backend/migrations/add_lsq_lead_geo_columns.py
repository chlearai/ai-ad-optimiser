"""
Idempotent migration: add city/state columns to leadsquared_leads if missing.
Works for both SQLite and PostgreSQL.

Run manually after deployment:
    python -m backend.migrations.add_lsq_lead_geo_columns
"""
import logging
from sqlalchemy import inspect, text

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
    logger.info(f"Running lsq lead geo columns migration on {active}")

    try:
        existing_cols = _existing_columns("leadsquared_leads")
    except Exception as e:
        logger.warning(f"Migration skipped (inspect leadsquared_leads failed): {e}")
        return

    if not existing_cols:
        logger.warning("leadsquared_leads table not found; skipping migration")
        return

    with engine.begin() as conn:
        if "city" not in existing_cols:
            if active.startswith("postgresql"):
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN IF NOT EXISTS city VARCHAR DEFAULT ''"))
            else:
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN city VARCHAR DEFAULT ''"))
            logger.info("Adding column leadsquared_leads.city")
        if "state" not in existing_cols:
            if active.startswith("postgresql"):
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN IF NOT EXISTS state VARCHAR DEFAULT ''"))
            else:
                conn.execute(text("ALTER TABLE leadsquared_leads ADD COLUMN state VARCHAR DEFAULT ''"))
            logger.info("Adding column leadsquared_leads.state")

    logger.info("LSQ lead geo columns migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()