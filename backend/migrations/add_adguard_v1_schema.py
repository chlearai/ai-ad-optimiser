"""
Additive migration: Add AdGuard V1 schema columns and tables.
Adds conversion_values, otp_settings, bot_caller_settings to adguard_accounts.
Adds session_id, fbclid, phone_hash, email_hash, stage, crm_id, bot_call_status,
bot_call_summary, bot_call_attempts, verified_at to adguard_leads.
"""
import logging
from sqlalchemy import text
from backend.db.database import engine, active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    logger.info(f"Running adguard v1 schema migration on {active_db}")
    with engine.connect() as conn:
        # Check and add columns to adguard_accounts
        acct_cols = [
            ("conversion_values", "TEXT"),
            ("otp_settings", "TEXT"),
            ("bot_caller_settings", "TEXT"),
        ]
        for col_name, col_type in acct_cols:
            try:
                if active_db in ("sqlite", "unknown"):
                    res = conn.execute(text("PRAGMA table_info(adguard_accounts)")).fetchall()
                    existing = [r[1] for r in res]
                    if col_name not in existing:
                        conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                        logger.info(f"Added column adguard_accounts.{col_name}")
                else:
                    conn.execute(text(f"ALTER TABLE adguard_accounts ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                    conn.commit()
                    logger.info(f"Added column adguard_accounts.{col_name}")
            except Exception as e:
                logger.warning(f"adguard_accounts.{col_name} migration note: {e}")

        # Check and add columns to adguard_leads
        lead_cols = [
            ("session_id", "INTEGER"),
            ("fbclid", "VARCHAR"),
            ("phone_hash", "VARCHAR"),
            ("email_hash", "VARCHAR"),
            ("stage", "VARCHAR DEFAULT 'submitted'"),
            ("crm_id", "VARCHAR"),
            ("bot_call_status", "VARCHAR DEFAULT 'none'"),
            ("bot_call_summary", "TEXT"),
            ("bot_call_attempts", "INTEGER DEFAULT 0"),
            ("verified_at", "TIMESTAMP"),
        ]
        for col_name, col_type in lead_cols:
            try:
                if active_db in ("sqlite", "unknown"):
                    res = conn.execute(text("PRAGMA table_info(adguard_leads)")).fetchall()
                    existing = [r[1] for r in res]
                    if col_name not in existing:
                        conn.execute(text(f"ALTER TABLE adguard_leads ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                        logger.info(f"Added column adguard_leads.{col_name}")
                else:
                    conn.execute(text(f"ALTER TABLE adguard_leads ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                    conn.commit()
                    logger.info(f"Added column adguard_leads.{col_name}")
            except Exception as e:
                logger.warning(f"adguard_leads.{col_name} migration note: {e}")

    logger.info("AdGuard V1 schema migration completed")


if __name__ == "__main__":
    run_migration()
