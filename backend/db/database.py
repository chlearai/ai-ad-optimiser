"""
Database setup using SQLAlchemy.
Supports SQLite (local dev) and PostgreSQL (Supabase/Railway).
Falls back to SQLite if PostgreSQL is unreachable.
"""
import os
import logging
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

# Load environment variables from .env if present
load_dotenv()

logger = logging.getLogger("AdOptima")

DATABASE_URL = os.getenv("DATABASE_URL")
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.getenv("ADOPTIMA_DB_PATH", os.path.join(ROOT_DIR, "adoptima.db"))
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
else:
    DB_PATH = os.path.join(ROOT_DIR, DB_PATH)
    os.makedirs(ROOT_DIR, exist_ok=True)


import socket

_pg_driver_name = None
_pg_driver_error = None


def _create_sqlite_engine():
    return create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def _load_pg_driver():
    """Load whichever Postgres DBAPI driver is available (psycopg2 or psycopg 3)."""
    global _pg_driver_name, _pg_driver_error
    if _pg_driver_name:
        return _pg_driver_name
    try:
        import psycopg2  # noqa: F401
        _pg_driver_name = "psycopg2"
        return _pg_driver_name
    except Exception:
        pass
    try:
        import psycopg  # noqa: F401
        _pg_driver_name = "psycopg"
        return _pg_driver_name
    except Exception as e:
        _pg_driver_error = f"{type(e).__name__}: {e}"
        return None


def _create_postgres_engine(dbapi_name, url):
    if dbapi_name == "psycopg":
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    connect_args = {"sslmode": "require", "connect_timeout": 10}
    # psycopg 3 prepares statements client-side; on Supabase transaction-pooler (Supavisor)
    # prepared statements COLLIDE across pooled sessions -> DuplicatePreparedStatement "_pg3_0".
    # Disable both the statement cache AND auto-prepare entirely (prepare_threshold=None):
    # with only the cache disabled, psycopg re-prepares on every execution after the
    # threshold, and those re-prepares still collide across pooled sessions.
    if dbapi_name == "psycopg":
        connect_args["prepared_statement_cache_size"] = 0
        connect_args["prepare_threshold"] = None
    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def _test_postgres_fast(url):
    """
    Verify PostgreSQL connectivity with whichever driver is installed.
    Retries up to 3 times (10s connect timeout each).
    """
    global _pg_driver_error
    if not url:
        return None, False
    driver = _load_pg_driver()
    if not driver:
        logger.error(f"No PostgreSQL driver available: {_pg_driver_error}")
        _pg_driver_error = _pg_driver_error or "No module named psycopg2 or psycopg"
        return None, False
    try:
        parsed = urlparse(url)
        conn_kwargs = {
            "dbname": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password,
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "connect_timeout": 10,
            "sslmode": "require",
        }
        for attempt in range(3):
            try:
                if driver == "psycopg2":
                    import psycopg2
                    test_conn = psycopg2.connect(**conn_kwargs)
                else:
                    import psycopg
                    test_conn = psycopg.connect(prepared_statement_cache_size=0, **conn_kwargs)
                test_conn.close()
                eng = _create_postgres_engine(driver, url)
                logger.info(f"PostgreSQL connectivity verified via driver '{driver}'")
                return eng, True
            except Exception as e:
                logger.warning(f"PostgreSQL connect attempt {attempt + 1}/3 ({driver}) failed: {type(e).__name__}: {e}")
        return None, False
    except Exception as e:
        logger.warning(f"PostgreSQL connection test failed: {type(e).__name__}: {e}")
        _pg_driver_error = f"{type(e).__name__}: {e}"
        return None, False


def _session_pooler_url(url: str):
    """Return a session-mode URL with transaction-pooler port 6543 swapped to 5432.

    Supabase transaction pooler (6543) does NOT support server-side prepared
    statements: statements collide across multiplexed sessions
    (DuplicatePreparedStatement "_pg3_N" / InvalidSqlStatementName). Session
    pooler (5432) does not multiplex, so it is always preferred when present.
    """
    try:
        parsed = urlparse(url)
        if parsed.port == 6543:
            return urlunparse(parsed._replace(netloc=f"{parsed.username}:{parsed.password}@{parsed.hostname}:5432"))
    except Exception:
        pass
    return None


engine = None
active_db = "unknown"

if DATABASE_URL:
    # Always try the session pooler first (prepared statements safe).
    candidates = []
    session_url = _session_pooler_url(DATABASE_URL)
    if session_url:
        candidates.append(session_url)
    candidates.append(DATABASE_URL)
    for candidate in candidates:
        eng, ok = _test_postgres_fast(candidate)
        if ok:
            engine = eng
            active_db = "postgresql"
            if candidate == session_url:
                logger.info("Using Supabase session pooler (5432) - transaction pooler breaks prepared statements")
            break
    if engine is None:
        # Legacy fallback: if primary URL was session mode, try transaction pooler
        parsed = urlparse(DATABASE_URL)
        try:
            if parsed.port == 5432:
                pooler_parts = parsed._replace(netloc=f"{parsed.username}:{parsed.password}@{parsed.hostname}:6543")
                pooler_url = urlunparse(pooler_parts)
                eng_pooler, ok_pooler = _test_postgres_fast(pooler_url)
                if ok_pooler:
                    engine = eng_pooler
                    active_db = "postgresql-pooler"
                    logger.info("Using Supabase connection pooler")
        except Exception as e:
            logger.warning(f"Pooler fallback failed: {e}")

