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
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()

from catalyst import get_catalyst_context
from market_context import _token_age_days, get_market_context
from models import MarketContext, SectorData, TradeSetup
from qqq_holdings import QQQ_TOP50
from scanner import run_all_detectors
from sector_analysis import get_sector_analysis
from technical_analysis import get_technical_contexts
from technical_scanner import scan_technical_setups
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
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

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
    "sector_analysis": [],    # list[SectorData]
    "sector_timestamp": None, # datetime
    "technical_setups": [],   # list[TechnicalSetup]
    "technical_timestamp": None, # datetime
    "technical_symbols_scanned": 0,  # int
}


def _is_weekday() -> bool:
    return datetime.now(ET).weekday() < 5


async def _run_scan() -> None:
    """Core scan logic — no day/time guards."""

    logger.info("Starting full scan of %d symbols", len(QQQ_TOP50))
    t_start = time.monotonic()

    try:
        # 1. Market context (fetch QQQ chain first)
        qqq_chain = await asyncio.get_event_loop().run_in_executor(
            None, fetch_option_chain, "QQQ"
        )
        market_ctx = get_market_context(qqq_chain)
        _cache["market_context"] = market_ctx

        # 2. Fetch technical context for all symbols (via yfinance, not Schwab)
        tech_contexts = await asyncio.get_event_loop().run_in_executor(
            None, get_technical_contexts, QQQ_TOP50
        )

        # 3. Fetch all 50 chains
        chains = await fetch_all_chains(QQQ_TOP50)

        # 4–6. Run detectors, construct spreads, score
        all_setups: list[TradeSetup] = []
        for chain in chains:
            if chain.stock_price == 0:
                continue
            catalyst = get_catalyst_context(chain.symbol, chain, trade_dte=35)
            tech_ctx = tech_contexts.get(chain.symbol)
            setups = run_all_detectors(chain, catalyst, technical_context=tech_ctx)
            all_setups.extend(setups)

        # 6. Filter
        filtered = [
            s for s in all_setups
            if s.score >= 55 and s.rr_ratio >= 2.0 and s.liquidity_ok
        ]
        filtered.sort(key=lambda s: s.score, reverse=True)

        _cache["opportunities"] = filtered
        _cache["scan_timestamp"] = datetime.utcnow()
        _cache["symbols_scanned"] = len(QQQ_TOP50)

        elapsed = time.monotonic() - t_start
        logger.info(
            "Scan complete: %d symbols, %d signals total, %d opportunities, %.1fs",
            len(QQQ_TOP50), len(all_setups), len(filtered), elapsed,
        )

    except Exception as e:
        logger.exception("Scan failed: %s", e)


async def _run_technical_scan() -> None:
    """Fetch price history + option chains for all symbols, find technical setups."""
    t_start = time.monotonic()
    logger.info("Starting technical scan of %d symbols", len(QQQ_TOP50))
    try:
        loop = asyncio.get_event_loop()
        setups = await loop.run_in_executor(
            None, scan_technical_setups, QQQ_TOP50, 2.0, "both"
        )
        if setups:
            _cache["technical_setups"] = setups
        _cache["technical_timestamp"] = datetime.utcnow()
        _cache["technical_symbols_scanned"] = len(QQQ_TOP50)
        elapsed = time.monotonic() - t_start
        logger.info("Technical scan complete: %d setups (cache has %d), %.1fs",
                    len(setups), len(_cache["technical_setups"]), elapsed)
    except Exception as e:
        logger.error("Technical scan failed: %s", e)


async def scan_all() -> None:
    """Scheduled scan — skips weekends."""
    if not _is_weekday():
        logger.info("Skipping scan — weekend")
        return
    await _run_scan()


async def refresh_sector_analysis() -> None:
    """Refresh sector ETF data once daily. Does not require Schwab auth."""
    if not _is_weekday():
        return
    try:
        logger.info("Refreshing sector analysis")
        sectors = await asyncio.get_event_loop().run_in_executor(
            None, get_sector_analysis
        )
        _cache["sector_analysis"] = sectors
        _cache["sector_timestamp"] = datetime.utcnow()
        logger.info("Sector analysis updated: %d sectors", len(sectors))
    except Exception as e:
        logger.exception("Sector analysis refresh failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduler — 08:00, 09:45, 11:00 AM ET on weekdays
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler(timezone=ET)
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET))
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=ET))
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=ET))
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET))
scheduler.add_job(refresh_sector_analysis, CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))


@app.on_event("startup")
async def startup():
    scheduler.start()
    logger.info("Scheduler started")
    # Trigger an immediate scan on startup if market is open
    from market_context import _is_market_open
    if _is_market_open() and _is_weekday():
        asyncio.create_task(scan_all())
    asyncio.create_task(refresh_sector_analysis())


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


@app.get("/sector-analysis")
@limiter.limit("10/minute")
async def get_sector_analysis_endpoint(request: Request):
    return JSONResponse(content={
        "sectors": [_serialize(s) for s in _cache["sector_analysis"]],
        "as_of": _cache["sector_timestamp"].isoformat() if _cache["sector_timestamp"] else None,
    })


@app.get("/technical-setups")
@limiter.limit("60/minute")
async def get_technical_setups(
    request: Request,
    direction: str = "both",
    min_rr: float = 2.0,
    sort: str = "rr",
):
    setups = _cache["technical_setups"]
    ts = _cache["technical_timestamp"]

    filtered = [
        s for s in setups
        if s.rr_ratio >= min_rr
        and (direction == "both" or s.direction == direction)
    ]

    if sort == "pop":
        filtered.sort(key=lambda s: s.probability_of_profit, reverse=True)
    else:
        filtered.sort(key=lambda s: s.rr_ratio, reverse=True)

    return JSONResponse(content={
        "setups": [_serialize(s) for s in filtered],
        "scan_timestamp": ts.isoformat() if ts else None,
        "symbols_scanned": _cache.get("technical_symbols_scanned", 0),
    })


@app.get("/scan-setups")
@limiter.limit("6/minute")
async def trigger_technical_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_technical_scan)
    return JSONResponse(content={"status": "scanning", "message": "Technical scan started. Fetch /technical-setups in ~45s."})


@app.get("/opportunities")
@limiter.limit("10/minute")
async def get_opportunities(
    request: Request,
    min_rr: float = 2.0,
    max_debit: float = 8.0,
    min_score: int = 55,
    detector: str = "all",
    direction: str = "both",
):
    opps = _cache["opportunities"]

    # Apply filters
    filtered = [
        s for s in opps
        if s.rr_ratio >= min_rr
        and s.net_debit <= max_debit
        and s.score >= min_score
        and (detector == "all" or s.signal.detector == detector)
        and (
            direction == "both"
            or (direction == "bullish" and s.structure in ("bull_call_spread", "calendar", "long_call"))
            or (direction == "bearish" and s.structure == "bear_put_spread")
        )
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


@app.get("/scan")
@limiter.limit("5/minute")
async def trigger_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_scan)
    return {"status": "scan started"}


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
