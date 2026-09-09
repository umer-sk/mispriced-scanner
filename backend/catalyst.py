"""
Catalyst context: earnings dates, dividend yield, IV trend analysis, volume
spike detection.
"""
import logging
import time
from datetime import date, timedelta
from typing import Optional

import yfinance as yf

from models import CatalystContext, OptionChainData

logger = logging.getLogger(__name__)

# In-process rolling IV30 history: (symbol, date) -> iv30
_iv_trend_history: dict[tuple[str, date], float] = {}

# Earnings dates get revised as the report approaches, so this cache is
# short-lived (1 day) — unlike the dividend cache below, which can be long.
_earnings_cache: dict[str, Optional[date]] = {}
_earnings_cache_time: dict[str, float] = {}
_EARNINGS_TTL_SECONDS = 86400

_dividend_cache: dict[str, float] = {}
_dividend_cache_time: dict[str, float] = {}
_DIVIDEND_TTL_SECONDS = 7 * 86400


def _fetch_earnings_date(symbol: str) -> Optional[date]:
    """Next confirmed/estimated earnings date via yfinance, or None on any
    failure or absence — never a guess. `Ticker.calendar` is JSON-backed
    (Yahoo's calendarEvents module), not the HTML-scrape-based
    get_earnings_dates(), which is brittle across Yahoo layout changes.
    `calendar['Earnings Date']` is a list (Yahoo gives a start/end estimate
    range); the key can be absent entirely.
    """
    now = time.monotonic()
    cached_at = _earnings_cache_time.get(symbol)
    if cached_at is not None and now - cached_at < _EARNINGS_TTL_SECONDS:
        return _earnings_cache.get(symbol)

    result: Optional[date] = None
    try:
        cal = yf.Ticker(symbol).calendar or {}
        dates = cal.get("Earnings Date") or []
        today = date.today()
        upcoming = [d for d in dates if isinstance(d, date) and d >= today]
        if upcoming:
            result = min(upcoming)
    except Exception as e:
        logger.debug("Earnings date fetch failed for %s: %s", symbol, e)

    _earnings_cache[symbol] = result
    _earnings_cache_time[symbol] = now
    return result


def _fetch_dividend_yield(symbol: str, spot: float) -> float:
    """Trailing-12-month continuous dividend yield, derived from actual cash
    dividend payments (yf.Ticker.dividends) rather than Ticker.info's
    dividendYield field — that field is a raw passthrough of Yahoo's JSON
    with no guaranteed stable unit across yfinance versions, and a
    wrong-by-100x yield would manufacture parity violations on every
    dividend payer instead of correcting for them. 0.0 on any failure or for
    a non-payer — matches "no dividend", never a guessed value. 7-day TTL:
    unlike an earnings date, a yield doesn't need daily freshness.
    """
    now = time.monotonic()
    cached_at = _dividend_cache_time.get(symbol)
    if cached_at is not None and now - cached_at < _DIVIDEND_TTL_SECONDS:
        return _dividend_cache.get(symbol, 0.0)

    result = 0.0
    if spot > 0:
        try:
            divs = yf.Ticker(symbol).dividends
            if divs is not None and len(divs) > 0:
                cutoff = date.today() - timedelta(days=365)
                trailing_total = sum(
                    float(amt) for ts, amt in divs.items() if ts.date() >= cutoff
                )
                result = max(0.0, min(0.15, trailing_total / spot))
        except Exception as e:
            logger.debug("Dividend yield fetch failed for %s: %s", symbol, e)

    _dividend_cache[symbol] = result
    _dividend_cache_time[symbol] = now
    return result


def _get_iv_trend(symbol: str, current_iv30: float) -> str:
    """
    Compare current IV30 to value 5 trading days ago.
    Returns "RISING" | "FALLING" | "STABLE"
    """
    today = date.today()
    _iv_trend_history[(symbol, today)] = current_iv30

    # Find a record from roughly 5 trading days ago (search 5–9 calendar days back)
    five_day_ago_iv: Optional[float] = None
    for lookback in range(5, 10):
        candidate_date = today - timedelta(days=lookback)
        val = _iv_trend_history.get((symbol, candidate_date))
        if val is not None:
            five_day_ago_iv = val
            break

    if five_day_ago_iv is None or five_day_ago_iv == 0:
        return "STABLE"

    if current_iv30 > five_day_ago_iv * 1.05:
        return "RISING"
    if current_iv30 < five_day_ago_iv * 0.95:
        return "FALLING"
    return "STABLE"


