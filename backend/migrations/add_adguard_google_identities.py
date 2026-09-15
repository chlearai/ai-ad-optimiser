"""
Safe additive migration: adguard_accounts.google_identities (TEXT NULL).

Stores multiple Google OAuth identities per workspace:
  [{"email": "...", "credentials": "<fernet-encrypted creds JSON>",
    "discovered": [accounts], "connected_at": "..."}]
Legacy single google_credentials remains for backward compatibility.
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard google_identities migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    if "google_identities" in cols:
        logger.info("adguard_accounts.google_identities already present")
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN google_identities TEXT NULL"))
        logger.info("Added column adguard_accounts.google_identities")
    except Exception as e:
        logger.warning(f"Add google_identities failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()