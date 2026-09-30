"""
Additive migration: adguard_accounts signup_source (VARCHAR 20) + is_beta (BOOL).
Separates self-serve beta signups from admin-invited paying subscribers.
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard signup_source/is_beta migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    try:
        with engine.begin() as conn:
            if "signup_source" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN signup_source VARCHAR(20) DEFAULT 'admin_invite'"))
                logger.info("Added column adguard_accounts.signup_source")
            if "is_beta" not in cols:
                conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN is_beta BOOLEAN DEFAULT FALSE"))
                logger.info("Added column adguard_accounts.is_beta")
    except Exception as e:
        logger.warning(f"Add signup_source/is_beta failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()