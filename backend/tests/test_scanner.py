"""Characterization tests for scanner.py's spread construction and scoring.

These pin CURRENT behavior before the fix-all changeset touches
construct_best_spread/construct_bear_put_spread/score_swing_quality/
compute_score_breakdown/run_all_detectors — signal-to-structure anchoring,
detector dedup, the dividend fix, POP, the DTE band, and the liquidity-ok
volume gate all land on top of this file. Where a test pins behavior that
the plan deliberately changes, it's marked so the update is a deliberate,
understood edit rather than a surprise regression.

All bull-spread fixtures use a long leg at strike S (delta 0.50, ~5.00 mid)
and a short leg 12 points out (delta ~0.20-0.28, ~2.00 mid) unless a test
needs different economics to isolate one specific gate — that combination
clears the constructor's own 35%-of-width debit ceiling (debit ~3.2 / width
12 = 27%) without ever engaging the "retry a wider strike" path, so each
test exercises exactly the gate it names.
"""
from datetime import date, datetime, timedelta, timezone

from models import CatalystContext, MispricingSignal, OptionChainData, OptionContract
from scanner import (
    RISK_FREE_RATE,
    compute_score_breakdown,
    construct_bear_put_spread,
    construct_best_spread,
    run_all_detectors,
    score_swing_quality,
)


def _contract(strike, dte, iv=0.30, delta=0.30, bid=2.0, ask=2.2, oi=500, volume=200, is_put=False):
    expiry = date.today() + timedelta(days=dte)
    d = -abs(delta) if is_put else abs(delta)
    return OptionContract(
        strike=strike, expiry=expiry, dte=dte,
        bid=bid, ask=ask, mid=round((bid + ask) / 2, 2), last=(bid + ask) / 2,
        volume=volume, open_interest=oi, iv=iv, delta=d,
        gamma=0.01, theta=-0.05, vega=0.10,
        theoretical_value=(bid + ask) / 2, in_the_money=False,
    )


def _chain(calls=None, puts=None, stock_price=100.0, iv30=0.30, hv30=0.28, iv_rank=40.0):
    return OptionChainData(
        symbol="TEST", stock_price=stock_price, iv30=iv30, hv30=hv30,
        iv_rank=iv_rank, iv_percentile=iv_rank,
        timestamp=datetime.now(timezone.utc), calls=calls or [], puts=puts or [],
    )


def _catalyst(earnings_date=None, earnings_in_window=False, iv_expansion_likely=False):
    return CatalystContext(
        earnings_date=earnings_date, earnings_dte=None,
        earnings_in_window=earnings_in_window, iv_trend="STABLE",
        iv_expansion_likely=iv_expansion_likely, recent_volume_spike=False,
        catalyst_summary="test",
    )


def _signal(detector="parity", raw_data=None):
    return MispricingSignal(
        symbol="TEST", detector=detector, description="test signal",
        confidence=0.75, raw_data=raw_data or {},
    )


def _long_short(strike, short_strike, dte, delta_short=0.22, oi=300, volume=200,
                 long_bid=4.9, long_ask=5.1, short_bid=1.9, short_ask=2.1, is_put=False):
    """A clean, gate-clearing long/short pair: width = |short_strike - strike|,
    debit = long_ask - short_bid. With the defaults (width 12, debit 3.2) that's
    27% of width — comfortably under the 35% ceiling with no retry needed."""
    return [
        _contract(strike, dte, delta=0.50, bid=long_bid, ask=long_ask, oi=oi, volume=volume, is_put=is_put),
        _contract(short_strike, dte, delta=delta_short, bid=short_bid, ask=short_ask, oi=oi, volume=volume, is_put=is_put),
    ]


def _bull_chain(**kwargs):
    calls = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300),
        _contract(105, 35, delta=0.32, bid=3.6, ask=3.8, oi=300),   # delta too high, excluded from band
        _contract(112, 35, delta=0.22, bid=1.9, ask=2.1, oi=300),   # only in-band candidate
        _contract(120, 35, delta=0.10, bid=0.5, ask=0.7, oi=300),   # delta too low, excluded
    ]
    return _chain(calls=calls, **kwargs)


