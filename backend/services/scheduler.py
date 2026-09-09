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
    _scheduler.start()
    logger.info("Background scheduler started (daily smart audit disabled, daily Mantri MIS refresh enabled)")


def _run_crashclub_sync():
    """Crash Club: poll Meta for new leads and push to Google Sheets (dedup in ledger)."""
    try:
        from backend.routes.crashclub import run_scheduled_sync
        run_scheduled_sync()
    except Exception as e:
        logger.warning(f"CrashClub scheduled sync failed: {e}")


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
