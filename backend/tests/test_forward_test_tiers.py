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
    assert classify(_base(dte_at_entry=60))[0] == "C"


def test_technical_quality_uses_signal_count_threshold():
    # 5 of 7 signals is the technical analogue of score >= 60.
    assert classify(_base(source="technical", quality=5))[0] == "A"
    assert classify(_base(source="technical", quality=4))[0] == "B"
    assert classify(_base(source="technical", quality=2))[0] == "C"


def test_trend_opposition_is_tier_c():
    tier, failed = classify(_base(trend_opposes=True))
    assert tier == "C"
    assert failed == ["trend"]
