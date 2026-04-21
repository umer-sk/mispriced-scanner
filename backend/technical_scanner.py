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

logger = logging.getLogger(__name__)

SIGNAL_THRESHOLD = 3


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
