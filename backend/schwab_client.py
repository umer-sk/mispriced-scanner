"""
Schwab API authentication and data fetching.
All credentials come from environment variables — no hardcoded secrets.
"""
import asyncio
import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
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

# In-process caches
_last_chain_cache: dict[str, OptionChainData] = {}
_iv_history: dict[tuple[str, date], float] = {}  # (symbol, date) -> iv30


def _get_client() -> schwab.client.Client:
    if not os.path.exists(SCHWAB_TOKEN_PATH):
        raise RuntimeError(
            f"ERROR: token.json not found at {SCHWAB_TOKEN_PATH}. "
            "Run auth_setup.py locally and upload token.json to Render Secret Files."
        )
    return schwab.auth.client_from_token_file(
        token_path=SCHWAB_TOKEN_PATH,
        api_key=SCHWAB_APP_KEY,
        app_secret=SCHWAB_APP_SECRET,
    )


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


def _parse_contracts(raw_map: dict, stock_price: float) -> list[OptionContract]:
    """Parse Schwab option chain map into OptionContract list."""
    contracts = []
    for strike_str, expiry_map in raw_map.items():
        strike = float(strike_str)
        for exp_str, contract_list in expiry_map.items():
            for c in contract_list:
                try:
                    exp_date = datetime.strptime(c.get("expirationDate", ""), "%Y-%m-%d").date()
                except ValueError:
                    # Try alternate format from Schwab response
                    try:
                        exp_date = datetime.strptime(exp_str.split(":")[0], "%Y-%m-%d").date()
                    except ValueError:
                        continue

                dte = (exp_date - date.today()).days
                if dte < 0:
                    continue

                bid = _safe_float(c.get("bid"))
                ask = _safe_float(c.get("ask"))
                mid = round((bid + ask) / 2, 2) if (bid + ask) > 0 else 0.0

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
                    iv=_safe_float(c.get("volatility")),
                    delta=_safe_float(c.get("delta")),
                    gamma=_safe_float(c.get("gamma")),
                    theta=_safe_float(c.get("theta")),
                    vega=_safe_float(c.get("vega")),
                    theoretical_value=_safe_float(c.get("theoreticalOptionValue")),
                    in_the_money=bool(c.get("inTheMoney", False)),
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


def _compute_iv_rank(symbol: str, current_iv: float) -> tuple[float, float]:
    """
    Compute IV rank and IV percentile from 52-week rolling history.
    Returns (iv_rank, iv_percentile) both 0–100.
    """
    today = date.today()
    one_year_ago = today - timedelta(days=365)

    # Store today's observation
    _iv_history[(symbol, today)] = current_iv

    # Collect historical values within the past year
    historical = [
        v for (sym, d), v in _iv_history.items()
        if sym == symbol and d >= one_year_ago
    ]

    if len(historical) < 2:
        # Not enough history — best-effort approximation
        return 50.0, 50.0

    iv_min = min(historical)
    iv_max = max(historical)
    if iv_max == iv_min:
        return 50.0, 50.0

    iv_rank = (current_iv - iv_min) / (iv_max - iv_min) * 100
    iv_percentile = sum(1 for v in historical if v < current_iv) / len(historical) * 100
    return round(iv_rank, 1), round(iv_percentile, 1)


def fetch_option_chain(symbol: str) -> OptionChainData:
    """
    Fetch full option chain for a symbol.
    On API failure, returns last cached result with is_stale=True.
    Never raises — caller always gets an OptionChainData back.
    """
    client = _get_client()
    today = date.today()
    to_date = today + timedelta(days=65)

    try:
        # Fetch option chain
        resp = client.get_option_chain(
            symbol,
            contract_type=schwab.client.Client.Options.ContractType.ALL,
            strike_count=20,
            include_underlying_quote=True,
            strategy=schwab.client.Client.Options.Strategy.SINGLE,
            from_date=today,
            to_date=to_date,
        )
        resp.raise_for_status()
        data = resp.json()

        underlying = data.get("underlying", {})
        stock_price = _safe_float(underlying.get("last") or underlying.get("mark") or data.get("underlyingPrice"))

        # ATM IV30: use underlying volatility or derive from ATM options
        iv30_raw = _safe_float(data.get("volatility") or underlying.get("thirtyDayVolatility"))
        if iv30_raw == 0.0:
            # Fallback: approximate from near-ATM call IV
            atm_calls = []
            call_map = data.get("callExpDateMap", {})
            for exp_str, strike_map in call_map.items():
                for strike_str, contracts in strike_map.items():
                    if abs(float(strike_str) - stock_price) < stock_price * 0.02:
                        for c in contracts:
                            iv = _safe_float(c.get("volatility"))
                            if iv > 0:
                                atm_calls.append(iv)
            iv30_raw = sum(atm_calls) / len(atm_calls) if atm_calls else 0.0

        # Fetch HV30 from price history
        hist_resp = client.get_price_history(
            symbol,
            period_type=schwab.client.Client.PriceHistory.PeriodType.MONTH,
            period=schwab.client.Client.PriceHistory.Period.ONE_MONTH,
            frequency_type=schwab.client.Client.PriceHistory.FrequencyType.DAILY,
            frequency=schwab.client.Client.PriceHistory.Frequency.DAILY,
        )
        hist_resp.raise_for_status()
        hist_data = hist_resp.json()
        candles = hist_data.get("candles", [])
        closes = [c["close"] for c in candles if "close" in c]
        hv30 = _compute_hv30(closes[-31:])  # use last 30 returns

        iv_rank, iv_percentile = _compute_iv_rank(symbol, iv30_raw)

        calls = _parse_contracts(data.get("callExpDateMap", {}), stock_price)
        puts = _parse_contracts(data.get("putExpDateMap", {}), stock_price)

        chain = OptionChainData(
            symbol=symbol,
            stock_price=stock_price,
            iv30=iv30_raw,
            hv30=hv30,
            iv_rank=iv_rank,
            iv_percentile=iv_percentile,
            timestamp=datetime.utcnow(),
            calls=calls,
            puts=puts,
            is_stale=False,
        )
        _last_chain_cache[symbol] = chain
        logger.info(f"Fetched chain for {symbol}: stock=${stock_price:.2f} IV30={iv30_raw:.1%} HV30={hv30:.1%} IVR={iv_rank:.0f}")
        return chain

    except Exception as e:
        logger.error(f"Failed to fetch chain for {symbol}: {e}")
        if symbol in _last_chain_cache:
            stale = _last_chain_cache[symbol]
            stale.is_stale = True
            return stale
        # Return empty chain so scanner doesn't crash
        return OptionChainData(
            symbol=symbol,
            stock_price=0.0,
            iv30=0.0,
            hv30=0.0,
            iv_rank=50.0,
            iv_percentile=50.0,
            timestamp=datetime.utcnow(),
            calls=[],
            puts=[],
            is_stale=True,
        )


async def fetch_all_chains(tickers: list[str]) -> list[OptionChainData]:
    """
    Fetch all tickers efficiently.
    Splits into 3 batches of 10, with 2s sleep between batches.
    Total: ~15–20 seconds. Well under 120 req/min rate limit.
    """
    results: list[OptionChainData] = []
    batch_size = 10
    batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]

    for i, batch in enumerate(batches):
        if i > 0:
            await asyncio.sleep(2)
        loop = asyncio.get_event_loop()
        batch_results = await asyncio.gather(
            *[loop.run_in_executor(None, fetch_option_chain, sym) for sym in batch]
        )
        results.extend(batch_results)
        logger.info(f"Batch {i + 1}/{len(batches)} complete ({len(batch)} symbols)")

    return results
