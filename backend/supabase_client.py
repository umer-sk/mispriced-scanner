"""
Supabase persistence for scan results.
Gracefully no-ops if SUPABASE_URL / SUPABASE_KEY are not set (local dev).
"""
import logging
import os
from datetime import datetime

from supabase import create_client, Client

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_client() -> Client | None:
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if url and key:
            _client = create_client(url, key)
    return _client


def save_scan_results(cache_key: str, results: list[dict], timestamp: datetime) -> None:
    db = _get_client()
    if db is None:
        return
    try:
        db.table("scanner_cache").upsert({
            "cache_key": cache_key,
            "scan_timestamp": timestamp.isoformat(),
            "data": results,
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
        logger.info("Saved %d results to Supabase (%s)", len(results), cache_key)
    except Exception as e:
        logger.error("Supabase save failed (%s): %s", cache_key, e)


def load_scan_results(cache_key: str) -> tuple[list[dict], datetime | None]:
    db = _get_client()
    if db is None:
        return [], None
    try:
        resp = db.table("scanner_cache").select("*").eq("cache_key", cache_key).execute()
        if resp.data:
            row = resp.data[0]
            return row["data"], datetime.fromisoformat(row["scan_timestamp"])
    except Exception as e:
        logger.error("Supabase load failed (%s): %s", cache_key, e)
    return [], None
