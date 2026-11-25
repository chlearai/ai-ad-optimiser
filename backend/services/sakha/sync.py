"""Refresh one company mirror and publish only completed syncs."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import logging
import os
from time import monotonic
from uuid import uuid4

import requests
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.db.database import SessionLocal
from backend.db.models import Account
from backend.db.sakha_models import (
    SakhaActivity, SakhaClient, SakhaDeal, SakhaDealStageEvent, SakhaIntegrationStatus,
    SakhaOwner, SakhaPerson, SakhaPipeline, SakhaRateBudget, SakhaSourceStatus, SakhaTask,
)
from backend.services.sakha.client import (
    ENDPOINTS, SNAPSHOTS, TIMESTAMPS, ReportingError, SyncDeferred, as_utc, config_for, iso, make_client,
)
from backend.services.sakha.issues import evaluate, reconcile
from backend.services.sakha.metrics import DEFAULT_TARGETS


logger = logging.getLogger("AdOptima")
DEFAULT_HOURLY_CALL_BUDGET = 90
MANTRI_SLUG = "mantri-webcity"
MANTRI_STAGES = {
    "qualified": "Lead Qualified", "visit_scheduled": "Site Visit Scheduled",
    "visit_completed": "Site Visit Completed", "won": "Booking Confirmed",
}
RECORDS = {
    "pipelines": SakhaPipeline, "users": SakhaOwner, "persons": SakhaPerson, "deals": SakhaDeal,
    "deal-stage-events": SakhaDealStageEvent, "activities": SakhaActivity, "tasks": SakhaTask,
    "connectors": SakhaSourceStatus, "integrations": SakhaIntegrationStatus,
}
FIELDS = {
    "pipelines": "id name stages",
    "users": "id name role active updated_at deleted_at",
    "persons": "id display_name created_at updated_at deleted_at source source_connector_id campaign utm_source utm_medium utm_campaign owner_user_id lifecycle_status",
    "deals": "id person_id pipeline_id stage_id status value currency lost_reason owner_user_id created_at updated_at won_at lost_at deleted_at",
    "deal-stage-events": "id deal_id from_stage_id to_stage_id changed_at changed_by_user_id",
    "activities": "id person_id deal_id type direction actor user_id outcome occurred_at updated_at deleted_at duration_seconds",
    "tasks": "id person_id deal_id owner_user_id type due_at completed_at status updated_at deleted_at",
    "connectors": "id name type status rejecting_new captured_count last_captured_at",
    "integrations": "id name status last_sync_at",
}
REQUIRED = {
    "pipelines": ("name", "stages"), "users": ("name",), "persons": ("created_at",),
    "deals": ("pipeline_id", "stage_id", "status"), "deal-stage-events": ("deal_id", "to_stage_id", "changed_at"),
    "activities": ("type", "occurred_at"), "tasks": ("status",), "connectors": ("name", "status"),
    "integrations": ("name", "status"),
}


def utcnow():
    return datetime.now(timezone.utc)


def ensure_mantri(db):
    """Link the verified, unambiguous account name used by the existing Mantri MIS."""
    client = db.query(SakhaClient).filter_by(slug=MANTRI_SLUG).first()
    if client:
        return client.id
    accounts = db.query(Account.id).filter(
        Account.is_active.is_(True), func.lower(func.trim(Account.name)) == "mantri webcity"
    ).limit(2).all()
    if len(accounts) != 1:
        logger.warning("Sakha Mantri setup needs one active Account named Mantri Webcity.")
        return None
    client = SakhaClient(account_id=accounts[0].id, slug=MANTRI_SLUG, display_name="Mantri Webcity",
                         stage_map=MANTRI_STAGES, targets=DEFAULT_TARGETS)
    db.add(client)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(SakhaClient.id).filter_by(slug=MANTRI_SLUG).scalar()
    return client.id


def read_snapshot(db, client_id, mode):
    return {key: [row.data for row in db.query(model).filter_by(sakha_client_id=client_id, data_mode=mode)]
            for key, model in RECORDS.items()}


def empty_snapshot():
    return {key: [] for key in RECORDS}


def clean_row(key, row):
    if not isinstance(row, dict):
        raise ReportingError(f"Sakhaa returned an invalid {key} record")
    data = {name: row[name] for name in FIELDS[key].split() if name in row}
    identity = data.get("id") or (data.get("name") if key == "integrations" else None)
    if not isinstance(identity, (str, int)) or not identity:
        raise ReportingError(f"Sakhaa returned a {key} record without an ID")
    data["id"] = str(identity)
    try:
        for name, value in data.items():
            if name in TIMESTAMPS and value is not None:
                data[name] = iso(value)
            elif name.endswith("_id") and value is not None:
                data[name] = str(value)
            elif name != "stages" and value is not None and not isinstance(value, (str, bool, int, float)):
                raise ValueError
        if data.get("deleted_at"):
            return {"id": data["id"], "deleted_at": data["deleted_at"]}
        if any(data.get(name) is None for name in REQUIRED[key]):
            raise ValueError
        if key == "pipelines":
            stages = [{field: stage[field] for field in ("id", "name", "order", "kind")}
                      for stage in data["stages"]]
            for stage in stages:
                stage["id"] = str(stage["id"])
                if (not isinstance(stage["name"], str) or not isinstance(stage["order"], int)
                        or stage["kind"] not in {"open", "won", "lost"}):
                    raise ValueError
            data["stages"] = stages
        if key == "connectors" and data.get("captured_count") is not None:
            data["captured_count"] = int(data["captured_count"])
    except (TypeError, ValueError, AttributeError, OverflowError, KeyError):
        raise ReportingError(f"Sakhaa returned an invalid {key} record") from None
    return data


def clean_rows(key, rows):
    if not isinstance(rows, list):
        raise ReportingError(f"Sakhaa returned an invalid {key} list")
    return [clean_row(key, row) for row in rows]


def pull(client, watermarks, progress):
    """Page into a private buffer. The first page's server time bounds each delta."""
    for endpoint in ENDPOINTS:
        if endpoint in progress["done"]:
            continue
        state = progress["pages"].setdefault(endpoint, {"rows": [], "cursor": None, "watermark": None, "seen": []})
        cursor = state["cursor"]
        while True:
            payload = client.page(endpoint, since=watermarks.get(endpoint), cursor=cursor)
            if endpoint == "health":
                state["rows"] = {key: clean_rows(key, payload.get(key)) for key in ("connectors", "integrations")}
                break
            if endpoint not in SNAPSHOTS and state["watermark"] is None:
                try:
                    first_page_time = as_utc(payload["server_time"])
                    # Overlap the boundary for timestamp precision and strict-since APIs.
                    state["watermark"] = iso(first_page_time - timedelta(seconds=1))
                except (TypeError, KeyError, ValueError, AttributeError, OverflowError):
                    raise ReportingError(f"Sakhaa omitted a valid server time for {endpoint}") from None
            rows = clean_rows(endpoint, payload.get("data"))
            next_cursor = payload.get("next_cursor") if endpoint not in SNAPSHOTS else None
            if next_cursor is not None and (
                not isinstance(next_cursor, str) or not next_cursor or next_cursor in state["seen"]
            ):
                raise ReportingError(f"Sakhaa returned an invalid cursor for {endpoint}")
            state["rows"].extend(rows)
            state["cursor"] = next_cursor
            if next_cursor is None:
                break
            state["seen"].append(next_cursor)
            cursor = next_cursor
        progress["done"].append(endpoint)


