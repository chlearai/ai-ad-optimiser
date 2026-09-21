"""
Background scheduler for running automatic audits and metric refreshes.
Reads global and per-account audit intervals.
"""
import logging
import os
import time
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from backend.services.auditor import audit_account, audit_all_accounts
from backend.services.keyword_auditor import run_keyword_audit
from backend.services.search_term_auditor import run_search_term_audit
from backend.db.database import SessionLocal
from backend.db.models import Account, AccountStatus, AuditRun
from backend.services.config import load_config
from backend.services.connectors import meta_system_token_configured

logger = logging.getLogger("AdOptima")
_scheduler = None


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler()
    # Recalculate schedules every minute
    _scheduler.add_job(_reschedule_all, 'interval', minutes=1, id='schedule_refresher', replace_existing=True)
    # Auto-refresh live account metrics every 10 minutes
    _scheduler.add_job(_auto_refresh_live_metrics, 'interval', minutes=10, id='auto_metrics_refresh', replace_existing=True, next_run_time=datetime.utcnow() + timedelta(seconds=30))
    # Per-account LeadSquared lead mirror sync (default 10 minutes, configurable per account)
    _schedule_lsq_sync_jobs(_scheduler)
    # Daily smart keyword + search term audit at 8:00 AM IST = 2:30 AM UTC
    # DISABLED: campaign-level manual audits are now used instead. Re-enable after AI is retrained.
    # _scheduler.add_job(_run_daily_smart_audit, 'cron', hour=2, minute=30, id='daily_smart_audit', replace_existing=True)
    # Daily Mantri MIS snapshot refresh at 6:30 AM IST = 1:00 AM UTC
    _scheduler.add_job(_run_daily_mantri_mis_refresh, 'cron', hour=1, minute=0, id='daily_mantri_mis_refresh', replace_existing=True)
    # Crash Club Meta leads -> Google Sheets, every 5 minutes
    if os.getenv("CRASH_CLUB_SYNC_ENABLED", "true").lower() in ("true", "1", "yes"):
        _scheduler.add_job(_run_crashclub_sync, 'interval', minutes=5, id='crashclub_meta_leads_sync', replace_existing=True)
    # AdGuard: poll Meta Pages for new leadgen leads every 5 minutes (webhook-free path)
    if os.getenv("ADGUARD_META_POLL_ENABLED", "true").lower() in ("true", "1", "yes"):
        _scheduler.add_job(_run_adguard_meta_poll, 'interval', minutes=5, id='adguard_meta_leads_poll', replace_existing=True, next_run_time=datetime.utcnow() + timedelta(minutes=1))
    # AdGuard Money Shield: junk-rate governor scan every 5 minutes (Layer 1 autonomous prevention)
    if os.getenv("ADGUARD_SHIELD_ENABLED", "true").lower() in ("true", "1", "yes"):
        _scheduler.add_job(_run_adguard_shield_scan, 'interval', minutes=5, id='adguard_shield_scan', replace_existing=True, next_run_time=datetime.utcnow() + timedelta(minutes=1))
    # AdGuard weekly report email (every Monday 8:30 AM IST = 3:00 AM UTC)
    if os.getenv("ADGUARD_WEEKLY_REPORT_ENABLED", "true").lower() in ("true", "1", "yes"):
        _scheduler.add_job(_run_adguard_weekly_reports, 'cron', day_of_week='mon', hour=3, minute=0, id='adguard_weekly_reports', replace_existing=True)
    # AdGuard Shield Layer 1: push FraudGraph exclusions to platforms (weekly, Mon 4:00 AM UTC)
    if os.getenv("ADGUARD_EXCLUSION_SYNC_ENABLED", "true").lower() in ("true", "1", "yes"):
        _scheduler.add_job(_run_adguard_exclusion_sync, 'cron', day_of_week='mon', hour=4, minute=0, id='adguard_exclusion_sync', replace_existing=True)
    # AdGuard V1: Conversion Signal Firewall dispatcher (every 1 minute)
    _scheduler.add_job(_run_adguard_conversion_dispatcher, 'interval', minutes=1, id='adguard_conversion_dispatcher', replace_existing=True, next_run_time=datetime.utcnow() + timedelta(seconds=15))
    # AdGuard V1: Shared Fraud Network score decay (daily at midnight UTC)
    _scheduler.add_job(_run_adguard_network_score_decay, 'cron', hour=0, minute=0, id='adguard_network_score_decay', replace_existing=True)
    _scheduler.start()
    logger.info("Background scheduler started (daily smart audit disabled, daily Mantri MIS refresh enabled)")


