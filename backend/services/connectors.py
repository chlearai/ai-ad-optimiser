"""
Platform connector factory for Google Ads and Meta Marketing API.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from backend.db.models import Account, AccountType
from backend.services.crypto import decrypt
import logging

logger = logging.getLogger("AdOptima")


def _today_ist() -> str:
    """Return today's date in IST (YYYY-MM-DD)."""
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _resolve_end_date(end_date: Optional[str], start_date: Optional[str]) -> str:
    """Return a real YYYY-MM-DD end date; never the literal 'today'."""
    if end_date and str(end_date).strip().lower() != "today":
        return str(end_date).strip()
    if start_date and str(start_date).strip().lower() != "today":
        return str(start_date).strip()
    return _today_ist()


class AdsConnector:
    """Base class for ad platform connectors."""
    def __init__(self, account: Account, start_date: Optional[str] = None, end_date: Optional[str] = None):
        self.account = account
        self.start_date = start_date
        self.end_date = end_date
        self.is_valid = False

    def validate_credentials(self) -> bool:
        return False

    def fetch_account_metrics(self) -> Dict[str, Any]:
        raise NotImplementedError

    def fetch_campaigns(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def fetch_search_terms(self, campaign_id: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def apply_negative_keyword(self, campaign_id: str, keyword: str, match_type: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_campaign_budget(self, campaign_id: str, new_budget: float) -> Dict[str, Any]:
        raise NotImplementedError

    def fetch_billing(self) -> Dict[str, Any]:
        """Fetch billing/balance data. Override in subclasses.

        Returns dict with keys:
          billing_type: "prepaid" | "postpaid" | "unknown"
          amount: float | None  (balance for prepaid, used for postpaid)
          status: "available" | "unavailable"
        """
        return {"billing_type": "unknown", "amount": None, "status": "unavailable"}


class GoogleAdsConnector(AdsConnector):
    def __init__(self, account: Account, start_date: Optional[str] = None, end_date: Optional[str] = None):
        super().__init__(account, start_date=start_date, end_date=end_date)
        self.client = None
        self.validate_credentials()

    def validate_credentials(self) -> bool:
        try:
            from google.ads.googleads.client import GoogleAdsClient
            creds = self._parse_credentials()
            if not creds:
                return False
            self.client = GoogleAdsClient.load_from_dict(creds)
            self.is_valid = True
            return True
        except Exception as e:
            logger.error(f"Google Ads connector failed for account {self.account.id}: {e}")
            self.is_valid = False
            return False

    def _parse_credentials(self) -> Optional[Dict[str, Any]]:
        raw = self.account.google_credentials or self.account.credentials
        if not raw:
            return None
        try:
            import json
            creds = json.loads(decrypt(raw))
            login_customer_id = creds.get("login_customer_id", "")
            result = {
                "developer_token": creds.get("developer_token", ""),
                "client_id": creds.get("client_id", ""),
                "client_secret": creds.get("client_secret", ""),
                "refresh_token": creds.get("refresh_token", ""),
                "use_proto_plus": True,
            }
            if login_customer_id and str(login_customer_id).strip():
                result["login_customer_id"] = str(login_customer_id).replace("-", "")
            return result
        except Exception as e:
            # Empty str(e) usually means Fernet InvalidToken: creds were
            # encrypted with a different ADOPTIMA_SECRET_KEY/ADOPTIMA_SALT
            # than the one this environment uses to decrypt.
            logger.error(
                f"Failed to parse Google credentials for account {self.account.id}: "
                f"{type(e).__name__}: {e or '(no message — likely decrypt key mismatch)'}"
            )
            return None

    def _date_clause(self) -> str:
        if self.start_date and self.end_date:
            return f"segments.date BETWEEN '{self.start_date}' AND '{self.end_date}'"
        return "segments.date DURING LAST_30_DAYS"

    def fetch_account_metrics(self) -> Dict[str, Any]:
        if not self.is_valid:
            return {"error": "Google Ads client not valid"}
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return {"error": "No Google customer ID configured"}
        date_clause = self._date_clause()
        query = f"""
            SELECT
              metrics.cost_micros,
              metrics.clicks,
              metrics.impressions,
              metrics.conversions
            FROM customer
            WHERE {date_clause}
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            total_cost = 0
            total_clicks = 0
            total_impressions = 0
            total_conversions = 0
            for row in response:
                total_cost += row.metrics.cost_micros or 0
                total_clicks += row.metrics.clicks or 0
                total_impressions += row.metrics.impressions or 0
                total_conversions += row.metrics.conversions or 0
            return {
                "spend": round(total_cost / 1_000_000.0, 2),
                "clicks": total_clicks,
                "impressions": total_impressions,
                "conversions": total_conversions,
            }
        except Exception as e:
            logger.error(f"Google fetch metrics failed: {e}")
            return {"error": str(e)}

    def fetch_campaigns(self) -> List[Dict[str, Any]]:
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        date_clause = self._date_clause()
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign_budget.amount_micros,
              metrics.cost_micros,
              metrics.clicks,
              metrics.impressions,
              metrics.conversions
            FROM campaign
            WHERE {date_clause}
              AND campaign.status = 'ENABLED'
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            results = []
            for row in response:
                cost = (row.metrics.cost_micros or 0) / 1_000_000.0
                clicks = row.metrics.clicks or 0
                impressions = row.metrics.impressions or 0
                conversions = row.metrics.conversions or 0
                ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0
                cpa = round(cost / conversions, 2) if conversions else 0.0
                results.append({
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                    "status": str(row.campaign.status),
                    "budget": (row.campaign_budget.amount_micros or 0) / 1_000_000.0,
                    "spend": cost,
                    "clicks": clicks,
                    "impressions": impressions,
                    "conversions": conversions,
                    "ctr": ctr,
                    "cpa": cpa,
                })
            return results
        except Exception as e:
            logger.error(f"Google fetch campaigns failed: {e}")
            return []

    def fetch_daily_metrics(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return per-day spend and conversions for a date range (inclusive)."""
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        s = start_date or self.start_date
        e = end_date or self.end_date
        if not s or not e:
            return []
        query = f"""
            SELECT
              segments.date,
              metrics.cost_micros,
              metrics.conversions
            FROM customer
            WHERE segments.date BETWEEN '{s}' AND '{e}'
            ORDER BY segments.date
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            results = []
            for row in response:
                results.append({
                    "date": str(row.segments.date),
                    "spend": round((row.metrics.cost_micros or 0) / 1_000_000.0, 2),
                    "leads": round(float(row.metrics.conversions or 0)),
                })
            return results
        except Exception as e:
            logger.error(f"Google fetch daily metrics failed: {e}")
            return []

    def fetch_billing(self) -> Dict[str, Any]:
        """Fetch billing/balance data from Google Ads API.

        Detects prepaid (active account_budget with spending limit) vs postpaid
        (no active budget or unlimited). For prepaid, calculates remaining balance
        and health. For postpaid, returns monthly spend.
        """
        if not self.is_valid:
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}
        try:
            service = self.client.get_service("GoogleAdsService")

            # Step 1: Fetch active account_budget to get spending limit and amount served
            query_ab = """
                SELECT
                  account_budget.id,
                  account_budget.status,
                  account_budget.approved_spending_limit_micros,
                  account_budget.adjusted_spending_limit_micros,
                  account_budget.amount_served_micros,
                  account_budget.approved_start_date_time
                FROM account_budget
            """
            response_ab = service.search(customer_id=customer_id, query=query_ab)
            # Collect ALL active budgets — a top-up creates a NEW account_budget
            # entry in Google Ads, so we must sum across all of them, not break on
            # the first one.
            active_budgets = []
            fallback_budget = None
            for row in response_ab:
                ab = row.account_budget
                # status 3 = ACTIVE in v24 enum
                if str(ab.status).endswith("3") or str(ab.status) == "AccountBudgetStatus.ACTIVE" or ab.status == 3:
                    active_budgets.append(ab)
                elif fallback_budget is None:
                    fallback_budget = ab  # fallback to first non-active row

            # Use the primary active budget for metadata (start date, etc.)
            active_budget = active_budgets[0] if active_budgets else fallback_budget

            # Determine billing type: prepaid if there's an active budget with a
            # positive, finite spending limit; otherwise postpaid.
            has_active_limit = (
                active_budget
                and active_budget.adjusted_spending_limit_micros
                and active_budget.adjusted_spending_limit_micros > 0
            )

            if not has_active_limit:
                # POSTPAID: fetch spend for this calendar month
                today_ist = _today_ist()
                first_of_month = today_ist[:8] + "01"  # e.g. "2026-07-01"
                query_month = f"""
                    SELECT
                      metrics.cost_micros
                    FROM customer
                    WHERE segments.date BETWEEN '{first_of_month}' AND '{_resolve_end_date(self.end_date, self.start_date)}'
                """
                try:
                    response_month = service.search(customer_id=customer_id, query=query_month)
                    monthly_spend = 0.0
                    for row in response_month:
                        monthly_spend += (row.metrics.cost_micros or 0) / 1_000_000.0
                except Exception:
                    monthly_spend = float(getattr(self.account, "spend", 0) or 0)

                # Build month label e.g. "July 2026"
                try:
                    _dt = datetime.strptime(first_of_month, "%Y-%m-%d")
                    month_label = _dt.strftime("%B %Y")
                except Exception:
                    month_label = ""

                # Resolve inception date for lifetime spend query
                inception_date = "2026-04-01"  # Default (new accounts launch)
                if self.account.id == 1 or "dsu" in self.account.name.lower():
                    inception_date = "2025-11-28"  # DSU inception date
                elif self.account.id == 2 or "dsi" in self.account.name.lower():
                    inception_date = "2026-01-08"  # DSI inception date
                else:
                    inception_date = "2020-01-01"

                today_str = _today_ist()
                query_lifetime = f"""
                    SELECT
                      metrics.cost_micros
                    FROM customer
                    WHERE segments.date BETWEEN '{inception_date}' AND '{today_str}'
                """
                lifetime_spend = 0.0
                try:
                    response_lifetime = service.search(customer_id=customer_id, query=query_lifetime)
                    for row in response_lifetime:
                        lifetime_spend += (row.metrics.cost_micros or 0) / 1_000_000.0
                except Exception as e:
                    logger.error(f"Failed to fetch lifetime spend for postpaid account {self.account.id}: {e}")
                    lifetime_spend = monthly_spend

                return {
                    "billing_type": "postpaid",
                    "amount": round(lifetime_spend, 2),
                    "status": "available",
                    "monthly_spend": round(monthly_spend, 2),
                    "lifetime_spend": round(lifetime_spend, 2),
                    "month_label": month_label,
                }

            # PREPAID: balance = sum(adjusted_spending_limit - amount_served) across
            # ALL active budgets. Google Ads creates a NEW account_budget on each
            # top-up, so only reading the first one misses recently added funds.
            if active_budgets:
                total_budget = sum(
                    (ab.adjusted_spending_limit_micros or 0) for ab in active_budgets
                ) / 1_000_000.0
                amount_served = sum(
                    (ab.amount_served_micros or 0) for ab in active_budgets
                ) / 1_000_000.0
            else:
                total_budget = active_budget.adjusted_spending_limit_micros / 1_000_000.0
                amount_served = (active_budget.amount_served_micros or 0) / 1_000_000.0
            budget_start = active_budget.approved_start_date_time or ""
            spend_start = budget_start[:10] if budget_start else "2026-01-01"

            balance = round(total_budget - amount_served, 2)
            balance_pct = round((balance / total_budget * 100), 1) if total_budget > 0 else 0.0
            if balance_pct > 30:
                health = "good"
            elif balance_pct > 10:
                health = "warning"
            else:
                health = "critical"

            # Resolve inception date for lifetime spend query
            inception_date = "2026-04-01"  # Default (new accounts launch)
            if self.account.id == 1 or "dsu" in self.account.name.lower():
                inception_date = "2025-11-28"  # DSU inception date
            elif self.account.id == 2 or "dsi" in self.account.name.lower():
                inception_date = "2026-01-08"  # DSI inception date
            else:
                if spend_start:
                    inception_date = spend_start

            today_str = _today_ist()
            query_lifetime = f"""
                SELECT
                  metrics.cost_micros
                FROM customer
                WHERE segments.date BETWEEN '{inception_date}' AND '{today_str}'
            """
            lifetime_spend = 0.0
            try:
                response_lifetime = service.search(customer_id=customer_id, query=query_lifetime)
                for row in response_lifetime:
                    lifetime_spend += (row.metrics.cost_micros or 0) / 1_000_000.0
            except Exception as e:
                logger.error(f"Failed to fetch lifetime spend for prepaid account {self.account.id}: {e}")
                lifetime_spend = amount_served  # fallback to active budget amount_served

            return {
                "billing_type": "prepaid",
                "amount": balance,
                "status": "available",
                "total_budget": round(total_budget, 2),
                "amount_served": round(amount_served, 2),
                "budget_start_date": spend_start,
                "balance_pct": balance_pct,
                "health": health,
                "lifetime_spend": round(lifetime_spend, 2),
            }
        except Exception as e:
            logger.warning(f"Google fetch billing failed for account {self.account.id}: {e}")
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}

    def fetch_keywords(self) -> List[Dict[str, Any]]:
        """Fetch active keywords with 30-day performance metrics."""
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        date_clause = self._date_clause()
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              ad_group.id,
              ad_group.name,
              ad_group_criterion.criterion_id,
              ad_group_criterion.keyword.text,
              ad_group_criterion.keyword.match_type,
              ad_group_criterion.status,
              metrics.cost_micros,
              metrics.conversions,
              metrics.clicks,
              metrics.impressions,
              metrics.ctr,
              metrics.average_cpc
            FROM keyword_view
            WHERE {date_clause}
              AND campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
              AND metrics.cost_micros > 0
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            results = []
            for row in response:
                cost = (row.metrics.cost_micros or 0) / 1_000_000.0
                clicks = row.metrics.clicks or 0
                impressions = row.metrics.impressions or 0
                conversions = row.metrics.conversions or 0
                ctr = round((row.metrics.ctr or 0) * 100, 2)
                results.append({
                    "campaign_id": str(row.campaign.id),
                    "campaign_name": row.campaign.name,
                    "ad_group_id": str(row.ad_group.id),
                    "ad_group_name": row.ad_group.name,
                    "criterion_id": str(row.ad_group_criterion.criterion_id),
                    "text": row.ad_group_criterion.keyword.text,
                    "match_type": str(row.ad_group_criterion.keyword.match_type).replace("KeywordMatchType.", ""),
                    "status": str(row.ad_group_criterion.status),
                    "spend": cost,
                    "conversions": conversions,
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": ctr,
                    "average_cpc": (row.metrics.average_cpc or 0) / 1_000_000.0,
                })
            return results
        except Exception as e:
            logger.error(f"Google fetch keywords failed for account {self.account.id}: {e}")
            return []

    def fetch_search_terms(self, campaign_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch search term report for last 30 days with metrics."""
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        date_clause = self._date_clause()
        campaign_filter = f" AND campaign.id = '{campaign_id}'" if campaign_id else ""
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              ad_group.id,
              ad_group.name,
              search_term_view.search_term,
              search_term_view.status,
              metrics.cost_micros,
              metrics.conversions,
              metrics.clicks,
              metrics.impressions,
              metrics.ctr
            FROM search_term_view
            WHERE {date_clause}
              AND campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND metrics.impressions > 0
              {campaign_filter}
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            results = []
            for row in response:
                cost = (row.metrics.cost_micros or 0) / 1_000_000.0
                clicks = row.metrics.clicks or 0
                impressions = row.metrics.impressions or 0
                conversions = row.metrics.conversions or 0
                ctr = round((row.metrics.ctr or 0) * 100, 2)
                results.append({
                    "campaign_id": str(row.campaign.id),
                    "campaign_name": row.campaign.name,
                    "campaign_status": str(row.campaign.status),
                    "ad_group_id": str(row.ad_group.id),
                    "ad_group_name": row.ad_group.name,
                    "term": row.search_term_view.search_term,
                    "status": str(row.search_term_view.status),
                    "spend": cost,
                    "conversions": conversions,
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": ctr,
                })
            return results
        except Exception as e:
            logger.error(f"Google fetch search terms failed for account {self.account.id}: {e}")
            return []

    def fetch_campaign_negative_keywords(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Fetch existing negative keywords at campaign level for a specific campaign."""
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        query = f"""
            SELECT
              campaign_criterion.criterion_id,
              campaign_criterion.keyword.text,
              campaign_criterion.keyword.match_type,
              campaign_criterion.negative
            FROM campaign_criterion
            WHERE campaign.id = '{campaign_id}'
              AND campaign_criterion.type = 'KEYWORD'
              AND campaign_criterion.negative = TRUE
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            results = []
            for row in response:
                results.append({
                    "criterion_id": str(row.campaign_criterion.criterion_id),
                    "text": row.campaign_criterion.keyword.text,
                    "match_type": str(row.campaign_criterion.keyword.match_type).replace("KeywordMatchType.", ""),
                })
            return results
        except Exception as e:
            logger.error(f"Google fetch campaign negatives failed for account {self.account.id}: {e}")
            return []

    def fetch_landing_pages(self) -> List[Dict[str, Any]]:
        """Fetch landing page URLs per campaign from active ad_group_ad rows.

        Picks the ad with the highest impressions per campaign as the primary landing page.

        Returns list of dicts: {campaign_id, campaign_name, landing_page_url}
        Campaigns with no final_url return landing_page_url=None.
        """
        if not self.is_valid:
            return []
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return []
        query = """
            SELECT
              campaign.id,
              campaign.name,
              ad_group_ad.ad.final_urls,
              metrics.impressions
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
        """
        try:
            service = self.client.get_service("GoogleAdsService")
            response = service.search(customer_id=customer_id, query=query)
            best: Dict[str, Dict[str, Any]] = {}
            for row in response:
                cid = str(row.campaign.id)
                cname = row.campaign.name
                impressions = row.metrics.impressions or 0
                final_urls_field = row.ad_group_ad.ad.final_urls
                urls = list(final_urls_field) if final_urls_field else []
                url = urls[0] if urls else None
                if cid not in best or impressions > best[cid].get("impressions", -1):
                    best[cid] = {
                        "campaign_id": cid,
                        "campaign_name": cname,
                        "landing_page_url": url,
                        "impressions": impressions,
                    }
            return list(best.values())
        except Exception as e:
            logger.error(f"Google fetch landing pages failed for account {self.account.id}: {e}")
            return []

    def apply_negative_keyword(self, campaign_id: str, keyword: str, match_type: str) -> Dict[str, Any]:
        """Add a campaign-level negative keyword."""
        if not self.is_valid:
            return {"success": False, "error": "Google Ads client not valid"}
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return {"success": False, "error": "No Google customer ID configured"}
        try:
            client = self.client
            campaign_service = client.get_service("CampaignCriterionService")
            campaign_resource_name = client.get_service("GoogleAdsService").campaign_path(customer_id, campaign_id)

            # Build the operation directly; recent Google Ads API versions do not
            # support CopyFrom on CampaignCriterionOperation.create.
            operation = client.get_type("CampaignCriterionOperation")
            operation.create.campaign = campaign_resource_name
            operation.create.negative = True
            operation.create.type_ = client.enums.CriterionTypeEnum.KEYWORD
            operation.create.keyword.text = keyword
            # Match type mapping
            mt = (match_type or "EXACT").upper()
            if mt == "EXACT":
                operation.create.keyword.match_type = client.enums.KeywordMatchTypeEnum.EXACT
            elif mt == "PHRASE":
                operation.create.keyword.match_type = client.enums.KeywordMatchTypeEnum.PHRASE
            else:
                operation.create.keyword.match_type = client.enums.KeywordMatchTypeEnum.BROAD

            response = campaign_service.mutate_campaign_criteria(
                customer_id=customer_id,
                operations=[operation],
            )
            created = response.results[0].resource_name if response.results else None
            print(f"[apply-negative-keyword] customer={customer_id} campaign={campaign_id} keyword={keyword} resource={created}")
            return {"success": True, "resource_name": created, "campaign_id": campaign_id, "keyword": keyword, "match_type": match_type}
        except Exception as e:
            logger.error(f"Google apply negative keyword failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def pause_keyword(self, ad_group_id: str, criterion_id: str) -> Dict[str, Any]:
        """Pause an ad group criterion (keyword) by setting status to PAUSED."""
        if not self.is_valid:
            return {"success": False, "error": "Google Ads client not valid"}
        customer_id = (self.account.google_external_id or self.account.external_id or "").replace("-", "")
        if not customer_id:
            return {"success": False, "error": "No Google customer ID configured"}
        try:
            client = self.client
            agc_service = client.get_service("AdGroupCriterionService")
            resource_name = agc_service.ad_group_criterion_path(customer_id, ad_group_id, criterion_id)

            operation = client.get_type("AdGroupCriterionOperation")
            operation.update.resource_name = resource_name
            operation.update.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
            operation.update_mask.paths.append("resource_name")
            operation.update_mask.paths.append("status")

            response = agc_service.mutate_ad_group_criteria(
                customer_id=customer_id,
                operations=[operation],
            )
            return {"success": True, "resource_name": resource_name, "ad_group_id": ad_group_id, "criterion_id": criterion_id}
        except Exception as e:
            logger.error(f"Google pause keyword failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def update_campaign_budget(self, campaign_id: str, new_budget: float) -> Dict[str, Any]:
        raise NotImplementedError


def get_meta_access_token(account: Optional[Account] = None) -> Optional[str]:
    """Resolve Meta access token.
    Priority order:
    1. DB per-account credentials (if stored for account)
    2. Account-specific META_ACCESS_TOKEN if account matches META_AD_ACCOUNT_ID
    3. Global META_SYSTEM_USER_TOKEN
    4. Fallback META_ACCESS_TOKEN
    """
    if account and account.meta_credentials:
        try:
            tok = json.loads(decrypt(account.meta_credentials)).get("access_token")
            if tok:
                return tok
        except Exception as e:
            logger.error(
                f"Failed to parse Meta credentials for account {getattr(account, 'id', '?')}: "
                f"{type(e).__name__}: {e or '(no message — likely decrypt key mismatch)'}"
            )

    env_ad_account = os.environ.get("META_AD_ACCOUNT_ID", "").replace("act_", "").strip()
    user_token = os.environ.get("META_ACCESS_TOKEN")
    system_token = os.environ.get("META_SYSTEM_USER_TOKEN")

    if account:
        ext_id = (account.meta_external_id or account.external_id or "").replace("act_", "").strip()
        if env_ad_account and ext_id and ext_id == env_ad_account and user_token:
            return user_token

    if system_token:
        return system_token

    return user_token



class MetaAdsConnector(AdsConnector):
    def __init__(self, account: Account, start_date: Optional[str] = None, end_date: Optional[str] = None):
        super().__init__(account, start_date=start_date, end_date=end_date)
        self.api = None
        self.validate_credentials()

    def validate_credentials(self) -> bool:
        try:
            from facebook_business.api import FacebookAdsApi
            access_token = get_meta_access_token(self.account)
            if not access_token:
                return False
            FacebookAdsApi.init(access_token=access_token)
            self.api = FacebookAdsApi.get_default_api()
            self.is_valid = True
            return True
        except Exception as e:
            logger.error(f"Meta connector failed for account {self.account.id}: {e}")
            self.is_valid = False
            return False

    def _parse_credentials(self) -> Optional[Dict[str, Any]]:
        """Return a credentials dict compatible with older callers."""
        access_token = get_meta_access_token(self.account)
        if not access_token:
            return None
        return {"access_token": access_token}

    def _date_params(self) -> Dict[str, Any]:
        if self.start_date and self.end_date:
            return {
                "time_range": {
                    "since": self.start_date,
                    "until": self.end_date,
                }
            }
        return {"date_preset": "last_30d"}

    def _fetch_insight_aggregates(self, fields: List[str]) -> Dict[str, Any]:
        """Fetch aggregated insight metrics for the configured date range."""
        from facebook_business.adobjects.adaccount import AdAccount
        account_id = self.account.meta_external_id or self.account.external_id or ""
        if not account_id:
            return {"error": "No Meta ad account ID configured"}
        account = AdAccount(account_id)
        params = self._date_params()
        params["level"] = "account"
        insights = account.get_insights(fields=fields, params=params)
        totals = {f: 0 for f in fields}
        for insight in insights:
            for f in fields:
                val = insight.get(f, 0)
                if val is None:
                    continue
                # conversions returns a list of action objects; sum their values
                if isinstance(val, list):
                    if f == "conversions":
                        val = sum(float(v.get("value", 0) or 0) for v in val if isinstance(v, dict))
                    else:
                        val = sum(float(v or 0) for v in val)
                if f in ("spend", "cpc", "ctr", "cost_per_result", "frequency"):
                    totals[f] += float(val or 0)
                else:
                    totals[f] += int(float(val or 0))
        return totals

    def fetch_account_metrics(self) -> Dict[str, Any]:
        if not self.is_valid:
            return {"error": "Meta API not valid"}
        try:
            fields = ["spend", "clicks", "impressions", "conversions", "reach", "frequency", "cpc", "ctr", "cost_per_result"]
            totals = self._fetch_insight_aggregates(fields)
            if "error" in totals:
                return totals
            # Recompute derived metrics from aggregates
            impressions = max(int(totals.get("impressions", 0) or 0), 1)
            clicks_val = int(totals.get("clicks", 0) or 0)
            conversions_val = int(totals.get("conversions", 0) or 0)
            spend = float(totals.get("spend", 0) or 0)
            return {
                "spend": round(spend, 2),
                "clicks": clicks_val,
                "impressions": int(totals.get("impressions", 0) or 0),
                "conversions": conversions_val,
                "reach": int(totals.get("reach", 0) or 0),
                "frequency": round(float(totals.get("frequency", 0) or 0), 2),
                "cpc": round(spend / max(clicks_val, 1), 2),
                "ctr": round((clicks_val / impressions) * 100, 2),
                "cost_per_result": round(spend / max(conversions_val, 1), 2),
            }
        except Exception as e:
            logger.error(f"Meta fetch metrics failed: {e}")
            return {"error": str(e)}

    def fetch_campaigns(self) -> List[Dict[str, Any]]:
        if not self.is_valid:
            return []
        try:
            from facebook_business.adobjects.adaccount import AdAccount
            account_id = self.account.meta_external_id or self.account.external_id or ""
            if not account_id:
                return []
            account = AdAccount(account_id)
            fields = ["id", "name", "status", "daily_budget", "spend_cap"]
            campaigns = account.get_campaigns(fields=fields)
            # Pull campaign-level insights for spend/performance
            insight_fields = ["spend", "clicks", "impressions", "conversions"]
            insight_params = self._date_params()
            insight_params["level"] = "campaign"
            insight_params["breakdowns"] = []
            try:
                insights = account.get_insights(fields=insight_fields, params=insight_params)
                insight_by_campaign = {}
                for row in insights:
                    cid = row.get("campaign_id") or row.get("id")
                    if cid:
                        insight_by_campaign[cid] = {
                            "spend": float(row.get("spend", 0) or 0),
                            "clicks": int(row.get("clicks", 0) or 0),
                            "impressions": int(row.get("impressions", 0) or 0),
                            "conversions": int(row.get("conversions", 0) or 0),
                        }
            except Exception as e:
                logger.warning(f"Meta campaign insights failed for account {self.account.id}: {e}")
                insight_by_campaign = {}
            result = []
            for c in campaigns:
                cid = c.get("id")
                ins = insight_by_campaign.get(cid, {})
                spend = ins.get("spend", 0.0)
                clicks = ins.get("clicks", 0)
                impressions = ins.get("impressions", 0)
                conversions = ins.get("conversions", 0)
                ctr = round((clicks / max(impressions, 1)) * 100, 2)
                cpa = round(spend / max(conversions, 1), 2)
                result.append({
                    "id": cid,
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "budget": (int(c.get("daily_budget", 0) or 0) / 100.0),
                    "spend": spend,
                    "clicks": clicks,
                    "impressions": impressions,
                    "conversions": conversions,
                    "ctr": ctr,
                    "cpa": cpa,
                })
            return result
        except Exception as e:
            logger.error(f"Meta fetch campaigns failed: {e}")
            return []

    def fetch_daily_metrics(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return per-day spend and conversions for a date range (inclusive)."""
        if not self.is_valid:
            return []
        from facebook_business.adobjects.adaccount import AdAccount
        account_id = self.account.meta_external_id or self.account.external_id or ""
        if not account_id:
            return []
        s = start_date or self.start_date
        e = end_date or self.end_date
        if not s or not e:
            return []
        try:
            account = AdAccount(account_id)
            params = {
                "time_range": {"since": s, "until": e},
                "level": "account",
                "time_increment": "1",
            }
            fields = ["spend", "actions"]
            insights = account.get_insights(fields=fields, params=params)
            results = []
            for row in insights:
                spend = float(row.get("spend", 0) or 0)
                leads = 0
                for action in row.get("actions", []) or []:
                    if action.get("action_type") == "lead":
                        leads = int(action.get("value", 0) or 0)
                        break
                date_str = row.get("date_start")
                results.append({
                    "date": date_str,
                    "spend": spend,
                    "leads": float(leads),
                })
            return results
        except Exception as e:
            logger.error(f"Meta fetch daily metrics failed: {e}")
            return []

    def fetch_billing(self) -> Dict[str, Any]:
        """Fetch billing data from Meta Marketing API.

        Meta doesn't expose a prepaid balance. We return monthly spend as
        postpaid so the dashboard can display "USED ₹X this month".
        """
        if not self.is_valid:
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}
        try:
            from facebook_business.adobjects.adaccount import AdAccount
            account_id = self.account.meta_external_id or self.account.external_id or ""
            if not account_id:
                return {"billing_type": "unknown", "amount": None, "status": "unavailable"}
            account = AdAccount(account_id)
            # Fetch insights for this calendar month (spend only)
            today_ist = _today_ist()
            first_of_month = today_ist[:8] + "01"
            try:
                params = {
                    "time_range": {"since": first_of_month, "until": today_ist},
                    "level": "account",
                }
                insights = account.get_insights(fields=["spend"], params=params)
                monthly_spend = 0.0
                for row in insights:
                    monthly_spend += float(row.get("spend", 0) or 0)
            except Exception as e:
                logger.warning(f"Meta billing insights failed for account {self.account.id}: {e}")
                # Fallback to account-level cached spend
                monthly_spend = float(getattr(self.account, "spend", 0) or 0)

            # Build month label e.g. "July 2026"
            try:
                _dt = datetime.strptime(first_of_month, "%Y-%m-%d")
                month_label = _dt.strftime("%B %Y")
            except Exception:
                month_label = ""

            # Resolve inception date for lifetime spend query
            # Meta caps insights time_range at 37 months back (#3018) — clamp.
            inception_date = "2026-04-01"  # Default (new accounts launch)
            if self.account.id == 1 or "dsu" in self.account.name.lower():
                inception_date = "2025-11-28"  # DSU inception date
            elif self.account.id == 2 or "dsi" in self.account.name.lower():
                inception_date = "2026-01-08"  # DSI inception date
            else:
                inception_date = "2020-01-01"
            try:
                _floor = (datetime.now() - timedelta(days=37 * 30)).strftime("%Y-%m-%d")
                if inception_date < _floor:
                    inception_date = _floor
            except Exception:
                pass

            # Fetch lifetime spend from inception to today
            lifetime_spend = 0.0
            try:
                params_lifetime = {
                    "time_range": {"since": inception_date, "until": today_ist},
                    "level": "account",
                }
                insights_lifetime = account.get_insights(fields=["spend"], params=params_lifetime)
                for row in insights_lifetime:
                    lifetime_spend += float(row.get("spend", 0) or 0)
            except Exception as e:
                logger.warning(f"Meta lifetime billing insights failed for account {self.account.id}: {e}")
                lifetime_spend = monthly_spend

            if monthly_spend > 0 or lifetime_spend > 0:
                return {
                    "billing_type": "postpaid",
                    "amount": round(lifetime_spend, 2),
                    "status": "available",
                    "monthly_spend": round(monthly_spend, 2),
                    "lifetime_spend": round(lifetime_spend, 2),
                    "month_label": month_label,
                }
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}
        except Exception as e:
            logger.warning(f"Meta fetch billing failed for account {self.account.id}: {e}")
            return {"billing_type": "unknown", "amount": None, "status": "unavailable"}


def meta_system_token_configured() -> bool:
    """Return True when a global system user token or env access token is configured."""
    return bool(os.environ.get("META_SYSTEM_USER_TOKEN") or os.environ.get("META_ACCESS_TOKEN"))


def get_connector(account: Account, platform: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Optional[AdsConnector]:
    if platform == "google":
        return GoogleAdsConnector(account, start_date=start_date, end_date=end_date)
    if platform == "meta":
        return MetaAdsConnector(account, start_date=start_date, end_date=end_date)
    if account.account_type == AccountType.GOOGLE:
        return GoogleAdsConnector(account, start_date=start_date, end_date=end_date)
    if account.account_type == AccountType.META:
        return MetaAdsConnector(account, start_date=start_date, end_date=end_date)
    return None
