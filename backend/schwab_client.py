"""
Schwab API authentication and data fetching.
All credentials come from environment variables — no hardcoded secrets.
"""
import asyncio
import logging
import math
import os
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import schwab
from dotenv import load_dotenv

from models import OptionChainData, OptionContract

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SCHWAB_APP_KEY = os.environ["SCHWAB_APP_KEY"]
SCHWAB_APP_SECRET = os.environ["SCHWAB_APP_SECRET"]
SCHWAB_CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
SCHWAB_TOKEN_PATH = os.environ.get("SCHWAB_TOKEN_PATH", "./token.json")


def _resolve_token_path() -> str:
    """
    Render mounts secret files at /etc/secrets/ as read-only.
    schwab-py writes back a refreshed token after each auth call, so we need
    a writable copy. Copy once to /tmp on startup if the configured path is
    read-only.
    """
    if os.path.exists(SCHWAB_TOKEN_PATH) and not os.access(SCHWAB_TOKEN_PATH, os.W_OK):
        writable = "/tmp/schwab_token.json"
        shutil.copy2(SCHWAB_TOKEN_PATH, writable)
        logger.info("Token at %s is read-only; copied to %s", SCHWAB_TOKEN_PATH, writable)
        return writable
    return SCHWAB_TOKEN_PATH


_EFFECTIVE_TOKEN_PATH = _resolve_token_path()

# In-process fallback cache: last good chain per (symbol, days_out,
# strike_count), served when a live fetch fails. Keyed on days_out as well
# as symbol because CELT fetches with days_out=730 while everything else
# uses 105 — sharing a key let a cached two-year LEAP chain be served to the
# full scan, which then built nominally 35-DTE setups out of contracts
# expiring years later. strike_count joined the key for the same reason:
# CELT also requests a wider strike_count than the default, so a chain
# fetched for one strike_count must not be silently served in place of one
# fetched for another.
#
# Bounded: this used to be an unbounded dict, which made the `del chain` and
# gc.collect() mitigations in technical_scanner.py no-ops (the cache still held
# every chain ever fetched) and let /chain/{symbol} grow it without limit.
_CHAIN_CACHE_MAX = 128
_last_chain_cache: "OrderedDict[tuple[str, int, int], OptionChainData]" = OrderedDict()


def _cache_chain(key: tuple[str, int, int], chain: OptionChainData) -> None:
    _last_chain_cache[key] = chain
    _last_chain_cache.move_to_end(key)
    while len(_last_chain_cache) > _CHAIN_CACHE_MAX:
        _last_chain_cache.popitem(last=False)

# Singleton Schwab client — each client_from_token_file creates a new httpx.Client
# with its own SSL context (~5 MB). Creating one per symbol (95 symbols = ~475 MB)
# causes OOM on Render's 512 MB free tier. Reuse one client for the process lifetime.
_schwab_client: Optional[schwab.client.Client] = None
_client_lock = threading.Lock()