def apply_rows(db, client_id, mode, key, changes, replace):
    model = RECORDS[key]
    old = {row.sakhaa_id: row for row in db.query(model).filter_by(sakha_client_id=client_id, data_mode=mode)}
    incoming = {item["id"]: item for item in changes}
    for sakhaa_id, payload in incoming.items():
        row = old.pop(sakhaa_id, None)
        if payload.get("deleted_at"):
            if row:
                db.delete(row)
        elif row:
            row.data = payload
        else:
            db.add(model(sakha_client_id=client_id, data_mode=mode, sakhaa_id=sakhaa_id, data=payload))
    if replace:
        for row in old.values():
            db.delete(row)


def publish(db, client, mode, progress, now):
    watermarks = dict(client.live_watermarks)
    for endpoint, page in progress["pages"].items():
        if endpoint == "health":
            for key, rows in page["rows"].items():
                apply_rows(db, client.id, mode, key, rows, replace=True)
            continue
        apply_rows(db, client.id, mode, endpoint, page["rows"],
                   replace=mode == "sample" or endpoint in SNAPSHOTS or endpoint not in watermarks)
        if mode == "live" and page["watermark"]:
            watermarks[endpoint] = max(page["watermark"], watermarks.get(endpoint, ""))
    db.flush()
    if mode == "live":
        client.live_watermarks = watermarks
        client.live_progress = None
    client.last_sync_at = now
    client.last_sync_mode = mode
    client.last_sync_ok = True
    client.last_error = None


