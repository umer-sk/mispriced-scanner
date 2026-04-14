"""
Market context: VIX regime assessment, skip recommendation, token expiry check.
"""
import logging
import os
from datetime import datetime, timedelta, date
from typing import Optional
from zoneinfo import ZoneInfo

from models import MarketContext, OptionChainData

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# In-process QQQ IV history: date -> iv30
_qqq_iv_history: dict[date, float] = {}

SCAN_TIMES_ET = ["08:00", "09:45", "11:00"]


def _is_market_open() -> bool:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def _next_scan_time() -> Optional[str]:
    now_et = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")
    for t in SCAN_TIMES_ET:
        h, m = map(int, t.split(":"))
        candidate = now_et.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate > now_et and now_et.weekday() < 5:
            return f"{t} AM ET"
    # Next business day at 8:00 AM
    days_ahead = 1
    while (now_et + timedelta(days=days_ahead)).weekday() >= 5:
        days_ahead += 1
    return f"Tomorrow 08:00 AM ET"


def _get_qqq_iv_trend(current_iv: float) -> str:
    today = date.today()
    _qqq_iv_history[today] = current_iv

    five_day_ago: Optional[float] = None
    for lookback in range(5, 10):
        d = today - timedelta(days=lookback)
        v = _qqq_iv_history.get(d)
        if v is not None:
            five_day_ago = v
            break

    if five_day_ago is None or five_day_ago == 0:
        return "STABLE"
    if current_iv > five_day_ago * 1.05:
        return "RISING"
    if current_iv < five_day_ago * 0.95:
        return "FALLING"
    return "STABLE"


def _token_age_days() -> float:
    token_path = os.environ.get("SCHWAB_TOKEN_PATH", "./token.json")
    if not os.path.exists(token_path):
        return 999.0
    mtime = os.path.getmtime(token_path)
    return (datetime.utcnow().timestamp() - mtime) / 86400


def get_market_context(qqq_chain: Optional[OptionChainData] = None) -> MarketContext:
    """
    Assess overall market conditions and generate skip recommendation.
    Uses QQQ IV30 as VIX proxy.
    """
    now_utc = datetime.utcnow()
    market_open = _is_market_open()
    next_scan = _next_scan_time()

    if qqq_chain is None or qqq_chain.iv30 == 0:
        return MarketContext(
            vix_level=0.0,
            vix_trend="STABLE",
            market_regime="NEUTRAL",
            skip_today=False,
            skip_reason=None,
            scan_timestamp=now_utc,
            market_is_open=market_open,
            next_scan_time=next_scan,
        )

    qqq_iv = qqq_chain.iv30
    # Schwab returns IV as a decimal (e.g., 0.22 for 22%) or percentage — normalize
    if qqq_iv < 1.0:
        qqq_iv_pct = qqq_iv * 100  # convert to percentage for regime thresholds
    else:
        qqq_iv_pct = qqq_iv  # already in percentage

    vix_trend = _get_qqq_iv_trend(qqq_iv_pct)

    # Regime determination
    iv_spiking = False
    if vix_trend == "RISING":
        today = date.today()
        five_day_ago: Optional[float] = None
        for lookback in range(5, 10):
            d = today - timedelta(days=lookback)
            v = _qqq_iv_history.get(d)
            if v is not None:
                five_day_ago = v
                break
        if five_day_ago and qqq_iv_pct > five_day_ago * 1.20:
            iv_spiking = True

    if qqq_iv_pct > 35 or iv_spiking:
        market_regime = "RISK_OFF"
    elif qqq_iv_pct < 20 and vix_trend in ("STABLE", "FALLING"):
        market_regime = "RISK_ON"
    else:
        market_regime = "NEUTRAL"

    # Skip reason (priority order — first match wins)
    skip_reason: Optional[str] = None
    skip_today = False

    token_age = _token_age_days()

    if iv_spiking:
        skip_today = True
        skip_reason = (
            "VIX spiking — IV elevated across the board. Long call spreads "
            "cost more and need bigger moves to profit. Consider waiting "
            "for vol to normalize."
        )
    elif qqq_iv_pct > 30:
        skip_today = True
        skip_reason = (
            f"Broad market IV above 30 ({qqq_iv_pct:.0f}%). Options expensive. "
            "Only take the highest-conviction setups (score >= 75) today."
        )
    elif vix_trend == "FALLING" and qqq_iv_pct < 25:
        skip_reason = (
            "IV falling fast after a spike. Calls getting cheaper. "
            "Good entry conditions improving."
        )

    # Token expiry warning (append, not replace)
    if token_age > 5:
        days_remaining = max(0.0, 7.0 - token_age)
        warning = (
            f" ⚠ Schwab token expires in {days_remaining:.1f} days. "
            "Re-run auth_setup.py and re-upload token.json."
        )
        skip_reason = (skip_reason or "") + warning
        if not skip_today and days_remaining < 1:
            skip_today = True

    return MarketContext(
        vix_level=round(qqq_iv_pct, 1),
        vix_trend=vix_trend,
        market_regime=market_regime,
        skip_today=skip_today,
        skip_reason=skip_reason if skip_reason else None,
        scan_timestamp=now_utc,
        market_is_open=market_open,
        next_scan_time=next_scan,
    )
