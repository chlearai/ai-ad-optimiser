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


def _create_sqlite_engine():
    return create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def _create_postgres_engine(url):
    return create_engine(
        url,
        connect_args={"sslmode": "require", "connect_timeout": 10},
        pool_pre_ping=True,
        pool_recycle=300,
    )


def _test_connection(eng):
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"DB connection test failed: {e}")
        return False


engine = None
active_db = "unknown"

if DATABASE_URL:
    # Try PostgreSQL first
    pg_engine = _create_postgres_engine(DATABASE_URL)
    if _test_connection(pg_engine):
        engine = pg_engine
        active_db = "postgresql"
        logger.info("Using PostgreSQL database")
    else:
        # Try Supabase transaction pooler (port 6543) as fallback
        try:
            parsed = urlparse(DATABASE_URL)
            if parsed.port == 5432:
                pooler_parts = parsed._replace(netloc=f"{parsed.username}:{parsed.password}@{parsed.hostname}:6543")
                pooler_url = urlunparse(pooler_parts)
                pooler_engine = _create_postgres_engine(pooler_url)
                if _test_connection(pooler_engine):
                    engine = pooler_engine
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
    import backend.db.revenueops_models  # noqa: F401
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
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


def get_active_db():
    return active_db
