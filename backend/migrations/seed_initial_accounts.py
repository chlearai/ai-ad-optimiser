"""
Seed default account groups, accounts, and rev_clients if they do not exist.
Idempotent. Works on PostgreSQL and SQLite.
"""
import logging
from sqlalchemy import text
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")

GROUPS = [
    {'id': 1, 'name': 'Education'},
    {'id': 2, 'name': 'Real Estate'},
    {'id': 3, 'name': 'Steel / Manufacturing'},
    {'id': 4, 'name': 'Healthcare'},
    {'id': 5, 'name': 'Fitness / Lifestyle'},
    {'id': 6, 'name': 'Fire Safety & Security Systems'},
    {'id': 7, 'name': 'Jewellery'}
]

ACCOUNTS = [
    {
        'id': 1, 'group_id': 1, 'name': 'DSU', 'account_type': 'GOOGLE', 'external_id': '290-991-9094',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': '290-991-9094', 'meta_external_id': '',
        'google_is_live': 1, 'meta_is_live': 0,
        'redirect_base_url': 'http://127.0.0.1:8000',
        'refresh_interval_minutes': 15, 'audit_interval_minutes': 10, 'is_active': 1, 'is_live': 1,
        'status': 'HEALTHY', 'spend': 33970.29, 'conversions': 58.68, 'clicks': 4094.0, 'impressions': 98478.0,
        'ctr': 4.16, 'cpa': 578.9, 'budget': 167380.22, 'budget_used_pct': 75.93,
        'crm_type': 'leadsquared',
        'target_cpa': 500.0, 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 1, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 2, 'group_id': 1, 'name': 'DSI', 'account_type': 'GOOGLE', 'external_id': '191-746-2211',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': '191-746-2211', 'meta_external_id': '',
        'google_is_live': 1, 'meta_is_live': 0,
        'redirect_base_url': None,
        'refresh_interval_minutes': 15, 'audit_interval_minutes': 10, 'is_active': 1, 'is_live': 1,
        'status': 'HEALTHY', 'spend': 8620.31, 'conversions': 1.0, 'clicks': 160.0, 'impressions': 3003.0,
        'ctr': 5.33, 'cpa': 8620.31, 'budget': 164701.0, 'budget_used_pct': 86.53,
        'crm_type': 'leadsquared',
        'target_cpa': None, 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 1, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 3, 'group_id': 3, 'name': 'Shyam Steel', 'account_type': 'BOTH', 'external_id': '102-912-8801',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 1,
        'google_external_id': '102-912-8801', 'meta_external_id': '',
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 60, 'audit_interval_minutes': 60,
        'is_active': 1, 'is_live': 0, 'status': 'WARNING', 'spend': 57411.0, 'conversions': 256.0, 'clicks': 2285.0,
        'impressions': 178230.0, 'ctr': 1.28, 'cpa': 224.26, 'budget': 63862.08, 'budget_used_pct': 89.9,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 9, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 4, 'group_id': 2, 'name': 'Mantri Developers', 'account_type': 'GOOGLE', 'external_id': '970-032-6931',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 1,
        'google_external_id': '970-032-6931', 'meta_external_id': 'act_557903702864382',
        'google_is_live': 1, 'meta_is_live': 1, 'refresh_interval_minutes': 15, 'audit_interval_minutes': 10,
        'is_active': 1, 'is_live': 1, 'status': 'WARNING', 'spend': 9832.75, 'conversions': 0.0, 'clicks': 160.0,
        'impressions': 15581.0, 'ctr': 1.03, 'cpa': 0.0, 'budget': 8606.48, 'budget_used_pct': 82.71,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 3, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 5, 'group_id': 2, 'name': 'Classic Featherlite (CF)', 'account_type': 'GOOGLE', 'external_id': '622-813-8182',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': '622-813-8182', 'meta_external_id': None,
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 15, 'audit_interval_minutes': 10,
        'is_active': 1, 'is_live': 0, 'status': 'HEALTHY', 'spend': 17448.0, 'conversions': 259.0, 'clicks': 990.0,
        'impressions': 41580.0, 'ctr': 2.38, 'cpa': 67.37, 'budget': 25030.28, 'budget_used_pct': 69.71,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 3, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 6, 'group_id': 4, 'name': 'Sparsh Hospitals', 'account_type': 'GOOGLE', 'external_id': '128-962-4888',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': '128-962-4888', 'meta_external_id': None,
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 15, 'audit_interval_minutes': 10,
        'is_active': 1, 'is_live': 0, 'status': 'WARNING', 'spend': 89316.0, 'conversions': 227.0, 'clicks': 1978.0,
        'impressions': 73186.0, 'ctr': 2.7, 'cpa': 393.46, 'budget': 112918.49, 'budget_used_pct': 79.1,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 2, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 7, 'group_id': 6, 'name': 'IFS', 'account_type': 'GOOGLE', 'external_id': '380-888-9347',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': '380-888-9347', 'meta_external_id': None,
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 60, 'audit_interval_minutes': 60,
        'is_active': 1, 'is_live': 0, 'status': 'HEALTHY', 'spend': 71531.0, 'conversions': 185.0, 'clicks': 1903.0,
        'impressions': 76120.0, 'ctr': 2.5, 'cpa': 386.65, 'budget': 104715.17, 'budget_used_pct': 68.31,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 11, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 8, 'group_id': 5, 'name': 'The Little Gym (TLG)', 'account_type': 'META', 'external_id': 'tlg-meta',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 0, 'has_meta': 1,
        'google_external_id': None, 'meta_external_id': 'tlg-meta',
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 60, 'audit_interval_minutes': 60,
        'is_active': 1, 'is_live': 0, 'status': 'WARNING', 'spend': 12950.0, 'conversions': 17.0, 'clicks': 2432.0,
        'impressions': 194560.0, 'ctr': 1.25, 'cpa': 761.76, 'budget': 16221.03, 'budget_used_pct': 79.83,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 10, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 9, 'group_id': 2, 'name': 'Surya Developers', 'account_type': 'GOOGLE', 'external_id': 'surya-developers-google',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 0,
        'google_external_id': 'surya-developers-google', 'meta_external_id': None,
        'google_is_live': 0, 'meta_is_live': 0, 'refresh_interval_minutes': 60, 'audit_interval_minutes': 60,
        'is_active': 1, 'is_live': 0, 'status': 'WARNING', 'spend': 90521.0, 'conversions': 5.0, 'clicks': 1553.0,
        'impressions': 108710.0, 'ctr': 1.43, 'cpa': 18104.2, 'budget': 120498.22, 'budget_used_pct': 75.12,
        'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60, 'category_id': 3, 'lsq_sync_interval_minutes': 10
    },
    {
        'id': 10, 'group_id': None, 'name': 'Crash Club', 'account_type': 'BOTH', 'external_id': 'act_577546498668650',
        'currency': 'INR', 'timezone': 'Asia/Kolkata', 'has_google': 1, 'has_meta': 1,
        'google_external_id': None, 'meta_external_id': 'act_577546498668650',
        'google_is_live': 1, 'meta_is_live': 0, 'redirect_base_url': 'http://127.0.0.1:8000',
        'refresh_interval_minutes': 5, 'audit_interval_minutes': 60, 'is_active': 1, 'is_live': 1,
        'status': 'DISCONNECTED', 'crm_type': 'none', 'client_status': 'Active', 'payment_due_days': 45,
        'address': '# 25, The Touchstone West, 9th Cross, Margosa Rd, Malleswaram, Bengaluru, Karnataka 560003',
        'state': 'Karnataka', 'state_code': '29', 'adpulse_refresh_interval': 5, 'adpulse_audit_interval': 60,
        'category_id': 12, 'rev_client_id': 6, 'contact_phone': '9900116713',
        'brand_name': 'C.Krishniah Chetty Jewellers Pvt Ltd', 'contact_email': 'amitatanksali@ckcsons.com',
        'lsq_sync_interval_minutes': 10, 'contact_person': 'Amita Tanksali'
    }
]

