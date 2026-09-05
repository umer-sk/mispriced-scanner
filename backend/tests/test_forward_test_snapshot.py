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