def _volume_spike(chain: OptionChainData) -> bool:
    """
    Simple heuristic: flag if today's total option volume exceeds a typical
    baseline approximation. In production, a 20-day average would be stored.
    For now: flag if total volume > 10x open interest as a rough anomaly check.
    """
    total_volume = sum(c.volume for c in chain.calls + chain.puts)
    total_oi = sum(c.open_interest for c in chain.calls + chain.puts)
    if total_oi == 0:
        return False
    # Typical daily volume is roughly 2–5% of OI for liquid stocks
    # Spike threshold: 1.5× the typical ~3% ratio
    typical = total_oi * 0.045
    return total_volume > typical


def _build_catalyst_summary(
    symbol: str,
    earnings_date: Optional[date],
    earnings_dte: Optional[int],
    iv_rank: float,
    iv_trend: str,
    iv_expansion_likely: bool,
    trade_dte: int,
) -> str:
    today = date.today()

    if earnings_date and earnings_dte is not None:
        if earnings_dte <= 5:
            return (
                f"Earnings in {earnings_dte} days — too close. "
                f"IV already elevated. Risk of IV crush on entry."
            )
        elif earnings_dte <= trade_dte:
            if iv_rank < 30 and iv_trend != "RISING":
                return (
                    f"Earnings in {earnings_dte} days ({earnings_date.strftime('%b %d')}). "
                    f"IV at {iv_rank:.0f}th percentile — market not pricing the vol expansion. "
                    f"Classic pre-earnings long vol setup."
                )
            else:
                return (
                    f"Earnings in {earnings_dte} days ({earnings_date.strftime('%b %d')}). "
                    f"IV at {iv_rank:.0f}th percentile, trend {iv_trend.lower()}. "
                    f"Catalyst present but IV not yet depressed."
                )

    if iv_trend == "FALLING" and iv_rank < 30:
        return (
            f"No earnings in window. IV falling from recent spike (rank {iv_rank:.0f}%). "
            f"Skew or parity anomaly suggests calls underpriced vs puts. "
            f"Mean reversion play."
        )

    if iv_rank < 20:
        return (
            f"IV at annual floor ({iv_rank:.0f}th percentile). "
            f"No upcoming catalyst identified — pure mean-reversion entry. "
            f"Options pricing less movement than historical norms."
        )

    return (
        f"IV rank {iv_rank:.0f}%, trend {iv_trend.lower()}. "
        f"Mispricing detected without clear catalyst. "
        f"Technical setup only — size conservatively."
    )


def get_catalyst_context(
    symbol: str,
    chain: OptionChainData,
    trade_dte: int,
    real_earnings_date: Optional[date] = None,
) -> CatalystContext:
    """
    Derive catalyst context for a given symbol and trade horizon.
    real_earnings_date: pass the result of _fetch_earnings_date(symbol) —
    the caller fetches it (see that function's docstring for why) rather
    than this function fetching it itself, so a direct unit-test call stays
    network-free. A missing date resolves to "we don't know" (0 catalyst
    points downstream), never a guess — this used to fall back to a
    term-structure proxy that read "next expiry with elevated near-term IV"
    as an earnings date and could grant real scoring weight to a plain
    date-gap artifact. Removed rather than kept as a fallback.
    """
    today = date.today()

    # 1. Earnings date — real source only, see docstring above.
    earnings_date = real_earnings_date

    earnings_dte: Optional[int] = None
    earnings_in_window = False
    if earnings_date is not None:
        earnings_dte = (earnings_date - today).days
        earnings_in_window = 0 < earnings_dte <= trade_dte

    # 2. IV trend
    iv_trend = _get_iv_trend(symbol, chain.iv30)

    # 3. IV expansion likely
    iv_expansion_likely = (
        earnings_in_window
        and chain.iv_rank < 30
        and iv_trend != "RISING"
    )

    # 4. Volume spike
    recent_volume_spike = _volume_spike(chain)

    # 5. Catalyst summary
    catalyst_summary = _build_catalyst_summary(
        symbol=symbol,
        earnings_date=earnings_date,
        earnings_dte=earnings_dte,
        iv_rank=chain.iv_rank,
        iv_trend=iv_trend,
        iv_expansion_likely=iv_expansion_likely,
        trade_dte=trade_dte,
    )

    return CatalystContext(
        earnings_date=earnings_date,
        earnings_dte=earnings_dte,
        earnings_in_window=earnings_in_window,
        iv_trend=iv_trend,
        iv_expansion_likely=iv_expansion_likely,
        recent_volume_spike=recent_volume_spike,
        catalyst_summary=catalyst_summary,
    )
