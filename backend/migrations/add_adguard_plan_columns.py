"""
Safe additive migration for AdGuard SaaS subscriber plans.

Adds to adguard_accounts:
  - plan: trial | starter | pro | agency (default trial)
  - plan_expires_at: trial end / renewal anchor
  - lead_quota: max leads stored (-1 = unlimited for agency)
  - is_archived: soft-archive of a workspace

Run manually or via database.init_db (registered below).
    python -m backend.migrations.add_adguard_plan_columns
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

_COLUMNS = {
    "plan": "VARCHAR(20) DEFAULT 'trial'",
    "plan_expires_at": "TIMESTAMP NULL",
    "lead_quota": "INTEGER DEFAULT 100",
    "is_archived": "BOOLEAN DEFAULT FALSE",
}


def _existing_columns(table):
    inspector = inspect(engine)
    try:
        return {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return set()


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard plan migration on {active}")

    try:
        existing = _existing_columns("adguard_accounts")
    except Exception as e:
        logger.warning(f"Migration skipped (inspect adguard_accounts failed): {e}")
        return

    for col, ddl in _COLUMNS.items():
        if col in existing:
            continue
        try:
            with engine.begin() as conn:
                if active.startswith("postgresql"):
                    conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN IF NOT EXISTS {col} {ddl_pg(ddl)}"))
                else:
                    conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN {col} {ddl_sqlite(ddl)}"))
            logger.info(f"Added column adguard_accounts.{col}")
        except Exception as e:
            logger.warning(f"Add column {col} failed: {e}")

    logger.info("AdGuard plan migration complete")


def ddl_pg(ddl: str) -> str:
    return ddl.replace("TIMESTAMP NULL", "TIMESTAMP")


def ddl_sqlite(ddl: str) -> str:
    return ddl


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()