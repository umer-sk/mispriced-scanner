"""
FastAPI application — endpoints, CORS, rate limiting.

Scans are triggered externally (see .github/workflows/scan.yml), not by an
in-process scheduler — Render's free tier stops the process when idle.
"""
import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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
from technical_scanner import BOUNCE_RR_MIN, scan_technical_setups
from schwab_client import fetch_all_chains, fetch_option_chain
from celt_scanner import scan_celt_setups
from supabase_client import load_scan_results, save_scan_results
from forward_test import mark_open_positions, snapshot_setups

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
    "last_scan_error": None,  # dict | None — set on failure, cleared on success
    "last_scan_note": None,   # dict | None — benign "nothing found", not a failure
}

# One scan at a time. The /scan* endpoints are unauthenticated and rate limited
# only per-IP, so without this a caller could start several full scans
# concurrently — each ~186 Schwab requests against a 120/min budget, each
# holding a full set of option chains on a 512MB instance.
_scan_lock = asyncio.Lock()

# A scan that fetched almost nothing is a failure, not an empty market. Below
# this fraction of chains we refuse to overwrite the cache or Supabase, because
# doing so destroys the last good snapshot and stamps it with a fresh timestamp.
MIN_CHAIN_SUCCESS_RATIO = 0.5


def _record_scan_note(scan: str, message: str) -> None:
    """A scan completed but produced nothing to store.

    This is NOT an error. CELT correctly finds zero setups whenever the market
    is not in a crash — which is almost always — and reporting that as a
    failure flips /health to "degraded" permanently and fails the scheduled
    job every day, which trains everyone to ignore both.
    """
    _cache["last_scan_note"] = {
        "scan": scan,
        "note": message,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("%s scan: %s", scan, message)


def _record_scan_error(scan: str, message: str) -> None:
    _cache["last_scan_error"] = {
        "scan": scan,
        "error": message,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    logger.error("%s scan failed: %s", scan, message)


async def _run_scan() -> None:
    """Core scan logic — no day/time guards."""

    if _scan_lock.locked():
        logger.info("Full scan already in progress — ignoring duplicate trigger")
        return

    async with _scan_lock:
        await _run_scan_inner()


async def _run_scan_inner() -> None:
    logger.info("Starting full scan of %d symbols", len(QQQ_TOP50))
    t_start = time.monotonic()

    try:
        # 1. Market context (fetch QQQ chain first)
        qqq_chain = await asyncio.get_running_loop().run_in_executor(
            None, fetch_option_chain, "QQQ"
        )
        market_ctx = get_market_context(qqq_chain)
        _cache["market_context"] = market_ctx

        # 2. Fetch technical context for all symbols (via yfinance, not Schwab)
        tech_contexts = await asyncio.get_running_loop().run_in_executor(
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

        # A wholesale fetch failure (expired token, 429 burst, network outage)
        # yields chains with stock_price == 0, which are skipped above — so
        # `filtered` comes out empty and looks exactly like a quiet market.
        # Writing that would overwrite the last good snapshot in both the cache
        # and Supabase, and stamp it with a fresh timestamp.
        if chains_ok < len(chains) * MIN_CHAIN_SUCCESS_RATIO:
            _record_scan_error(
                "full",
                f"only {chains_ok}/{len(chains)} chains fetched — refusing to "
                f"overwrite cached results with a failed scan",
            )
            return

        # One timestamp for both the cache and the persisted copy. Calling
        # now() twice made the in-memory and Supabase values differ by
        # microseconds, so last_scan appeared to CHANGE across a restart — and
        # the dashboard's scan poller treats any change as "scan finished",
        # reporting success for a scan that never ran.
        scan_ts = datetime.now(timezone.utc)
        _cache["opportunities"] = filtered
        _cache["scan_timestamp"] = scan_ts
        _cache["symbols_scanned"] = len(QQQ_TOP50)
        _cache["last_scan_error"] = None

        save_scan_results("opportunities", [_serialize(s) for s in filtered], scan_ts)

        # After the chains_ok guard, so a failed scan never records positions.
        # Both do sequential Supabase/Schwab I/O (snapshot: 1-2 round-trips per
        # setup; marking: a query, a chunked quote fetch, two writes per open
        # position) — off the event loop like every other blocking call above,
        # so /health and /opportunities don't stall and the scan lock doesn't
        # hold the loop hostage for other triggers.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, snapshot_setups, filtered, "scanner")
        await loop.run_in_executor(None, mark_open_positions)

        elapsed = time.monotonic() - t_start
        logger.info(
            "Scan complete: %d/%d chains ok, %d setups raw, %d opportunities, max_score=%d, %.1fs",
            chains_ok, len(chains), len(all_setups), len(filtered),
            max(scores) if scores else 0, elapsed,
        )

    except Exception as e:
        logger.exception("Scan failed: %s", e)
        _record_scan_error("full", repr(e))


async def _run_celt_scan() -> None:
    """Fetch closes + LEAP chains for all symbols, find CELT setups."""
    if _scan_lock.locked():
        logger.info("Another scan in progress — ignoring CELT trigger")
        return

    async with _scan_lock:
        t_start = time.monotonic()
        logger.info("Starting CELT scan of %d symbols", len(QQQ_TOP50))
        try:
            loop = asyncio.get_running_loop()
            setups = await loop.run_in_executor(None, scan_celt_setups, QQQ_TOP50)
            if not setups:
                # Same trap as the full scan: an empty result is far more often
                # a yfinance/Schwab failure than a genuine "no setups today".
                # The in-memory copy was already guarded; the Supabase save was
                # not, so a failure silently emptied the persisted copy and only
                # became visible after the next cold start.
                _record_scan_note("celt", "0 setups — market not in a crash; cached results kept")
                return
            celt_ts = datetime.now(timezone.utc)
            _cache["celt_setups"] = setups
            _cache["last_scan_note"] = None
            _cache["celt_timestamp"] = celt_ts
            _cache["last_scan_error"] = None
            save_scan_results("celt_results", [_serialize(s) for s in setups], celt_ts)
            elapsed = time.monotonic() - t_start
            logger.info("CELT scan complete: %d setups, %.1fs", len(setups), elapsed)
        except Exception as e:
            logger.exception("CELT scan failed: %s", e)
            _record_scan_error("celt", repr(e))


async def _run_technical_scan() -> None:
    """Fetch price history + option chains for all symbols, find technical setups."""
    if _scan_lock.locked():
        logger.info("Another scan in progress — ignoring technical trigger")
        return

    async with _scan_lock:
        t_start = time.monotonic()
        logger.info("Starting technical scan of %d symbols", len(QQQ_TOP50))
        try:
            loop = asyncio.get_running_loop()
            # BOUNCE_RR_MIN, not the shared 2.0: every non-bounce constructor
            # already enforces its own >= 2.0 gate internally
            # (technical_scanner.py), so no spread/long-call/long-put setup
            # can ever have rr in [BOUNCE_RR_MIN, 2.0) regardless of this
            # value — lowering it changes nothing for them. It matters only
            # for the 200W bounce, whose own calibrated gate is
            # BOUNCE_RR_MIN; passing 2.0 here would silently drop a
            # qualifying bounce setup before it ever reached the cache.
            setups = await loop.run_in_executor(
                None, scan_technical_setups, QQQ_TOP50, BOUNCE_RR_MIN, "both"
            )
            if not setups:
                _record_scan_note("technical", "0 setups — cached results kept")
                return
            _cache["technical_setups"] = setups
            _cache["last_scan_note"] = None
            tech_ts = datetime.now(timezone.utc)
            _cache["technical_timestamp"] = tech_ts
            _cache["technical_symbols_scanned"] = len(QQQ_TOP50)
            _cache["last_scan_error"] = None
            save_scan_results("technical_setups", [_serialize(s) for s in setups], tech_ts)
            await loop.run_in_executor(None, snapshot_setups, setups, "technical")
            elapsed = time.monotonic() - t_start
            logger.info("Technical scan complete: %d setups, %.1fs", len(setups), elapsed)
        except Exception as e:
            logger.exception("Technical scan failed: %s", e)
            _record_scan_error("technical", repr(e))


async def refresh_sector_analysis() -> None:
    """Refresh sector ETF data once daily. Does not require Schwab auth."""
    try:
        logger.info("Refreshing sector analysis")
        sectors = await asyncio.get_running_loop().run_in_executor(
            None, get_sector_analysis
        )
        _cache["sector_analysis"] = sectors
        _cache["sector_timestamp"] = datetime.now(timezone.utc)
        logger.info("Sector analysis updated: %d sectors", len(sectors))
    except Exception as e:
        logger.exception("Sector analysis refresh failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
# There is deliberately no in-process scheduler here. This service runs on
# Render's free tier, which stops the process after ~15 minutes of inactivity;
# an in-process cron cannot fire while the process is stopped, and a cold start
# does not back-fill missed jobs. Scans are therefore driven externally by
# .github/workflows/scan.yml, which calls the /scan* endpoints below on a
# schedule. The inbound request is what wakes the instance, so the trigger no
# longer depends on the instance already being awake.


@app.on_event("startup")
async def startup():
    # Load persisted results so cold starts serve last known data
    try:
        opps_raw, opps_ts = load_scan_results("opportunities")
        if opps_raw and opps_ts:
            _cache["opportunities"] = opps_raw   # plain dicts — _attr() handles these
            _cache["scan_timestamp"] = opps_ts
            _cache["symbols_scanned"] = len(QQQ_TOP50)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_age_seconds() -> int:
    ts = _cache.get("scan_timestamp")
    if ts is None:
        return -1
    # scan_timestamp arrives from two sources with different tz-awareness: an
    # in-process scan stores a naive UTC datetime, while a Supabase restore
    # returns an aware one (the column is timestamptz). Subtracting one from
    # the other raises TypeError, so normalise both to aware UTC first.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - ts).total_seconds())


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
    err = _cache.get("last_scan_error")
    return {
        # "ok" only means the process is up. A scan can fail without the
        # process noticing, so callers must check last_scan_error too — the
        # GitHub Actions workflow fails its job on a non-null value.
        "status": "degraded" if err else "ok",
        "last_scan": ts.isoformat() if ts else None,
        "data_age_seconds": _data_age_seconds(),
        "market_open": _is_market_open(),
        # _attr, not attribute access: after a Supabase restore cached values
        # are plain dicts, and market_context is a natural thing to persist next.
        "next_scan": _attr(_cache["market_context"], "next_scan_time"),
        "token_age_days": round(_token_age_days(), 2),
        "last_scan_stats": _cache.get("last_scan_stats"),
        "last_scan_error": err,
        "last_scan_note": _cache.get("last_scan_note"),
        "scan_in_progress": _scan_lock.locked(),
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


@app.get("/scan-sectors")
@limiter.limit("3/minute")
async def trigger_sector_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(refresh_sector_analysis)
    return JSONResponse(content={"status": "scanning", "message": "Sector refresh started. Fetch /sector-analysis in ~20s."})


@app.get("/technical-setups")
@limiter.limit("60/minute")
async def get_technical_setups(
    request: Request,
    direction: str = "both",
    # BOUNCE_RR_MIN, not 2.0 — see the comment at _run_technical_scan's call site.
    min_rr: float = BOUNCE_RR_MIN,
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


@app.get("/forward-test")
@limiter.limit("20/minute")
async def get_forward_test(request: Request):
    import ft_store
    from forward_test import aggregate
    try:
        rows = ft_store.fetch_all_positions()
        result = aggregate(rows)
        # fetch_all_positions caps at MAX_FETCH_POSITIONS rows ordered
        # entry_ts desc — once the table exceeds that, the cap silently
        # drops the oldest (fully-closed) rows the win rate depends on.
        # Surface it rather than let the aggregate look complete.
        result["rows_fetched"] = len(rows)
        result["truncated"] = len(rows) == ft_store.MAX_FETCH_POSITIONS
        return JSONResponse(content=result)
    except Exception as e:
        # aggregate() is not itself guarded (e.g. a malformed
        # realized_pnl_pct raises TypeError on comparison) — this is a
        # public route, so never let that turn into a 500.
        logger.exception("forward test: /forward-test failed: %s", e)
        result = aggregate([])
        result["rows_fetched"] = 0
        result["truncated"] = False
        return JSONResponse(content=result)


@app.get("/forward-test/positions")
@limiter.limit("20/minute")
async def get_forward_test_positions(
    request: Request,
    status: Optional[str] = None,
    tier: Optional[str] = None,
):
    import ft_store
    try:
        return JSONResponse(content={
            "positions": ft_store.fetch_all_positions(status=status, tier=tier),
        })
    except Exception as e:
        logger.exception("forward test: /forward-test/positions failed: %s", e)
        return JSONResponse(content={"positions": []})


@app.get("/scan-celt")
@limiter.limit("3/minute")
async def trigger_celt_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_celt_scan)
    return JSONResponse(content={"status": "scanning", "message": "CELT scan started. Fetch /celt-setups in ~60s."})


@app.get("/chain/{symbol}")
@limiter.limit("5/minute")
async def get_chain_debug(request: Request, symbol: str):
    """Debug: fetch chain for one symbol and return key diagnostic fields."""
    symbol = symbol.upper()
    # Restricted to the scanned universe: this endpoint does a live Schwab
    # fetch per request and caches the result by symbol, so an arbitrary
    # symbol lets an anonymous caller spend Schwab quota and grow the chain
    # cache on a 512MB instance.
    if symbol not in QQQ_TOP50 and symbol != "QQQ":
        raise HTTPException(status_code=404, detail=f"{symbol} is not in the scanned universe")
    chain = await asyncio.get_running_loop().run_in_executor(
        None, fetch_option_chain, symbol
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
