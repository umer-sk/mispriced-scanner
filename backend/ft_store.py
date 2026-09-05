"""
Supabase persistence for the forward test.

Every function degrades to a no-op when Supabase is unconfigured or erroring:
the forward test must never block or fail a scan.
"""
import logging
from datetime import date, datetime
from typing import Any

from supabase_client import get_client

logger = logging.getLogger(__name__)

POSITIONS = "ft_positions"
MARKS = "ft_marks"


def _encode(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(d: dict) -> dict:
    return {k: _encode(v) for k, v in d.items()}


def insert_position(row: dict) -> str | None:
    db = get_client()
    if db is None:
        return None
    try:
        resp = db.table(POSITIONS).insert(_row(row)).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as e:
        logger.error("forward test: insert_position failed: %s", e)
        return None


def touch_position(position_id: str, now: datetime) -> None:
    db = get_client()
    if db is None:
        return
    try:
        current = db.table(POSITIONS).select("times_seen").eq("id", position_id).execute()
        seen = (current.data[0]["times_seen"] if current.data else 0) + 1
        db.table(POSITIONS).update(
            {"times_seen": seen, "last_seen_ts": now.isoformat()}
        ).eq("id", position_id).execute()
    except Exception as e:
        logger.error("forward test: touch_position failed: %s", e)


def find_open_by_dedup(keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    db = get_client()
    if db is None:
        return {}
    try:
        resp = (db.table(POSITIONS).select("*")
                  .in_("dedup_key", keys).is_("closed_ts", "null").execute())
        return {r["dedup_key"]: r for r in (resp.data or [])}
    except Exception as e:
        logger.error("forward test: find_open_by_dedup failed: %s", e)
        return {}


def list_open_positions() -> list[dict]:
    db = get_client()
    if db is None:
        return []
    try:
        resp = db.table(POSITIONS).select("*").is_("closed_ts", "null").execute()
        return resp.data or []
    except Exception as e:
        logger.error("forward test: list_open_positions failed: %s", e)
        return []


def insert_mark(position_id: str, ts: datetime, spread_mid: float,
                pnl: float, stock_price: float | None) -> None:
    db = get_client()
    if db is None:
        return
    try:
        db.table(MARKS).upsert({
            "position_id": position_id, "ts": ts.isoformat(),
            "spread_mid": spread_mid, "pnl_pct": pnl, "stock_price": stock_price,
        }).execute()
    except Exception as e:
        logger.error("forward test: insert_mark failed: %s", e)


def update_position(position_id: str, fields: dict) -> None:
    db = get_client()
    if db is None:
        return
    try:
        db.table(POSITIONS).update(_row(fields)).eq("id", position_id).execute()
    except Exception as e:
        logger.error("forward test: update_position failed: %s", e)


def fetch_all_positions(status: str | None = None,
                        tier: str | None = None) -> list[dict]:
    db = get_client()
    if db is None:
        return []
    try:
        q = db.table(POSITIONS).select("*")
        if status:
            q = q.eq("status", status)
        if tier:
            q = q.eq("tier", tier)
        return q.order("entry_ts", desc=True).limit(1000).execute().data or []
    except Exception as e:
        logger.error("forward test: fetch_all_positions failed: %s", e)
        return []