def update_budget(client_id, delay=None):
    """Use a version check so concurrent workers share the rolling request budget."""
    try:
        limit = int(os.getenv("SAKHA_HOURLY_CALL_BUDGET", str(DEFAULT_HOURLY_CALL_BUDGET)))
    except ValueError:
        limit = DEFAULT_HOURLY_CALL_BUDGET
    if limit <= 0:
        limit = DEFAULT_HOURLY_CALL_BUDGET

    while True:
        with SessionLocal() as db:
            now = utcnow()
            budget = db.get(SakhaRateBudget, client_id)
            if budget is None:
                db.add(SakhaRateBudget(sakha_client_id=client_id))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                continue
            calls = [stamp for stamp in budget.calls if stamp > (now - timedelta(hours=1)).timestamp()]
            retry_after = as_utc(budget.retry_after)
            if delay is not None:
                retry_after = max(retry_after or now, now + timedelta(seconds=delay))
            else:
                if retry_after and now < retry_after:
                    raise SyncDeferred("Sakhaa Reporting API cooldown is still active",
                                       (retry_after - now).total_seconds())
                if len(calls) >= limit:
                    raise SyncDeferred("Hourly Sakhaa request budget reached")
                calls.append(now.timestamp())
            result = db.execute(update(SakhaRateBudget).where(
                SakhaRateBudget.sakha_client_id == client_id, SakhaRateBudget.version == budget.version
            ).values(version=budget.version + 1, calls=calls, retry_after=retry_after))
            if result.rowcount:
                db.commit()
                return


def claim_client(client_id, token):
    with SessionLocal() as db:
        now = utcnow()
        result = db.execute(update(SakhaClient).where(
            SakhaClient.id == client_id,
            or_(SakhaClient.lease_until.is_(None), SakhaClient.lease_until <= now),
        ).values(lease_token=token, lease_until=now + timedelta(minutes=15)))
        db.commit()
        return bool(result.rowcount)


def sync_client(client_id, now=None):
    token = uuid4().hex
    if not claim_client(client_id, token):
        return "busy"
    outcome, error = "ok", None
    try:
        with SessionLocal() as db:
            client = db.get(SakhaClient, client_id)
            config = config_for(client.slug, client.base_url)
            mode = config.mode
            progress = client.live_progress if mode == "live" else None
            progress = progress or {"done": [], "pages": {}}
            watermarks = client.live_watermarks if mode == "live" else {}
        deadline = monotonic() + 180

        def reserve():
            if monotonic() > deadline:
                raise SyncDeferred("Sakhaa sync will continue on the next scheduled pull")
            update_budget(client_id)

        try:
            with (requests.Session() if mode == "live" else nullcontext()) as session:
                if session:
                    session.trust_env = False
                transport = make_client(config, reserve, session=session, now=now,
                                        defer_calls=lambda seconds: update_budget(client_id, seconds))
                pull(transport, watermarks, progress)
        except SyncDeferred as exc:
            outcome, error = "deferred", str(exc)
        except ReportingError as exc:
            outcome, error = "failed", str(exc)
        except Exception:
            outcome, error = "failed", "Sakha could not process the reporting response"
        with SessionLocal() as db:
            client = db.query(SakhaClient).filter_by(id=client_id, lease_token=token).with_for_update().first()
            if not client:
                return "busy"
            checked = now or utcnow()
            if outcome == "ok":
                publish(db, client, mode, progress, checked)
            elif outcome == "deferred" and mode == "live":
                client.live_progress = progress
            elif mode == "live":
                client.live_progress = None
            client.sync_attempt_mode = mode
            client.last_sync_ok = {"ok": True, "deferred": None, "failed": False}[outcome]
            client.last_error = error
            last_sync_at = client.last_sync_at if client.last_sync_mode == mode else None
            snapshot = read_snapshot(db, client_id, mode) if last_sync_at else empty_snapshot()
            reconcile(db, client_id, mode,
                      evaluate(snapshot, checked, client.stage_map, client.targets, last_sync_at,
                               client.last_sync_ok, have_snapshot=last_sync_at is not None), checked)
            client.lease_token = None
            client.lease_until = None
            db.commit()
    finally:
        with SessionLocal() as db:
            db.execute(update(SakhaClient).where(
                SakhaClient.id == client_id, SakhaClient.lease_token == token
            ).values(lease_token=None, lease_until=None))
            db.commit()
    if outcome == "failed":
        logger.warning("Sakha reporting sync failed for client %s: %s", client_id, error)
    return outcome


def sync_all():
    with SessionLocal() as db:
        ensure_mantri(db)
        client_ids = [row.id for row in db.query(SakhaClient.id).all()]
    for client_id in client_ids:
        try:
            sync_client(client_id)
        except SQLAlchemyError:
            logger.error("Sakha could not save reporting data for client %s", client_id)