def _bear_chain(**kwargs):
    puts = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300, is_put=True),
        _contract(95, 35, delta=0.32, bid=3.6, ask=3.8, oi=300, is_put=True),   # too high, excluded
        _contract(88, 35, delta=0.22, bid=1.9, ask=2.1, oi=300, is_put=True),   # only in-band candidate
        _contract(80, 35, delta=0.10, bid=0.5, ask=0.7, oi=300, is_put=True),   # too low, excluded
    ]
    return _chain(puts=puts, **kwargs)


# ─── expiry selection ─────────────────────────────────────────────────────

def test_picks_the_first_candidate_expiry_in_28_50_dte_window():
    calls = _long_short(100, 112, 60) + _long_short(100, 112, 35)
    chain = _chain(calls=calls)
    setup = construct_best_spread(_signal(), chain, _catalyst())
    assert setup is not None
    assert setup.dte == 35


def test_falls_back_to_soonest_candidate_when_none_sit_in_28_50():
    calls = _long_short(100, 112, 60) + _long_short(100, 112, 90)
    chain = _chain(calls=calls)
    setup = construct_best_spread(_signal(), chain, _catalyst())
    assert setup is not None
    assert setup.dte == 60  # soonest of the two candidates


def test_earnings_redirect_picks_the_post_earnings_expiry_over_the_soonest_candidate():
    """Only fires when no candidate sits in 28-50 DTE — see scanner.py STEP 1.
    Both expiries stay within the 30-60 DTE hard gate (STEP 6, aligned with
    forward_test.py's own band). earnings_date = today+48; the 58-DTE expiry
    is 10 days after (in 7-14), the 52-DTE expiry is only 4 days after (not
    in 7-14) and is not in 28-50 either, so without the earnings context the
    fallback would pick the soonest candidate (52) instead."""
    earnings = date.today() + timedelta(days=48)
    calls = _long_short(100, 112, 52) + _long_short(100, 112, 58)
    chain = _chain(calls=calls)
    catalyst = _catalyst(earnings_date=earnings, earnings_in_window=True)
    setup = construct_best_spread(_signal(), chain, catalyst)
    assert setup is not None
    assert setup.dte == 58

    # Without earnings context, the same chain falls back to the soonest candidate.
    setup_no_earnings = construct_best_spread(_signal(), chain, _catalyst())
    assert setup_no_earnings.dte == 52


# ─── strike selection ─────────────────────────────────────────────────────

def test_bull_long_leg_is_closest_atm_at_or_below_spot():
    setup = construct_best_spread(_signal(), _bull_chain(), _catalyst())
    assert setup is not None
    assert setup.long_strike == 100.0


def test_bull_short_leg_targets_015_030_delta_band():
    setup = construct_best_spread(_signal(), _bull_chain(), _catalyst())
    assert setup is not None
    assert setup.short_strike == 112.0  # only candidate with delta in [0.15, 0.30]


def test_bear_long_leg_is_closest_atm_at_or_above_spot():
    setup = construct_bear_put_spread(_signal(detector="put_parity"), _bear_chain(), _catalyst())
    assert setup is not None
    assert setup.long_strike == 100.0


def test_bear_short_leg_targets_015_030_delta_band():
    setup = construct_bear_put_spread(_signal(detector="put_parity"), _bear_chain(), _catalyst())
    assert setup is not None
    assert setup.short_strike == 88.0


def test_widens_short_strike_when_initial_debit_exceeds_35pct_of_width():
    # 103 is the first in-band (delta 0.28) candidate by strike order, but its
    # debit (1.8) exceeds 35% of its 3-wide spread (1.05) — the constructor
    # must retry the next higher strike (112) rather than reject outright.
    calls = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300),
        _contract(103, 35, delta=0.28, bid=3.3, ask=3.5, oi=300),
        _contract(112, 35, delta=0.20, bid=1.9, ask=2.1, oi=300),
    ]
    chain = _chain(calls=calls)
    setup = construct_best_spread(_signal(), chain, _catalyst())
    assert setup is not None
    assert setup.short_strike == 112.0


# ─── hard gates (pin current bounds; DTE 30-100 changes to 30-60 later) ───

