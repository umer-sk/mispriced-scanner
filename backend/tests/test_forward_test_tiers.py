from forward_test import classify


def _base(**over):
    """A setup passing every gate; override one field per test."""
    d = {
        "source": "scanner",
        "quality": 70,                 # score for scanner, signal_count for technical
        "rr_ratio": 3.0,
        "liquidity_ok": True,
        "long_leg_spread_pct": 4.0,
        "short_leg_spread_pct": 4.0,
        "earnings_in_window": False,
        "dte_at_entry": 35,
        "breakeven_move_pct": 2.0,
        "trend_opposes": False,
    }
    d.update(over)
    return d


def test_all_gates_pass_is_tier_a():
    tier, failed = classify(_base())
    assert tier == "A"
    assert failed == []


def test_narrow_quality_miss_is_tier_b():
    tier, failed = classify(_base(quality=55))
    assert tier == "B"
    assert failed == ["quality"]


def test_wide_quality_miss_is_tier_c():
    tier, failed = classify(_base(quality=30))
    assert tier == "C"


def test_narrow_breakeven_miss_is_tier_b():
    tier, failed = classify(_base(breakeven_move_pct=4.2))
    assert tier == "B"
    assert failed == ["breakeven"]


def test_wide_breakeven_miss_is_tier_c():
    tier, failed = classify(_base(breakeven_move_pct=8.0))
    assert tier == "C"


def test_breakeven_gate_uses_magnitude_so_bearish_is_not_penalised():
    # bear put spreads carry a positive breakeven_move_pct meaning "must fall";
    # a sign-sensitive comparison would wrongly pass or fail every bear setup.
    assert classify(_base(breakeven_move_pct=-2.0))[0] == "A"


def test_earnings_in_window_is_tier_c_even_as_sole_failure():
    # Binary gate — there is no "nearly no earnings".
    tier, failed = classify(_base(earnings_in_window=True))
    assert tier == "C"
    assert failed == ["earnings"]


def test_wide_spread_is_tier_c_even_as_sole_failure():
    tier, failed = classify(_base(long_leg_spread_pct=9.0))
    assert tier == "C"
    assert failed == ["liquidity"]


def test_two_failures_is_tier_c():
    tier, failed = classify(_base(quality=55, breakeven_move_pct=4.2))
    assert tier == "C"
    assert sorted(failed) == ["breakeven", "quality"]


def test_dte_outside_window_is_tier_c():
    assert classify(_base(dte_at_entry=12))[0] == "C"
    assert classify(_base(dte_at_entry=70))[0] == "C"


def test_technical_quality_uses_signal_count_threshold():
    # 5 of 7 signals is the technical analogue of score >= 60.
    assert classify(_base(source="technical", quality=5))[0] == "A"
    assert classify(_base(source="technical", quality=4))[0] == "B"
    assert classify(_base(source="technical", quality=2))[0] == "C"


def test_trend_opposition_is_tier_c():
    tier, failed = classify(_base(trend_opposes=True))
    assert tier == "C"
    assert failed == ["trend"]


# Finding 1: RR gate has zero test coverage
def test_rr_below_minimum_is_tier_c():
    """rr_ratio < 2.0 is the sole failure; should be Tier C (not a near-miss gate)."""
    tier, failed = classify(_base(rr_ratio=1.5))
    assert tier == "C"
    assert failed == ["rr"]


# Finding 2: Boundary tests for all thresholds
def test_spread_max_pct_boundary_pass():
    """long_leg_spread_pct exactly at 6.0 should PASS (operator is >)."""
    tier, failed = classify(_base(long_leg_spread_pct=6.0))
    assert tier == "A"
    assert failed == []


def test_spread_max_pct_boundary_fail():
    """long_leg_spread_pct at 6.1 should FAIL as sole Tier C."""
    tier, failed = classify(_base(long_leg_spread_pct=6.1))
    assert tier == "C"
    assert failed == ["liquidity"]


def test_dte_min_boundary_pass():
    """dte_at_entry exactly at 30 should PASS (operator is <=)."""
    tier, failed = classify(_base(dte_at_entry=30))
    assert tier == "A"
    assert failed == []


def test_dte_min_boundary_fail():
    """dte_at_entry at 29 should FAIL as sole Tier C."""
    tier, failed = classify(_base(dte_at_entry=29))
    assert tier == "C"
    assert failed == ["dte"]


def test_dte_max_boundary_pass():
    """dte_at_entry exactly at 60 should PASS (operator is <=)."""
    tier, failed = classify(_base(dte_at_entry=60))
    assert tier == "A"
    assert failed == []


def test_dte_max_boundary_fail():
    """dte_at_entry at 61 should FAIL as sole Tier C."""
    tier, failed = classify(_base(dte_at_entry=61))
    assert tier == "C"
    assert failed == ["dte"]


def test_quality_min_boundary_pass():
    """quality exactly at 60 should PASS (Tier A)."""
    tier, failed = classify(_base(quality=60))
    assert tier == "A"
    assert failed == []


def test_quality_near_miss_lower():
    """quality at 59 should FAIL but within near-miss band (>= 50), Tier B."""
    tier, failed = classify(_base(quality=59))
    assert tier == "B"
    assert failed == ["quality"]


def test_quality_near_miss_floor():
    """quality at 50 should FAIL but at the floor of near-miss band, Tier B."""
    tier, failed = classify(_base(quality=50))
    assert tier == "B"
    assert failed == ["quality"]


