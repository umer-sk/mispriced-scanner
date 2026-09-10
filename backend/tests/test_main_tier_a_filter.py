"""/opportunities, /celt-setups: read-time filtering is now Tier A only
(main._is_tier_a -> forward_test.classify/normalise), replacing the old
min_rr/min_score sliders. See test_technical_scanner.py for the /technical-
setups equivalent.
"""
from datetime import date, datetime, timedelta, timezone

import main
from models import CatalystContext, CeltSetup, MispricingSignal, TradeSetup


def _catalyst(earnings_in_window=False):
    return CatalystContext(
        earnings_date=None, earnings_dte=None, earnings_in_window=earnings_in_window,
        iv_trend="STABLE", iv_expansion_likely=False, recent_volume_spike=False,
        catalyst_summary="",
    )


def _signal(detector="parity"):
    return MispricingSignal(symbol="NVDA", detector=detector, description="", confidence=0.8, raw_data={})


def _trade_setup(score, rr_ratio=2.5, breakeven_move_pct=2.0, dte=45):
    return TradeSetup(
        symbol="NVDA", stock_price=100.0, signal=_signal(), catalyst=_catalyst(),
        structure="bull_call_spread", long_strike=100.0, short_strike=105.0,
        expiry=date.today() + timedelta(days=dte), dte=dte,
        net_debit=2.0, max_gain=3.0, max_loss=2.0, breakeven=102.0,
        breakeven_move_pct=breakeven_move_pct, rr_ratio=rr_ratio, probability_of_profit=50.0,
        net_delta=0.3, net_theta=-0.05, net_vega=0.1,
        long_leg_oi=500, short_leg_oi=500, long_leg_volume=200,
        long_leg_spread_pct=4.0, short_leg_spread_pct=4.0, liquidity_ok=True,
        scenarios_5d=[], scenarios_10d=[], scenarios_expiry=[],
        score=score, timestamp=datetime.now(timezone.utc), order_string="BUY +1 NVDA CALL",
    )


def _celt_setup(confidence, rr_ratio=1.0, breakeven_move_pct=10.0, dte=400):
    return CeltSetup(
        symbol="NVDA", stock_price=100.0, timestamp=datetime.now(timezone.utc),
        signal_score=2.6, price_damage_score=0.8, volatility_score=0.8, sentiment_score=1.0,
        drawdown_pct=30.0, below_200sma=True, pct_from_200sma=-15.0,
        hv30=0.6, hv60=0.5, hv_ratio=1.5, hv_expansion=1.2,
        iv_rank=70.0, leap_put_call_oi_ratio=1.5,
        leap_strike=70.0, leap_expiry=date.today() + timedelta(days=dte), leap_dte=dte,
        leap_delta=0.75, leap_ask=32.0, leap_bid=30.0, leap_mid=31.0, leap_oi=500, leap_iv=0.55,
        confidence=confidence, entry_notes="", leap_spread_pct=5.0,
        rr_ratio=rr_ratio, breakeven_move_pct=breakeven_move_pct,
    )


def test_opportunities_endpoint_surfaces_only_tier_a(monkeypatch):
    # score=55 used to clear the old default (min_score=55); Tier A needs
    # >=60, so this one must now be excluded on its own.
    below = _trade_setup(score=55)
    above = _trade_setup(score=65)
    main._cache["opportunities"] = [below, above]
    main._cache["scan_timestamp"] = datetime.now(timezone.utc)
    main._cache["symbols_scanned"] = 1

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    resp = client.get("/opportunities")
    scores = [o["score"] for o in resp.json()["opportunities"]]
    assert scores == [65]


def test_celt_setups_endpoint_surfaces_only_tier_a(monkeypatch):
    # confidence=45 used to clear the old default (min_score=2.2 on the raw
    # signal_score scale); Tier A needs confidence>=60.
    below = _celt_setup(confidence=45)
    above = _celt_setup(confidence=65)
    main._cache["celt_setups"] = [below, above]
    main._cache["celt_timestamp"] = datetime.now(timezone.utc)
    main._cache["celt_last_attempt"] = datetime.now(timezone.utc)

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    resp = client.get("/celt-setups")
    confidences = [s["confidence"] for s in resp.json()["setups"]]
    assert confidences == [65]


def test_is_tier_a_falls_back_to_attr_tolerant_check_for_a_plain_dict():
    """A Supabase-restored cache entry (cold start, before the next scan
    tick) is a plain dict — normalise() does real attribute access
    (setup.signal.detector etc.) and would crash on one. _is_tier_a must
    fall back instead of raising, so setups don't vanish on a restart."""
    passing = {"rr_ratio": 2.5, "score": 65}
    failing = {"rr_ratio": 1.0, "score": 65}
    assert main._is_tier_a(passing, "scanner") is True
    assert main._is_tier_a(failing, "scanner") is False