def _run_adguard_conversion_dispatcher():
    """Flush pending conversion events via Conversion Signal Firewall (Meta CAPI + Google)."""
    try:
        from backend.services.adguard_firewall import flush_pending_conversions
        db = SessionLocal()
        try:
            flush_pending_conversions(db, limit=50)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[Scheduler] Conversion dispatcher error: {e}")


def _run_adguard_network_score_decay():
    """Decay inactive threat scores in the Shared Fraud Network."""
    try:
        from backend.services.adguard_shared_network import run_network_score_decay
        db = SessionLocal()
        try:
            run_network_score_decay(db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[Scheduler] Network score decay error: {e}")



def _run_adguard_exclusion_sync():
    """Push flagged-lead exclusions (last 180 days) to Google Customer Match + Meta Custom Audiences per workspace."""
    try:
        from backend.db.models import AdGuardAccount, AdGuardLead
        from backend.services.adguard_exclusion_sync import (
            push_google_exclusions, push_meta_exclusions_v2, log_shield_action,
        )
        from backend.services.adguard_meta import get_meta_token_from_credentials
        import json as _json

        db = SessionLocal()
        synced = 0
        try:
            for ws in db.query(AdGuardAccount).filter(AdGuardAccount.shield_enabled == True).all():  # noqa: E712
                try:
                    cutoff = datetime.utcnow() - timedelta(days=180)
                    leads = (
                        db.query(AdGuardLead.email, AdGuardLead.phone)
                        .filter(AdGuardLead.adguard_account_id == ws.id)
                        .filter(AdGuardLead.verdict == "flagged")
                        .filter(AdGuardLead.received_at >= cutoff)
                        .all()
                    )
                    lead_items = [{"email": e or "", "phone": p or ""} for e, p in leads if (e or p)]
                    if not lead_items:
                        continue

                    # Google: use first identity's creds
                    g_result = None
                    try:
                        identities = _json.loads(ws.google_identities) if ws.google_identities else []
                        if identities and identities[0].get("credentials"):
                            g_result = push_google_exclusions(identities[0]["credentials"], lead_items)
                            if g_result.get("ok"):
                                log_shield_action(db, ws, "google_exclusion_sync",
                                                  f"{g_result.get('added', 0)} identifiers pushed to Customer Match (customer {g_result.get('customer_id')})")
                                synced += 1
                            else:
                                log_shield_action(db, ws, "google_exclusion_sync_failed",
                                                  str(g_result.get("error"))[:200])
                    except Exception as ge:
                        logger.warning(f"[Shield sync] ws {ws.id} google: {ge}")

                    # Meta: one audience per ad account per identity
                    try:
                        meta_idents = _json.loads(ws.meta_identities) if ws.meta_identities else []
                        if not meta_idents and ws.meta_credentials:
                            meta_idents = [{"label": "meta-account", "credentials": ws.meta_credentials,
                                            "discovered_accounts": _json.loads(ws.discovered_meta_accounts or "[]")}]
                        for ident in meta_idents:
                            token = get_meta_token_from_credentials(ident.get("credentials") or "")
                            if not token:
                                continue
                            for acc in (ident.get("discovered_accounts") or [])[:10]:
                                r = push_meta_exclusions_v2(token, acc.get("id"), lead_items)
                                if r.get("ok"):
                                    log_shield_action(db, ws, "meta_exclusion_sync",
                                                      f"{r.get('added', 0)} identifiers pushed to audience for {acc.get('name') or acc.get('id')}")
                                    synced += 1
                                else:
                                    log_shield_action(db, ws, "meta_exclusion_failed",
                                                      f"{acc.get('name') or acc.get('id')}: {str(r.get('error'))[:180]}")
                    except Exception as me:
                        logger.warning(f"[Shield sync] ws {ws.id} meta: {me}")
                except Exception as wse:
                    logger.warning(f"[Shield sync] ws {ws.id} failed: {wse}")
        finally:
            db.close()
        if synced:
            logger.info(f"[Shield sync] exclusion push completed for {synced} workspace sync(s)")
    except Exception as e:
        logger.warning(f"AdGuard exclusion sync failed: {e}")


def _run_adguard_weekly_reports():
    """Email the weekly campaign junk report to each workspace's alert emails."""
    try:
        from backend.db.models import AdGuardAccount
        from backend.routes.adguard_support import campaign_report
        from backend.services.onboarding_email import _smtp_from_env
        import smtplib
        import socket
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.utils import formataddr
        import json as _json

        db = SessionLocal()
        sent = 0
        try:
            cfg = _smtp_from_env()
            for ws in db.query(AdGuardAccount).filter(AdGuardAccount.is_archived == False).all():  # noqa: E712
                try:
                    recipients = _json.loads(ws.alert_emails) if ws.alert_emails else []
                    if not recipients and ws.owner_email:
                        recipients = [ws.owner_email]
                    if not recipients:
                        continue
                    # Build report data for this workspace (7 days)
                    from datetime import timedelta as _td
                    start = datetime.utcnow() - _td(days=7)
                    from backend.db.models import AdGuardLead
                    rows = (
                        db.query(AdGuardLead.campaign_name, AdGuardLead.verdict)
                        .filter(AdGuardLead.adguard_account_id == ws.id)
                        .filter(AdGuardLead.received_at >= start)
                        .all()
                    )
                    per: dict = {}
                    for cname, verdict in rows:
                        b = per.setdefault((cname or "(unknown)").strip() or "(unknown)", {"total": 0, "flagged": 0})
                        b["total"] += 1
                        if verdict == "flagged":
                            b["flagged"] += 1
                    if not per:
                        continue
                    html_rows = ""
                    total_leads = total_flagged = 0
                    for cname, b in sorted(per.items(), key=lambda kv: -kv[1]["total"]):
                        pct = round(100 * b["flagged"] / b["total"], 1) if b["total"] else 0
                        total_leads += b["total"]
                        total_flagged += b["flagged"]
                        html_rows += f"<tr><td style='padding:6px 10px;border:1px solid #e7e5e4;'>{cname}</td><td style='padding:6px 10px;border:1px solid #e7e5e4;text-align:center;'>{b['total']}</td><td style='padding:6px 10px;border:1px solid #e7e5e4;text-align:center;'>{b['flagged']}</td><td style='padding:6px 10px;border:1px solid #e7e5e4;text-align:center;'><b>{pct}%</b></td><td style='padding:6px 10px;border:1px solid #e7e5e4;text-align:center;'>₹{b['flagged'] * 350:,}</td></tr>"
                    saved = total_flagged * 350
                    html = f"""
                    <html><body style="font-family:Arial,sans-serif;color:#1c1917;">
                    <div style="max-width:600px;margin:0 auto;border:1px solid #e7e5e4;border-radius:12px;overflow:hidden;">
                      <div style="background:#d97706;padding:16px 20px;"><span style="color:#fff;font-weight:700;font-size:16px;">🛡️ AdGuard Weekly Report — {ws.display_name or ws.owner_email}</span></div>
                      <div style="padding:20px;">
                        <p>Last 7 days: <b>{total_leads}</b> leads audited · <b style="color:#dc2626;">{total_flagged}</b> blocked · <b style="color:#059669;">₹{saved if (saved := total_flagged * 350) else 0} recovered spend</b></p>
                        <table style="border-collapse:collapse;font-size:13px;">
                          <tr style="background:#f5f5f4;"><th style="padding:6px 10px;border:1px solid #e7e5e4;text-align:left;">Campaign</th><th style="padding:6px 10px;border:1px solid #e7e5e4;">Leads</th><th style="padding:6px 10px;border:1px solid #e7e5e4;">Blocked</th><th style="padding:6px 10px;border:1px solid #e7e5e4;">Junk %</th><th style="padding:6px 10px;border:1px solid #e7e5e4;">Recovered ₹</th></tr>
                          {html_rows}
                        </table>
                        <p style="font-size:11px;color:#a8a29e;margin-top:16px;">Stop paying for garbage leads. — AdGuard</p>
                      </div>
                    </div></body></html>"""
                    if not cfg.get("error"):
                        addrs = socket.getaddrinfo(cfg["host"], cfg["port"], socket.AF_INET, socket.SOCK_STREAM)
                        msg = MIMEMultipart("alternative")
                        msg["From"] = formataddr((cfg["sender_name"], cfg["from"]))
                        msg["To"] = ", ".join(recipients)
                        msg["Subject"] = f"AdGuard Weekly Report — {ws.display_name or ws.owner_email}"
                        msg.attach(MIMEText("Weekly AdGuard report attached as HTML.", "plain"))
                        msg.attach(MIMEText(html, "html"))
                        server = smtplib.SMTP(addrs[0][4][0], cfg["port"], timeout=30)
                        server.ehlo(cfg["host"]); server.starttls(); server.ehlo(cfg["host"])
                        server.login(cfg["user"], cfg["pass"])
                        server.sendmail(cfg["from"], recipients, msg.as_string())
                        server.quit()
                        sent += 1
                    else:
                        logger.warning(f"[AdGuard weekly] SMTP not configured; skipping {ws.owner_email}")
                except Exception as we:
                    logger.warning(f"[AdGuard weekly] ws {ws.id} failed: {we}")
        finally:
            db.close()
        if sent:
            logger.info(f"[AdGuard weekly] {sent} report email(s) sent")
    except Exception as e:
        logger.warning(f"AdGuard weekly reports failed: {e}")


def _run_adguard_shield_scan():
    """AdGuard Money Shield governor: scan shield-enabled workspaces for junk-heavy campaigns."""
    try:
        from backend.services.adguard_shield import run_shield_scan_all
        db = SessionLocal()
        try:
            summary = run_shield_scan_all(db)
            if summary.get("campaigns_paused"):
                logger.warning(f"[Shield scan] paused {summary['campaigns_paused']} campaign(s) across {summary['workspaces_scanned']} workspace(s)")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"AdGuard shield scan failed: {e}")


