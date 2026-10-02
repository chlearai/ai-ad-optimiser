"""
Idempotent migration: fix PostgreSQL SERIAL sequence desync on tables that
received manual seed inserts with explicit IDs (accounts, rev_clients,
account_groups, mis_daily_snapshots).

Without this, future ORM INSERTs collide with seeded rows
(duplicate keyvalue violates unique constraint) and abort transactions.
SQLite is skipped - it has no sequences.
"""
import logging
from sqlalchemy import inspect, text

logger = logging.getLogger("AdOptima")

# (table, pk column) pairs whose sequences may be out of sync
_SEQUENCE_TABLES = [
    ("accounts", "id"),
    ("rev_clients", "id"),
    ("account_groups", "id"),
    ("mis_daily_snapshots", "id"),
    ("mis_projects", "id"),
    ("activity_log", "id"),
]


def run_migration():
    from backend.db.database import engine, get_active_db

    try:
        if get_active_db() == "sqlite":
            logger.info("SQLite database; sequence sync not needed")
            return

        inspector = inspect(engine)
        try:
            tables = set(inspector.get_table_names())
        except Exception:
            tables = set()

        with engine.begin() as conn:
            for table, pk in _SEQUENCE_TABLES:
                if table not in tables:
                    continue
                try:
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                        f"COALESCE((SELECT MAX({pk}) FROM {table}), 1))"
                    ))
                    logger.info(f"Sequence synced for {table}.{pk}")
                except Exception as e:
                    logger.warning(f"Sequence sync skipped for {table}.{pk}: {e}")

        logger.info("Serial sequence self-heal migration complete")
    except Exception as e:
        logger.error(f"Sequence self-heal migration failed: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()