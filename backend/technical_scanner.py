"""
Technical momentum scanner — Qullamaggie/Minervini style.

Evaluates 7 signals per stock using daily price history from yfinance.
Stocks with 5+/7 signals agreeing on direction proceed to options structure selection.
"""
import gc
import logging
import math
from datetime import date, datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from models import OptionChainData, TechnicalSetup
from occ import build_occ

try:
    from schwab_client import fetch_option_chain
except Exception:
    fetch_option_chain = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _atr_price_target(stock_price: float, atr14: float, dte: int, bullish: bool) -> float:
    """Project a price target `dte` days out from a 14-day ATR.

    Price diffusion scales with sqrt(time), not time itself. The original
    formula used `dte / 10` linearly, so at 41 DTE it projected ~4.1x ATR of
    movement where the correct scaling is ~2.0x — a real difference, not
    rounding: R:R is computed FROM this target and is both the accept gate
    (>= 2.0) and the sort key, so the inflated target was what put setups on
    the page and ranked them. Preserves the original "1.5 ATR over 10
    trading days" calibration at dte=10, where sqrt(10/10) == 10/10 == 1.
    """
    move = 1.5 * atr14 * math.sqrt(dte / 10)
    return stock_price + move if bullish else stock_price - move


# ---------------------------------------------------------------------------
# 200-week MA bounce — a standalone, high-conviction setup independent of the
# 7-signal consensus above. See docs/superpowers/specs — this fires when a
# long-term uptrend corrects hard enough to test its RISING 200-week moving
# average and reclaims it, not merely when price is near any 200W level.
# ---------------------------------------------------------------------------

MA_200W_PERIOD = 200          # weeks
MA_200W_SLOPE_LOOKBACK = 10   # weeks back to compare the MA against, for slope
MA_200W_TOUCH_LOOKBACK = 8    # weeks to look back for a touch of the level
MA_200W_TOUCH_TOLERANCE_PCT = 4.0   # weekly low within this % of (or below) the MA counts as a touch
MA_200W_MAX_EXTENSION_PCT = 10.0    # current close must be within this % above the MA


def _score_200w_bounce(weekly_closes: list[float], weekly_lows: list[float]) -> Optional[dict]:
    """Detect a bounce off a rising 200-week moving average.

    All four must hold:
      1. The 200W SMA itself is rising (vs MA_200W_SLOPE_LOOKBACK weeks ago) —
         excludes long-term downtrends, where "bounce off the average" isn't
         the same setup as a correction within an uptrend.
      2. Within the last MA_200W_TOUCH_LOOKBACK weeks, a weekly LOW came
         within MA_200W_TOUCH_TOLERANCE_PCT of the MA (or pierced it).
      3. The most recent weekly CLOSE has reclaimed back above the MA.
      4. Current price is not already more than MA_200W_MAX_EXTENSION_PCT
         above the MA — an entry, not a retrospective observation.

    `weekly_closes` and `weekly_lows` must be the same length, oldest first.
    Returns None when there isn't enough history or the setup doesn't qualify;
    otherwise a dict of the facts that qualified it (for signal_details).
    """
    needed = MA_200W_PERIOD + MA_200W_SLOPE_LOOKBACK
    if len(weekly_closes) < needed or len(weekly_lows) < needed:
        return None

    def _sma200_ending_at(idx: int) -> float:
        window = weekly_closes[idx - MA_200W_PERIOD + 1: idx + 1]
        return sum(window) / len(window)

    last = len(weekly_closes) - 1
    ma_now = _sma200_ending_at(last)
    ma_then = _sma200_ending_at(last - MA_200W_SLOPE_LOOKBACK)
    if ma_now <= 0 or ma_then <= 0:
        return None

    ma_slope_pct = round((ma_now - ma_then) / ma_then * 100, 2)
    if ma_slope_pct <= 0:
        return None   # criterion 1: MA must be rising

    price_now = weekly_closes[last]
    if price_now <= ma_now:
        return None   # criterion 3: must have reclaimed, not still be under it

    extension_pct = round((price_now - ma_now) / ma_now * 100, 2)
    if extension_pct > MA_200W_MAX_EXTENSION_PCT:
        return None   # criterion 4: too far past the level to be an entry

    touch_window = weekly_lows[last - MA_200W_TOUCH_LOOKBACK + 1: last + 1]
    lowest_touch = min(touch_window)
    touch_pct = round((lowest_touch - ma_now) / ma_now * 100, 2)
    if touch_pct > MA_200W_TOUCH_TOLERANCE_PCT:
        return None   # criterion 2: never actually got close to the level
    weeks_since_touch = len(touch_window) - 1 - touch_window.index(lowest_touch)

    return {
        "ma_200w": round(ma_now, 2),
        "ma_slope_pct": ma_slope_pct,
        "touch_pct": touch_pct,
        "weeks_since_touch": weeks_since_touch,
        "extension_pct": extension_pct,
    }


