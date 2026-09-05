"""
Supabase persistence for scan results.
Gracefully no-ops if SUPABASE_URL / SUPABASE_KEY are not set (local dev).
"""
import logging
import os
import time
from datetime import datetime, timezone

from supabase import create_client, Client

logger = logging.getLogger(__name__)

_client: Client | None = None

# If client construction raises (e.g. malformed URL/key), don't hammer
# create_client() on every call from a hot scan loop — but don't cache the
# failure forever either, so a fixed configuration can recover without a
# process restart. A short cooldown gets both: at most one error log and one
# retry attempt per window.
_last_failure_monotonic: float | None = None
_FAILURE_RETRY_SECONDS = 60


def _get_client() -> Client | None:
    global _client, _last_failure_monotonic
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not (url and key):
        return None

    if (_last_failure_monotonic is not None
            and time.monotonic() - _last_failure_monotonic < _FAILURE_RETRY_SECONDS):
        return None

    try:
        _client = create_client(url, key)
        _last_failure_monotonic = None
    except Exception as e:
        logger.error("Supabase client construction failed: %s", e)
        _last_failure_monotonic = time.monotonic()
        return None
    return _client


def get_client() -> Client | None:
    """Public accessor. Returns None when Supabase is not configured, in which
    case every caller must degrade gracefully rather than raise."""
    return _get_client()


def save_scan_results(cache_key: str, results: list[dict], timestamp: datetime) -> None:
    db = _get_client()
    if db is None:
        return
    try:
        db.table("scanner_cache").upsert({
            "cache_key": cache_key,
            "scan_timestamp": timestamp.isoformat(),
            "data": results,
            "updated_at": datetime.now(timezone.utc).isoformat(),
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
