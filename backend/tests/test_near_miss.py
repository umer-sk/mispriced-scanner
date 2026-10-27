"""Calibration-band near-misses: built and recorded, but never surfaced."""
import asyncio
from unittest.mock import patch

import forward_test
import main
import technical_scanner as ts
from scanner import construct_best_spread, meets_surfacing_gates
from tests.test_forward_test_normalise import _technical_setup, _trade_setup
from tests.test_scanner import _catalyst, _chain, _long_short, _signal
from tests.test_technical_scanner import _make_chain


# ─── scanner.py constructors ──────────────────────────────────────────────

def test_rr_between_calibration_floor_and_surfacing_gate_is_built_not_surfaced():
    # width 12, debit 4.5 -> rr 1.67: past the 2.0 gate, inside the 1.5 band.
    calls = _long_short(100, 112, 35, short_bid=0.6, short_ask=0.65)
    setup = construct_best_spread(_signal(), _chain(calls=calls), _catalyst())
    assert setup is not None
    assert 1.5 <= setup.rr_ratio < 2.0
    assert not meets_surfacing_gates(setup)


def test_rr_below_calibration_floor_is_still_rejected():
    calls = _long_short(100, 106, 35)  # width 6, debit 3.2 -> rr 0.9
    assert construct_best_spread(_signal(), _chain(calls=calls), _catalyst()) is None


def test_dte_just_above_60_is_built_not_surfaced():
    # Only the upper side opens up for an unanchored signal: expiry selection
    # itself searches 30-100 DTE, so nothing under 30 is ever a candidate.
    setup = construct_best_spread(
        _signal(), _chain(calls=_long_short(100, 112, 70)), _catalyst())
    assert setup is not None
    assert not meets_surfacing_gates(setup)


def test_dte_far_outside_the_band_is_still_rejected():
    for dte in (25, 90):
        assert construct_best_spread(
            _signal(), _chain(calls=_long_short(100, 112, dte)), _catalyst()) is None


def test_liquidity_exclusions_stay_hard_for_a_near_miss():
    # A near-miss on rr that is also unfillable (OI < 100) must not be built.
    calls = _long_short(100, 112, 35, short_bid=0.6, short_ask=0.65, oi=50)
    assert construct_best_spread(_signal(), _chain(calls=calls), _catalyst()) is None


def test_clean_setup_meets_surfacing_gates():
    setup = construct_best_spread(
        _signal(), _chain(calls=_long_short(100, 112, 35)), _catalyst())
    assert setup is not None and meets_surfacing_gates(setup)


# ─── forward_test.select_for_recording ────────────────────────────────────

def _near(score, **over):
    return _trade_setup(rr_ratio=1.7, score=score, **over)


def test_select_keeps_tier_a_and_caps_near_misses_best_first():
    a = _trade_setup()
    assert forward_test.classify(forward_test.normalise(a, "scanner"))[0] == "A"
    near = [_near(s) for s in (61, 72, 65, 78, 63, 75, 68)]
    chosen = forward_test.select_for_recording([a] + near, "scanner", max_calibration=3)
    assert a in chosen
    kept = sorted((s.score for s in chosen if s is not a), reverse=True)
    assert kept == [78, 75, 72]


def test_select_excludes_near_misses_that_fail_a_non_calibration_gate():
    illiquid = _near(70, liquidity_ok=False)
    spread_wide = _near(70, long_leg_spread_pct=20.0)
    assert forward_test.select_for_recording([illiquid, spread_wide], "scanner") == []


def test_select_allows_quality_miss_inside_the_near_band_only():
    # quality_min 60, near band 10: 52 is inside, 40 is far below.
    inside = _near(52)
    far = _near(40)
    chosen = forward_test.select_for_recording([inside, far], "scanner")
    assert chosen == [inside]


def test_select_never_raises_on_a_bad_setup():
    assert forward_test.select_for_recording([object()], "scanner") == []


# ─── technical_scanner near-miss pass ─────────────────────────────────────

def test_near_miss_pass_builds_what_the_strict_pass_rejects():
    chain = _make_chain()
    found = None
    for atr in (a / 2 for a in range(10, 120)):
        strict = ts._construct_long_call("NVDA", 875.0, chain, 7, {}, atr)
        near = ts._construct_long_call("NVDA", 875.0, chain, 7, {}, atr, near_miss=True)
        if strict is None and near is not None:
            found = (atr, near)
            break
    assert found is not None, "no ATR put the long call inside the near-miss band"
    atr, near = found
    assert 2.0 - ts.NEAR_MISS_RR_MARGIN <= near.rr_ratio < 2.0


def test_near_miss_never_relaxes_when_flag_is_off():
    chain = _make_chain()
    assert ts._construct_long_call("NVDA", 875.0, chain, 7, {}, atr14=5.0) is None


# ─── main._run_technical_scan ─────────────────────────────────────────────

def _run_tech(returned, cache_before):
    async def go():
        main._cache["technical_setups"] = cache_before
        with patch.object(main, "scan_technical_setups", return_value=returned), \
             patch.object(main, "snapshot_setups") as snap, \
             patch.object(main, "save_scan_results") as save:
            await main._run_technical_scan()
            return snap, save
    return asyncio.run(go())


def test_only_near_misses_keep_the_cache_and_still_record():
    before = ["previous"]
    nm = _technical_setup(near_miss=True, rr_ratio=1.2, structure="bull_call_spread")
    snap, save = _run_tech([nm], before)
    assert main._cache["technical_setups"] == before
    save.assert_not_called()
    snap.assert_called_once()
    assert nm in snap.call_args.args[0]


def test_near_misses_are_recorded_but_not_cached_alongside_real_setups():
    real = _technical_setup()
    nm = _technical_setup(near_miss=True, rr_ratio=1.2, symbol="XYZ")
    snap, _ = _run_tech([real, nm], [])
    assert main._cache["technical_setups"] == [real]
    recorded = snap.call_args.args[0]
    assert real in recorded and nm in recorded