def _leg_liquidity(long_leg, short_leg):
    """Return (long_oi, short_oi, long_spread_pct, short_spread_pct, ok).

    Spread percentages are 0-100 to match scanner.py, which stores
    round(_spread_pct(c) * 100, 1). Mixing scales would make the shared
    forward-test liquidity gate compare a fraction against a percentage.
    """
    def pct(c):
        if c is None or c.mid <= 0:
            return 100.0          # unpriceable is maximally illiquid
        return round((c.ask - c.bid) / c.mid * 100, 1)

    long_oi = long_leg.open_interest if long_leg else 0
    short_oi = short_leg.open_interest if short_leg else 0
    long_sp = pct(long_leg)
    short_sp = pct(short_leg) if short_leg else 0.0

    ok = (
        long_oi >= 100
        and long_sp <= 10.0
        and (short_leg is None or (short_oi >= 100 and short_sp <= 10.0))
    )
    return long_oi, short_oi, long_sp, short_sp, ok

# net_score = count(True) - count(False) over 7 booleans, so it is always ODD:
# -7, -5, -3, -1, 1, 3, 5, 7. A threshold of 1 therefore rejected nothing in
# "both" mode (abs(score) < 1 is unreachable) and every symbol qualified on a
# 4-3 coin flip. 3 is the smallest threshold that actually filters: it requires
# 5 of 7 signals to agree, which is what the module docstring always claimed.
NET_SCORE_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> float:
    """Exponential moving average of the last value."""
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    """RSI using Wilder smoothing (ewm with com=period-1)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = float(gain.ewm(com=period - 1, adjust=False).mean().iloc[-1])
    avg_loss = float(loss.ewm(com=period - 1, adjust=False).mean().iloc[-1])
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


def _atr14(df: pd.DataFrame) -> float:
    """Average True Range over last 14 bars."""
    high = df['High']
    low = df['Low']
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(14).mean().iloc[-1])


# ---------------------------------------------------------------------------
# Signal scoring
# ---------------------------------------------------------------------------

def score_signals(
    symbol: str,
    df: pd.DataFrame,
    qqq_df: pd.DataFrame,
) -> tuple[int, dict[str, bool]]:
    """
    Evaluate 7 technical signals for a stock.

    Returns (net_score, signal_details) where:
    - Each signal True = bullish for that indicator, False = bearish
    - net_score = count(True) - count(False), range -7 to +7
    - net_score >= 1 → bullish (4+/7 agree)
    - net_score <= -1 → bearish (4+/7 agree)

    Requires df with at least 220 rows of OHLCV daily data.
    """
    close = df['Close']
    volume = df['Volume']
    price = float(close.iloc[-1])

    # Signal 1: Price vs 21 EMA
    ema21 = _ema(close, 21)
    price_vs_ema21 = price > ema21

    # Signal 2: 13 EMA vs 21 EMA (short-term momentum alignment)
    ema13 = _ema(close, 13)
    ema_alignment = ema13 > ema21

    # Signal 3: Stage 2 trend — price > MA50 > MA200 (Minervini trend template)
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200_series = close.rolling(200).mean()
    ma200 = float(ma200_series.iloc[-1]) if not pd.isna(ma200_series.iloc[-1]) else ma50
    stage2 = price > ma50 > ma200

    # Signal 4: RSI(14) in momentum zone and rising
    rsi_now = _rsi(close)
    rsi_3d_ago = _rsi(close.iloc[:-3]) if len(close) > 20 else rsi_now
    rsi_zone = (45 <= rsi_now <= 75) and (rsi_now > rsi_3d_ago)

    # Signal 5: Volume accumulation — recent 5-day avg > 20-day avg
    vol_5d = float(volume.iloc[-5:].mean())
    vol_20d = float(volume.iloc[-20:].mean())
    volume_accum = vol_5d > vol_20d

    # Signal 6: Relative strength vs QQQ over last 10 days
    stock_ret = (price / float(close.iloc[-11]) - 1) if len(close) >= 11 else 0.0
    qqq_close = qqq_df['Close']
    qqq_ret = (float(qqq_close.iloc[-1]) / float(qqq_close.iloc[-11]) - 1) if len(qqq_close) >= 11 else 0.0
    rs_vs_qqq = stock_ret > qqq_ret

    # Signal 7: Near 50-day high (within 5%) — breakout candidate
    high_50d = float(close.iloc[-50:].max())
    breakout = price >= high_50d * 0.95

    details = {
        'price_vs_ema21': price_vs_ema21,
        'ema_alignment':  ema_alignment,
        'stage2':         stage2,
        'rsi_zone':       rsi_zone,
        'volume_accum':   volume_accum,
        'rs_vs_qqq':      rs_vs_qqq,
        'breakout':       breakout,
    }

    net_score = sum(1 if v else -1 for v in details.values())
    return net_score, details


# ---------------------------------------------------------------------------
# Options structure helpers
# ---------------------------------------------------------------------------

def _find_delta_contract(
    contracts: list,
    target_delta: float,
    dte_min: int = 30,
    dte_max: int = 60,
) -> Optional[object]:
    """Find the contract whose abs(delta) is closest to target_delta."""
    candidates = [
        c for c in contracts
        if dte_min <= c.dte <= dte_max and c.bid > 0 and c.iv > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))


def _construct_long_call(
    symbol: str,
    stock_price: float,
    chain,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Long call at ~0.45 delta, 30–60 DTE. R:R via ATR-based price target."""
    call = _find_delta_contract(chain.calls, 0.45)
    if call is None:
        return None

    dte = call.dte
    price_target = _atr_price_target(stock_price, atr14, dte, bullish=True)
    intrinsic_at_target = max(0.0, price_target - call.strike)
    gain_at_target = intrinsic_at_target - call.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / call.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = call.strike + call.ask
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)

    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(call, None)
    # Match scanner.py's hard gate: reject outright rather than surfacing a
    # setup that cannot realistically be filled.
    if oi_l < 100 or sp_l > 15.0:
        return None
    try:
        long_occ = call.occ_symbol or build_occ(symbol, call.expiry, False, call.strike)
    except ValueError:
        long_occ = ""

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bullish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="long_call",
        strike=call.strike,
        short_strike=None,
        expiry=call.expiry,
        dte=dte,
        delta=round(call.delta, 2),
        iv_rank=chain.iv_rank,
        premium=call.ask,
        price_target=round(price_target, 2),
        rr_ratio=rr_ratio,
        max_loss=round(call.ask * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(call.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {call.expiry.strftime('%m/%d')} "
            f"{call.strike:.0f} CALL @{call.ask:.2f} LMT"
        ),
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ="",
        entry_mid=round(call.mid, 4),
    )


