"""
Seed default Super Admin if no superadmin exists, and ensure all module accesses are granted.
Idempotent. Works on PostgreSQL and SQLite.
"""
import logging
from sqlalchemy import text
from passlib.context import CryptContext
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def run_migration():
    active = get_active_db()
    logger.info(f"Checking default Super Admin seed on {active}")

    try:
        with engine.begin() as conn:
            res = conn.execute(text("SELECT id, email FROM users WHERE role = 'superadmin' OR email = 'admin@adoptima.ai' LIMIT 1")).fetchone()
            if not res:
                hashed = pwd_context.hash("admin123")
                logger.info("Seeding default Super Admin (admin@adoptima.ai / admin123)...")
                conn.execute(
                    text("""
                        INSERT INTO users (
                            email, hashed_password, full_name, role, is_active,
                            access_adpulse, access_insightdesk, access_revenueops, access_adguard, access_audit_review,
                            onboarding_completed, created_at, updated_at
                        ) VALUES (
                            :email, :pwd, :name, :role, 1,
                            1, 1, 1, 1, 1,
                            1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                    """),
                    {
                        "email": "admin@adoptima.ai",
                        "pwd": hashed,
                        "name": "Admin",
                        "role": "superadmin"
                    }
                )
                logger.info("Default Super Admin seeded successfully.")
            else:
                logger.info(f"Super Admin exists ({res[1]}), ensuring all module permissions are enabled...")
                conn.execute(
                    text("""
                        UPDATE users SET
                            role = 'superadmin',
                            is_active = 1,
                            access_adpulse = 1,
                            access_insightdesk = 1,
                            access_revenueops = 1,
                            access_adguard = 1,
                            access_audit_review = 1,
                            onboarding_completed = 1
                        WHERE id = :uid
                    """),
                    {"uid": res[0]}
                )
                logger.info("Super Admin permissions updated.")
    except Exception as e:
        logger.warning(f"Default admin seed skipped/failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