def test_rejects_rr_below_2_to_1():
    calls = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300),
        _contract(103, 35, delta=0.28, bid=3.0, ask=3.2, oi=300),  # width 3, richly priced -> RR too low
    ]
    chain = _chain(calls=calls)
    assert construct_best_spread(_signal(), chain, _catalyst()) is None


def test_rejects_when_no_expiry_in_30_100_dte_window_at_all():
    calls = [_contract(100, 20, delta=0.50, bid=2.0, ask=2.2, oi=300)]  # 20 DTE, below 30
    chain = _chain(calls=calls)
    assert construct_best_spread(_signal(), chain, _catalyst()) is None


def test_dte_hard_gate_is_30_to_60_inclusive():
    """Tightened from 30-100, moved together with forward_test.py's DTE band
    (see scanner.py STEP 6's comment) — a constructor that could still emit
    a >60 DTE setup would produce something the forward test always
    Tier-Cs on a pure timing mismatch."""
    within = _long_short(100, 112, 60)
    chain_within = _chain(calls=within)
    assert construct_best_spread(_signal(), chain_within, _catalyst()) is not None

    outside = _long_short(100, 112, 90)
    chain_outside = _chain(calls=outside)
    assert construct_best_spread(_signal(), chain_outside, _catalyst()) is None


def test_rejects_when_open_interest_below_100():
    calls = _long_short(100, 112, 35, oi=300)
    calls[0] = _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=50)  # long leg OI below 100
    chain = _chain(calls=calls)
    assert construct_best_spread(_signal(), chain, _catalyst()) is None


def test_rejects_when_spread_wider_than_15pct():
    calls = _long_short(100, 112, 35, long_bid=4.0, long_ask=6.0)  # 40% wide on the long leg
    chain = _chain(calls=calls)
    assert construct_best_spread(_signal(), chain, _catalyst()) is None


def test_liquidity_ok_no_longer_requires_volume_zero_volume_still_passes():
    """volume>=50 was removed from liquidity_ok — it made every 08:00 ET
    pre-market scan come back empty, since option volume is 0 before the
    open. OI and spread still gate it; only volume stopped."""
    calls = _long_short(100, 112, 35, volume=0)
    chain = _chain(calls=calls)
    setup = construct_best_spread(_signal(), chain, _catalyst())
    assert setup is not None
    assert setup.liquidity_ok is True


def test_liquidity_ok_still_fails_on_a_wide_spread_regardless_of_volume():
    # 12% spread: clears the 15% hard-reject gate but fails liquidity_ok's 10% bar.
    calls = _long_short(100, 112, 35, short_bid=1.88, short_ask=2.12)
    chain = _chain(calls=calls)
    setup = construct_best_spread(_signal(), chain, _catalyst())
    assert setup is not None
    assert setup.liquidity_ok is False


# ─── economics pinned for the current formulas (POP, RISK_FREE_RATE) ─────

def test_probability_of_profit_is_a_real_breakeven_probability_not_delta():
    """Replaces the |delta|*100 proxy (P of finishing ITM at all — a looser
    bar than beating breakeven). For this fixture (breakeven 103.2, 35 DTE,
    IV 30%, mu=0) the real model gives 35.0%, well below the 50.0 the old
    delta proxy reported."""
    setup = construct_best_spread(_signal(), _bull_chain(), _catalyst())
    assert setup is not None
    assert setup.probability_of_profit == 35.0


def test_risk_free_rate_constant_is_a_plausible_rate():
    # Not a construction test — just pins that RISK_FREE_RATE is the single
    # source of truth other tests/fixtures should import rather than hardcode.
    assert 0.0 < RISK_FREE_RATE < 0.15


# ─── scoring ───────────────────────────────────────────────────────────────

def test_score_swing_quality_awards_parity_and_rr_and_dte_points():
    setup = construct_best_spread(_signal(detector="parity"), _bull_chain(), _catalyst())
    assert setup is not None
    score = score_swing_quality(setup)
    # parity (20) + rr in [2.0,3.0) (10) + dte 28-50 (10) at minimum.
    assert score >= 20 + 10 + 10


def test_compute_score_breakdown_sums_to_the_score_for_a_single_detector():
    setup = construct_best_spread(_signal(detector="parity"), _bull_chain(), _catalyst())
    assert setup is not None
    setup.score = score_swing_quality(setup)
    items = compute_score_breakdown(setup)
    assert sum(i["pts"] for i in items) == setup.score


