"""
Market context: VIX regime assessment, skip recommendation, token expiry check.
"""
import json
import logging
import os
from datetime import datetime, timedelta, date, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

from models import MarketContext, OptionChainData

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# In-process QQQ IV history: date -> iv30
_qqq_iv_history: dict[date, float] = {}

# Full-scan slots that refresh /opportunities. Must stay in sync with the
# cron entries in .github/workflows/scan.yml (which also runs the technical
# scan at 10:30 and the CELT scan at 16:15 — neither refreshes /opportunities,
# so they are deliberately not listed here).
SCAN_TIMES_ET = ["08:00", "09:45", "11:00", "15:45"]


def _is_market_open() -> bool:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def _next_scan_time() -> Optional[str]:
    now_et = datetime.now(ET)
    for t in SCAN_TIMES_ET:
        h, m = map(int, t.split(":"))
        candidate = now_et.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate > now_et and now_et.weekday() < 5:
            # 12-hour clock: "AM" was hardcoded, so the afternoon slot rendered
            # as the nonsensical "15:45 AM ET".
            return f"{candidate.strftime('%I:%M %p').lstrip('0')} ET"
    # Next business day at the first slot
    return f"Tomorrow {SCAN_TIMES_ET[0]} AM ET"


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
    """Days since the Schwab token was first created.

    Reads `creation_timestamp` from inside the token file rather than the
    file's mtime. On Render the token is a mounted secret whose mtime is set
    when the secret is mounted — i.e. at deploy time — so an mtime-based age
    measured "time since last deploy" and silently reset to zero on every
    deploy, exactly when it was most needed. schwab-py preserves
    `creation_timestamp` across token refreshes (see TokenMetadata in
    schwab/auth.py), so it tracks the real 7-day refresh-token clock.

    Returns 999.0 when the token is missing or unreadable, so callers treat an
    unknown token as expired rather than as fresh.
    """
    token_path = os.environ.get("SCHWAB_TOKEN_PATH", "./token.json")
    if not os.path.exists(token_path):
        return 999.0

    try:
        with open(token_path) as f:
            created = json.load(f)["creation_timestamp"]
        # Compare POSIX timestamp to POSIX timestamp. Note that
        # datetime.utcnow().timestamp() would be wrong here: .timestamp()
        # reads a naive datetime as local time, skewing the result by the
        # host's UTC offset.
        return (datetime.now(timezone.utc).timestamp() - float(created)) / 86400
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning(
            "Could not read creation_timestamp from %s (%s) — reporting token "
            "as expired so this surfaces rather than reading falsely fresh.",
            token_path, e,
        )
        return 999.0


_mas_cache: dict = {}
_mas_cache_time: Optional[datetime] = None
_MAS_TTL_SECONDS = 3600  # refresh at most once per hour


def _fetch_index_mas() -> dict:
    """Fetch SPY and QQQ price + 7EMA / 20MA / 50MA via yfinance. Cached for 1 hour."""
    global _mas_cache, _mas_cache_time
    now = datetime.now(timezone.utc)
    if _mas_cache_time and (now - _mas_cache_time).total_seconds() < _MAS_TTL_SECONDS:
        return _mas_cache
    try:
        raw = yf.download(["SPY", "QQQ"], period="3mo", interval="1d", auto_adjust=True, progress=False)
        result = {}
        for sym in ("SPY", "QQQ"):
            closes = raw["Close"][sym].dropna()
            if len(closes) < 20:
                continue
            price = float(closes.iloc[-1])
            ema7 = float(closes.ewm(span=7, adjust=False).mean().iloc[-1])
            ma20 = float(closes.rolling(20).mean().iloc[-1])
            ma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else 0.0
            result[sym] = {"price": round(price, 2), "ema7": round(ema7, 2), "ma20": round(ma20, 2), "ma50": round(ma50, 2)}
        _mas_cache = result
        _mas_cache_time = now
        return result
    except Exception as e:
        logger.warning("SPY/QQQ MA fetch failed: %s", e)
        return _mas_cache  # return stale data rather than empty dict


def get_market_context(qqq_chain: Optional[OptionChainData] = None) -> MarketContext:
    """
    Assess overall market conditions and generate skip recommendation.
    Uses QQQ IV30 as VIX proxy.
    """
    now_utc = datetime.now(timezone.utc)
    market_open = _is_market_open()
    next_scan = _next_scan_time()
    mas = _fetch_index_mas()
    spy = mas.get("SPY", {})
    qqq = mas.get("QQQ", {})

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
            spy_price=spy.get("price", 0.0),
            spy_ema7=spy.get("ema7", 0.0),
            spy_ma20=spy.get("ma20", 0.0),
            spy_ma50=spy.get("ma50", 0.0),
            qqq_price=qqq.get("price", 0.0),
            qqq_ema7=qqq.get("ema7", 0.0),
            qqq_ma20=qqq.get("ma20", 0.0),
            qqq_ma50=qqq.get("ma50", 0.0),
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
        spy_price=spy.get("price", 0.0),
        spy_ema7=spy.get("ema7", 0.0),
        spy_ma20=spy.get("ma20", 0.0),
        spy_ma50=spy.get("ma50", 0.0),
        qqq_price=qqq.get("price", 0.0),
        qqq_ema7=qqq.get("ema7", 0.0),
        qqq_ma20=qqq.get("ma20", 0.0),
        qqq_ma50=qqq.get("ma50", 0.0),
    )