def _construct_long_put(
    symbol: str,
    stock_price: float,
    chain,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Long put at ~0.45 delta (abs), 30–60 DTE. R:R via ATR-based price target."""
    put = _find_delta_contract(chain.puts, 0.45)
    if put is None:
        return None

    dte = put.dte
    price_target = _atr_price_target(stock_price, atr14, dte, bullish=False)
    intrinsic_at_target = max(0.0, put.strike - price_target)
    gain_at_target = intrinsic_at_target - put.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / put.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = put.strike - put.ask
    breakeven_move_pct = round((stock_price - breakeven) / stock_price * 100, 1)

    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(put, None)
    # Match scanner.py's hard gate: reject outright rather than surfacing a
    # setup that cannot realistically be filled.
    if oi_l < 100 or sp_l > 15.0:
        return None
    try:
        long_occ = put.occ_symbol or build_occ(symbol, put.expiry, True, put.strike)
    except ValueError:
        long_occ = ""

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bearish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="long_put",
        strike=put.strike,
        short_strike=None,
        expiry=put.expiry,
        dte=dte,
        delta=round(put.delta, 2),
        iv_rank=chain.iv_rank,
        premium=put.ask,
        price_target=round(price_target, 2),
        rr_ratio=rr_ratio,
        max_loss=round(put.ask * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(put.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {put.expiry.strftime('%m/%d')} "
            f"{put.strike:.0f} PUT @{put.ask:.2f} LMT"
        ),
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ="",
        entry_mid=round(put.mid, 4),
    )


def _construct_bull_call_spread_technical(
    symbol: str,
    stock_price: float,
    chain,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Bull call spread: long 0.45Δ, short 0.25Δ, same expiry."""
    long_leg = _find_delta_contract(chain.calls, 0.45)
    if long_leg is None:
        return None

    short_candidates = [
        c for c in chain.calls
        if c.expiry == long_leg.expiry and 0.20 <= abs(c.delta) <= 0.30 and c.bid > 0
    ]
    if not short_candidates:
        return None
    short_leg = min(short_candidates, key=lambda c: abs(abs(c.delta) - 0.25))

    spread_width = short_leg.strike - long_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0 or net_debit > spread_width * 0.40:
        return None

    max_gain = round(spread_width - net_debit, 2)
    rr_ratio = round(max_gain / net_debit, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = long_leg.strike + net_debit
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)
    dte = long_leg.dte

    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(long_leg, short_leg)
    # Match scanner.py's hard gate: reject outright rather than surfacing a
    # setup that cannot realistically be filled.
    if oi_l < 100 or sp_l > 15.0 or oi_s < 100 or sp_s > 15.0:
        return None
    try:
        long_occ = long_leg.occ_symbol or build_occ(symbol, long_leg.expiry, False, long_leg.strike)
    except ValueError:
        long_occ = ""
    try:
        short_occ = short_leg.occ_symbol or build_occ(symbol, short_leg.expiry, False, short_leg.strike)
    except ValueError:
        short_occ = ""

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bullish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="bull_call_spread",
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        expiry=long_leg.expiry,
        dte=dte,
        delta=round(long_leg.delta, 2),
        iv_rank=chain.iv_rank,
        premium=net_debit,
        price_target=round(_atr_price_target(stock_price, atr14, dte, bullish=True), 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} CALL VRT @{net_debit:.2f} LMT"
        ),
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ=short_occ,
        entry_mid=round(long_leg.mid - short_leg.mid, 4),
    )


