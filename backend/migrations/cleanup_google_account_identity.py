"""
Cleanup migration: removes legacy 'google-account' placeholder identities from adguard_accounts.google_identities.
When multiple accounts are added, real email IDs are used.
"""
import json
import logging
from sqlalchemy import text
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running cleanup_google_account_identity on {active}")
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT id, google_identities, discovered_accounts FROM adguard_accounts WHERE google_identities IS NOT NULL")).fetchall()
            for r in rows:
                ws_id = r[0]
                raw_idents = r[1]
                if not raw_idents or "google-account" not in str(raw_idents):
                    continue
                try:
                    idents = json.loads(raw_idents)
                    if not isinstance(idents, list):
                        continue
                    cleaned = [i for i in idents if i.get("email") != "google-account" and not str(i.get("email", "")).startswith("google-account")]
                    if len(cleaned) != len(idents):
                        logger.info(f"Removing legacy google-account identity from workspace {ws_id} (had {len(idents)}, now {len(cleaned)})")
                        merged = []
                        seen_ids = set()
                        for i in cleaned:
                            for a in i.get("discovered") or []:
                                if a.get("id") not in seen_ids:
                                    merged.append(a)
                                    seen_ids.add(a.get("id"))
                        new_idents_json = json.dumps(cleaned) if cleaned else None
                        new_disc_json = json.dumps(merged) if merged else r[2]
                        conn.execute(
                            text("UPDATE adguard_accounts SET google_identities = :gi, discovered_accounts = :da WHERE id = :wid"),
                            {"gi": new_idents_json, "da": new_disc_json, "wid": ws_id}
                        )
                        logger.info(f"Workspace {ws_id} google_identities cleaned successfully.")
                except Exception as e:
                    logger.warning(f"Failed cleaning google_identities for ws {ws_id}: {e}")
    except Exception as e:
        logger.warning(f"cleanup_google_account_identity migration skipped/failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
