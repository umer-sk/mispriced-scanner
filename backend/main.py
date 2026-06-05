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
from celt_scanner import scan_celt_setups
from supabase_client import load_scan_results, save_scan_results

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
    "last_scan_stats": None,  # dict with diagnostic counts from last scan
    "celt_setups": [],        # list[CeltSetup]
    "celt_timestamp": None,   # datetime
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
        chains_ok = 0
        for chain in chains:
            if chain.stock_price == 0:
                continue
            chains_ok += 1
            catalyst = get_catalyst_context(chain.symbol, chain, trade_dte=35)
            tech_ctx = tech_contexts.get(chain.symbol)
            setups = run_all_detectors(chain, catalyst, technical_context=tech_ctx)
            all_setups.extend(setups)

        # 6. Filter
        filtered = [
            s for s in all_setups
            if s.score >= 45 and s.rr_ratio >= 2.0 and s.liquidity_ok
        ]
        filtered.sort(key=lambda s: s.score, reverse=True)

        scores = [s.score for s in all_setups]
        _cache["last_scan_stats"] = {
            "chains_fetched": chains_ok,
            "chains_total": len(chains),
            "setups_raw": len(all_setups),
            "setups_passing": len(filtered),
            "max_score": max(scores) if scores else 0,
            "score_distribution": {
                ">=55": sum(1 for s in scores if s >= 55),
                "40-54": sum(1 for s in scores if 40 <= s < 55),
                "<40":   sum(1 for s in scores if s < 40),
            },
            "by_detector": {
                det: sum(1 for s in all_setups if s.signal.detector == det)
                for det in ["iv_rank", "skew", "parity", "term", "move",
                            "put_iv_rank", "skew_inversion", "put_parity", "downside_move"]
            },
        }

        _cache["opportunities"] = filtered
        _cache["scan_timestamp"] = datetime.utcnow()
        _cache["symbols_scanned"] = len(QQQ_TOP50)

        save_scan_results("opportunities", [_serialize(s) for s in filtered], datetime.utcnow())

        elapsed = time.monotonic() - t_start
        logger.info(
            "Scan complete: %d/%d chains ok, %d setups raw, %d opportunities, max_score=%d, %.1fs",
            chains_ok, len(chains), len(all_setups), len(filtered),
            max(scores) if scores else 0, elapsed,
        )

    except Exception as e:
        logger.exception("Scan failed: %s", e)


async def _run_celt_scan() -> None:
    """Fetch closes + LEAP chains for all symbols, find CELT setups."""
    t_start = time.monotonic()
    logger.info("Starting CELT scan of %d symbols", len(QQQ_TOP50))
    try:
        loop = asyncio.get_event_loop()
        setups = await loop.run_in_executor(None, scan_celt_setups, QQQ_TOP50)
        if setups:
            _cache["celt_setups"] = setups
        _cache["celt_timestamp"] = datetime.utcnow()
        save_scan_results("celt_results", [_serialize(s) for s in setups], datetime.utcnow())
        elapsed = time.monotonic() - t_start
        logger.info("CELT scan complete: %d setups, %.1fs", len(setups), elapsed)
    except Exception as e:
        logger.error("CELT scan failed: %s", e)


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
        save_scan_results("technical_setups", [_serialize(s) for s in _cache["technical_setups"]], datetime.utcnow())
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
scheduler.add_job(_run_celt_scan, CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=ET))


@app.on_event("startup")
async def startup():
    scheduler.start()
    logger.info("Scheduler started")

    # Load persisted results so cold starts serve last known data
    try:
        opps_raw, opps_ts = load_scan_results("opportunities")
        if opps_raw and opps_ts:
            _cache["opportunities"] = opps_raw   # plain dicts — _attr() handles these
            _cache["scan_timestamp"] = opps_ts
            _cache["symbols_scanned"] = len(opps_raw)
            logger.info("Loaded %d opportunities from Supabase (as_of=%s)", len(opps_raw), opps_ts)
    except Exception as e:
        logger.warning("Could not load opportunities from Supabase: %s", e)

    try:
        tech_raw, tech_ts = load_scan_results("technical_setups")
        if tech_raw and tech_ts:
            _cache["technical_setups"] = tech_raw
            _cache["technical_timestamp"] = tech_ts
            _cache["technical_symbols_scanned"] = len(QQQ_TOP50)
            logger.info("Loaded %d technical setups from Supabase (as_of=%s)", len(tech_raw), tech_ts)
    except Exception as e:
        logger.warning("Could not load technical setups from Supabase: %s", e)

    try:
        celt_raw, celt_ts = load_scan_results("celt_results")
        if celt_raw and celt_ts:
            _cache["celt_setups"] = celt_raw     # plain dicts — _attr() handles these
            _cache["celt_timestamp"] = celt_ts
            logger.info("Loaded %d CELT setups from Supabase (as_of=%s)", len(celt_raw), celt_ts)
    except Exception as e:
        logger.warning("Could not load CELT results from Supabase: %s", e)

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
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _attr(obj, *keys, default=None):
    """Read nested attribute from either a dataclass or a plain dict (Supabase-loaded)."""
    for key in keys:
        if obj is None:
            return default
        obj = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return obj


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/scan-now")
@limiter.limit("2/minute")
async def trigger_scan(request: Request, background_tasks: BackgroundTasks):
    """Manually trigger a full scan regardless of day/time."""
    background_tasks.add_task(_run_scan)
    return {"status": "scan started"}


