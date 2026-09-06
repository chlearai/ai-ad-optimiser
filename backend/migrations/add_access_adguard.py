"""
Safe additive migration to add users.access_adguard column.
Idempotent. Works on PostgreSQL and SQLite.

Run manually after deployment:
    python -m backend.migrations.add_access_adguard
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
    logger.info(f"Running access_adguard migration on {active}")

    try:
        existing_cols = _existing_columns("users")
    except Exception as e:
        logger.warning(f"Migration skipped (inspect users failed): {e}")
        return

    if "access_adguard" not in existing_cols:
        with engine.begin() as conn:
            if active.startswith("postgresql"):
                sql = "ALTER TABLE users ADD COLUMN IF NOT EXISTS access_adguard BOOLEAN DEFAULT FALSE"
            else:
                sql = "ALTER TABLE users ADD COLUMN access_adguard BOOLEAN DEFAULT FALSE"
            logger.info("Adding column users.access_adguard")
            conn.execute(text(sql))
    else:
        logger.info("users.access_adguard already present")

    logger.info("Access adguard migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()