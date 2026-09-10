"""
Safe additive migration: Meta OAuth columns on adguard_accounts.
Idempotent. Works on PostgreSQL and SQLite.
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

COLUMNS = [
    ("meta_credentials", "TEXT"),
    ("meta_is_live", "BOOLEAN DEFAULT FALSE"),
    ("discovered_meta_accounts", "TEXT"),
    ("discovered_meta_pages", "TEXT"),
]


def _existing_columns(table):
    inspector = inspect(engine)
    try:
        return {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return set()


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard_accounts Meta columns migration on {active}")

    try:
        existing = _existing_columns("adguard_accounts")
    except Exception as e:
        logger.warning(f"Migration skipped (inspect adguard_accounts failed): {e}")
        return

    with engine.begin() as conn:
        for name, ddl in COLUMNS:
            if name in existing:
                continue
            if active.startswith("postgresql"):
                sql = f"ALTER TABLE adguard_accounts ADD COLUMN IF NOT EXISTS {name} {ddl}"
            else:
                sql = f"ALTER TABLE adguard_accounts ADD COLUMN {name} {ddl}"
            logger.info(f"Adding column adguard_accounts.{name}")
            conn.execute(text(sql))

    logger.info("AdGuard Meta columns migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()