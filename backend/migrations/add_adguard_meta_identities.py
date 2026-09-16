"""
Safe additive migration: adguard_accounts.meta_identities (TEXT NULL).

Stores multiple Meta (Facebook login) identities per workspace:
  [{"label": "<profile name or email>", "credentials": "<fernet>",
    "discovered_accounts": [...], "discovered_pages": [...], "connected_at": "..."}]
Legacy single meta_credentials stays for backward compatibility.
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard meta_identities migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    if "meta_identities" in cols:
        logger.info("adguard_accounts.meta_identities already present")
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN meta_identities TEXT NULL"))
        logger.info("Added column adguard_accounts.meta_identities")
    except Exception as e:
        logger.warning(f"Add meta_identities failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()