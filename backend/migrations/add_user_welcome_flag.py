"""
Additive migration: users.welcome_email_sent (BOOL, default FALSE).
Dedupes the AdGuard "Welcome Aboard" email so it fires exactly once,
on first login AFTER activation (not on every login, not for admins).
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running users.welcome_email_sent migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("users")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    try:
        with engine.begin() as conn:
            if "welcome_email_sent" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN welcome_email_sent BOOLEAN DEFAULT FALSE"))
                logger.info("Added column users.welcome_email_sent")
    except Exception as e:
        logger.warning(f"Add welcome_email_sent failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()