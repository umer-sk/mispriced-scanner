from datetime import date, datetime, timezone

from models import (CatalystContext, CeltSetup, MispricingSignal, TechnicalContext,
                    TechnicalSetup, TradeSetup)
from forward_test import classify, normalise


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
        long_occ="NVDA  261016C00190000", short_occ="NVDA  261016C00200000",
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


def _celt_setup(**over):
    kwargs = dict(
        symbol="NVDA", stock_price=100.0, timestamp=datetime.now(timezone.utc),
        signal_score=2.6, price_damage_score=1.0, volatility_score=0.8,
        sentiment_score=0.8, drawdown_pct=42.0, below_200sma=True,
        pct_from_200sma=-15.0, hv30=0.65, hv60=0.55, hv_ratio=2.1,
        hv_expansion=1.6, iv_rank=78.0, leap_put_call_oi_ratio=1.4,
        leap_strike=70.0, leap_expiry=date(2027, 6, 18), leap_dte=400,
        leap_delta=0.75, leap_ask=33.5, leap_bid=32.5, leap_mid=33.0,
        leap_oi=800, leap_iv=0.45, confidence=75, entry_notes="",
        leap_spread_pct=3.0, leap_occ="NVDA  270618C00070000",
        price_target=115.0, rr_ratio=0.7, max_loss=3350.0,
        breakeven=103.5, breakeven_move_pct=3.5,
    )
    kwargs.update(over)
    return CeltSetup(**kwargs)


def test_celt_setup_does_not_crash_normalise():
    # The load-bearing risk: normalise() used to read rr_ratio/breakeven_
    # move_pct/liquidity_ok/long_leg_spread_pct/short_leg_spread_pct
    # unconditionally, outside any per-source branch — a CeltSetup lacking
    # any one of them would crash normalise() outright on the first CELT
    # setup ever recorded.
    n = normalise(_celt_setup(), "celt")
    assert n["symbol"] == "NVDA"


def test_celt_setup_maps_leap_fields_onto_the_common_shape():
    n = normalise(_celt_setup(), "celt")
    assert n["structure"] == "celt_leap"
    assert n["long_strike"] == 70.0
    assert n["short_strike"] is None
    assert n["entry_debit"] == 33.5          # leap_ask, the worst-case fill
    assert n["quality"] == 75                # confidence
    assert n["direction"] == "bullish"
    assert n["detector"] is None
    assert n["long_occ"] == "NVDA  270618C00070000"
    assert n["short_occ"] == ""
    assert n["dte_at_entry"] == 400
    assert n["expiry"] == date(2027, 6, 18)


def test_celt_setup_earnings_in_window_is_always_false():
    # scan_celt_setups discards a symbol outright when earnings falls within
    # its own 7-day near-term check — nothing that reaches normalise() can
    # have failed it, so there is no field to read here (unlike the
    # scanner/technical branches, which do carry a real flag).
    assert normalise(_celt_setup(), "celt")["earnings_in_window"] is False


def test_celt_setup_gets_its_own_dte_band_and_rr_minimum():
    from celt_scanner import LEAP_DTE_MIN
    from forward_test import CELT_DTE_MAX, CELT_RR_MIN
    n = normalise(_celt_setup(), "celt")
    assert n["dte_min"] == LEAP_DTE_MIN
    assert n["dte_max"] == CELT_DTE_MAX
    assert n["rr_min"] == CELT_RR_MIN


def test_celt_setup_gets_its_own_quality_band():
    # Confirms QUALITY_MIN/QUALITY_NEAR["celt"] actually exist and are wired
    # in — a missing key here raises KeyError inside classify(), which
    # snapshot_setups' outer except swallows silently: CELT positions would
    # then never be recorded, with nothing anywhere surfacing why.
    from forward_test import QUALITY_MIN, QUALITY_NEAR
    n = normalise(_celt_setup(), "celt")
    assert n["quality_min"] == QUALITY_MIN["celt"] == 60
    assert n["quality_near"] == QUALITY_NEAR["celt"] == 15


def test_celt_setup_gets_a_wider_spread_and_breakeven_band():
    # Deep-ITM LEAPs genuinely quote 8-15% wide (celt_scanner.
    # LEAP_MAX_SPREAD_PCT=15.0) and single-leg breakeven runs much wider than
    # a spread's — the shared 6.0%/3.5% bands would fail every CELT position
    # regardless of quality.
    from forward_test import CELT_SPREAD_MAX_PCT
    n = normalise(_celt_setup(), "celt")
    assert n["spread_max"] == CELT_SPREAD_MAX_PCT == 12.0
    assert n["breakeven_max"] == 15.0
    assert n["breakeven_near"] == 20.0


def test_celt_setup_liquidity_ok_derived_from_leap_spread_and_oi():
    tight = normalise(_celt_setup(leap_spread_pct=3.0, leap_oi=800), "celt")
    assert tight["liquidity_ok"] is True
    assert tight["long_leg_spread_pct"] == 3.0
    assert tight["short_leg_spread_pct"] == 0.0

    wide = normalise(_celt_setup(leap_spread_pct=20.0), "celt")
    assert wide["liquidity_ok"] is False

    thin_oi = normalise(_celt_setup(leap_oi=50), "celt")
    assert thin_oi["liquidity_ok"] is False

    unknown = normalise(_celt_setup(leap_spread_pct=-1.0), "celt")
    assert unknown["liquidity_ok"] is False
    assert unknown["long_leg_spread_pct"] == 100.0   # unknown reads as maximally illiquid