def test_quality_wide_miss():
    """quality at 49 should FAIL and outside near-miss band (< 50), Tier C."""
    tier, failed = classify(_base(quality=49))
    assert tier == "C"
    assert failed == ["quality"]


def test_breakeven_max_pct_boundary_pass():
    """abs(breakeven_move_pct) exactly at 3.5 should PASS (operator is >)."""
    tier, failed = classify(_base(breakeven_move_pct=3.5))
    assert tier == "A"
    assert failed == []


def test_breakeven_near_miss_lower():
    """abs(breakeven_move_pct) at 3.6 should FAIL but within near-miss band (<= 5.0), Tier B."""
    tier, failed = classify(_base(breakeven_move_pct=3.6))
    assert tier == "B"
    assert failed == ["breakeven"]


def test_breakeven_near_miss_ceil():
    """abs(breakeven_move_pct) at 5.0 should FAIL but at the ceiling of near-miss band, Tier B."""
    tier, failed = classify(_base(breakeven_move_pct=5.0))
    assert tier == "B"
    assert failed == ["breakeven"]


def test_breakeven_wide_miss():
    """abs(breakeven_move_pct) at 5.1 should FAIL and outside near-miss band (> 5.0), Tier C."""
    tier, failed = classify(_base(breakeven_move_pct=5.1))
    assert tier == "C"
    assert failed == ["breakeven"]


def test_breakeven_near_miss_lower_negative():
    """abs(breakeven_move_pct) at -3.6 should FAIL but within near-miss band, Tier B."""
    tier, failed = classify(_base(breakeven_move_pct=-3.6))
    assert tier == "B"
    assert failed == ["breakeven"]


def test_breakeven_wide_miss_negative():
    """abs(breakeven_move_pct) at -5.1 should FAIL and outside near-miss band, Tier C."""
    tier, failed = classify(_base(breakeven_move_pct=-5.1))
    assert tier == "C"
    assert failed == ["breakeven"]


def test_dte_gate_uses_the_bounce_band_when_present():
    # A normalised bounce dict carries dte_min/dte_max=60/100 instead of the
    # default 25/45. 75 DTE would fail the default band but must pass here —
    # without the per-source override, every 200W bounce position is
    # permanently Tier C regardless of anything else about the trade.
    tier, failed = classify(_base(dte_at_entry=75, dte_min=60, dte_max=100))
    assert tier == "A"
    assert "dte" not in failed


def test_dte_gate_falls_back_to_the_default_band_without_the_override():
    tier, failed = classify(_base(dte_at_entry=75))
    assert tier == "C"
    assert "dte" in failed


def test_rr_gate_uses_the_bounce_minimum_when_present():
    # A normalised bounce dict carries rr_min=1.5 (BOUNCE_RR_MIN), not the
    # shared 2.0 — the bounce's own construction gate is calibrated to 1.5
    # (technical_scanner.BOUNCE_RR_MIN). rr=1.72 would fail the shared 2.0
    # gate but must pass here — without this override every 200W bounce
    # position is permanently Tier C, since "rr" carries no near-miss band.
    tier, failed = classify(_base(rr_ratio=1.72, rr_min=1.5))
    assert tier == "A"
    assert "rr" not in failed


def test_rr_gate_falls_back_to_the_default_minimum_without_the_override():
    tier, failed = classify(_base(rr_ratio=1.72))
    assert tier == "C"
    assert "rr" in failed


def test_quality_gate_uses_the_bounce_minimum_when_present():
    # A normalised bounce dict carries quality_min=4 (BOUNCE_QUALITY_MIN),
    # not the shared technical minimum of 5 — the bounce's signal_count is
    # hardcoded to 4 (its own 4 qualifying criteria, not a fraction of 7), so
    # without this override every bounce position permanently fails the
    # quality gate outright rather than landing in the intended tautological
    # pass.
    tier, failed = classify(_base(source="technical", quality=4, quality_min=4, quality_near=1))
    assert tier == "A"
    assert "quality" not in failed


def test_quality_gate_falls_back_to_the_default_minimum_without_the_override():
    tier, failed = classify(_base(source="technical", quality=4))
    assert tier == "B"          # 4 is within QUALITY_NEAR["technical"]=1 of the default min 5
    assert failed == ["quality"]


def test_spread_gate_uses_the_override_when_present():
    tier, failed = classify(_base(long_leg_spread_pct=10.0, short_leg_spread_pct=0.0, spread_max=12.0))
    assert tier == "A"
    assert "liquidity" not in failed


def test_spread_gate_falls_back_to_the_default_maximum_without_the_override():
    tier, failed = classify(_base(long_leg_spread_pct=10.0, short_leg_spread_pct=0.0))
    assert "liquidity" in failed


def test_breakeven_gate_uses_the_override_when_present():
    # A single-leg long call/CELT LEAP's breakeven (extrinsic/spot exactly)
    # runs far wider than a spread's — see _BREAKEVEN_BANDS.
    tier, failed = classify(_base(breakeven_move_pct=8.0, breakeven_max=10.0, breakeven_near=15.0))
    assert tier == "A"
    assert "breakeven" not in failed


def test_breakeven_gate_falls_back_to_the_default_band_without_the_override():
    tier, failed = classify(_base(breakeven_move_pct=8.0))
    assert "breakeven" in failed
