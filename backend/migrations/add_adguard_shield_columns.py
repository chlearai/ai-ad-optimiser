"""
Safe additive migration for AdGuard Money Shield (Layer 1).

Adds to adguard_accounts:
  - shield_enabled: master toggle for Layer 1 prevention
  - shield_junk_threshold: campaign junk-rate % that triggers auto-pause (default 40)
  - shield_min_leads: minimum leads in window before pausing (default 50)
  - shield_actions: JSON log of shield actions taken (pauses, exclusions)

Run manually or via database.init_db (registered below).
    python -m backend.migrations.add_adguard_shield_columns
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

_COLUMNS = {
    "shield_enabled": "BOOLEAN DEFAULT FALSE",
    "shield_junk_threshold": "INTEGER DEFAULT 40",
    "shield_min_leads": "INTEGER DEFAULT 50",
    "shield_actions": "TEXT NULL",
}


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard shield migration on {active}")
    try:
        inspector = inspect(engine)
        existing = {c["name"] for c in inspector.get_columns("adguard_accounts")}
    except Exception as e:
        logger.warning(f"Migration skipped (inspect failed): {e}")
        return

    for col, ddl in _COLUMNS.items():
        if col in existing:
            continue
        try:
            with engine.begin() as conn:
                if active.startswith("postgresql"):
                    conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN IF NOT EXISTS {col} {ddl}"))
                else:
                    conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN {col} {ddl}"))
            logger.info(f"Added column adguard_accounts.{col}")
        except Exception as e:
            logger.warning(f"Add column {col} failed: {e}")

    logger.info("AdGuard shield migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()