def test_celt_setup_classifies_end_to_end_without_a_keyerror():
    # Runs the full normalise -> classify path a real snapshot_setups call
    # would take, not just normalise() in isolation.
    n = normalise(_celt_setup(), "celt")
    tier, failed = classify(n)
    assert tier in ("A", "B", "C")


def test_celt_setup_dedup_key_has_no_short_strike_component():
    n = normalise(_celt_setup(), "celt")
    assert n["dedup_key"] == "NVDA|celt|celt_leap|2027-06-18|70.0|"


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


def test_entry_mid_is_carried_when_the_setup_has_one():
    # entry_debit is the worst-case fill; entry_mid is the mid-to-mid entry
    # every mark is measured against. Both are needed to unpick the bias.
    n = normalise(_trade_setup(entry_mid=2.75), "scanner")
    assert n["entry_debit"] == 3.0
    assert n["entry_mid"] == 2.75
    t = normalise(_technical_setup(entry_mid=2.3), "technical")
    assert t["entry_mid"] == 2.3


def test_entry_mid_is_none_when_not_derivable_never_guessed():
    assert normalise(_trade_setup(), "scanner")["entry_mid"] is None
    assert normalise(_technical_setup(), "technical")["entry_mid"] is None


def test_200w_bounce_gets_its_own_detector_tag():
    # This is what lets aggregate()'s by_detector breakdown answer "does the
    # 200W bounce actually work" as a question separate from the 7-signal
    # consensus — without it both land in the same "-" bucket and the
    # question the forward test exists to answer becomes unanswerable.
    setup = _technical_setup(setup_type="200w_bounce")
    n = normalise(setup, "technical")
    assert n["detector"] == "200w_bounce"


def test_consensus_technical_setup_keeps_detector_none():
    setup = _technical_setup()  # default setup_type="consensus"
    n = normalise(setup, "technical")
    assert n["detector"] is None


def test_200w_bounce_gets_its_own_dte_band():
    from forward_test import BOUNCE_DTE_MIN, BOUNCE_DTE_MAX
    setup = _technical_setup(setup_type="200w_bounce", dte=75)
    n = normalise(setup, "technical")
    assert n["dte_min"] == BOUNCE_DTE_MIN
    assert n["dte_max"] == BOUNCE_DTE_MAX


def test_consensus_and_scanner_keep_the_original_dte_band():
    from forward_test import DTE_MIN, DTE_MAX
    n_tech = normalise(_technical_setup(), "technical")
    assert n_tech["dte_min"] == DTE_MIN and n_tech["dte_max"] == DTE_MAX
    n_scan = normalise(_trade_setup(), "scanner")
    assert n_scan["dte_min"] == DTE_MIN and n_scan["dte_max"] == DTE_MAX


def test_200w_bounce_gets_its_own_rr_minimum():
    # Mirrors the DTE override above, on the gate the 3rd review found still
    # missing one: without it, every bounce position (rr typically 1.5-2.0)
    # fails the shared RR_MIN=2.0 gate unconditionally, and since "rr" has no
    # near-miss band that means permanent Tier C regardless of the trade.
    from forward_test import BOUNCE_RR_MIN
    setup = _technical_setup(setup_type="200w_bounce")
    n = normalise(setup, "technical")
    assert n["rr_min"] == BOUNCE_RR_MIN


def test_consensus_and_scanner_keep_the_original_rr_minimum():
    from forward_test import RR_MIN
    n_tech = normalise(_technical_setup(), "technical")
    assert n_tech["rr_min"] == RR_MIN
    n_scan = normalise(_trade_setup(), "scanner")
    assert n_scan["rr_min"] == RR_MIN


def test_200w_bounce_gets_its_own_quality_band():
    # The bounce's signal_count is hardcoded to 4 (its own 4 qualifying
    # criteria — see technical_scanner._construct_200w_bounce_long_call —
    # not a fraction of the 7-signal consensus). Without this override, 4 is
    # permanently read against the consensus's QUALITY_MIN["technical"]=5.
    from forward_test import BOUNCE_QUALITY_MIN, BOUNCE_QUALITY_NEAR
    setup = _technical_setup(setup_type="200w_bounce", signal_count=4)
    n = normalise(setup, "technical")
    assert n["quality_min"] == BOUNCE_QUALITY_MIN == 4
    assert n["quality_near"] == BOUNCE_QUALITY_NEAR == 1


def test_consensus_and_scanner_keep_the_original_quality_band():
    from forward_test import QUALITY_MIN, QUALITY_NEAR
    n_tech = normalise(_technical_setup(), "technical")
    assert n_tech["quality_min"] == QUALITY_MIN["technical"]
    assert n_tech["quality_near"] == QUALITY_NEAR["technical"]
    n_scan = normalise(_trade_setup(), "scanner")
    assert n_scan["quality_min"] == QUALITY_MIN["scanner"]
    assert n_scan["quality_near"] == QUALITY_NEAR["scanner"]


def test_single_leg_structure_gets_a_wider_breakeven_band():
    # breakeven_move_pct for a single-leg long option is extrinsic/spot
    # exactly — measured 4.3-9.2% across realistic IV, well past the
    # spread-calibrated shared 3.5% band.
    n = normalise(_technical_setup(structure="long_call", short_strike=None), "technical")
    assert n["breakeven_max"] == 7.0
    assert n["breakeven_near"] == 10.0


def test_spread_structure_keeps_the_default_breakeven_band():
    from forward_test import BREAKEVEN_MAX_PCT, BREAKEVEN_NEAR_PCT
    n = normalise(_trade_setup(), "scanner")  # bull_call_spread
    assert n["breakeven_max"] == BREAKEVEN_MAX_PCT
    assert n["breakeven_near"] == BREAKEVEN_NEAR_PCT
