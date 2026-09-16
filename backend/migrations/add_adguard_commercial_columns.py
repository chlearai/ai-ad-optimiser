"""
Safe additive migration for AdGuard commercial metadata and offline payment ledger.

Adds columns on adguard_accounts:
  - phone (VARCHAR)
  - company_name (VARCHAR)
  - industry (VARCHAR)
  - overage_policy (VARCHAR)
  - payment_mode (VARCHAR)
  - payment_ref (VARCHAR)
  - amount_paid (FLOAT)
  - gst_invoice_no (VARCHAR)
  - payment_status (VARCHAR)
  - account_status (VARCHAR)
"""
import logging
from sqlalchemy import text, inspect
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

_COLUMNS = {
    "phone": "VARCHAR(50) NULL",
    "company_name": "VARCHAR(255) NULL",
    "industry": "VARCHAR(100) NULL",
    "overage_policy": "VARCHAR(30) DEFAULT 'block'",
    "payment_mode": "VARCHAR(50) NULL",
    "payment_ref": "VARCHAR(100) NULL",
    "amount_paid": "FLOAT DEFAULT 0.0",
    "gst_invoice_no": "VARCHAR(50) NULL",
    "payment_status": "VARCHAR(30) DEFAULT 'paid'",
    "account_status": "VARCHAR(30) DEFAULT 'active'",
}


def run_migration():
    active = get_active_db()
    logger.info(f"Running adguard commercial columns migration on {active}")
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

    logger.info("AdGuard commercial columns migration complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
