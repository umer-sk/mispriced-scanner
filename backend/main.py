"""
FastAPI application — endpoints, scheduler, CORS, rate limiting.
"""
import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()

from catalyst import get_catalyst_context
from market_context import _token_age_days, get_market_context
from models import MarketContext, TradeSetup
from qqq_holdings import QQQ_TOP30
from scanner import run_all_detectors
from schwab_client import fetch_all_chains, fetch_option_chain

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="QQQ Options Scanner", version="1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — specific origins only, no wildcard
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_cache: dict = {
    "opportunities": [],      # list[TradeSetup]
    "market_context": None,   # MarketContext
    "scan_timestamp": None,   # datetime
    "symbols_scanned": 0,
}


def _is_weekday() -> bool:
    return datetime.now(ET).weekday() < 5


async def scan_all() -> None:
    """Main scan routine — called by scheduler 3× per morning."""
    if not _is_weekday():
        logger.info("Skipping scan — weekend")
        return

    logger.info("Starting full scan of %d symbols", len(QQQ_TOP30))
    t_start = time.monotonic()

    try:
        # 1. Market context (fetch QQQ chain first)
        qqq_chain = await asyncio.get_event_loop().run_in_executor(
            None, fetch_option_chain, "QQQ"
        )
        market_ctx = get_market_context(qqq_chain)
        _cache["market_context"] = market_ctx

        # 2. Fetch all 30 chains
        chains = await fetch_all_chains(QQQ_TOP30)

        # 3–5. Run detectors, construct spreads, score
        all_setups: list[TradeSetup] = []
        for chain in chains:
            if chain.stock_price == 0:
                continue
            catalyst = get_catalyst_context(chain.symbol, chain, trade_dte=35)
            setups = run_all_detectors(chain, catalyst)
            all_setups.extend(setups)

        # 6. Filter
        filtered = [
            s for s in all_setups
            if s.score >= 55 and s.rr_ratio >= 2.0 and s.liquidity_ok
        ]
        filtered.sort(key=lambda s: s.score, reverse=True)

        _cache["opportunities"] = filtered
        _cache["scan_timestamp"] = datetime.utcnow()
        _cache["symbols_scanned"] = len(QQQ_TOP30)

        elapsed = time.monotonic() - t_start
        logger.info(
            "Scan complete: %d symbols, %d signals total, %d opportunities, %.1fs",
            len(QQQ_TOP30), len(all_setups), len(filtered), elapsed,
        )

    except Exception as e:
        logger.exception("Scan failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduler — 08:00, 09:45, 11:00 AM ET on weekdays
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler(timezone=ET)
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET))
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=ET))
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=ET))


@app.on_event("startup")
async def startup():
    scheduler.start()
    logger.info("Scheduler started")
    # Trigger an immediate scan on startup if market is open
    from market_context import _is_market_open
    if _is_market_open() and _is_weekday():
        asyncio.create_task(scan_all())


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_age_seconds() -> int:
    ts = _cache.get("scan_timestamp")
    if ts is None:
        return -1
    return int((datetime.utcnow() - ts).total_seconds())


def _serialize(obj):
    """Recursively convert dataclasses/dates to JSON-safe types."""
    import dataclasses
    from datetime import date, datetime

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
@limiter.limit("10/minute")
async def health(request: Request):
    ts = _cache.get("scan_timestamp")
    from market_context import _is_market_open
    return {
        "status": "ok",
        "last_scan": ts.isoformat() if ts else None,
        "data_age_seconds": _data_age_seconds(),
        "market_open": _is_market_open(),
        "next_scan": _cache["market_context"].next_scan_time if _cache["market_context"] else None,
        "token_age_days": round(_token_age_days(), 2),
    }


@app.get("/opportunities")
@limiter.limit("10/minute")
async def get_opportunities(
    request: Request,
    min_rr: float = 2.0,
    max_debit: float = 8.0,
    min_score: int = 55,
    detector: str = "all",
):
    opps = _cache["opportunities"]

    # Apply filters
    filtered = [
        s for s in opps
        if s.rr_ratio >= min_rr
        and s.net_debit <= max_debit
        and s.score >= min_score
        and (detector == "all" or s.signal.detector == detector)
    ]

    age = _data_age_seconds()
    return JSONResponse(
        content={
            "market_context": _serialize(_cache["market_context"]),
            "opportunities": [_serialize(s) for s in filtered],
            "scan_timestamp": _cache["scan_timestamp"].isoformat() if _cache["scan_timestamp"] else None,
            "symbols_scanned": _cache["symbols_scanned"],
            "data_age_seconds": age,
        },
        headers={"X-Data-Age": str(age)},
    )


@app.get("/opportunity/{symbol}")
@limiter.limit("10/minute")
async def get_opportunity(request: Request, symbol: str):
    symbol = symbol.upper()
    matches = [s for s in _cache["opportunities"] if s.symbol == symbol]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No opportunity found for {symbol}")
    # Return the highest-scored setup for this symbol
    best = max(matches, key=lambda s: s.score)
    return JSONResponse(content=_serialize(best))