# ─── run_all_detectors routing ─────────────────────────────────────────────

def test_run_all_detectors_bullish_only_produces_bull_call_spreads():
    setups = run_all_detectors(_bull_chain(iv_rank=15.0, iv30=0.20, hv30=0.30), _catalyst(), direction="bullish")
    assert all(s.structure == "bull_call_spread" for s in setups)


def test_run_all_detectors_bearish_only_produces_bear_put_spreads():
    setups = run_all_detectors(_bear_chain(iv_rank=15.0, iv30=0.20, hv30=0.30), _catalyst(), direction="bearish")
    assert all(s.structure == "bear_put_spread" for s in setups)


# ─── detector merge / contributing_detectors ───────────────────────────────

def test_mispricing_points_sums_distinct_categories_and_caps_at_35():
    from scanner import _mispricing_points
    pts, items = _mispricing_points(["parity", "skew", "move"], {"iv_rank": 15.0})
    # 20 (parity) + 10 (skew) + 10 (move) + 15 (iv_rank) = 55, capped to 35
    assert pts == 35
    assert sum(i["pts"] for i in items) == 35

    pts2, items2 = _mispricing_points(["skew"], {})
    assert pts2 == 10
    assert sum(i["pts"] for i in items2) == 10


def test_run_all_detectors_merges_setups_that_construct_identically(monkeypatch):
    """Two unrelated bullish detectors (iv_rank, move) can legitimately
    construct the identical trade off the same chain+catalyst, since neither
    anchors to a specific contract. run_all_detectors must merge them into
    one setup, not surface the same physical trade twice."""
    import scanner as scanner_module

    sig_a = _signal(detector="iv_rank", raw_data={"iv_rank": 12.0})
    sig_b = _signal(detector="move", raw_data={"underpricing_ratio": 0.7})
    monkeypatch.setattr(scanner_module, "detect_iv_rank_cheap", lambda chain: sig_a)
    monkeypatch.setattr(scanner_module, "detect_skew_anomaly", lambda chain: None)
    monkeypatch.setattr(scanner_module, "detect_parity_violation", lambda chain, dividend_yield=0.0: None)
    monkeypatch.setattr(scanner_module, "detect_term_structure_gap", lambda chain, earnings_date=None: None)
    monkeypatch.setattr(scanner_module, "detect_move_underpricing", lambda chain: sig_b)

    setups = scanner_module.run_all_detectors(_bull_chain(), _catalyst(), direction="bullish")

    assert len(setups) == 1
    assert setups[0].contributing_detectors == ["iv_rank", "move"]
    assert setups[0].signal.raw_data.get("iv_rank") == 12.0   # merged in, not lost
    assert setups[0].signal.raw_data.get("underpricing_ratio") == 0.7
    # Mispricing points now include BOTH categories (move=10) plus iv_rank
    # (15, since merged raw_data["iv_rank"]=12.0 < 20) = 25, not just one.
    mispricing_pts, _ = scanner_module._mispricing_points(
        setups[0].contributing_detectors, setups[0].signal.raw_data,
    )
    assert mispricing_pts == 25


# ─── skew / skew_inversion direction tie-break ─────────────────────────────

def test_skew_and_skew_inversion_discriminate_by_which_side_is_actually_cheap():
    """Same flat raw_skew (0.04 < 0.05) on both sides, but only the call leg
    reads cheap vs the same-expiry ATM IV — only 'skew' should fire, not
    both detectors off the identical raw_skew reading."""
    from scanner import detect_skew_anomaly, detect_skew_inversion
    expiry = date.today() + timedelta(days=35)
    atm_call = OptionContract("", expiry, 35, 5.9, 6.1, 6.0, 6.0, 200, 500, 0.30, 0.50,
                              0.01, -0.05, 0.10, 6.0, False)
    atm_call.strike = 150
    atm_put = OptionContract("", expiry, 35, 5.9, 6.1, 6.0, 6.0, 200, 500, 0.30, -0.50,
                             0.01, -0.05, 0.10, 6.0, False)
    atm_put.strike = 150
    call_10d = OptionContract("", expiry, 35, 0.9, 1.1, 1.0, 1.0, 200, 500, 0.24, 0.10,
                              0.01, -0.05, 0.10, 1.0, False)
    call_10d.strike = 175
    put_10d = OptionContract("", expiry, 35, 0.9, 1.1, 1.0, 1.0, 200, 500, 0.28, -0.10,
                             0.01, -0.05, 0.10, 1.0, False)
    put_10d.strike = 125
    chain = _chain(calls=[atm_call, call_10d], puts=[atm_put, put_10d], stock_price=150.0)

    assert detect_skew_anomaly(chain) is not None    # call side is cheap vs ATM
    assert detect_skew_inversion(chain) is None       # put side is not


