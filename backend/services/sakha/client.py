"""Read-only Reporting API transport and the offline Mantri adapter."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests


logger = logging.getLogger("AdOptima")
FIXTURES = Path(__file__).with_name("fixtures") / "mantri_webcity"
ENDPOINTS = ("pipelines", "users", "persons", "deals", "deal-stage-events", "activities", "tasks", "health")
SNAPSHOTS = {"pipelines", "health"}
TIMESTAMPS = {
    "created_at", "updated_at", "deleted_at", "won_at", "lost_at", "due_at",
    "completed_at", "changed_at", "occurred_at", "server_time", "last_captured_at", "last_sync_at",
}


class ReportingError(Exception):
    """An error message suitable for sync diagnostics and API responses."""


class SyncDeferred(ReportingError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def as_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def iso(value):
    return as_utc(value).isoformat().replace("+00:00", "Z") if value else None


@dataclass(frozen=True)
class ReportingConfig:
    requested_mode: str
    base_url: str = ""
    key_header: str = ""
    secret_header: str = ""
    key: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)

    @property
    def configured(self):
        return bool(self.key and self.secret)

    @property
    def mode(self):
        return "live" if self.requested_mode == "live" and self.configured else "sample"


def config_for(slug, base_url=None):
    prefix = "SAKHAA_" + slug.replace("-", "_").upper()
    return ReportingConfig(
        os.getenv("SAKHA_DATA_MODE", "sample").strip().lower(),
        os.getenv("SAKHAA_API_BASE", base_url or "").strip(),
        os.getenv("SAKHAA_API_KEY_HEADER", "").strip(),
        os.getenv("SAKHAA_API_SECRET_HEADER", "").strip(),
        os.getenv(prefix + "_KEY", ""),
        os.getenv(prefix + "_SECRET", ""),
    )


def retry_delay(value, attempt):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            seconds = (as_utc(parsedate_to_datetime(value)) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError, AttributeError):
            seconds = 2 ** attempt
    return max(0, seconds)


class SakhaaClient:
    data_mode = "live"

    def __init__(self, config, reserve_call, session, defer_calls=lambda seconds: None):
        try:
            parsed = urlsplit(config.base_url)
        except ValueError:
            raise ReportingError("Configure the HTTPS Sakhaa Reporting API base URL") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ReportingError("Configure the HTTPS Sakhaa Reporting API base URL")
        names = (config.key_header, config.secret_header)
        reserved = {"host", "cookie", "content-length", "transfer-encoding", "connection"}
        if (not all(re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names)
                or names[0].lower() == names[1].lower() or any(name.lower() in reserved for name in names)):
            raise ReportingError("Configure the two Sakhaa Reporting API auth header names")
        self.base_url = config.base_url.rstrip("/")
        self.headers = dict(zip(names, (config.key, config.secret)))
        self.session = session
        self.reserve_call = reserve_call
        self.defer_calls = defer_calls

    def page(self, endpoint, *, since=None, cursor=None, limit=200):
        if endpoint not in ENDPOINTS:
            raise ValueError("Unknown Sakhaa Reporting API endpoint")
        params = {}
        if endpoint not in SNAPSHOTS:
            params["limit"] = limit
            if cursor is not None:
                params["cursor"] = cursor
            if since:
                params["since" if endpoint == "deal-stage-events" else "updated_since"] = since
        for attempt in range(3):
            self.reserve_call()
            try:
                response = self.session.get(
                    f"{self.base_url}/{endpoint}", params=params, headers=self.headers,
                    timeout=(4, 10), allow_redirects=False,
                )
            except requests.RequestException:
                if attempt == 2:
                    raise ReportingError("Could not reach the Sakhaa Reporting API") from None
                time.sleep(2 ** attempt)
                continue
            status = response.status_code
            if status == 429:
                delay = max(1, retry_delay(response.headers.get("Retry-After"), attempt))
                self.defer_calls(delay)
                if attempt == 2 or delay > 5:
                    raise SyncDeferred("Sakhaa Reporting API asked us to retry later", delay)
                time.sleep(delay)
                continue
            if status in (500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if status != 200:
                raise ReportingError(f"Sakhaa Reporting API returned HTTP {status}")
            try:
                payload = response.json()
            except ValueError:
                raise ReportingError("Sakhaa Reporting API returned invalid JSON") from None
            if not isinstance(payload, dict):
                raise ReportingError("Sakhaa Reporting API returned an invalid response")
            return payload
        raise ReportingError("Sakhaa Reporting API retries exhausted")


@lru_cache(maxsize=len(ENDPOINTS))
def fixture(endpoint):
    return json.loads((FIXTURES / f"{endpoint}.json").read_text(encoding="utf-8"))


def shift_fixture(value, delta):
    if isinstance(value, list):
        return [shift_fixture(item, delta) for item in value]
    if isinstance(value, dict):
        return {
            key: iso(as_utc(item) + delta) if key in TIMESTAMPS and item else shift_fixture(item, delta)
            for key, item in value.items()
        }
    return value


class SampleSakhaaClient:
    data_mode = "sample"

    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)
        self.delta = self.now - as_utc("2026-11-25T10:00:00Z")

    def page(self, endpoint, *, since=None, cursor=None, limit=200):
        payload = shift_fixture(fixture(endpoint), self.delta)
        if endpoint in SNAPSHOTS:
            return payload
        timestamp = "changed_at" if endpoint == "deal-stage-events" else "updated_at"
        if since:
            payload["data"] = [row for row in payload["data"] if row[timestamp] > since]
        offset = int(cursor or 0)
        rows = payload["data"]
        payload["data"] = rows[offset:offset + limit]
        payload["next_cursor"] = str(offset + limit) if offset + limit < len(rows) else None
        return payload


def make_client(config, reserve_call, session=None, now=None, defer_calls=lambda seconds: None):
    if config.mode == "sample":
        if config.requested_mode == "live":
            logger.warning("Sakha live credentials are missing. Using sample data.")
        return SampleSakhaaClient(now)
    return SakhaaClient(config, reserve_call, session, defer_calls)
