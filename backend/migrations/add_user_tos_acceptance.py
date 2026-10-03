"""
Additive migration: users.tos_accepted_version (VARCHAR 20) + tos_accepted_at (DATETIME).
Records the ToS version each user accepted (signup checkbox / login grandfathering).
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running users ToS acceptance migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("users")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    try:
        with engine.begin() as conn:
            if "tos_accepted_version" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN tos_accepted_version VARCHAR(20) NULL"))
                logger.info("Added column users.tos_accepted_version")
            if "tos_accepted_at" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN tos_accepted_at DATETIME NULL"))
                logger.info("Added column users.tos_accepted_at")
    except Exception as e:
        logger.warning(f"Add ToS columns failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()