if engine is None:
    engine = _create_sqlite_engine()
    active_db = "sqlite"
    if DATABASE_URL:
        logger.warning("PostgreSQL unreachable, falling back to SQLite")
    logger.info(f"Using SQLite database at {DB_PATH}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_raw_connection():
    """Return a DB-API style connection for raw SQL. Works for SQLite and PostgreSQL."""
    return engine.raw_connection()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import backend.db.models  # noqa: F401
    import backend.db.adguard_ops  # noqa: F401
    import backend.db.revenueops_models  # noqa: F401
    import backend.services.crashclub_db  # noqa: F401  (registers crashclub_leads table)
    try:
        Base.metadata.create_all(bind=engine)
        logger.info(f"Database initialized ({active_db})")
        # Run safe additive migrations for Phase 1 single-client master
        try:
            from backend.migrations.add_account_billing_columns import run_migration
            run_migration()
        except Exception as me:
            logger.warning(f"Additive account migration skipped/failed: {me}")
        try:
            from backend.migrations.add_missing_account_columns import run_migration as run_missing_cols_migration
            run_missing_cols_migration()
        except Exception as me:
            logger.warning(f"Missing account columns migration skipped/failed: {me}")
        try:
            from backend.migrations.add_invoice_upload_columns import run_invoice_migration
            run_invoice_migration()
        except Exception as me:
            logger.warning(f"Additive invoice migration skipped/failed: {me}")
        # Run safe additive migrations for Phase 2 smart keyword auditor
        try:
            from backend.migrations.add_brand_keyword_audit_tables import run_migration as run_brand_migration
            run_brand_migration()
        except Exception as me:
            logger.warning(f"Additive brand keyword audit migration skipped/failed: {me}")
        # Run safe additive migration for audit review access permission
        try:
            from backend.migrations.add_audit_review_access import run_migration as run_audit_review_migration
            run_audit_review_migration()
        except Exception as me:
            logger.warning(f"Additive audit review access migration skipped/failed: {me}")
        # Run safe additive migration for campaign knowledge (landing pages, business context, confidence)
        # MUST run before categories migration since Account model now references business_context
        try:
            from backend.migrations.add_campaign_knowledge_schema import run_migration as run_campaign_knowledge_migration
            run_campaign_knowledge_migration()
        except Exception as me:
            logger.warning(f"Additive campaign knowledge migration skipped/failed: {me}")
        # Run safe additive migration for dynamic category management
        try:
            from backend.migrations.add_categories_table import run_migration as run_categories_migration
            run_categories_migration()
        except Exception as me:
            logger.warning(f"Additive categories migration skipped/failed: {me}")
        # Run safe additive migration for Mantri MIS Reports
        try:
            from backend.migrations.add_mis_mantri_tables import run_migration as run_mis_migration
            run_mis_migration()
        except Exception as me:
            logger.warning(f"Additive MIS Mantri migration skipped/failed: {me}")
        # Run safe additive migration for mis_daily_snapshots.crm_leads column
        try:
            from backend.migrations.add_mis_crm_leads_column import run_migration as run_crm_leads_migration
            run_crm_leads_migration()
        except Exception as me:
            logger.warning(f"Additive crm_leads migration skipped/failed: {me}")
        # Run safe additive migration for rev_clients.account_id column
        try:
            from backend.migrations.add_rev_clients_account_id import run_migration as run_rev_clients_account_id_migration
            run_rev_clients_account_id_migration()
        except Exception as me:
            logger.warning(f"Additive rev_clients.account_id migration skipped/failed: {me}")
        # Run safe additive migration for LSQ lead mirror city/state columns
        try:
            from backend.migrations.add_lsq_lead_geo_columns import run_migration as run_lsq_geo_migration
            run_lsq_geo_migration()
        except Exception as me:
            logger.warning(f"Additive LSQ lead geo columns migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard module access permission
        try:
            from backend.migrations.add_access_adguard import run_migration as run_adguard_migration
            run_adguard_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard access migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard lead workspace linkage
        try:
            from backend.migrations.add_adguard_lead_workspace_column import run_migration as run_adguard_ws_migration
            run_adguard_ws_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard lead workspace migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard Meta OAuth columns
        try:
            from backend.migrations.add_adguard_meta_columns import run_migration as run_adguard_meta_migration
            run_adguard_meta_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard Meta columns migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard SaaS plan/quota columns
        try:
            from backend.migrations.add_adguard_plan_columns import run_migration as run_adguard_plan_migration
            run_adguard_plan_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard plan columns migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard per-subscriber CRM preference
        try:
            from backend.migrations.add_adguard_crm_preference import run_migration as run_adguard_crm_migration
            run_adguard_crm_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard crm_preference migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard Money Shield (Layer 1 prevention)
        try:
            from backend.migrations.add_adguard_shield_columns import run_migration as run_adguard_shield_migration
            run_adguard_shield_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard shield migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard per-subscriber CRM credentials
        try:
            from backend.migrations.add_adguard_crm_credentials import run_migration as run_adguard_crm_creds_migration
            run_adguard_crm_creds_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard crm_credentials migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard support tickets + settings + sync health
        try:
            from backend.migrations.add_adguard_support_settings import run_migration as run_adguard_support_migration
            run_adguard_support_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard support/settings migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard multi-identity Google connections
        try:
            from backend.migrations.add_adguard_google_identities import run_migration as run_adguard_gid_migration
            run_adguard_gid_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard google_identities migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard multi-identity Meta connections
        try:
            from backend.migrations.add_adguard_meta_identities import run_migration as run_adguard_mid_migration
            run_adguard_mid_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard meta_identities migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard commercial metadata and offline payment columns
        try:
            from backend.migrations.add_adguard_commercial_columns import run_migration as run_adguard_commercial_migration
            run_adguard_commercial_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard commercial columns migration skipped/failed: {me}")
        # Clean up any legacy 'google-account' placeholder identities
        try:
            from backend.migrations.cleanup_google_account_identity import run_migration as run_adguard_cleanup_gid_migration
            run_adguard_cleanup_gid_migration()
        except Exception as me:
            logger.warning(f"Cleanup AdGuard google-account identities migration skipped/failed: {me}")
        # Update Meta identity email to actual connected Meta email
        try:
            from backend.migrations.update_meta_identity_email import run_migration as run_adguard_meta_email_migration
            run_adguard_meta_email_migration()
        except Exception as me:
            logger.warning(f"Update AdGuard Meta identity email migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard signup provenance (beta separation)
        try:
            from backend.migrations.add_adguard_signup_source import run_migration as run_adguard_source_migration
            run_adguard_source_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard signup_source migration skipped/failed: {me}")
        # Run safe additive migration: users.welcome_email_sent dedupe flag
        try:
            from backend.migrations.add_user_welcome_flag import run_migration as run_user_welcome_flag_migration
            run_user_welcome_flag_migration()
        except Exception as me:
            logger.warning(f"Additive users.welcome_email_sent migration skipped/failed: {me}")
        # Run safe additive migration: monthly lead counters (plan hard-stop enforcement)
        try:
            from backend.migrations.add_adguard_monthly_lead_counters import run_migration as run_monthly_counters_migration
            run_monthly_counters_migration()
        except Exception as me:
            logger.warning(f"Additive monthly lead counters migration skipped/failed: {me}")
        # Run safe additive migration: ToS acceptance tracking
        try:
            from backend.migrations.add_user_tos_acceptance import run_migration as run_tos_migration
            run_tos_migration()
        except Exception as me:
            logger.warning(f"Additive ToS acceptance migration skipped/failed: {me}")
        # Run safe additive migration for cached campaigns & pages map
        try:
            from backend.migrations.add_adguard_cached_campaigns import run_migration as run_adguard_cached_campaigns_migration
            run_adguard_cached_campaigns_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard cached_campaigns migration skipped/failed: {me}")
        # Run safe additive migration for AdGuard V1 schema columns
        try:
            from backend.migrations.add_adguard_v1_schema import run_migration as run_adguard_v1_schema_migration
            run_adguard_v1_schema_migration()
        except Exception as me:
            logger.warning(f"Additive AdGuard V1 schema migration skipped/failed: {me}")
        # Run seed default admin migration
        try:
            from backend.migrations.seed_default_admin import run_migration as run_seed_admin_migration
            run_seed_admin_migration()
        except Exception as me:
            logger.warning(f"Seed default admin migration skipped/failed: {me}")
        # Run seed initial accounts and groups migration
        try:
            from backend.migrations.seed_initial_accounts import run_migration as run_seed_accounts_migration
            run_seed_accounts_migration()
        except Exception as me:
            logger.warning(f"Seed initial accounts migration skipped/failed: {me}")
        # Run safe additive migration to self-heal PostgreSQL serial sequences
        # (fixes duplicate-key aborts after manual seed inserts with explicit IDs)
        try:
            from backend.migrations.fix_serial_sequences import run_migration as run_fix_sequences_migration
            run_fix_sequences_migration()
        except Exception as me:
            logger.warning(f"Serial sequence self-heal migration skipped/failed: {me}")
    except Exception as e:

        logger.error(f"Database initialization failed: {e}")
        raise


def get_active_db():
    return active_db
