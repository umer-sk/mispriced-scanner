from datetime import date, datetime, timezone

from models import (CatalystContext, MispricingSignal, TechnicalContext,
                    TechnicalSetup, TradeSetup)
from forward_test import normalise


def _trade_setup(**over):
    sig = MispricingSignal(symbol="NVDA", detector="parity", description="",
                           confidence=0.8, raw_data={})
    cat = CatalystContext(earnings_date=None, earnings_dte=None,
                          earnings_in_window=False, iv_trend="STABLE",
                          iv_expansion_likely=False, recent_volume_spike=False,
                          catalyst_summary="")
    kwargs = dict(
        symbol="NVDA", stock_price=190.0, signal=sig, catalyst=cat,
        structure="bull_call_spread", long_strike=190.0, short_strike=200.0,
        expiry=date(2026, 10, 16), dte=35, net_debit=3.0, max_gain=7.0,
        max_loss=3.0, breakeven=193.0, breakeven_move_pct=1.6, rr_ratio=2.33,
        probability_of_profit=45.0, net_delta=0.3, net_theta=0.01,
        net_vega=0.05, long_leg_oi=800, short_leg_oi=600, long_leg_volume=300,
        long_leg_spread_pct=4.0, short_leg_spread_pct=4.0, liquidity_ok=True,
        scenarios_5d=[], scenarios_10d=[], scenarios_expiry=[], score=70,
        timestamp=datetime.now(timezone.utc), order_string="",
    )
    kwargs.update(over)
    return TradeSetup(**kwargs)


def _technical_setup(**over):
    kwargs = dict(
        symbol="AMD", stock_price=150.0, direction="bullish", signal_count=6,
        signal_details={}, structure="bull_call_spread", strike=150.0,
        short_strike=160.0, expiry=date(2026, 10, 16), dte=35, delta=0.45,
        iv_rank=40.0, premium=2.5, price_target=165.0, rr_ratio=3.0,
        max_loss=250.0, breakeven_move_pct=1.7, probability_of_profit=45.0,
        order_string="", earnings_within_dte=False,
        long_leg_oi=800, short_leg_oi=600, long_leg_spread_pct=4.0,
        short_leg_spread_pct=4.0, liquidity_ok=True,
        long_occ="AMD   261016C00150000", short_occ="AMD   261016C00160000",
    )
    kwargs.update(over)
    return TechnicalSetup(**kwargs)


def test_trade_setup_maps_net_debit_to_entry_debit():
    n = normalise(_trade_setup(), "scanner")
    assert n["entry_debit"] == 3.0
    assert n["quality"] == 70
    assert n["detector"] == "parity"


def test_technical_setup_maps_premium_and_strike():
    n = normalise(_technical_setup(), "technical")
    assert n["entry_debit"] == 2.5
    assert n["long_strike"] == 150.0
    assert n["quality"] == 6
    assert n["detector"] is None


def test_dedup_key_includes_strikes_and_expiry():
    n = normalise(_trade_setup(), "scanner")
    assert n["dedup_key"] == "NVDA|parity|bull_call_spread|2026-10-16|190.0|200.0"


def test_dedup_key_differs_when_strikes_move():
    a = normalise(_trade_setup(), "scanner")["dedup_key"]
    b = normalise(_trade_setup(long_strike=195.0), "scanner")["dedup_key"]
    assert a != b


def test_direction_derived_from_structure_for_trade_setup():
    assert normalise(_trade_setup(), "scanner")["direction"] == "bullish"
    assert normalise(_trade_setup(structure="bear_put_spread"),
                     "scanner")["direction"] == "bearish"


def test_technical_direction_taken_directly():
    assert normalise(_technical_setup(direction="bearish"),
                     "technical")["direction"] == "bearish"


def test_trend_opposes_when_bias_contradicts_structure():
    ctx = TechnicalContext(symbol="NVDA", price=190.0, ma50=180.0, ma200=170.0,
                           pct_from_ma50=5.0, pct_from_ma200=10.0,
                           trend="downtrend", bias="bearish")
    n = normalise(_trade_setup(technical_context=ctx), "scanner")
    assert n["trend_opposes"] is True


def test_missing_technical_context_counts_as_neutral():
    n = normalise(_trade_setup(technical_context=None), "scanner")
    assert n["trend_opposes"] is False


def test_technical_setups_never_oppose_their_own_trend_signal():
    # `direction` IS the trend read for this source, so the gate is tautological.
    assert normalise(_technical_setup(direction="bearish"),
                     "technical")["trend_opposes"] is False


def test_earnings_flag_reads_the_right_field_per_source():
    cat = CatalystContext(earnings_date=date(2026, 10, 1), earnings_dte=10,
                          earnings_in_window=True, iv_trend="RISING",
                          iv_expansion_likely=True, recent_volume_spike=False,
                          catalyst_summary="")
    assert normalise(_trade_setup(catalyst=cat), "scanner")["earnings_in_window"] is True
    assert normalise(_technical_setup(earnings_within_dte=True),
                     "technical")["earnings_in_window"] is True