def _get_client() -> schwab.client.Client:
    global _schwab_client
    if _schwab_client is None:
        with _client_lock:
            if _schwab_client is None:
                if not os.path.exists(_EFFECTIVE_TOKEN_PATH):
                    raise RuntimeError(
                        f"ERROR: token.json not found at {_EFFECTIVE_TOKEN_PATH}. "
                        "Run auth_setup.py locally and upload token.json to Render Secret Files."
                    )
                _schwab_client = schwab.auth.client_from_token_file(
                    token_path=_EFFECTIVE_TOKEN_PATH,
                    api_key=SCHWAB_APP_KEY,
                    app_secret=SCHWAB_APP_SECRET,
                )
    return _schwab_client


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _parse_contracts(
    raw_map: dict,
    underlying: str | None = None,
    is_put: bool | None = None,
) -> list[OptionContract]:
    """Parse Schwab option chain map into OptionContract list.
    Schwab format: {expiry_str:days -> {strike_str -> [contracts]}}
    """
    contracts = []
    for exp_str, strike_map in raw_map.items():
        try:
            exp_date = datetime.strptime(exp_str.split(":")[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        for strike_str, contract_list in strike_map.items():
            try:
                strike = float(strike_str)
            except ValueError:
                continue
            for c in contract_list:

                dte = (exp_date - date.today()).days
                if dte < 0:
                    continue

                bid = _safe_float(c.get("bid"))
                ask = _safe_float(c.get("ask"))
                mid = round((bid + ask) / 2, 2) if (bid + ask) > 0 else 0.0

                iv_raw = _safe_float(c.get("volatility"))
                # Schwab sends the OCC symbol; prefer it. Only construct one as
                # a fallback, and only when we know the root and the side —
                # never guess, because a wrong symbol quotes a different
                # company's option and the error would be invisible.
                occ_symbol = str(c.get("symbol") or "")
                if not occ_symbol and underlying and is_put is not None:
                    from occ import build_occ
                    try:
                        occ_symbol = build_occ(underlying, exp_date, is_put, strike)
                    except ValueError:
                        occ_symbol = ""
                contracts.append(OptionContract(
                    strike=strike,
                    expiry=exp_date,
                    dte=dte,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    last=_safe_float(c.get("last")),
                    volume=_safe_int(c.get("totalVolume")),
                    open_interest=_safe_int(c.get("openInterest")),
                    iv=iv_raw / 100.0 if iv_raw > 1.0 else iv_raw,
                    delta=_safe_float(c.get("delta")),
                    gamma=_safe_float(c.get("gamma")),
                    theta=_safe_float(c.get("theta")),
                    vega=_safe_float(c.get("vega")),
                    theoretical_value=_safe_float(c.get("theoreticalOptionValue")),
                    in_the_money=bool(c.get("inTheMoney", False)),
                    occ_symbol=occ_symbol,
                ))
    return contracts


def _compute_hv30(prices: list[float]) -> float:
    """Compute annualized 30-day historical volatility from daily close prices."""
    if len(prices) < 2:
        return 0.0
    log_returns = [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i - 1] > 0 and prices[i] > 0
    ]
    if len(log_returns) < 2:
        return 0.0
    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _compute_iv_rank(iv30: float, closes: list[float]) -> tuple[float, float]:
    """
    Compute IV rank and IV percentile stateless — no in-memory history needed.

    Compares current IV30 against the distribution of 30-day realized HV values
    computed from the past year of daily closes. Works correctly on fresh restarts.

    iv30 must be in decimal form (e.g. 0.29); caller is responsible for normalisation.
    Guard handles legacy whole-number values defensively.

    NOTE the guard is lossy above 1.0: a genuine 120% IV (1.2) is indistinguishable
    from a legacy "1.2%" and gets divided by 100. That is harmless for a blended
    30-day IV, which effectively never exceeds 100%, but NOT for a front-month IV
    in a crash — which is exactly when it does. Callers holding a value they know
    is already decimal must use iv_rank_from_decimal() directly.
    """
    return iv_rank_from_decimal(
        iv30 / 100.0 if iv30 > 1.0 else iv30, closes
    )


def iv_rank_from_decimal(iv_dec: float, closes: list[float]) -> tuple[float, float]:
    """Rank an already-decimal IV against the realised-HV30 distribution.

    No normalisation heuristic — the caller has asserted the unit, so a 150% IV
    (1.5) ranks as 150%, not as 1.5%.
    """
    if len(closes) < 32:
        return 50.0, 50.0

    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(log_returns) < 30:
        return 50.0, 50.0

    hv_series: list[float] = []
    for i in range(30, len(log_returns) + 1):
        window = log_returns[i - 30:i]
        n = len(window)
        mean = sum(window) / n
        variance = sum((r - mean) ** 2 for r in window) / (n - 1) if n > 1 else 0.0
        hv_series.append(math.sqrt(variance) * math.sqrt(252))

    if not hv_series:
        return 50.0, 50.0

    hv_min, hv_max = min(hv_series), max(hv_series)
    if hv_max == hv_min:
        return 50.0, 50.0

    iv_rank = (iv_dec - hv_min) / (hv_max - hv_min) * 100
    iv_rank = round(max(0.0, min(100.0, iv_rank)), 1)
    iv_percentile = round(sum(1 for v in hv_series if v < iv_dec) / len(hv_series) * 100, 1)
    return iv_rank, iv_percentile


def fetch_option_chain(symbol: str, days_out: int = 105, strike_count: int = 20) -> OptionChainData:
    """
    Fetch full option chain for a symbol.
    On API failure, returns last cached result with is_stale=True.
    Never raises — caller always gets an OptionChainData back.

    strike_count: default 20 (ATM-centred) suits every caller except CELT,
    which requests a wider count — a crashed name's older/higher-struck
    puts (needed for its P/C OI ratio read) fall outside a narrow window,
    and a wider window also gives more strike choices for CELT's deep-ITM
    selection on names with tight strike spacing.
    """
    client = _get_client()
    today = date.today()
    to_date = today + timedelta(days=days_out)

    try:
        # Fetch option chain
        resp = client.get_option_chain(
            symbol,
            contract_type=schwab.client.Client.Options.ContractType.ALL,
            strike_count=strike_count,
            include_underlying_quote=True,
            strategy=schwab.client.Client.Options.Strategy.SINGLE,
            from_date=today,
            to_date=to_date,
        )
        resp.raise_for_status()
        data = resp.json()

        underlying = data.get("underlying", {})
        stock_price = _safe_float(underlying.get("last") or underlying.get("mark") or data.get("underlyingPrice"))

        # ATM IV30: derive from near-ATM call IV (per-symbol; top-level data.volatility
        # returns a market-wide value that is the same for all symbols)
        atm_calls = []
        call_map = data.get("callExpDateMap", {})
        for _, strike_map in call_map.items():
            for strike_str, contracts in strike_map.items():
                try:
                    if abs(float(strike_str) - stock_price) < stock_price * 0.03:
                        for c in contracts:
                            iv = _safe_float(c.get("volatility"))
                            if iv > 0:
                                atm_calls.append(iv)
                except (ValueError, TypeError):
                    continue
        if atm_calls:
            iv30_raw = sum(atm_calls) / len(atm_calls)
        else:
            iv30_raw = _safe_float(underlying.get("thirtyDayVolatility") or data.get("volatility"))

        # Schwab returns per-contract volatility as whole-number percent (e.g. 29.5).
        # Normalise to decimal so chain.iv30 is consistent with chain.hv30.
        iv30 = iv30_raw / 100.0 if iv30_raw > 1.0 else iv30_raw

        # Fetch 1 year of daily closes for HV30 + IV rank
        hist_resp = client.get_price_history(
            symbol,
            period_type=schwab.client.Client.PriceHistory.PeriodType.YEAR,
            period=schwab.client.Client.PriceHistory.Period.ONE_YEAR,
            frequency_type=schwab.client.Client.PriceHistory.FrequencyType.DAILY,
            frequency=schwab.client.Client.PriceHistory.Frequency.DAILY,
        )
        hist_resp.raise_for_status()
        hist_data = hist_resp.json()
        candles = hist_data.get("candles", [])
        closes = [c["close"] for c in candles if "close" in c]
        hv30 = _compute_hv30(closes[-31:])
        iv_rank, iv_percentile = _compute_iv_rank(iv30, closes)

        calls = _parse_contracts(data.get("callExpDateMap", {}), underlying=symbol, is_put=False)
        puts = _parse_contracts(data.get("putExpDateMap", {}), underlying=symbol, is_put=True)

        chain = OptionChainData(
            symbol=symbol,
            stock_price=stock_price,
            iv30=iv30,
            hv30=hv30,
            iv_rank=iv_rank,
            iv_percentile=iv_percentile,
            timestamp=datetime.now(timezone.utc),
            calls=calls,
            puts=puts,
            is_stale=False,
        )
        _cache_chain((symbol, days_out, strike_count), chain)
        logger.info(f"Fetched chain for {symbol}: stock=${stock_price:.2f} IV30={iv30:.1%} HV30={hv30:.1%} IVR={iv_rank:.0f}")
        return chain

    except Exception as e:
        logger.error(f"Failed to fetch chain for {symbol}: {e}")
        cached = _last_chain_cache.get((symbol, days_out, strike_count))
        if cached is not None:
            # Copy rather than mutating the cached object in place: the same
            # instance is handed to every caller, so flipping is_stale on it
            # permanently marks the cached copy.
            stale = replace(cached, is_stale=True)
            return stale
        # Return empty chain so scanner doesn't crash
        return OptionChainData(
            symbol=symbol,
            stock_price=0.0,
            iv30=0.0,
            hv30=0.0,
            iv_rank=50.0,
            iv_percentile=50.0,
            timestamp=datetime.now(timezone.utc),
            calls=[],
            puts=[],
            is_stale=True,
        )


_BATCH_SIZE = 5

# fetch_option_chain makes TWO Schwab calls per symbol (get_option_chain +
# get_price_history), so a 93-symbol scan is ~186 requests. Schwab's limit is
# 120/min. At the old 2s pause that ran ~185 req/min, and a 429 burst mid-scan
# does not degrade gracefully — failed chains come back with stock_price 0 and
# look like an empty market. Pace the batches to stay under the limit with room
# to spare rather than relying on the guard in main.py to catch the fallout.
_CALLS_PER_SYMBOL = 2
_SCHWAB_LIMIT_PER_MIN = 120
_TARGET_UTILISATION = 0.75   # aim for ~90 req/min

# Seconds each batch must occupy to hold the target rate.
_BATCH_MIN_SECONDS = (
    _BATCH_SIZE * _CALLS_PER_SYMBOL
) / (_SCHWAB_LIMIT_PER_MIN * _TARGET_UTILISATION) * 60


async def fetch_all_chains(tickers: list[str]) -> list[OptionChainData]:
    """
    Fetch all tickers, pacing batches to stay within Schwab's 120 req/min.

    Batches of _BATCH_SIZE keep concurrent Schwab responses in memory low on
    constrained hosts; the pacing sleep is computed from actual elapsed time so
    slow batches don't get an additional fixed penalty.
    """
    results: list[OptionChainData] = []
    batches = [tickers[i:i + _BATCH_SIZE] for i in range(0, len(tickers), _BATCH_SIZE)]

    for i, batch in enumerate(batches):
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        batch_results = await asyncio.gather(
            *[loop.run_in_executor(None, fetch_option_chain, sym) for sym in batch]
        )
        results.extend(batch_results)
        elapsed = time.monotonic() - t0
        logger.info(
            "Batch %d/%d complete (%d symbols, %.1fs)", i + 1, len(batches), len(batch), elapsed
        )

        if i < len(batches) - 1:
            # Only sleep for the time the batch didn't already consume.
            await asyncio.sleep(max(0.0, _BATCH_MIN_SECONDS - elapsed))

    return results


def _looks_like_occ_symbol(symbol: str) -> bool:
    """Cheap shape check before spending a quote slot on it.

    An OCC symbol is a fixed 21 characters: 6-char root, YYMMDD, 'C'/'P' at
    index 12, then an 8-digit strike (see occ.py). Schwab's `symbol` field is
    otherwise trusted verbatim; a malformed value here would waste a slot in
    the batch and could in principle collide with something unintended.
    """
    return (
        isinstance(symbol, str)
        and len(symbol) == 21
        and symbol[12] in ("C", "P")
    )


def fetch_quotes(symbols: list[str]) -> dict[str, float]:
    """Quote specific contracts by OCC symbol; returns {symbol: mid}.

    Used to re-price tracked positions without re-fetching chains — chains are
    ATM-centred (strike_count=20), so a position that moved deep ITM or OTM
    would simply fall out of one.
    """
    if not symbols:
        return {}
    shaped = [s for s in symbols if _looks_like_occ_symbol(s)]
    dropped = [s for s in symbols if s not in shaped]
    if dropped:
        logger.warning("fetch_quotes: dropped %d symbol(s) failing OCC shape check: %s",
                        len(dropped), dropped)
    symbols = shaped
    if not symbols:
        return {}
    out: dict[str, float] = {}
    try:
        client = _get_client()
    except Exception as e:
        logger.error("fetch_quotes: could not get Schwab client: %s", e)
        return out
    # Chunked: Schwab caps symbols per request, and one oversized request
    # failing would lose every quote rather than one chunk's worth.
    for i in range(0, len(symbols), 25):
        chunk = symbols[i:i + 25]
        try:
            resp = client.get_quotes(chunk)
            data = resp.json() if hasattr(resp, "json") else {}
            for sym, payload in (data or {}).items():
                q = payload.get("quote") or {}
                # -1.0 sentinel: an absent or unparseable bid is distinguishable
                # from a genuine 0.00 bid, which a plain 0.0 default would not be.
                bid = _safe_float(q.get("bidPrice"), -1.0)
                ask = _safe_float(q.get("askPrice"))
                # A zero bid with a live ask (0.00 / 0.05) is a real, tradeable
                # market worth 0.025 — not a missing quote. Rejecting it would
                # censor exactly the positions that went to near-zero, i.e. the
                # total losses, and inflate the measured win rate.
                #
                # An ABSENT bid is a different thing: no data, not a zero
                # market. That still yields no entry, so a genuinely missing
                # quote can never be mistaken for a price.
                if ask > 0 and bid >= 0:
                    out[sym] = round((bid + ask) / 2, 4)
        except Exception as e:
            logger.error("fetch_quotes failed for %d symbols: %s", len(chunk), e)
    return out
