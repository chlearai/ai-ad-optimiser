"""
Idempotent migration: add account_id column to rev_clients if missing.
Works for both SQLite and PostgreSQL.
"""
import logging
from sqlalchemy import inspect, text

logger = logging.getLogger("AdOptima")


def run_migration():
    from backend.db.database import engine

    try:
        # Dialect-safe column check. Never use pragma_table_info here — it is
        # SQLite-only syntax and throws on PostgreSQL.
        inspector = inspect(engine)
        try:
            columns = {c["name"] for c in inspector.get_columns("rev_clients")}
        except Exception:
            columns = set()

        if not columns:
            logger.warning("rev_clients table not found; skipping migration")
            return

        if "account_id" in columns:
            logger.info("rev_clients.account_id column already exists; skipping migration")
            return

        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE rev_clients ADD COLUMN account_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rev_clients_account_id ON rev_clients (account_id)"))
        logger.info("Migration applied: added account_id column to rev_clients")
    except Exception as e:
        logger.error(f"Migration add_rev_clients_account_id failed: {e}")
        raise