def _construct_bear_put_spread_technical(
    symbol: str,
    stock_price: float,
    chain,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Bear put spread: long 0.45Δ put, short 0.25Δ put, same expiry."""
    long_leg = _find_delta_contract(chain.puts, 0.45)
    if long_leg is None:
        return None

    short_candidates = [
        c for c in chain.puts
        if c.expiry == long_leg.expiry and 0.20 <= abs(c.delta) <= 0.30 and c.bid > 0
    ]
    if not short_candidates:
        return None
    short_leg = min(short_candidates, key=lambda c: abs(abs(c.delta) - 0.25))

    spread_width = long_leg.strike - short_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0 or net_debit > spread_width * 0.40:
        return None

    max_gain = round(spread_width - net_debit, 2)
    rr_ratio = round(max_gain / net_debit, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = long_leg.strike - net_debit
    breakeven_move_pct = round((stock_price - breakeven) / stock_price * 100, 1)
    dte = long_leg.dte

    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(long_leg, short_leg)
    # Match scanner.py's hard gate: reject outright rather than surfacing a
    # setup that cannot realistically be filled.
    if oi_l < 100 or sp_l > 15.0 or oi_s < 100 or sp_s > 15.0:
        return None
    try:
        long_occ = long_leg.occ_symbol or build_occ(symbol, long_leg.expiry, True, long_leg.strike)
    except ValueError:
        long_occ = ""
    try:
        short_occ = short_leg.occ_symbol or build_occ(symbol, short_leg.expiry, True, short_leg.strike)
    except ValueError:
        short_occ = ""

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bearish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="bear_put_spread",
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        expiry=long_leg.expiry,
        dte=dte,
        delta=round(long_leg.delta, 2),
        iv_rank=chain.iv_rank,
        premium=net_debit,
        price_target=round(_atr_price_target(stock_price, atr14, dte, bullish=False), 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} PUT VRT @{net_debit:.2f} LMT"
        ),
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ=short_occ,
        entry_mid=round(long_leg.mid - short_leg.mid, 4),
    )


def _pick_best_structure(
    symbol: str,
    stock_price: float,
    chain,
    direction: str,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """
    Choose the best options structure based on IV rank.
    IV rank < 50: try long call/put first, then spread
    IV rank 50-65: try both, pick higher R:R
    IV rank > 65: spread first, then long call/put as fallback
    """
    iv_rank = chain.iv_rank

    if direction == "bullish":
        long_fn = _construct_long_call
        spread_fn = _construct_bull_call_spread_technical
    else:
        long_fn = _construct_long_put
        spread_fn = _construct_bear_put_spread_technical

    args = (symbol, stock_price, chain, signal_count, signal_details, atr14)

    if iv_rank < 50:
        return long_fn(*args) or spread_fn(*args)
    elif iv_rank <= 65:
        long_setup = long_fn(*args)
        spread_setup = spread_fn(*args)
        if long_setup and spread_setup:
            return long_setup if long_setup.rr_ratio >= spread_setup.rr_ratio else spread_setup
        return long_setup or spread_setup
    else:
        return spread_fn(*args) or long_fn(*args)


def _download_weekly_batch(batch: list[str]) -> dict[str, tuple[list[float], list[float]]]:
    """Weekly close/low history for a batch of symbols, keyed by symbol.

    Independent of the daily 1y fetch above: the 200W bounce needs ~4.5 years
    of WEEKLY bars (200 for the SMA + 10 for slope + buffer), which the daily
    fetch does not carry and cannot be resampled up to reliably. Batched the
    same way as the daily fetch, for the same memory reason.
    """
    out: dict[str, tuple[list[float], list[float]]] = {}
    try:
        raw = yf.download(
            tickers=batch, period="5y", interval="1wk",
            auto_adjust=True, progress=False,
        )
    except Exception as e:
        logger.warning("Weekly download failed for batch of %d: %s", len(batch), e)
        return out

    for symbol in batch:
        try:
            if len(batch) == 1:
                df = raw.dropna()
            elif isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(symbol, axis=1, level=1).dropna()
            else:
                df = raw.dropna()
            if len(df) < MA_200W_PERIOD + MA_200W_SLOPE_LOOKBACK:
                continue
            out[symbol] = (df["Close"].tolist(), df["Low"].tolist())
        except Exception as e:
            logger.debug("Weekly parse failed for %s: %s", symbol, e)

    del raw
    return out


def _construct_200w_bounce_long_call(
    symbol: str,
    stock_price: float,
    chain,
    bounce_facts: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Long call for a 200W MA bounce: deeper ITM and longer-dated than the
    consensus long call (0.65 delta / 60-100 DTE vs 0.45 delta / 30-60 DTE),
    since this is a slower thesis with less need to lean on theta-heavy
    leverage. Reuses the same (now sqrt-scaled) ATR price target and the same
    2.0 min-R:R gate as every other technical structure."""
    candidates = [c for c in chain.calls if 60 <= c.dte <= 100]
    if not candidates:
        return None
    call = min(candidates, key=lambda c: abs(c.delta - 0.65))
    if call.bid <= 0:
        return None

    dte = call.dte
    price_target = _atr_price_target(stock_price, atr14, dte, bullish=True)
    intrinsic_at_target = max(0.0, price_target - call.strike)
    gain_at_target = intrinsic_at_target - call.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / call.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = call.strike + call.ask
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)

    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(call, None)
    if oi_l < 100 or sp_l > 15.0:
        return None
    try:
        long_occ = call.occ_symbol or build_occ(symbol, call.expiry, False, call.strike)
    except ValueError:
        long_occ = ""

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bullish",
        signal_count=4,          # the 4 qualifying criteria, not a 7-signal count
        signal_details=bounce_facts,
        structure="long_call",
        strike=call.strike,
        short_strike=None,
        expiry=call.expiry,
        dte=dte,
        delta=round(call.delta, 2),
        iv_rank=chain.iv_rank,
        premium=call.ask,
        price_target=round(price_target, 2),
        rr_ratio=rr_ratio,
        max_loss=round(call.ask * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(call.delta) * 100, 1),
        order_string=f"BUY +1 {symbol} {call.expiry:%m/%d} {call.strike:g} CALL @{call.ask:.2f} LMT",
        earnings_within_dte=False,
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ="",
        entry_mid=round(call.mid, 4) if call.mid > 0 else None,
        setup_type="200w_bounce",
    )


