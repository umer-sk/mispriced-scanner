from datetime import date, datetime, timedelta, timezone

import pytest

from forward_test import apply_mark, pnl_pct, realized_pnl

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 10)
EXPIRY = date(2026, 10, 16)


def _pos(**over):
    d = {"entry_debit": 3.0, "expiry": EXPIRY, "status": "open",
         "t1_ts": None, "t2_ts": None, "stop_ts": None,
         "mfe_pct": 0.0, "mae_pct": 0.0, "mark_failures": 0}
    d.update(over)
    return d


def test_pnl_pct_basic():
    assert pnl_pct(4.5, 3.0) == 50.0
    assert pnl_pct(6.0, 3.0) == 100.0
    assert pnl_pct(1.5, 3.0) == -50.0
    assert pnl_pct(3.0, 3.0) == 0.0


def test_pnl_pct_zero_entry_is_not_a_division_error():
    assert pnl_pct(1.0, 0.0) == 0.0


def test_first_target_records_t1_but_stays_open():
    upd = apply_mark(_pos(), 55.0, NOW, TODAY)
    assert upd["t1_ts"] == NOW
    assert upd["status"] == "target1"
    assert upd["closed_ts"] is None      # second half still running


def test_second_target_closes():
    upd = apply_mark(_pos(status="target1", t1_ts=NOW), 105.0, NOW, TODAY)
    assert upd["t2_ts"] == NOW
    assert upd["status"] == "target2"
    assert upd["closed_ts"] == NOW


def test_a_single_mark_can_cross_both_targets():
    # A gap through +100% must not be recorded as only the first target.
    upd = apply_mark(_pos(), 120.0, NOW, TODAY)
    assert upd["t1_ts"] == NOW and upd["t2_ts"] == NOW
    assert upd["status"] == "target2"


def test_stop_closes():
    upd = apply_mark(_pos(), -55.0, NOW, TODAY)
    assert upd["stop_ts"] == NOW
    assert upd["status"] == "stopped"
    assert upd["closed_ts"] == NOW


def test_stop_after_first_target_still_closes():
    upd = apply_mark(_pos(status="target1", t1_ts=NOW), -60.0, NOW, TODAY)
    assert upd["status"] == "stopped"
    assert upd["closed_ts"] == NOW


def test_t1_is_not_overwritten_on_a_later_mark():
    earlier = NOW - timedelta(days=2)
    upd = apply_mark(_pos(status="target1", t1_ts=earlier), 60.0, NOW, TODAY)
    assert upd["t1_ts"] == earlier


def test_expiry_closes_using_the_last_mark():
    upd = apply_mark(_pos(expiry=date(2026, 9, 9)), -20.0, NOW, TODAY)
    assert upd["status"] == "expired"
    assert upd["closed_ts"] == NOW


def test_mfe_and_mae_track_extremes():
    upd = apply_mark(_pos(mfe_pct=30.0, mae_pct=-10.0), 45.0, NOW, TODAY)
    assert upd["mfe_pct"] == 45.0 and upd["mae_pct"] == -10.0
    assert upd["status"] == "open"
    assert upd["closed_ts"] is None
    assert upd["last_pnl_pct"] == 45.0
    upd = apply_mark(_pos(mfe_pct=30.0, mae_pct=-10.0), -25.0, NOW, TODAY)
    assert upd["mfe_pct"] == 30.0 and upd["mae_pct"] == -25.0
    assert upd["status"] == "open"
    assert upd["closed_ts"] is None
    assert upd["last_pnl_pct"] == -25.0


def test_t1_threshold_fires_at_exactly_plus_50():
    upd = apply_mark(_pos(), 50.0, NOW, TODAY)
    assert upd["t1_ts"] == NOW
    assert upd["status"] == "target1"


def test_t1_threshold_does_not_fire_just_below_plus_50():
    upd = apply_mark(_pos(), 49.999, NOW, TODAY)
    assert upd["t1_ts"] is None
    assert upd["status"] == "open"


def test_t2_threshold_fires_at_exactly_plus_100():
    upd = apply_mark(_pos(), 100.0, NOW, TODAY)
    assert upd["t2_ts"] == NOW
    assert upd["status"] == "target2"
    assert upd["closed_ts"] == NOW


def test_stop_threshold_fires_at_exactly_minus_50():
    upd = apply_mark(_pos(), -50.0, NOW, TODAY)
    assert upd["stop_ts"] == NOW
    assert upd["status"] == "stopped"
    assert upd["closed_ts"] == NOW


def test_stop_threshold_does_not_fire_just_above_minus_50():
    upd = apply_mark(_pos(), -49.999, NOW, TODAY)
    assert upd["stop_ts"] is None
    assert upd["status"] == "open"


def test_expiry_day_itself_does_not_close_the_position():
    # Options are live on expiry day itself; the comparison is strictly `>`.
    upd = apply_mark(_pos(expiry=TODAY), 10.0, NOW, TODAY)
    assert upd["status"] == "open"
    assert upd["closed_ts"] is None


def test_apply_mark_accepts_iso_string_expiry_like_a_date():
    upd_str = apply_mark(_pos(expiry="2026-09-09"), -20.0, NOW, TODAY)
    upd_date = apply_mark(_pos(expiry=date(2026, 9, 9)), -20.0, NOW, TODAY)
    assert upd_str == upd_date
    assert upd_str["status"] == "expired"
    assert upd_str["closed_ts"] == NOW


def test_realized_pnl_is_none_while_open():
    assert realized_pnl(_pos(status="open"), None) is None


def test_realized_pnl_is_none_at_target1_only():
    assert realized_pnl(_pos(status="target1", t1_ts=NOW), None) is None


def test_realized_pnl_is_none_when_unpriceable():
    assert realized_pnl(_pos(status="unpriceable"), None) is None


def test_realized_target1_then_target2_is_75():
    assert realized_pnl(_pos(status="target2", t1_ts=NOW, t2_ts=NOW), None) == 75.0


def test_realized_target1_then_stopped_is_zero():
    assert realized_pnl(_pos(status="stopped", t1_ts=NOW, stop_ts=NOW), None) == 0.0


def test_realized_stopped_without_target_is_minus_50():
    assert realized_pnl(_pos(status="stopped"), None) == -50.0


def test_realized_expired_without_target_is_the_final_mark():
    assert realized_pnl(_pos(status="expired"), -18.0) == -18.0


def test_realized_expired_after_target1_is_blended():
    assert realized_pnl(_pos(status="expired", t1_ts=NOW), 10.0) == 30.0


def test_realized_expired_with_no_final_mark_is_none():
    assert realized_pnl(_pos(status="expired"), None) is None
