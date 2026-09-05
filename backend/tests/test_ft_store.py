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
