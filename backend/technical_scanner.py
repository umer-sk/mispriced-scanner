"""
Technical momentum scanner — Qullamaggie/Minervini style.

Evaluates 7 signals per stock using daily price history from yfinance.
Stocks with 5+/7 signals agreeing on direction proceed to options structure selection.
"""
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from models import OptionChainData, TechnicalSetup

try:
    from schwab_client import fetch_option_chain
except Exception:
    fetch_option_chain = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# net_score = count(True) - count(False); score of 3 means 5 agree, 2 disagree (5+/7)
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
    - net_score >= 3 → clear bullish (5+/7 agree)
    - net_score <= -3 → clear bearish (5+/7 agree)

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
    price_target = stock_price + 1.5 * atr14 * dte / 10
    intrinsic_at_target = max(0.0, price_target - call.strike)
    gain_at_target = intrinsic_at_target - call.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / call.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = call.strike + call.ask
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)

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
    price_target = stock_price - 1.5 * atr14 * dte / 10
    intrinsic_at_target = max(0.0, put.strike - price_target)
    gain_at_target = intrinsic_at_target - put.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / put.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = put.strike - put.ask
    breakeven_move_pct = round((stock_price - breakeven) / stock_price * 100, 1)

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
        price_target=round(stock_price + 1.5 * atr14 * dte / 10, 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} CALL VRT @{net_debit:.2f} LMT"
        ),
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
        price_target=round(stock_price - 1.5 * atr14 * dte / 10, 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} PUT VRT @{net_debit:.2f} LMT"
        ),
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


def scan_technical_setups(
    symbols: list[str],
    min_rr: float = 2.0,
    direction: str = "both",
) -> list[TechnicalSetup]:
    """
    Full technical scan:
    1. Fetch 220-day daily OHLCV from yfinance for all symbols + QQQ
    2. Score 7 signals per stock
    3. For stocks with score >= NET_SCORE_THRESHOLD (5+/7 agree):
       fetch Schwab option chain, pick best structure
    4. Filter by min_rr, sort by rr_ratio descending
    """
    logger.info("Technical scan: fetching price history for %d symbols", len(symbols))

    all_tickers = symbols + (["QQQ"] if "QQQ" not in symbols else [])
    try:
        raw = yf.download(
            tickers=all_tickers,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        logger.error("yfinance batch download failed: %s", e)
        return []

    # Extract QQQ DataFrame
    if len(all_tickers) == 1:
        qqq_df = raw
    elif isinstance(raw.columns, pd.MultiIndex):
        try:
            qqq_df = raw.xs("QQQ", axis=1, level=1) if "QQQ" in all_tickers else raw
        except Exception:
            qqq_df = raw
    else:
        qqq_df = raw

    qualifying = []

    for symbol in symbols:
        try:
            if len(all_tickers) > 1 and isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(symbol, axis=1, level=1).dropna()
            else:
                df = raw.dropna()

            if len(df) < 50:
                logger.debug("%s: insufficient price history (%d rows)", symbol, len(df))
                continue

            score, details = score_signals(symbol, df, qqq_df)

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
            qualifying.append((symbol, signal_count, details, df, signal_direction))

        except Exception as e:
            logger.warning("Signal scoring failed for %s: %s", symbol, e)

    logger.info("Technical scan: %d/%d symbols qualify for options check", len(qualifying), len(symbols))

    setups: list[TechnicalSetup] = []

    for symbol, signal_count, details, df, sig_direction in qualifying:
        try:
            chain = fetch_option_chain(symbol)
            if chain.stock_price == 0:
                continue

            atr = _atr14(df)
            setup = _pick_best_structure(
                symbol, chain.stock_price, chain,
                sig_direction, signal_count, details, atr,
            )
            if setup is None or setup.rr_ratio < min_rr:
                continue
            setups.append(setup)

        except Exception as e:
            logger.warning("Options structure failed for %s: %s", symbol, e)

    setups.sort(key=lambda s: s.rr_ratio, reverse=True)
    logger.info("Technical scan complete: %d setups found", len(setups))
    return setups