# ─── term structure expiry pinning ─────────────────────────────────────────

def test_term_structure_pins_atm_to_the_exact_expiry_not_a_dte_range():
    """A 3-DTE weekly at spot with elevated IV must not be mistaken for the
    real (20-DTE) expiry_1 contract just because both fall under a
    dte<=dte_1 range search — only contracts at the exact expiry_1 date
    should be considered."""
    from scanner import detect_term_structure_gap
    weekly = _contract(100, 3, iv=0.60, delta=0.50, bid=1.0, ask=1.1, oi=300)
    near = _contract(101, 20, iv=0.30, delta=0.48, bid=3.0, ask=3.1, oi=300)
    far = _contract(100, 40, iv=0.25, delta=0.50, bid=4.0, ask=4.1, oi=300)
    chain = _chain(calls=[weekly, near, far], stock_price=100.0)
    signal = detect_term_structure_gap(chain)
    assert signal is not None
    assert signal.raw_data["iv_near"] == 0.30  # real expiry_1 IV, not the weekly's 0.60


# ─── parity dividend adjustment ────────────────────────────────────────────

def test_parity_dividend_adjustment_suppresses_a_dividend_driven_false_positive():
    """Without a dividend adjustment, a dividend payer's PV(dividends) reads
    as call underpricing on every strike/expiry. Same chain, only the
    dividend_yield argument differs."""
    from scanner import detect_parity_violation
    expiry = date.today() + timedelta(days=90)
    K = 150.0
    call = OptionContract(K, expiry, 90, 5.9, 6.1, 6.0, 6.0, 200, 200, 0.25, 0.50,
                          0.01, -0.05, 0.10, 6.0, False)
    put = OptionContract(K, expiry, 90, 4.9, 5.1, 5.0, 5.0, 200, 200, 0.28, -0.48,
                         0.01, -0.05, 0.10, 5.0, False)
    chain = _chain(calls=[call], puts=[put], stock_price=150.0)

    assert detect_parity_violation(chain, dividend_yield=0.0) is not None
    assert detect_parity_violation(chain, dividend_yield=0.03) is None


def test_put_parity_dividend_adjustment_can_reveal_a_violation_q0_misses():
    """theoretical_put RISES with q (opposite direction from the call side),
    so the dividend fix makes put_parity fire MORE often on dividend payers,
    not less — a put priced fairly at q=0 can read as underpriced once the
    dividend PV is accounted for."""
    from scanner import detect_put_parity_violation
    expiry = date.today() + timedelta(days=90)
    K = 150.0
    call = OptionContract(K, expiry, 90, 5.9, 6.1, 6.0, 6.0, 200, 200, 0.25, 0.50,
                          0.01, -0.05, 0.10, 6.0, False)
    put = OptionContract(K, expiry, 90, 4.9, 5.1, 5.0, 5.0, 200, 200, 0.28, -0.48,
                         0.01, -0.05, 0.10, 5.0, False)
    chain = _chain(calls=[call], puts=[put], stock_price=150.0)

    assert detect_put_parity_violation(chain, dividend_yield=0.0) is None
    assert detect_put_parity_violation(chain, dividend_yield=0.05) is not None


# ─── signal-to-structure anchoring ─────────────────────────────────────────

