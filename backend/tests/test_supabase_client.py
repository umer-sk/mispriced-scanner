from unittest.mock import patch

import supabase_client


def _reset_module_state():
    supabase_client._client = None
    supabase_client._last_failure_monotonic = None


def test_get_client_swallows_construction_errors():
    # A present-but-malformed SUPABASE_URL/KEY must not raise into callers —
    # every ft_store / save_scan_results / load_scan_results caller assumes
    # get_client() either returns a client or None, never an exception.
    _reset_module_state()
    try:
        with patch.object(supabase_client, "create_client", side_effect=RuntimeError("bad url")):
            with patch.dict("os.environ", {"SUPABASE_URL": "not-a-url", "SUPABASE_KEY": "k"}):
                assert supabase_client.get_client() is None
    finally:
        _reset_module_state()


def test_get_client_does_not_retry_within_cooldown():
    _reset_module_state()
    try:
        with patch.object(supabase_client, "create_client", side_effect=RuntimeError("bad url")) as mock_create:
            with patch.dict("os.environ", {"SUPABASE_URL": "not-a-url", "SUPABASE_KEY": "k"}):
                assert supabase_client.get_client() is None
                assert supabase_client.get_client() is None
        assert mock_create.call_count == 1  # second call hit the cooldown, not create_client again
    finally:
        _reset_module_state()


def test_get_client_recovers_after_cooldown_elapses():
    _reset_module_state()
    try:
        with patch.object(supabase_client, "create_client", side_effect=RuntimeError("bad url")):
            with patch.dict("os.environ", {"SUPABASE_URL": "not-a-url", "SUPABASE_KEY": "k"}):
                assert supabase_client.get_client() is None

        # Simulate the cooldown window having passed.
        supabase_client._last_failure_monotonic -= supabase_client._FAILURE_RETRY_SECONDS + 1

        sentinel = object()
        with patch.object(supabase_client, "create_client", return_value=sentinel):
            with patch.dict("os.environ", {"SUPABASE_URL": "https://good", "SUPABASE_KEY": "k"}):
                assert supabase_client.get_client() is sentinel
    finally:
        _reset_module_state()


def test_get_client_returns_none_when_unconfigured():
    _reset_module_state()
    try:
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("SUPABASE_URL", None)
            _os.environ.pop("SUPABASE_KEY", None)
            assert supabase_client.get_client() is None
    finally:
        _reset_module_state()
