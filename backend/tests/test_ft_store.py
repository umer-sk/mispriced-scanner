from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import ft_store

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)


def test_every_function_is_a_noop_without_supabase():
    # Supabase unconfigured must never raise — it would fail the whole scan.
    with patch.object(ft_store, "get_client", return_value=None):
        assert ft_store.insert_position({"dedup_key": "x"}) is None
        assert ft_store.find_open_by_dedup(["x"]) == {}
        assert ft_store.list_open_positions() == []
        assert ft_store.fetch_all_positions(None, None) == []
        ft_store.touch_position("id", NOW)          # must not raise
        ft_store.insert_mark("id", NOW, 1.0, 2.0, 3.0)
        ft_store.update_position("id", {"status": "stopped"})


def test_insert_position_serialises_dates_and_returns_id():
    from datetime import date
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "abc"}]
    with patch.object(ft_store, "get_client", return_value=client):
        new_id = ft_store.insert_position({
            "dedup_key": "k", "expiry": date(2026, 10, 16), "entry_ts": NOW,
        })
    assert new_id == "abc"
    sent = client.table.return_value.insert.call_args[0][0]
    assert sent["expiry"] == "2026-10-16"       # date -> ISO string for jsonb/date
    assert sent["entry_ts"] == NOW.isoformat()


def test_insert_position_swallows_errors():
    client = MagicMock()
    client.table.side_effect = RuntimeError("supabase down")
    with patch.object(ft_store, "get_client", return_value=client):
        assert ft_store.insert_position({"dedup_key": "k"}) is None


def test_find_open_by_dedup_keys_the_result_by_dedup_key():
    client = MagicMock()
    q = client.table.return_value.select.return_value.in_.return_value.is_.return_value
    q.execute.return_value.data = [
        {"id": "1", "dedup_key": "a"}, {"id": "2", "dedup_key": "b"},
    ]
    with patch.object(ft_store, "get_client", return_value=client):
        out = ft_store.find_open_by_dedup(["a", "b"])
    assert set(out) == {"a", "b"}
    assert out["a"]["id"] == "1"


def test_find_open_by_dedup_with_no_keys_makes_no_query():
    client = MagicMock()
    with patch.object(ft_store, "get_client", return_value=client):
        assert ft_store.find_open_by_dedup([]) == {}
    client.table.assert_not_called()


def test_find_open_by_dedup_filters_on_closed_ts_not_status():
    # The DDL's partial unique index is on `closed_ts is null` — filtering on
    # `status` instead would silently miss the real "still open" condition.
    client = MagicMock()
    q = client.table.return_value.select.return_value.in_.return_value.is_.return_value
    q.execute.return_value.data = []
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.find_open_by_dedup(["a", "b"])

    client.table.assert_called_with(ft_store.POSITIONS)
    select_call = client.table.return_value.select
    select_call.assert_called_with("*")
    in_call = select_call.return_value.in_
    in_call.assert_called_with("dedup_key", ["a", "b"])
    is_call = in_call.return_value.is_
    is_call.assert_called_with("closed_ts", "null")


def test_list_open_positions_filters_on_closed_ts_not_status():
    client = MagicMock()
    q = client.table.return_value.select.return_value.is_.return_value
    q.execute.return_value.data = [{"id": "1"}]
    with patch.object(ft_store, "get_client", return_value=client):
        out = ft_store.list_open_positions()

    assert out == [{"id": "1"}]
    client.table.assert_called_with(ft_store.POSITIONS)
    select_call = client.table.return_value.select
    select_call.assert_called_with("*")
    select_call.return_value.is_.assert_called_with("closed_ts", "null")


def test_touch_position_increments_times_seen_and_writes_iso_timestamp():
    client = MagicMock()
    select_chain = client.table.return_value.select.return_value.eq.return_value
    select_chain.execute.return_value.data = [{"times_seen": 4}]
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.touch_position("pos-1", NOW)

    # Read side: selects times_seen for the given position id.
    client.table.return_value.select.assert_called_with("times_seen")
    client.table.return_value.select.return_value.eq.assert_called_with("id", "pos-1")

    # Write side: increments times_seen by exactly 1 and stamps last_seen_ts as ISO.
    update_call = client.table.return_value.update
    sent = update_call.call_args[0][0]
    assert sent["times_seen"] == 5
    assert sent["last_seen_ts"] == NOW.isoformat()
    update_call.return_value.eq.assert_called_with("id", "pos-1")


def test_touch_position_defaults_to_zero_seen_when_row_missing():
    client = MagicMock()
    select_chain = client.table.return_value.select.return_value.eq.return_value
    select_chain.execute.return_value.data = []
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.touch_position("pos-1", NOW)

    sent = client.table.return_value.update.call_args[0][0]
    assert sent["times_seen"] == 1


def test_insert_mark_upserts_expected_columns():
    client = MagicMock()
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.insert_mark("pos-1", NOW, 1.23, 4.5, 101.0)

    client.table.assert_called_with(ft_store.MARKS)
    sent = client.table.return_value.upsert.call_args[0][0]
    assert sent == {
        "position_id": "pos-1",
        "ts": NOW.isoformat(),
        "spread_mid": 1.23,
        "pnl_pct": 4.5,
        "stock_price": 101.0,
    }


def test_update_position_updates_expected_table_and_filter():
    client = MagicMock()
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.update_position("pos-1", {"status": "stopped", "closed_ts": NOW})

    client.table.assert_called_with(ft_store.POSITIONS)
    sent = client.table.return_value.update.call_args[0][0]
    assert sent == {"status": "stopped", "closed_ts": NOW.isoformat()}
    client.table.return_value.update.return_value.eq.assert_called_with("id", "pos-1")


def test_fetch_all_positions_filters_by_status_and_tier_and_orders_by_entry_ts():
    client = MagicMock()
    q = client.table.return_value.select.return_value
    q.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "1"},
    ]
    with patch.object(ft_store, "get_client", return_value=client):
        out = ft_store.fetch_all_positions(status="open", tier="A")

    assert out == [{"id": "1"}]
    client.table.assert_called_with(ft_store.POSITIONS)
    q.eq.assert_called_with("status", "open")
    q.eq.return_value.eq.assert_called_with("tier", "A")
    q.eq.return_value.eq.return_value.order.assert_called_with("entry_ts", desc=True)
    q.eq.return_value.eq.return_value.order.return_value.limit.assert_called_with(1000)


def test_fetch_all_positions_with_no_filters_skips_eq_calls():
    client = MagicMock()
    q = client.table.return_value.select.return_value
    q.order.return_value.limit.return_value.execute.return_value.data = []
    with patch.object(ft_store, "get_client", return_value=client):
        ft_store.fetch_all_positions()

    q.eq.assert_not_called()
    q.order.assert_called_with("entry_ts", desc=True)
