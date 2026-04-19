# backend/tests/test_bearish_detectors.py
import math
from datetime import date, timedelta

import pytest
from models import OptionChainData, OptionContract, MispricingSignal


def _make_chain(symbol="AAPL", stock_price=150.0, iv30=0.25, hv30=0.30,
                iv_rank=20.0, calls=None, puts=None):
    import datetime
    return OptionChainData(
        symbol=symbol, stock_price=stock_price,
        iv30=iv30, hv30=hv30, iv_rank=iv_rank,
        iv_percentile=20.0, timestamp=datetime.datetime.utcnow(),
        calls=calls or [], puts=puts or [],
    )


def _make_contract(strike, dte, iv, delta, bid, ask, oi=500, volume=200, is_put=False):
    expiry = date.today() + timedelta(days=dte)
    d = -abs(delta) if is_put else abs(delta)
    return OptionContract(
        strike=strike, expiry=expiry, dte=dte,
        bid=bid, ask=ask, mid=(bid + ask) / 2, last=(bid + ask) / 2,
        volume=volume, open_interest=oi, iv=iv,
        delta=d, gamma=0.01, theta=-0.05, vega=0.10,
        theoretical_value=(bid + ask) / 2,
        in_the_money=False,
    )


# ─── put_iv_rank ──────────────────────────────────────────────────────────────

def test_put_iv_rank_fires_when_iv_low_and_bearish_flow():
    from scanner import detect_put_iv_rank_cheap
    puts = [_make_contract(148, 35, 0.20, 0.45, 1.0, 1.2, oi=500, volume=300, is_put=True)
            for _ in range(5)]
    calls = [_make_contract(152, 35, 0.20, 0.45, 0.5, 0.7, oi=500, volume=100)
             for _ in range(2)]
    chain = _make_chain(iv_rank=18, iv30=0.20, hv30=0.30, puts=puts, calls=calls)
    signal = detect_put_iv_rank_cheap(chain)
    assert signal is not None
    assert signal.detector == "put_iv_rank"


def test_put_iv_rank_no_fire_when_iv_high():
    from scanner import detect_put_iv_rank_cheap
    chain = _make_chain(iv_rank=50)
    signal = detect_put_iv_rank_cheap(chain)
    assert signal is None


# ─── skew_inversion ───────────────────────────────────────────────────────────

def test_skew_inversion_fires_when_put_skew_flat():
    from scanner import detect_skew_inversion
    expiry = date.today() + timedelta(days=35)
    put = OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.22, -0.10,
                         0.01, -0.05, 0.10, 1.0, False)
    call = OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.21, 0.10,
                          0.01, -0.05, 0.10, 1.0, False)
    put.strike = 135; call.strike = 165
    chain = _make_chain(stock_price=150, puts=[put], calls=[call])
    signal = detect_skew_inversion(chain)
    assert signal is not None
    assert signal.detector == "skew_inversion"


def test_skew_inversion_no_fire_when_normal_skew():
    from scanner import detect_skew_inversion
    expiry = date.today() + timedelta(days=35)
    put = OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.35, -0.10,
                         0.01, -0.05, 0.10, 1.0, False)
    call = OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.20, 0.10,
                          0.01, -0.05, 0.10, 1.0, False)
    put.strike = 135; call.strike = 165
    chain = _make_chain(stock_price=150, puts=[put], calls=[call])
    signal = detect_skew_inversion(chain)
    assert signal is None


# ─── put_parity ───────────────────────────────────────────────────────────────

def test_put_parity_fires_when_put_underpriced():
    from scanner import detect_put_parity_violation
    expiry = date.today() + timedelta(days=35)
    S, K, r, T = 150.0, 150.0, 0.0525, 35 / 365
    call_mid = 4.0
    theoretical_put = call_mid - S + K * math.exp(-r * T)  # ≈ 3.72
    # Actual put trades below theoretical
    put_mid = theoretical_put * 0.90
    put = OptionContract(K, expiry, 35, put_mid - 0.1, put_mid + 0.1, put_mid, put_mid,
                         200, 500, 0.25, -0.48, 0.01, -0.05, 0.10, put_mid, False)
    call = OptionContract(K, expiry, 35, call_mid - 0.1, call_mid + 0.1, call_mid, call_mid,
                          200, 500, 0.25, 0.52, 0.01, -0.05, 0.10, call_mid, False)
    chain = _make_chain(stock_price=S, puts=[put], calls=[call])
    signal = detect_put_parity_violation(chain)
    assert signal is not None
    assert signal.detector == "put_parity"


# ─── downside_move ────────────────────────────────────────────────────────────

def test_downside_move_fires_when_implied_less_than_historical():
    from scanner import detect_downside_move_underpricing
    S = 150.0
    expiry = date.today() + timedelta(days=35)
    # ATM put with cheap premium (implied move < HV)
    put = OptionContract(S, expiry, 35, 2.0, 2.5, 2.25, 2.25,
                         300, 600, 0.25, -0.50, 0.01, -0.05, 0.10, 2.25, False)
    chain = _make_chain(stock_price=S, iv30=0.25, hv30=0.50, puts=[put])
    signal = detect_downside_move_underpricing(chain)
    assert signal is not None
    assert signal.detector == "downside_move"
