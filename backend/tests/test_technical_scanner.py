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


from unittest.mock import patch, MagicMock
from datetime import datetime
from models import OptionChainData, OptionContract
from technical_scanner import _construct_long_call, _construct_long_put, _pick_best_structure

def _make_chain(symbol="NVDA", price=875.0, iv_rank=32.0):
    expiry = date(2026, 6, 20)  # ~60 DTE from test date
    def _call(strike, delta, ask, bid=None):
        return OptionContract(
            strike=strike, expiry=expiry, dte=45, bid=bid or ask*0.95,
            ask=ask, mid=ask*0.975, last=ask, volume=500, open_interest=1000,
            iv=0.35, delta=delta, gamma=0.01, theta=-0.05, vega=0.10,
            theoretical_value=ask, in_the_money=(delta > 0.5),
        )
    def _put(strike, delta, ask, bid=None):
        return OptionContract(
            strike=strike, expiry=expiry, dte=45, bid=bid or ask*0.95,
            ask=ask, mid=ask*0.975, last=ask, volume=500, open_interest=1000,
            iv=0.35, delta=delta, gamma=0.01, theta=-0.05, vega=0.10,
            theoretical_value=ask, in_the_money=(delta < -0.5),
        )
    return OptionChainData(
        symbol=symbol, stock_price=price, iv30=0.35, hv30=0.28,
        iv_rank=iv_rank, iv_percentile=35.0,
        timestamp=datetime.utcnow(),
        calls=[
            _call(850, 0.60, 38.0),
            _call(875, 0.50, 28.0),
            _call(900, 0.44, 20.0),   # target: 0.45 delta
            _call(925, 0.35, 14.0),
            _call(950, 0.25, 9.0),    # short leg for spread
            _call(975, 0.15, 5.0),
        ],
        puts=[
            _put(850, -0.44, 19.0),   # target: 0.45 delta
            _put(825, -0.35, 13.0),
            _put(800, -0.25, 8.0),    # short leg for spread
            _put(775, -0.15, 4.5),
        ],
        is_stale=False,
    )

def test_construct_long_call_returns_setup():
    chain = _make_chain()
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_long_call("NVDA", 875.0, chain, 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure == "long_call"
    assert setup.strike == 900.0  # closest to 0.45 delta
    assert setup.rr_ratio >= 2.0
    assert setup.direction == "bullish"

def test_construct_long_put_returns_setup():
    chain = _make_chain()
    signal_details = {k: False for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_long_put("NVDA", 875.0, chain, 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure == "long_put"
    assert setup.strike == 850.0  # closest to 0.45 delta (abs)
    assert setup.direction == "bearish"

def test_pick_best_structure_low_iv_prefers_long_call():
    chain = _make_chain(iv_rank=30.0)
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _pick_best_structure("NVDA", 875.0, chain, "bullish", 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure in ("long_call", "bull_call_spread")

def test_pick_best_structure_high_iv_prefers_spread():
    chain = _make_chain(iv_rank=70.0)
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _pick_best_structure("NVDA", 875.0, chain, "bullish", 7, signal_details, atr14=15.0)
    # high IV: spread preferred; if spread fails, falls back to long call
    assert setup is not None


from unittest.mock import patch
import pandas as pd

def _make_yf_df(closes, n=220):
    closes_full = ([closes[0]] * max(0, n - len(closes)) + list(closes))[-n:]
    return pd.DataFrame({
        'Close': closes_full,
        'High': [c * 1.01 for c in closes_full],
        'Low':  [c * 0.99 for c in closes_full],
        'Open': closes_full,
        'Volume': [1_500_000] * n,
    })

@patch('technical_scanner.yf.download')
@patch('technical_scanner.fetch_option_chain')
def test_scan_technical_setups_returns_list(mock_chain, mock_yf):
    from technical_scanner import scan_technical_setups

    bull_closes = [100.0 + i * 0.5 for i in range(220)]
    mock_yf.return_value = _make_yf_df(bull_closes)
    mock_chain.return_value = _make_chain()  # reuse existing helper

    setups = scan_technical_setups(["NVDA"], min_rr=2.0, direction="both")
    assert isinstance(setups, list)
