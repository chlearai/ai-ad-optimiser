"""
Idempotent migration: add account_id column to rev_clients if missing.
Works for both SQLite and PostgreSQL.
"""
import logging
from sqlalchemy import text

logger = logging.getLogger("AdOptima")


def run_migration():
    from backend.db.database import engine

    try:
        with engine.connect() as conn:
            # Check whether the column already exists (SQLite compatible introspection)
            result = conn.execute(
                text("SELECT name FROM pragma_table_info('rev_clients')")
            )
            columns = {row[0] for row in result.fetchall()}

            if "account_id" in columns:
                logger.info("rev_clients.account_id column already exists; skipping migration")
                return

            conn.execute(text("ALTER TABLE rev_clients ADD COLUMN account_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rev_clients_account_id ON rev_clients (account_id)"))
            conn.commit()
            logger.info("Migration applied: added account_id column to rev_clients")
    except Exception as e:
        logger.error(f"Migration add_rev_clients_account_id failed: {e}")
        raise