def _run_crashclub_sync():
    """Crash Club: poll Meta for new leads and push to Google Sheets (dedup in ledger)."""
    try:
        from backend.routes.crashclub import run_scheduled_sync
        run_scheduled_sync()
    except Exception as e:
        logger.warning(f"CrashClub scheduled sync failed: {e}")


def _run_adguard_meta_poll():
    """AdGuard: poll all connected workspaces' Meta Pages for new leads."""
    try:
        from backend.db.models import AdGuardAccount, AdGuardLead
        from backend.services.adguard_meta import (
            _graph_get,
            get_meta_token_from_credentials,
            get_all_page_tokens,
        )
        from backend.services.adguard import process_incoming_lead, QuotaExceededError
        import json as _json

        db = SessionLocal()
        processed, failed = 0, 0
        try:
            for ws in db.query(AdGuardAccount).filter(AdGuardAccount.meta_is_live == True).all():  # noqa: E712
                token = get_meta_token_from_credentials(ws.meta_credentials or "")
                if not token:
                    continue
                # Connection Manager: record successful sync time
                ws.meta_last_sync_at = datetime.utcnow()
                db.commit()
                try:
                    pages = _json.loads(ws.discovered_meta_pages) if ws.discovered_meta_pages else []
                except Exception:
                    pages = []
                page_tokens = get_all_page_tokens(token)
                if "__error__" in page_tokens:
                    logger.warning(f"[AdGuard poll] ws {ws.id} bulk page tokens failed: {page_tokens['__error__']}")
                    continue
                for p in pages:
                    pid = str(p.get("id"))
                    page_token = page_tokens.get(pid) or ""
                    if not page_token:
                        continue
                    try:
                        data = _graph_get(f"{pid}/leads", {
                            "fields": "id,created_time,form_id,ad_id,ad_name,campaign_id,campaign_name,field_data",
                            "limit": "25",
                            "token": page_token,
                        })
                        for ld in (data or {}).get("data", []):
                            lead_id = str(ld.get("id") or "")
                            if not lead_id:
                                continue
                            exists = db.query(AdGuardLead).filter(AdGuardLead.raw_payload.like(f"%{lead_id}%")).first()
                            if exists:
                                continue
                            fields = {}
                            for item in ld.get("field_data") or []:
                                name = (item.get("name") or "").strip()
                                vals = item.get("values") or []
                                if name and vals:
                                    fields[name] = vals[0]
                            payload = {
                                "full_name": fields.get("full_name") or fields.get("name") or "",
                                "email": fields.get("email") or "",
                                "phone": fields.get("phone_number") or fields.get("phone") or "",
                                "city": fields.get("city") or "",
                                "state": fields.get("state") or "",
                                "country": fields.get("country") or "",
                                "postal_code": fields.get("zip_code") or fields.get("postal_code") or "",
                                "message": fields.get("message") or fields.get("comments") or "",
                                "campaign_name": ld.get("campaign_name") or ld.get("ad_name") or "",
                                "form_id": str(ld.get("form_id") or ""),
                                "gclid": None,
                                "lead_type": "meta_leadgen",
                                "platform": "meta",
                            }
                            try:
                                process_incoming_lead(payload, account=None, raw_payload=_json.dumps(ld), workspace_id=ws.id)
                                processed += 1
                            except QuotaExceededError:
                                logger.warning(f"[AdGuard poll] ws {ws.id} over quota — skipping rest of page {pid}")
                                break
                            except Exception as pe:
                                failed += 1
                                logger.warning(f"[AdGuard poll] lead {lead_id} failed: {pe}")
                    except Exception as pe:
                        failed += 1
                        logger.warning(f"[AdGuard poll] page {pid} failed: {pe}")
        finally:
            db.close()
        if processed or failed:
            logger.info(f"[AdGuard poll] processed={processed} failed={failed}")
    except Exception as e:
        logger.warning(f"AdGuard Meta poll failed: {e}")