@app.get("/health")
@limiter.limit("30/minute")
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
        "last_scan_stats": _cache.get("last_scan_stats"),
        "celt_last_scan": _cache["celt_timestamp"].isoformat() if _cache["celt_timestamp"] else None,
        "celt_setups_count": len(_cache["celt_setups"]),
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
        if _attr(s, 'rr_ratio', default=0) >= min_rr
        and (direction == "both" or _attr(s, 'direction', default='') == direction)
    ]

    if sort == "pop":
        filtered.sort(key=lambda s: _attr(s, 'probability_of_profit', default=0), reverse=True)
    else:
        filtered.sort(key=lambda s: _attr(s, 'rr_ratio', default=0), reverse=True)

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
        if _attr(s, 'rr_ratio', default=0) >= min_rr
        and _attr(s, 'net_debit', default=999) <= max_debit
        and _attr(s, 'score', default=0) >= min_score
        and (detector == "all" or _attr(s, 'signal', 'detector', default='') == detector)
        and (
            direction == "both"
            or (direction == "bullish" and _attr(s, 'structure', default='') in ("bull_call_spread", "calendar", "long_call"))
            or (direction == "bearish" and _attr(s, 'structure', default='') == "bear_put_spread")
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
    matches = [s for s in _cache["opportunities"] if _attr(s, 'symbol', default='') == symbol]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No opportunity found for {symbol}")
    best = max(matches, key=lambda s: _attr(s, 'score', default=0))
    return JSONResponse(content=_serialize(best))


@app.get("/celt-setups")
@limiter.limit("10/minute")
async def get_celt_setups(
    request: Request,
    min_score: float = 2.2,
    sort: str = "score",
):
    setups = _cache["celt_setups"]
    ts = _cache["celt_timestamp"]

    filtered = [s for s in setups if _attr(s, 'signal_score', default=0) >= min_score]
    if sort == "drawdown":
        filtered.sort(key=lambda s: _attr(s, 'drawdown_pct', default=0), reverse=True)
    elif sort == "ivrank":
        filtered.sort(key=lambda s: _attr(s, 'iv_rank', default=0), reverse=True)
    else:
        filtered.sort(key=lambda s: _attr(s, 'signal_score', default=0), reverse=True)

    return JSONResponse(content={
        "setups": [_serialize(s) for s in filtered],
        "scan_timestamp": ts.isoformat() if ts else None,
        "symbols_scanned": len(QQQ_TOP50),
    })


@app.get("/scan-celt")
@limiter.limit("3/minute")
async def trigger_celt_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_celt_scan)
    return JSONResponse(content={"status": "scanning", "message": "CELT scan started. Fetch /celt-setups in ~60s."})


@app.get("/chain/{symbol}")
@limiter.limit("5/minute")
async def get_chain_debug(request: Request, symbol: str):
    """Debug: fetch chain for one symbol and return key diagnostic fields."""
    chain = await asyncio.get_event_loop().run_in_executor(
        None, fetch_option_chain, symbol.upper()
    )

    def _fmt(c):
        return {"strike": c.strike, "dte": c.dte, "bid": c.bid, "ask": c.ask,
                "iv": round(c.iv, 4), "delta": round(c.delta, 3), "oi": c.open_interest}

    atm_calls = sorted(
        [c for c in chain.calls if c.bid > 0 or c.iv > 0],
        key=lambda c: abs(c.delta - 0.5)
    )[:5]
    near_10d_puts = sorted(
        [p for p in chain.puts if p.bid > 0 or p.iv > 0],
        key=lambda p: abs(abs(p.delta) - 0.1)
    )[:5]

    return JSONResponse(content={
        "symbol": chain.symbol,
        "stock_price": chain.stock_price,
        "iv30": chain.iv30,
        "hv30": chain.hv30,
        "iv_rank": chain.iv_rank,
        "is_stale": chain.is_stale,
        "calls_total": len(chain.calls),
        "puts_total": len(chain.puts),
        "calls_with_iv": sum(1 for c in chain.calls if c.iv > 0),
        "calls_with_bid": sum(1 for c in chain.calls if c.bid > 0),
        "puts_with_iv": sum(1 for p in chain.puts if p.iv > 0),
        "puts_with_bid": sum(1 for p in chain.puts if p.bid > 0),
        "atm_calls_sample": [_fmt(c) for c in atm_calls],
        "near_10d_puts_sample": [_fmt(p) for p in near_10d_puts],
    })
