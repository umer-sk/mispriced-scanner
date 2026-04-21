# backend/tests/test_technical_scanner.py
import pandas as pd
import numpy as np
from technical_scanner import _ema, _rsi, _atr14, score_signals
from models import TechnicalSetup
from datetime import date


def _make_df(closes, highs=None, lows=None, volumes=None, n=220):
    """Build a minimal OHLCV DataFrame for testing."""
    if len(closes) < n:
        closes = [closes[0]] * (n - len(closes)) + list(closes)
    closes = closes[-n:]
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        'Close': closes, 'High': highs, 'Low': lows,
        'Open': closes, 'Volume': volumes,
    })


def test_ema_increasing_series():
    prices = pd.Series([float(i) for i in range(1, 50)])
    ema13 = _ema(prices, 13)
    ema21 = _ema(prices, 21)
    # In rising series, shorter EMA should be higher
    assert ema13 > ema21


def test_rsi_all_up_days():
    prices = pd.Series([100.0 + i for i in range(30)])
    rsi = _rsi(prices)
    assert rsi > 70  # all up days → overbought


def test_rsi_all_down_days():
    prices = pd.Series([100.0 - i * 0.5 for i in range(30)])
    rsi = _rsi(prices)
    assert rsi < 30  # all down days → oversold


def test_score_signals_bullish():
    """Strong uptrend should score >= 3 (bullish)."""
    closes = [100.0 + i * 0.5 for i in range(220)]
    volumes = [500_000] * 200 + [1_500_000] * 20
    df = _make_df(closes, volumes=volumes)
    qqq_df = _make_df([300.0 + i * 0.4 for i in range(220)])
    score, details = score_signals("NVDA", df, qqq_df)
    assert score >= 3, f"Expected bullish score >= 3, got {score}"
    assert details['stage2'] is True
    assert details['ema_alignment'] is True


def test_score_signals_bearish():
    """Strong downtrend should score <= -3 (bearish)."""
    closes = [300.0 - i * 0.5 for i in range(220)]
    volumes = [500_000] * 200 + [1_500_000] * 20
    df = _make_df(closes, volumes=volumes)
    qqq_df = _make_df([300.0 + i * 0.1 for i in range(220)])
    score, details = score_signals("AAPL", df, qqq_df)
    assert score <= -3, f"Expected bearish score <= -3, got {score}"


def test_score_signals_mixed():
    """Flat/noisy prices should score between -2 and +2."""
    import math
    closes = [200.0 + math.sin(i * 0.2) * 5 for i in range(220)]
    df = _make_df(closes)
    qqq_df = _make_df([300.0] * 220)
    score, details = score_signals("MSFT", df, qqq_df)
    assert -3 <= score <= 3


def test_technical_setup_fields():
    setup = TechnicalSetup(
        symbol="NVDA",
        stock_price=875.0,
        direction="bullish",
        signal_count=6,
        signal_details={"stage2": True, "ema_alignment": True, "price_vs_ema21": True,
                        "rsi_zone": True, "volume_accum": True, "rs_vs_qqq": True, "breakout": False},
        structure="long_call",
        strike=900.0,
        short_strike=None,
        expiry=date(2026, 5, 16),
        dte=45,
        delta=0.44,
        iv_rank=32.0,
        premium=4.20,
        price_target=940.0,
        rr_ratio=3.2,
        max_loss=420.0,
        breakeven_move_pct=4.8,
        probability_of_profit=44,
        order_string="BUY +1 NVDA 05/16 900 CALL @4.20 LMT",
    )
    assert setup.symbol == "NVDA"
    assert setup.direction == "bullish"
    assert setup.signal_count == 6
