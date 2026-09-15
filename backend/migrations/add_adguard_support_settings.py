"""
Safe additive migration for AdGuard professional pass (support + settings + sync health).

1. New tables: adguard_support_tickets, adguard_ticket_messages (create_all covers,
   but created here for idempotent standalone use).
2. New columns on adguard_accounts:
   - google_last_sync_at, meta_last_sync_at (connection manager)
   - timezone, alert_emails, protection_mode (settings)
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

_COLUMNS = {
    "google_last_sync_at": "TIMESTAMP NULL",
    "meta_last_sync_at": "TIMESTAMP NULL",
    "timezone": "VARCHAR(50) DEFAULT 'Asia/Kolkata'",
    "alert_emails": "TEXT NULL",
    "protection_mode": "VARCHAR(20) DEFAULT 'monitor'",
}


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard support/settings migration on {active}")
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

    # Support tables (safe: Base.create_all handles if-not-exists via init_db, but
    # ensure standalone runs create them too)
    try:
        from backend.db.models import AdGuardSupportTicket, AdGuardTicketMessage  # noqa: F401
        from backend.db.database import Base
        Base.metadata.create_all(bind=engine, tables=[
            AdGuardSupportTicket.__table__,
            AdGuardTicketMessage.__table__,
        ])
        logger.info("Support ticket tables ensured")
    except Exception as e:
        logger.warning(f"Support tables ensure failed: {e}")

    logger.info("AdGuard support/settings migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()