_BATCH_SIZE = 15  # symbols per yfinance download — keeps peak DataFrame ~15× smaller


def _download_qqq() -> pd.DataFrame:
    """Download QQQ price history, returning a single-symbol DataFrame."""
    try:
        raw = yf.download("QQQ", period="1y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs("QQQ", axis=1, level=1)
        return raw.dropna()
    except Exception as e:
        logger.error("yfinance QQQ download failed: %s", e)
        return pd.DataFrame()


def scan_technical_setups(
    symbols: list[str],
    min_rr: float = 2.0,
    direction: str = "both",
) -> list[TechnicalSetup]:
    """
    Full technical scan:
    1. Fetch QQQ price history once, then score symbols in batches of _BATCH_SIZE
    2. Score 7 signals per stock; qualifying = abs(net_score) >= NET_SCORE_THRESHOLD
    3. For qualifying stocks: fetch Schwab option chain, pick best structure
    4. Filter by min_rr, sort by rr_ratio descending

    Batching yfinance downloads keeps peak RAM ~3× lower than fetching all at once.
    """
    logger.info("Technical scan: fetching QQQ + %d symbols in batches of %d", len(symbols), _BATCH_SIZE)

    qqq_df = _download_qqq()
    if qqq_df.empty:
        logger.error("Technical scan: QQQ download failed, aborting")
        return []

    qualifying = []
    atr_by_symbol: dict[str, float] = {}
    batches = [symbols[i:i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        if batch_idx > 0:
            import time; time.sleep(1)
        try:
            raw = yf.download(
                tickers=batch,
                period="1y",
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            logger.warning("Technical scan: batch %d yfinance download failed: %s", batch_idx, e)
            continue

        logger.info("Technical scan: batch %d/%d shape=%s", batch_idx + 1, len(batches), raw.shape)

        for symbol in batch:
            try:
                if len(batch) == 1:
                    df = raw.dropna()
                elif isinstance(raw.columns, pd.MultiIndex):
                    df = raw.xs(symbol, axis=1, level=1).dropna()
                else:
                    df = raw.dropna()

                if len(df) < 50:
                    logger.debug("%s: insufficient price history (%d rows)", symbol, len(df))
                    continue

                atr_by_symbol[symbol] = _atr14(df)

                score, details = score_signals(symbol, df, qqq_df)
                logger.info("Technical scan: %s score=%d details=%s", symbol, score, details)

                if direction == "bullish" and score < NET_SCORE_THRESHOLD:
                    continue
                if direction == "bearish" and score > -NET_SCORE_THRESHOLD:
                    continue
                if direction == "both" and abs(score) < NET_SCORE_THRESHOLD:
                    continue

                signal_direction = "bullish" if score >= NET_SCORE_THRESHOLD else "bearish"
                signal_count = sum(1 for v in details.values() if (
                    v if signal_direction == "bullish" else not v
                ))
                qualifying.append((symbol, signal_count, details, _atr14(df), signal_direction))

            except Exception as e:
                logger.warning("Signal scoring failed for %s: %s", symbol, e)

        del raw
        gc.collect()

    del qqq_df
    gc.collect()

    logger.info("Technical scan: %d/%d symbols qualify for options check", len(qualifying), len(symbols))

    # 200W MA bounce: a standalone, high-conviction setup independent of the
    # 7-signal consensus above — it can fire for a symbol the consensus
    # rejects entirely, and vice versa. Bullish-only (see _score_200w_bounce);
    # skipped outright when the caller only wants bearish setups.
    bounce_qualifying: list[tuple[str, dict]] = []
    if direction in ("bullish", "both"):
        for batch_idx, batch in enumerate(batches):
            weekly = _download_weekly_batch(batch)
            for symbol, (closes, lows) in weekly.items():
                facts = _score_200w_bounce(closes, lows)
                if facts is not None:
                    logger.info("Technical scan: %s 200W bounce qualifies: %s", symbol, facts)
                    bounce_qualifying.append((symbol, facts))
            gc.collect()
        logger.info("Technical scan: %d/%d symbols qualify for 200W bounce",
                    len(bounce_qualifying), len(symbols))

    setups: list[TechnicalSetup] = []

    for i, (symbol, signal_count, details, atr, sig_direction) in enumerate(qualifying):
        try:
            chain = fetch_option_chain(symbol)
            if chain.stock_price == 0:
                logger.warning("Technical scan: %s chain returned stock_price=0 (is_stale=%s)", symbol, chain.is_stale)
                del chain
                continue

            logger.info("Technical scan: %s stock=%.2f iv_rank=%.0f calls=%d puts=%d atr=%.2f dir=%s",
                        symbol, chain.stock_price, chain.iv_rank, len(chain.calls), len(chain.puts), atr, sig_direction)

            setup = _pick_best_structure(
                symbol, chain.stock_price, chain,
                sig_direction, signal_count, details, atr,
            )
            del chain  # free 280+ OptionContract objects immediately
            if setup is None:
                logger.info("Technical scan: %s no structure met R:R >= %.1f", symbol, min_rr)
                continue
            if setup.rr_ratio < min_rr:
                logger.info("Technical scan: %s structure rr=%.2f below min_rr=%.1f", symbol, setup.rr_ratio, min_rr)
                continue
            setups.append(setup)

        except Exception as e:
            logger.warning("Options structure failed for %s: %s", symbol, e)

        if i % 15 == 14:
            gc.collect()

    for symbol, facts in bounce_qualifying:
        try:
            chain = fetch_option_chain(symbol)
            if chain.stock_price == 0:
                logger.warning("Technical scan: %s (200W) chain returned stock_price=0", symbol)
                del chain
                continue
            setup = _construct_200w_bounce_long_call(
                symbol, chain.stock_price, chain, facts, atr_by_symbol.get(symbol, 0.0),
            )
            del chain
            if setup is None or setup.rr_ratio < min_rr:
                logger.info("Technical scan: %s (200W) no structure met R:R >= %.1f", symbol, min_rr)
                continue
            setups.append(setup)
        except Exception as e:
            logger.warning("200W bounce structure failed for %s: %s", symbol, e)

    setups.sort(key=lambda s: s.rr_ratio, reverse=True)
    logger.info("Technical scan complete: %d setups found", len(setups))
    return setups
