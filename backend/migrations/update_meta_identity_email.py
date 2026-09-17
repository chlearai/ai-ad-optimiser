"""
Migration: Sets the correct Meta identity email on adguard_accounts.meta_identities.
When Meta was connected before explicit email scope capture, the identity was either
unlabeled or defaulted to workspace owner_email. This migration ensures the connected
Meta identity displays the actual Facebook account email (vishnuprasadekm2@gmail.com).
"""
import json
import logging
from datetime import datetime
from sqlalchemy import text
from backend.db.database import engine, get_active_db

logger = logging.getLogger("AdOptima")


def run_migration():
    active = get_active_db()
    logger.info(f"Running update_meta_identity_email on {active}")
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, owner_email, meta_credentials, meta_identities, discovered_meta_accounts, discovered_meta_pages, meta_is_live "
                "FROM adguard_accounts WHERE meta_is_live = true OR meta_credentials IS NOT NULL"
            )).fetchall()

            for r in rows:
                ws_id = r[0]
                owner_email = r[1] or ""
                meta_creds = r[2]
                raw_idents = r[3]
                raw_accounts = r[4]
                raw_pages = r[5]

                idents = []
                if raw_idents:
                    try:
                        idents = json.loads(raw_idents)
                        if not isinstance(idents, list):
                            idents = []
                    except Exception:
                        idents = []

                # Target email for this workspace's Meta connection
                target_email = "vishnuprasadekm2@gmail.com"

                # Try to see if token can give us the live profile info
                if meta_creds:
                    try:
                        from backend.services.adguard_meta import get_meta_token_from_credentials, get_meta_profile_info
                        tok = get_meta_token_from_credentials(meta_creds)
                        if tok:
                            info = get_meta_profile_info(tok)
                            if info.get("email"):
                                target_email = info["email"]
                    except Exception as ex:
                        logger.warning(f"Could not read meta token for ws {ws_id}: {ex}")

                # If no identities yet, construct the identity entry
                if not idents:
                    accounts = []
                    pages = []
                    try:
                        accounts = json.loads(raw_accounts) if raw_accounts else []
                    except Exception:
                        pass
                    try:
                        pages = json.loads(raw_pages) if raw_pages else []
                    except Exception:
                        pass

                    idents = [{
                        "label": target_email,
                        "email": target_email,
                        "credentials": meta_creds,
                        "discovered_accounts": accounts,
                        "discovered_pages": pages,
                        "connected_at": datetime.utcnow().isoformat(),
                    }]
                else:
                    # Update existing identity entries to replace any owner_email fallback
                    for ident in idents:
                        curr_label = str(ident.get("label", ""))
                        curr_email = str(ident.get("email", ""))
                        # If label/email is owner_email, meta-account, or missing, set target_email
                        if not curr_email or curr_email == owner_email or curr_label == owner_email or curr_label.startswith("meta-account"):
                            ident["email"] = target_email
                            ident["label"] = target_email

                new_idents_json = json.dumps(idents)
                conn.execute(
                    text("UPDATE adguard_accounts SET meta_identities = :mi WHERE id = :wid"),
                    {"mi": new_idents_json, "wid": ws_id}
                )
                logger.info(f"Workspace {ws_id} meta_identities updated to email {target_email}.")
    except Exception as e:
        logger.warning(f"update_meta_identity_email migration skipped/failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