def test_anchoring_uses_the_signal_flagged_strike_not_the_closest_atm():
    expiry = date.today() + timedelta(days=35)
    calls = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300),  # would win WITHOUT an anchor
        _contract(105, 35, delta=0.40, bid=2.9, ask=3.1, oi=300),  # the flagged contract
        _contract(115, 35, delta=0.20, bid=1.05, ask=1.15, oi=300),
    ]
    chain = _chain(calls=calls)
    signal = _signal(detector="parity", raw_data={
        "strike": 105.0, "expiry": expiry.isoformat(), "dte": 35,
    })
    setup = construct_best_spread(signal, chain, _catalyst())
    assert setup is not None
    assert setup.long_strike == 105.0


def test_anchoring_rejects_rather_than_falls_back_when_the_flagged_strike_is_absent():
    expiry = date.today() + timedelta(days=35)
    calls = _long_short(100, 112, 35)  # no 107 strike present at this expiry
    chain = _chain(calls=calls)
    signal = _signal(detector="parity", raw_data={
        "strike": 107.0, "expiry": expiry.isoformat(), "dte": 35,
    })
    assert construct_best_spread(signal, chain, _catalyst()) is None


def test_anchoring_applies_to_bear_put_spread_too():
    expiry = date.today() + timedelta(days=35)
    puts = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300, is_put=True),
        _contract(95, 35, delta=0.40, bid=2.9, ask=3.1, oi=300, is_put=True),   # flagged
        _contract(85, 35, delta=0.20, bid=1.05, ask=1.15, oi=300, is_put=True),
    ]
    chain = _chain(puts=puts)
    signal = _signal(detector="put_parity", raw_data={
        "strike": 95.0, "expiry": expiry.isoformat(), "dte": 35,
    })
    setup = construct_bear_put_spread(signal, chain, _catalyst())
    assert setup is not None
    assert setup.long_strike == 95.0


def test_anchoring_recognizes_mispriced_strike_key_from_skew_type_b():
    expiry = date.today() + timedelta(days=35)
    calls = [
        _contract(100, 35, delta=0.50, bid=4.9, ask=5.1, oi=300),
        _contract(105, 35, delta=0.40, bid=2.9, ask=3.1, oi=300),
        _contract(115, 35, delta=0.20, bid=1.05, ask=1.15, oi=300),
    ]
    chain = _chain(calls=calls)
    signal = _signal(detector="skew", raw_data={
        "mispriced_strike": 105.0, "expiry": expiry.isoformat(),
        "deviation": -0.04, "r_squared": 0.8, "raw_skew": 0.03,
    })
    setup = construct_best_spread(signal, chain, _catalyst())
    assert setup is not None
    assert setup.long_strike == 105.0


def test_iv_rank_signal_has_no_anchor_and_keeps_free_choice_construction():
    # iv_rank's raw_data carries no strike/expiry — confirms detectors with
    # no single flagged contract are unaffected by anchoring.
    signal = _signal(detector="iv_rank", raw_data={"iv_rank": 15.0, "iv30": 0.20, "hv30": 0.30})
    setup = construct_best_spread(signal, _bull_chain(), _catalyst())
    assert setup is not None
    assert setup.long_strike == 100.0  # closest-ATM choice, unchanged


def test_parity_requires_edge_beyond_the_round_trip_spread():
    """A violation just over 2% by mid can still be smaller than the round
    trip you'd actually pay — the edge floor (against bid/ask, not mid) must
    reject it even though the plain violation_pct check alone would not."""
    from scanner import detect_parity_violation
    expiry = date.today() + timedelta(days=35)
    K = 150.0
    # theoretical_call ~= 3.56 (put.mid=3.0, S=150, K*exp(-rT)~=149.44 at
    # RISK_FREE_RATE); call priced at mid 3.485 clears violation_pct>0.02 by
    # mid, but the bid/ask edge against the real transacting prices is ~0.035.
    call = OptionContract(K, expiry, 35, 3.465, 3.505, 3.485, 3.485, 200, 200,
                          0.25, 0.50, 0.01, -0.05, 0.10, 3.485, False)
    put = OptionContract(K, expiry, 35, 2.98, 3.02, 3.0, 3.0, 200, 200,
                         0.28, -0.48, 0.01, -0.05, 0.10, 3.0, False)
    chain = _chain(calls=[call], puts=[put], stock_price=150.0)
    assert detect_parity_violation(chain, dividend_yield=0.0) is None