REV_CLIENTS = [
    {'id': 1, 'client_name': 'DSU', 'brand_name': 'DSU', 'business_manager_id': 1, 'client_status': 'active', 'invoice_day': 5, 'default_due_days': 30},
    {'id': 2, 'client_name': 'DSI', 'brand_name': 'DSI', 'business_manager_id': 1, 'client_status': 'active', 'invoice_day': 1, 'default_due_days': 30},
    {'id': 3, 'client_name': 'Shyam Steel', 'brand_name': 'Shyam Steel', 'business_manager_id': 1, 'client_status': 'active', 'invoice_day': 10, 'default_due_days': 30},
    {'id': 4, 'client_name': 'Mantri Developers', 'brand_name': 'Mantri Developers', 'business_manager_id': 1, 'client_status': 'active', 'invoice_day': 1, 'default_due_days': 15},
    {'id': 5, 'client_name': 'Classic Featherlite', 'brand_name': 'Classic Featherlite', 'business_manager_id': None, 'client_status': 'paused', 'invoice_day': 15, 'default_due_days': 30},
    {'id': 6, 'client_name': 'Crash Club', 'brand_name': 'C.Krishniah Chetty Jewellers Pvt Ltd', 'contact_person': 'Amita Tanksali', 'contact_email': 'amitatanksali@ckcsons.com', 'contact_phone': '9900116713', 'business_manager_id': None, 'client_status': 'active', 'invoice_day': 1, 'default_due_days': 45, 'account_id': 10}
]


def run_migration():
    active = get_active_db()
    logger.info(f"Checking default accounts & groups seed on {active}")

    try:
        with engine.begin() as conn:
            # 1. Seed Groups
            for g in GROUPS:
                exists = conn.execute(text("SELECT id FROM account_groups WHERE id = :id"), {"id": g["id"]}).fetchone()
                if not exists:
                    conn.execute(
                        text("INSERT INTO account_groups (id, name, created_at, updated_at) VALUES (:id, :name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                        {"id": g["id"], "name": g["name"]}
                    )
                    logger.info(f"Seeded account group: {g['name']}")

            # 2. Seed Accounts
            for a in ACCOUNTS:
                exists = conn.execute(text("SELECT id FROM accounts WHERE id = :id"), {"id": a["id"]}).fetchone()
                if not exists:
                    cols = list(a.keys())
                    placeholders = [f":{k}" for k in cols]
                    sql = f"INSERT INTO accounts ({', '.join(cols)}, created_at, updated_at) VALUES ({', '.join(placeholders)}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    conn.execute(text(sql), a)
                    logger.info(f"Seeded account: {a['name']}")

            # 3. Seed Rev Clients
            for rc in REV_CLIENTS:
                exists = conn.execute(text("SELECT id FROM rev_clients WHERE id = :id"), {"id": rc["id"]}).fetchone()
                if not exists:
                    cols = list(rc.keys())
                    placeholders = [f":{k}" for k in cols]
                    sql = f"INSERT INTO rev_clients ({', '.join(cols)}, created_at, updated_at) VALUES ({', '.join(placeholders)}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    conn.execute(text(sql), rc)
                    logger.info(f"Seeded rev_client: {rc['client_name']}")

        logger.info("Default accounts and groups seed completed.")
    except Exception as e:
        logger.warning(f"Default accounts seed skipped/failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
