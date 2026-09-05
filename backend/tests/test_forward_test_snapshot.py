from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import forward_test
from tests.test_forward_test_normalise import _technical_setup, _trade_setup

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)


def test_snapshot_inserts_new_positions_with_tier():
    inserted = []
    with patch.object(forward_test.ft_store, "find_open_by_dedup", return_value={}), \
         patch.object(forward_test.ft_store, "insert_position",
                      side_effect=lambda r: inserted.append(r) or "id1"):
        n = forward_test.snapshot_setups([_trade_setup()], "scanner")
    assert n == 1
    assert inserted[0]["tier"] in {"A", "B", "C"}
    assert inserted[0]["status"] == "open"
    assert inserted[0]["entry_debit"] == 3.0


def test_snapshot_stores_the_entry_mid_alongside_the_entry_debit():
    # Impossible to reconstruct later: it is the mid at the moment of entry.
    inserted = []
    with patch.object(forward_test.ft_store, "find_open_by_dedup", return_value={}), \
         patch.object(forward_test.ft_store, "insert_position",
                      side_effect=lambda r: inserted.append(r) or "id1"):
        forward_test.snapshot_setups([_trade_setup(entry_mid=2.75)], "scanner")
    assert inserted[0]["entry_debit"] == 3.0
    assert inserted[0]["entry_mid"] == 2.75


def test_snapshot_does_not_reinsert_an_open_duplicate():
    existing = {"NVDA|parity|bull_call_spread|2026-10-16|190.0|200.0":
                {"id": "id1", "times_seen": 1}}
    touched = []
    with patch.object(forward_test.ft_store, "find_open_by_dedup", return_value=existing), \
         patch.object(forward_test.ft_store, "insert_position") as ins, \
         patch.object(forward_test.ft_store, "touch_position",
                      side_effect=lambda i, n: touched.append(i)):
        n = forward_test.snapshot_setups([_trade_setup()], "scanner")
    assert n == 0
    ins.assert_not_called()
    assert touched == ["id1"]      # entry price untouched, sighting counted


def test_snapshot_never_raises_when_the_store_is_broken():
    with patch.object(forward_test.ft_store, "find_open_by_dedup",
                      side_effect=RuntimeError("down")):
        assert forward_test.snapshot_setups([_trade_setup()], "scanner") == 0


def test_snapshot_skips_setups_with_no_occ_symbols():
    # Unpriceable at entry means it could never be marked; recording it would
    # add a permanent open row that only ever times out.
    s = _trade_setup()
    s.long_occ, s.short_occ = "", ""
    with patch.object(forward_test.ft_store, "find_open_by_dedup", return_value={}), \
         patch.object(forward_test.ft_store, "insert_position") as ins:
        assert forward_test.snapshot_setups([s], "scanner") == 0
    ins.assert_not_called()


def test_mark_computes_spread_mid_and_writes_a_mark():
    pos = {"id": "p1", "long_occ": "L", "short_occ": "S", "entry_debit": 3.0,
           "expiry": "2026-10-16", "status": "open", "t1_ts": None,
           "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
           "mark_failures": 0}
    marks, updates = [], []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value={"L": 6.0, "S": 1.5}), \
         patch.object(forward_test.ft_store, "insert_mark",
                      side_effect=lambda *a: marks.append(a)), \
         patch.object(forward_test.ft_store, "update_position",
                      side_effect=lambda i, f: updates.append(f)):
        n = forward_test.mark_open_positions(now=NOW)
    assert n == 1
    assert marks[0][2] == 4.5                 # 6.0 - 1.5
    assert updates[0]["t1_ts"] == NOW         # +50% exactly
    assert updates[0]["status"] == "target1"


def test_mark_counts_a_missing_quote_as_a_failure_not_a_price_move():
    pos = {"id": "p1", "long_occ": "L", "short_occ": "S", "entry_debit": 3.0,
           "expiry": "2026-10-16", "status": "open", "t1_ts": None,
           "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
           "mark_failures": 2}
    updates = []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value={}), \
         patch.object(forward_test.ft_store, "insert_mark") as im, \
         patch.object(forward_test.ft_store, "update_position",
                      side_effect=lambda i, f: updates.append(f)):
        forward_test.mark_open_positions(now=NOW)
    im.assert_not_called()
    assert updates[0] == {"mark_failures": 3}


def test_mark_gives_up_after_the_failure_ceiling():
    pos = {"id": "p1", "long_occ": "L", "short_occ": "", "entry_debit": 3.0,
           "expiry": "2026-10-16", "status": "open", "t1_ts": None,
           "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
           "mark_failures": forward_test.MAX_MARK_FAILURES - 1}
    updates = []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value={}), \
         patch.object(forward_test.ft_store, "update_position",
                      side_effect=lambda i, f: updates.append(f)):
        forward_test.mark_open_positions(now=NOW)
    assert updates[0]["status"] == "unpriceable"
    assert updates[0]["closed_ts"] == NOW


def test_mark_resets_failure_count_on_a_successful_mark():
    # A position that failed to price a few times and then marks successfully
    # must not carry those failures toward MAX_MARK_FAILURES.
    pos = {"id": "p1", "long_occ": "L", "short_occ": "S", "entry_debit": 3.0,
           "expiry": "2026-10-16", "status": "open", "t1_ts": None,
           "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
           "mark_failures": 3}
    updates = []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value={"L": 6.0, "S": 1.5}), \
         patch.object(forward_test.ft_store, "insert_mark"), \
         patch.object(forward_test.ft_store, "update_position",
                      side_effect=lambda i, f: updates.append(f)):
        n = forward_test.mark_open_positions(now=NOW)
    assert n == 1
    assert updates[0]["mark_failures"] == 0