def _run_daily_smart_audit():
    """Run keyword audit + search term audit for all active Google Ads accounts."""
    from datetime import date
    logger.info("Daily smart keyword + search term audit started")
    db = SessionLocal()
    run = None
    try:
        run = AuditRun(
            run_date=date.today(),
            run_type="daily_scheduled",
            start_time=datetime.utcnow(),
            status="pending",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        accounts = db.query(Account).filter(Account.is_active == True, Account.has_google == True, Account.google_is_live == True).all()
        total_kw = 0
        total_st = 0
        errors = []
        accounts_audited = 0

        for account in accounts:
            try:
                logger.info(f"Smart auditing account: {account.name} (id={account.id})")
                # Pull landing page URLs and crawl stale pages BEFORE keyword/search term audits
                try:
                    from backend.services.landing_page_service import fetch_campaign_landing_pages, crawl_stale_landing_pages
                    fetch_campaign_landing_pages(account.id, db=db)
                    crawl_stale_landing_pages(account.id, db=db)
                except Exception as lpe:
                    logger.warning(f"Landing page fetch/crawl failed for {account.name}: {lpe}")
                kw_result = run_keyword_audit(account.id, db=db)
                time.sleep(5)
                st_result = run_search_term_audit(account.id, db=db)
                time.sleep(5)

                kw_count = kw_result.get("actions_generated", 0)
                st_count = st_result.get("actions_generated", 0)
                total_kw += kw_count
                total_st += st_count
                accounts_audited += 1
                logger.info(f"Smart audit {account.name}: keyword_flags={kw_count}, search_term_flags={st_count}")
                if kw_result.get("error"):
                    errors.append(f"{account.name} keyword: {kw_result['error']}")
                if st_result.get("error"):
                    errors.append(f"{account.name} search_term: {st_result['error']}")
            except Exception as e:
                logger.error(f"Smart audit failed for account {account.id}: {e}", exc_info=True)
                errors.append(f"{account.name}: {str(e)}")

        run.end_time = datetime.utcnow()
        run.accounts_audited = accounts_audited
        run.total_keyword_flags = total_kw
        run.total_search_term_flags = total_st
        run.status = "completed" if not errors else "partial"
        if errors:
            run.error_log = "\n".join(errors)
        db.commit()
        logger.info(f"Daily smart audit completed: accounts={accounts_audited}, keyword_flags={total_kw}, search_term_flags={total_st}")
    except Exception as e:
        logger.error(f"Daily smart audit orchestration failed: {e}", exc_info=True)
        if run:
            try:
                run.end_time = datetime.utcnow()
                run.status = "failed"
                run.error_log = str(e)
                db.commit()
            except Exception:
                pass
    finally:
        db.close()


def run_manual_smart_audit(account_id: int, campaign_id: Optional[str] = None) -> Dict[str, Any]:
    """Manual on-demand smart audit for a single account, optionally a single campaign."""
    from datetime import date
    db = SessionLocal()
    try:
        run = AuditRun(
            run_date=date.today(),
            run_type="manual",
            start_time=datetime.utcnow(),
            status="pending",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        account = db.query(Account).filter(Account.id == account_id).first()
        account_name = account.name if account else None

        # Pull landing page URLs and crawl stale pages before audit
        # When a single campaign is requested, refresh only that landing page
        try:
            from backend.services.landing_page_service import fetch_campaign_landing_pages, crawl_stale_landing_pages, fetch_single_landing_page
            if campaign_id:
                fetch_single_landing_page(account_id, campaign_id, db=db)
            else:
                fetch_campaign_landing_pages(account_id, db=db)
                crawl_stale_landing_pages(account_id, db=db)
        except Exception as lpe:
            logger.warning(f"Landing page fetch/crawl failed for account {account_id} campaign {campaign_id}: {lpe}")

        kw_result = run_keyword_audit(account_id, db=db, campaign_id=campaign_id)
        st_result = run_search_term_audit(account_id, db=db, campaign_id=campaign_id)

        run.end_time = datetime.utcnow()
        run.accounts_audited = 1
        run.total_keyword_flags = kw_result.get("actions_generated", 0)
        run.total_search_term_flags = st_result.get("actions_generated", 0)
        run.status = "completed" if not (kw_result.get("error") or st_result.get("error")) else "partial"
        if kw_result.get("error") or st_result.get("error"):
            run.error_log = "\n".join([kw_result.get("error", ""), st_result.get("error", "")]).strip()
        db.commit()

        result = {
            "audit_run_id": run.id,
            "keyword_audit": kw_result,
            "search_term_audit": st_result,
        }
        if account:
            result["account_id"] = account.id
            result["account_name"] = account.name
        if campaign_id:
            result["campaign_id"] = campaign_id
        return result
    except Exception as e:
        logger.error(f"Manual smart audit failed for account {account_id}: {e}", exc_info=True)
        if run:
            try:
                run.end_time = datetime.utcnow()
                run.status = "failed"
                run.error_log = str(e)
                db.commit()
            except Exception:
                pass
        return {"error": str(e)}
    finally:
        db.close()


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Background scheduler stopped")


def _auto_refresh_live_metrics():
    """Refresh metrics for all live accounts from Google Ads and Meta APIs."""
    logger.info("Auto-refreshing live account metrics")
    from backend.services.connectors import get_connector
    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.is_active == True, Account.is_live == True).all()
        today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        for account in accounts:
            # Reset combined metrics at the start of each refresh cycle so we don't
            # accumulate stale data when both platforms are enabled.
            if account.has_google and account.has_meta:
                account.spend = 0.0
                account.clicks = 0
                account.impressions = 0
                account.conversions = 0
            for platform in ["google", "meta"]:
                if platform == "google" and not account.has_google:
                    continue
                if platform == "meta" and not account.has_meta:
                    continue
                platform_live = account.google_is_live if platform == "google" else account.meta_is_live
                platform_creds = account.google_credentials if platform == "google" else account.meta_credentials
                has_system_meta_token = platform == "meta" and meta_system_token_configured()
                if not (platform_live and (platform_creds or has_system_meta_token)):
                    continue
                try:
                    connector = get_connector(account, platform=platform, start_date=today, end_date=today)
                    if connector and connector.is_valid:
                        metrics = connector.fetch_account_metrics()
                        if "error" not in metrics:
                            if not (account.has_google and account.has_meta):
                                account.spend = metrics.get("spend", 0.0)
                                account.clicks = metrics.get("clicks", 0)
                                account.impressions = metrics.get("impressions", 0)
                                account.conversions = metrics.get("conversions", 0)
                            else:
                                account.spend = (account.spend or 0.0) + metrics.get("spend", 0.0)
                                account.clicks = (account.clicks or 0) + metrics.get("clicks", 0)
                                account.impressions = (account.impressions or 0) + metrics.get("impressions", 0)
                                account.conversions = (account.conversions or 0) + metrics.get("conversions", 0)
                            account.ctr = round((account.clicks / account.impressions) * 100, 2) if account.impressions else 0.0
                            account.cpa = round(account.spend / account.conversions, 2) if account.conversions else 0.0
                            # Health badges: compute API + Performance health
                            from backend.services.health import compute_health_badges
                            from backend.services.lsq_mirror import count_leads_by_course
                            _leads_today = 0
                            try:
                                _counts = count_leads_by_course(db, account.id, today, today)
                                _leads_today = sum(_counts.values())
                            except Exception as _lsq_e:
                                # On PostgreSQL, a failed statement aborts the whole
                                # transaction until an explicit rollback -- without this,
                                # any error here (e.g. a schema mismatch) would silently
                                # poison `db` for the rest of this loop iteration and make
                                # the later db.commit() below fail with
                                # InFailedSqlTransaction, even though it's unrelated.
                                logger.warning(f"count_leads_by_course failed for {account.name}: {_lsq_e}")
                                db.rollback()
                            _badges = compute_health_badges(account, api_success=True, platform=platform, leads=_leads_today, active_start=0, active_end=23)
                            _api_status = _badges["api_health"]["status"]
                            _perf_status = _badges["perf_health"]["status"]
                            # Map combined health to legacy status enum for backward compatibility
                            if _api_status == "DISCONNECTED":
                                account.status = AccountStatus.DISCONNECTED
                            elif _perf_status == "CRITICAL":
                                account.status = AccountStatus.CRITICAL
                            elif _perf_status in ("WARNING", "UNKNOWN"):
                                account.status = AccountStatus.WARNING
                            else:
                                account.status = AccountStatus.HEALTHY
                            account.last_sync_at = datetime.utcnow()
                            account.last_sync_error = None
                            # Fetch and cache billing data (best-effort, never breaks metrics)
                            try:
                                _billing = connector.fetch_billing()
                                if _billing:
                                    import json as _json
                                    # Low-balance alert (prepaid only, log once per critical state)
                                    if _billing.get("billing_type") == "prepaid" and _billing.get("health") == "critical":
                                        # Check if we already logged the alert
                                        _prev = None
                                        try:
                                            _prev = _json.loads(account.billing_cache) if account.billing_cache else {}
                                        except Exception:
                                            _prev = {}
                                        if not _prev.get("alert_logged"):
                                            _billing["alert_logged"] = True
                                            try:
                                                from backend.services.activity_log import log_activity
                                                log_activity(
                                                    module="AdPulse",
                                                    action="Low Balance Alert",
                                                    description=f"{account.name} balance critically low: {_billing.get('amount'):.0f} remaining ({_billing.get('balance_pct')}% of {_billing.get('total_budget'):.0f}). Ads may stop soon.",
                                                    account_id=account.id,
                                                    account_name=account.name,
                                                    entity_type="account",
                                                    entity_id=str(account.id),
                                                    details={
                                                        "balance": _billing.get("amount"),
                                                        "balance_pct": _billing.get("balance_pct"),
                                                        "total_budget": _billing.get("total_budget"),
                                                    },
                                                    db=db,
                                                )
                                                logger.warning(f"Low balance alert logged for {account.name}: {_billing.get('balance_pct')}%")
                                            except Exception as _ae:
                                                logger.warning(f"Failed to log low balance alert: {_ae}")
                                    elif _billing.get("billing_type") == "prepaid" and _billing.get("health") != "critical":
                                        # Reset alert flag when balance recovers
                                        _billing["alert_logged"] = False
                                    account.billing_cache = _json.dumps(_billing)
                                    logger.info(f"Billing cached for {account.name} ({platform}): type={_billing.get('billing_type')}, amount={_billing.get('amount')}")
                            except Exception as _be:
                                logger.warning(f"Billing fetch failed for {account.name} ({platform}): {_be}")
                            db.commit()
                            logger.info(f"Auto-refreshed {account.name} ({platform}): spend={account.spend}, clicks={account.clicks}, api={_api_status}, perf={_perf_status}")
                        else:
                            logger.warning(f"Auto-refresh {account.name} ({platform}) returned error: {metrics.get('error')}")
                            from backend.services.health import compute_health_badges
                            _badges = compute_health_badges(account, api_success=False, platform=platform, active_start=0, active_end=23)
                            account.status = AccountStatus.DISCONNECTED
                            account.last_sync_error = metrics.get("error")
                            db.commit()
                except Exception as e:
                    logger.error(f"Auto-refresh failed for {account.name} ({platform}): {e}")
    except Exception as e:
        logger.error(f"Auto-refresh metrics failed: {e}")
    finally:
        db.close()


def _reschedule_all():
    """Dynamic rescheduling based on global and per-account settings."""
    if not _scheduler:
        return

    global_interval = _global_audit_interval()

    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.is_active == True).all()
        for account in accounts:
            for platform in ["google", "meta"]:
                if (platform == "google" and not account.has_google) or (platform == "meta" and not account.has_meta):
                    continue
                job_id = f"audit_{account.id}_{platform}"
                interval = account.audit_interval_minutes if account.audit_interval_minutes else global_interval
                if interval is None or interval <= 0:
                    # disabled
                    try:
                        _scheduler.remove_job(job_id)
                    except Exception:
                        pass
                    continue
                try:
                    existing = _scheduler.get_job(job_id)
                    if existing and getattr(existing.trigger, 'interval', None) == interval:
                        continue
                except Exception:
                    pass
                try:
                    _scheduler.remove_job(job_id)
                except Exception:
                    pass
                _scheduler.add_job(
                    _run_account_audit,
                    'interval',
                    minutes=max(15, interval),
                    id=job_id,
                    replace_existing=True,
                    args=[account.id, platform],
                )
                logger.info(f"Scheduled audit for account {account.id} ({platform}) every {interval} minutes")

        # Global fallback audit job
        global_job_id = "global_audit"
        if global_interval and global_interval > 0:
            try:
                existing = _scheduler.get_job(global_job_id)
                if not existing or getattr(existing.trigger, 'interval', None) != global_interval:
                    _scheduler.remove_job(global_job_id)
                    _scheduler.add_job(_scheduled_audit_tick, 'interval', minutes=max(15, global_interval), id=global_job_id, replace_existing=True)
            except Exception:
                _scheduler.add_job(_scheduled_audit_tick, 'interval', minutes=max(15, global_interval), id=global_job_id, replace_existing=True)
        else:
            try:
                _scheduler.remove_job(global_job_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Rescheduling failed: {e}")
    finally:
        db.close()


def _global_audit_interval() -> int:
    cfg = load_config()
    val = cfg.get("global_audit_interval_minutes")
    try:
        return int(val) if val else 60
    except Exception:
        return 60


def _scheduled_audit_tick():
    logger.info("Running scheduled global audit")
    try:
        result = audit_all_accounts()
        logger.info(f"Scheduled audit complete: {result.get('total_actions_generated', 0)} actions generated")
    except Exception as e:
        logger.error(f"Scheduled audit failed: {e}")


def _run_account_audit(account_id: int, platform: str):
    logger.info(f"Running scheduled audit for account {account_id} ({platform})")
    try:
        audit_account(account_id, platform=platform)
    except Exception as e:
        logger.error(f"Account audit failed: {e}")


def _sync_lsq_leads_for_account(account_id: int):
    """Incremental sync of LeadSquared leads for a single account."""
    logger.info(f"Running scheduled LSQ lead mirror sync for account {account_id}")
    from backend.services.lsq_mirror import sync_account_leads
    db = SessionLocal()
    try:
        result = sync_account_leads(account_id, db=db)
        logger.info(f"LSQ sync account {account_id}: {result}")
        db.commit()
    except Exception as e:
        logger.error(f"Scheduled LSQ sync failed for account {account_id}: {e}")
    finally:
        db.close()


def _schedule_lsq_sync_jobs(scheduler):
    """Schedule per-account LSQ sync jobs based on account settings."""
    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(
            Account.is_active == True,
            Account.crm_type == "leadsquared",
            Account.lsq_access_key.isnot(None),
            Account.lsq_secret_key.isnot(None),
            Account.lsq_base_url.isnot(None),
        ).all()
        for account in accounts:
            interval = account.lsq_sync_interval_minutes or 10
            if interval <= 0:
                continue
            job_id = f"lsq_sync_{account.id}"
            try:
                scheduler.add_job(
                    _sync_lsq_leads_for_account,
                    'interval',
                    minutes=interval,
                    args=[account.id],
                    id=job_id,
                    replace_existing=True,
                    next_run_time=datetime.utcnow() + timedelta(seconds=30),
                )
                logger.info(f"Scheduled LSQ sync for account {account.name} (id={account.id}) every {interval} minutes")
            except Exception as e:
                logger.error(f"Failed to schedule LSQ sync for account {account.id}: {e}")
    except Exception as e:
        logger.error(f"Failed to schedule LSQ sync jobs: {e}")
    finally:
        db.close()


def reschedule_lsq_sync_for_account(account_id: int):
    """Reschedule LSQ sync job for a single account after settings change."""
    if not _scheduler:
        return
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return
        job_id = f"lsq_sync_{account_id}"
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass
        if (
            account.is_active
            and account.crm_type == "leadsquared"
            and account.lsq_access_key
            and account.lsq_secret_key
            and account.lsq_base_url
        ):
            interval = account.lsq_sync_interval_minutes or 10
            if interval > 0:
                _scheduler.add_job(
                    _sync_lsq_leads_for_account,
                    'interval',
                    minutes=interval,
                    args=[account_id],
                    id=job_id,
                    replace_existing=True,
                    next_run_time=datetime.utcnow() + timedelta(seconds=30),
                )
                logger.info(f"Rescheduled LSQ sync for account {account_id} every {interval} minutes")
    except Exception as e:
        logger.error(f"Failed to reschedule LSQ sync for account {account_id}: {e}")
    finally:
        db.close()


def schedule_account_audit(account_id: int, interval_minutes: int):
    """Manual helper to schedule a per-account audit immediately."""
    if not _scheduler:
        start_scheduler()
    _reschedule_all()


def remove_account_schedule(account_id: int):
    if _scheduler:
        for platform in ["google", "meta"]:
            job_id = f"audit_{account_id}_{platform}"
            try:
                _scheduler.remove_job(job_id)
            except Exception:
                pass


def _run_daily_mantri_mis_refresh():
    """Refresh Mantri MIS daily snapshots for both platforms at 6:30 AM IST."""
    from datetime import date, timedelta
    from backend.routes.mis_mantri import _refresh_platform
    from backend.db.models import MisProject, User

    logger.info("Daily Mantri MIS snapshot refresh started")
    db = SessionLocal()
    try:
        project = db.query(MisProject).filter(MisProject.name == "Serenity", MisProject.is_active == True).first()
        if not project:
            logger.warning("Mantri MIS Serenity project not found; skipping daily refresh")
            return
        user = db.query(User).filter(User.role == "admin").first() or db.query(User).first()
        if not user:
            logger.warning("No admin user found; skipping daily Mantri MIS refresh")
            return
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for platform, platform_start in [("meta", "2026-04-17"), ("google", "2026-05-25")]:
            try:
                result = _refresh_platform(db, project, platform, platform_start, yesterday, user)
                logger.info(f"Daily Mantri MIS refresh {platform}: {result}")
            except Exception as e:
                logger.error(f"Daily Mantri MIS refresh failed for {platform}: {e}", exc_info=True)
        db.commit()
        logger.info("Daily Mantri MIS snapshot refresh complete")
    except Exception as e:
        logger.error(f"Daily Mantri MIS refresh orchestration failed: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
