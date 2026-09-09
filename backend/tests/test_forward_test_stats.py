from forward_test import aggregate


def _p(**over):
    d = {"tier": "A", "detector": "parity", "source": "scanner",
         "status": "target2", "realized_pnl_pct": 75.0,
         "mfe_pct": 110.0, "mae_pct": -10.0}
    d.update(over)
    return d


def test_open_positions_are_counted_but_excluded_from_stats():
    out = aggregate([_p(), _p(status="open", realized_pnl_pct=None)])
    assert out["open_count"] == 1
    assert out["closed_count"] == 1
    assert out["overall"]["n"] == 1


def test_win_rate_counts_positive_realized_only():
    out = aggregate([_p(realized_pnl_pct=75.0), _p(realized_pnl_pct=-50.0),
                     _p(realized_pnl_pct=0.0)])
    assert out["overall"]["n"] == 3
    assert abs(out["overall"]["win_rate"] - 33.3) < 0.1     # 0% is not a win


def test_grouped_by_tier_and_detector():
    out = aggregate([_p(tier="A", detector="parity", realized_pnl_pct=75.0),
                     _p(tier="B", detector="skew", realized_pnl_pct=-50.0)])
    assert out["by_tier"]["A"]["n"] == 1
    assert out["by_tier"]["B"]["avg_pnl"] == -50.0
    assert out["by_detector"]["skew"]["n"] == 1


def test_unpriceable_positions_are_excluded_entirely():
    out = aggregate([_p(), _p(status="unpriceable", realized_pnl_pct=None)])
    assert out["closed_count"] == 1
    assert out["unpriceable_count"] == 1
    assert out["open_count"] == 0


def test_empty_input_does_not_divide_by_zero():
    out = aggregate([])
    assert out["overall"] == {"n": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                             "avg_mfe": 0.0, "avg_mae": 0.0, "avg_hold_days": 0.0}


def test_avg_hold_days_computed_from_entry_and_closed_timestamps():
    # Contextualizes cross-structure win-rate comparisons (a 45-day spread
    # next to a 400-day CELT LEAP in the same aggregate) — see _stats.
    out = aggregate([
        _p(entry_ts="2026-01-01T00:00:00+00:00", closed_ts="2026-01-11T00:00:00+00:00"),  # 10 days
        _p(entry_ts="2026-01-01T00:00:00+00:00", closed_ts="2026-01-21T00:00:00+00:00"),  # 20 days
    ])
    assert out["overall"]["avg_hold_days"] == 15.0


def test_avg_hold_days_ignores_rows_missing_either_timestamp():
    out = aggregate([_p(entry_ts=None, closed_ts=None)])
    assert out["overall"]["avg_hold_days"] == 0.0


def test_target1_counts_as_open_not_closed():
    # target1 is a half-closed state (first target hit, position still
    # running) — it must land in open_count, never in the closed win-rate
    # set, even though realized_pnl_pct is None here just like a plain
    # "open" row.
    out = aggregate([_p(), _p(status="target1", realized_pnl_pct=None)])
    assert out["open_count"] == 1
    assert out["closed_count"] == 1
    assert out["overall"]["n"] == 1


def test_grouped_by_source():
    out = aggregate([_p(source="scanner", realized_pnl_pct=75.0),
                     _p(source="technical", realized_pnl_pct=-50.0)])
    assert out["by_source"]["scanner"]["n"] == 1
    assert out["by_source"]["technical"]["avg_pnl"] == -50.0


def test_null_detector_falls_back_to_dash():
    # normalise() sets detector=None for technical-source setups; aggregate
    # must not crash or drop them, and should bucket them under "—".
    out = aggregate([_p(detector=None, source="technical", realized_pnl_pct=75.0)])
    assert out["by_detector"]["—"]["n"] == 1