def test_single_leg_position_uses_the_long_mid_alone():
    pos = {"id": "p1", "long_occ": "L", "short_occ": "", "entry_debit": 2.0,
           "expiry": "2026-10-16", "status": "open", "t1_ts": None,
           "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
           "mark_failures": 0}
    marks = []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value={"L": 3.0}), \
         patch.object(forward_test.ft_store, "insert_mark",
                      side_effect=lambda *a: marks.append(a)), \
         patch.object(forward_test.ft_store, "update_position"):
        forward_test.mark_open_positions(now=NOW)
    assert marks[0][2] == 3.0


def _open_pos(**over):
    d = {"id": "p1", "long_occ": "L", "short_occ": "S", "entry_debit": 3.0,
         "expiry": "2026-10-16", "status": "open", "t1_ts": None,
         "t2_ts": None, "stop_ts": None, "mfe_pct": 0.0, "mae_pct": 0.0,
         "mark_failures": 0, "last_pnl_pct": None}
    d.update(over)
    return d


def _run_mark(pos, quotes, now=NOW):
    """Run mark_open_positions over one position; return its field updates."""
    updates, marks = [], []
    with patch.object(forward_test.ft_store, "list_open_positions", return_value=[pos]), \
         patch.object(forward_test, "fetch_quotes", return_value=quotes), \
         patch.object(forward_test.ft_store, "insert_mark",
                      side_effect=lambda *a: marks.append(a)), \
         patch.object(forward_test.ft_store, "update_position",
                      side_effect=lambda i, f: updates.append(f)):
        n = forward_test.mark_open_positions(now=now)
    return n, updates, marks


def test_a_successful_mark_persists_last_pnl_pct():
    # Without this column there is nothing to close an expiring position with.
    n, updates, _ = _run_mark(_open_pos(), {"L": 4.0, "S": 1.0})
    assert n == 1
    assert updates[0]["last_pnl_pct"] == 0.0     # mid 3.0 vs entry 3.0


def test_expiry_with_no_quote_closes_as_expired_not_unpriceable():
    # An expired option cannot be quoted, so the closing mark never arrives.
    # Taking the failure branch here would bury the position in `unpriceable`,
    # which is excluded from every statistic.
    pos = _open_pos(expiry="2026-09-09", last_pnl_pct=-18.0)
    n, updates, marks = _run_mark(pos, {})
    assert n == 0                       # no mark was taken
    assert marks == []
    assert updates[0]["status"] == "expired"
    assert updates[0]["closed_ts"] == NOW
    assert updates[0]["realized_pnl_pct"] == -18.0     # from the last mark
    assert "mark_failures" not in updates[0]


def test_expiry_after_target1_blends_the_last_mark():
    pos = _open_pos(expiry="2026-09-09", status="target1",
                    t1_ts=NOW - timedelta(days=3), last_pnl_pct=10.0)
    _, updates, _ = _run_mark(pos, {})
    assert updates[0]["status"] == "expired"
    assert updates[0]["realized_pnl_pct"] == 30.0      # (50 + 10) / 2


def test_expiry_check_beats_the_failure_ceiling():
    # Even a position already at the ceiling resolves as expired, not
    # unpriceable — the expiry check runs first.
    pos = _open_pos(expiry="2026-09-09", last_pnl_pct=-40.0,
                    mark_failures=forward_test.MAX_MARK_FAILURES - 1)
    _, updates, _ = _run_mark(pos, {})
    assert updates[0]["status"] == "expired"
    assert updates[0]["realized_pnl_pct"] == -40.0


def test_no_quote_before_expiry_only_increments_mark_failures():
    pos = _open_pos(expiry="2026-10-16", mark_failures=2)
    n, updates, marks = _run_mark(pos, {})
    assert n == 0
    assert marks == []
    assert updates[0] == {"mark_failures": 3}          # nothing closed


def test_expiry_uses_the_et_date_not_the_utc_date():
    # 01:00 UTC on the 11th is 21:00 ET on the 10th. A position expiring on
    # the 10th is not yet past expiry.
    late = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
    pos = _open_pos(expiry="2026-09-10", last_pnl_pct=-5.0)
    _, updates, _ = _run_mark(pos, {}, now=late)
    assert updates[0] == {"mark_failures": 1}


def test_zero_positions_marked_is_logged_as_an_error(caplog):
    # A symbol-format mismatch raises nothing and would silently close the
    # whole dataset as unpriceable; it has to be visible in the logs.
    with caplog.at_level("ERROR", logger="forward_test"):
        _run_mark(_open_pos(), {})
    assert any("0 of 1 open positions" in r.getMessage() for r in caplog.records)


def test_a_successful_mark_does_not_log_the_zero_marked_error(caplog):
    with caplog.at_level("ERROR", logger="forward_test"):
        _run_mark(_open_pos(), {"L": 4.0, "S": 1.0})
    assert not [r for r in caplog.records if "could be marked" in r.getMessage()]


def test_expiry_with_no_mark_ever_taken_is_unpriceable_not_expired():
    # Nothing was ever priced, so there is no P&L to close with. That is the
    # genuine `unpriceable` case — not a censored outcome.
    pos = _open_pos(expiry="2026-09-09", last_pnl_pct=None)
    _, updates, _ = _run_mark(pos, {})
    assert updates[0]["status"] == "unpriceable"
    assert updates[0]["closed_ts"] == NOW
    assert "realized_pnl_pct" not in updates[0]
