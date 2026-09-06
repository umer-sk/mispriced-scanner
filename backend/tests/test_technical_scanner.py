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
from technical_scanner import (_construct_long_call, _construct_long_put, _pick_best_structure,
                                _construct_bull_call_spread_technical, _construct_bear_put_spread_technical)

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
    # atr14=30, not 15: price_target now scales as sqrt(dte/10), not dte/10 (see
    # _atr_price_target). At dte=45 that is ~2.12x ATR instead of ~4.5x, so the
    # old atr14=15 no longer clears the rr_ratio >= 2.0 gate.
    setup = _construct_long_call("NVDA", 875.0, chain, 7, signal_details, atr14=30.0)
    assert setup is not None
    assert setup.structure == "long_call"
    assert setup.strike == 900.0  # closest to 0.45 delta
    assert setup.rr_ratio >= 2.0
    assert setup.direction == "bullish"
    # Pins the actual number, not just "a setup exists" — a construct_long_call
    # that stopped delegating to _atr_price_target (e.g. inlined the old
    # linear dte/10 formula again) would still pass rr_ratio >= 2.0 here with
    # atr14=30, since a bigger, wrong target only helps the gate. Only an
    # exact-value assertion catches that regression.
    from technical_scanner import _atr_price_target
    expected_target = round(_atr_price_target(875.0, 30.0, setup.dte, bullish=True), 2)
    assert setup.price_target == expected_target

def test_construct_long_put_returns_setup():
    chain = _make_chain()
    signal_details = {k: False for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    # See test_construct_long_call_returns_setup for why atr14 changed from 15.
    setup = _construct_long_put("NVDA", 875.0, chain, 7, signal_details, atr14=30.0)
    assert setup is not None
    assert setup.structure == "long_put"
    assert setup.strike == 850.0  # closest to 0.45 delta (abs)
    assert setup.direction == "bearish"
    from technical_scanner import _atr_price_target
    expected_target = round(_atr_price_target(875.0, 30.0, setup.dte, bullish=False), 2)
    assert setup.price_target == expected_target

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


# ─── ATR price-target scaling ────────────────────────────────────────────────

def test_atr_price_target_matches_original_calibration_at_10_dte():
    # sqrt(10/10) == 10/10 == 1, so the new formula must agree with the old
    # one exactly at the point it was originally calibrated against.
    from technical_scanner import _atr_price_target
    assert _atr_price_target(100.0, 4.0, 10, bullish=True) == 106.0
    assert _atr_price_target(100.0, 4.0, 10, bullish=False) == 94.0


def test_atr_price_target_scales_as_sqrt_not_linear():
    from technical_scanner import _atr_price_target
    move_10d = _atr_price_target(100.0, 4.0, 10, bullish=True) - 100.0
    move_40d = _atr_price_target(100.0, 4.0, 40, bullish=True) - 100.0
    # Linear (the bug) would give exactly 4x; sqrt gives 2x.
    assert abs(move_40d / move_10d - 2.0) < 0.001


def test_atr_price_target_no_longer_overstates_at_41_dte():
    """Reproduces the live PANW case from production: shipped target implied a
    -37.8% move on a 41 DTE put; the corrected target implies about -18.7%."""
    from technical_scanner import _atr_price_target
    spot, atr = 333.26, 20.47
    target = _atr_price_target(spot, atr, 41, bullish=False)
    move_pct = (target - spot) / spot * 100
    assert -20.0 < move_pct < -17.0   # was -37.8% under the linear formula


# ─── spread constructors: no test existed for either before this, so the
# linear-vs-sqrt ATR mutation on their price_target lines was invisible ───────

def test_construct_bull_call_spread_technical_price_target():
    chain = _make_chain()
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_bull_call_spread_technical("NVDA", 875.0, chain, 7, signal_details, atr14=30.0)
    assert setup is not None
    assert setup.structure == "bull_call_spread"
    assert setup.strike == 900.0 and setup.short_strike == 950.0
    from technical_scanner import _atr_price_target
    expected = round(_atr_price_target(875.0, 30.0, setup.dte, bullish=True), 2)
    assert setup.price_target == expected


def test_construct_bear_put_spread_technical_price_target():
    chain = _make_chain()
    signal_details = {k: False for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_bear_put_spread_technical("NVDA", 875.0, chain, 7, signal_details, atr14=30.0)
    assert setup is not None
    assert setup.structure == "bear_put_spread"
    from technical_scanner import _atr_price_target
    expected = round(_atr_price_target(875.0, 30.0, setup.dte, bullish=False), 2)
    assert setup.price_target == expected


def test_order_string_does_not_round_half_strikes():
    """f'{87.5:.0f}' silently rounds to '88' — a copy-pasted order for the
    wrong contract on any $2.50-strike-increment name (AMD, INTC, CSCO, PYPL,
    ...). All four order_string sites must use a format that preserves it."""
    chain = _make_chain()
    # Shift every strike by +0.5 rather than collapsing them to one value —
    # spread construction needs distinct long/short strikes to have a
    # positive width.
    for c in chain.calls + chain.puts:
        c.strike += 0.5
    signal_details_bull = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    signal_details_bear = {k: False for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}

    call_setup = _construct_long_call("NVDA", 875.0, chain, 7, signal_details_bull, atr14=30.0)
    assert call_setup is not None
    assert call_setup.strike == 900.5
    assert "900.5" in call_setup.order_string and "901 " not in call_setup.order_string

    put_setup = _construct_long_put("NVDA", 875.0, chain, 7, signal_details_bear, atr14=30.0)
    assert put_setup is not None
    assert put_setup.strike == 850.5
    assert "850.5" in put_setup.order_string

    bull_spread = _construct_bull_call_spread_technical("NVDA", 875.0, chain, 7, signal_details_bull, atr14=30.0)
    assert bull_spread is not None
    assert "900.5/950.5" in bull_spread.order_string

    bear_spread = _construct_bear_put_spread_technical("NVDA", 875.0, chain, 7, signal_details_bear, atr14=30.0)
    assert bear_spread is not None
    assert "850.5/800.5" in bear_spread.order_string
