"""
Safe additive migration: adguard_accounts.cached_campaigns (TEXT NULL).

Stores cached campaigns & pages map per account for fast dashboard loading:
  {
     "<account_id>": [
        {"id": "...", "name": "...", "type": "campaign"|"page", "platform": "google"|"meta", "status": "ENABLED"}
     ]
  }
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard cached_campaigns migration on {active}")
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return
    if "cached_campaigns" in cols:
        logger.info("adguard_accounts.cached_campaigns already present")
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE adguard_accounts ADD COLUMN cached_campaigns TEXT NULL"))
        logger.info("Added column adguard_accounts.cached_campaigns")
    except Exception as e:
        logger.warning(f"Add cached_campaigns failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
