"""
Catalyst context: earnings dates, IV trend analysis, volume spike detection.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from models import CatalystContext, OptionChainData

logger = logging.getLogger(__name__)

# In-process rolling IV30 history: (symbol, date) -> iv30
_iv_trend_history: dict[tuple[str, date], float] = {}


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


def _detect_earnings_from_term_structure(chain: OptionChainData) -> Optional[date]:
    """
    Proxy for earnings date: find near-term expiry with significantly elevated IV
    vs the next expiry (backwardation suggests event).
    """
    from datetime import date as date_type
    expiries = sorted(set(c.expiry for c in chain.calls if 7 <= c.dte <= 60))
    if len(expiries) < 2:
        return None

    exp_1 = expiries[0]
    exp_2 = expiries[1]

    def atm_iv(expiry: date_type) -> float:
        S = chain.stock_price
        candidates = [c for c in chain.calls if c.expiry == expiry and c.iv > 0 and c.bid > 0]
        if not candidates:
            return 0.0
        closest = min(candidates, key=lambda c: abs(c.strike - S))
        return closest.iv

    iv1 = atm_iv(exp_1)
    iv2 = atm_iv(exp_2)

    if iv1 > 0 and iv2 > 0 and (iv1 - iv2) > 0.10:
        # Significant near-term IV spike — earnings likely between exp_1 and today
        return exp_1
    return None


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
    schwab_earnings_date: Optional[date] = None,
) -> CatalystContext:
    """
    Derive catalyst context for a given symbol and trade horizon.
    schwab_earnings_date: pass if fetched from Schwab fundamental endpoint.
    """
    today = date.today()

    # 1. Earnings date
    earnings_date = schwab_earnings_date
    if earnings_date is None:
        earnings_date = _detect_earnings_from_term_structure(chain)

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
