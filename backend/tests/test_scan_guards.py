"""Memory guards around the scans and the after-hours chain-request fix."""
import asyncio
from unittest.mock import MagicMock, patch

import main
import schwab_client


def _run(coro):
    return asyncio.run(coro)


def test_chain_request_omits_from_date():
    # Schwab 400s a fromDate that its own trading day has rolled past, so the
    # request must leave it off and let Schwab default to its "today".
    client = MagicMock()
    client.get_option_chain.return_value.raise_for_status.side_effect = RuntimeError("stop")
    with patch.object(schwab_client, "_get_client", return_value=client):
        schwab_client.fetch_option_chain("ZZZZ", days_out=1, strike_count=1)
    kwargs = client.get_option_chain.call_args.kwargs
    assert "from_date" not in kwargs
    assert "to_date" in kwargs


def test_sector_refresh_skipped_while_scan_running():
    async def go():
        with patch.object(main, "get_sector_analysis") as gsa:
            async with main._scan_lock:
                await main.refresh_sector_analysis()
            gsa.assert_not_called()
    _run(go())


def test_sector_refresh_skipped_when_already_running():
    async def go():
        with patch.object(main, "get_sector_analysis") as gsa:
            async with main._sector_lock:
                await main.refresh_sector_analysis()
            gsa.assert_not_called()
    _run(go())


def test_sector_refresh_runs_and_releases_memory():
    async def go():
        with patch.object(main, "get_sector_analysis", return_value=[]) as gsa, \
             patch.object(main, "release_memory") as rel:
            await main.refresh_sector_analysis()
            gsa.assert_called_once()
            rel.assert_called_once()
        assert not main._sector_lock.locked()
    _run(go())


def test_scan_does_not_drop_when_sector_refresh_running():
    # The sector lock must be independent of the scan lock: a scan arriving
    # mid-refresh still runs (the 09:45 tick fires /scan-sectors then /scan).
    async def go():
        async with main._sector_lock:
            assert not main._scan_lock.locked()
            async with main._exclusive_scan():
                assert main._scan_lock.locked()
    _run(go())


def test_exclusive_scan_releases_memory_even_on_error():
    async def go():
        with patch.object(main, "release_memory") as rel:
            try:
                async with main._exclusive_scan():
                    raise ValueError("boom")
            except ValueError:
                pass
            rel.assert_called_once()
        assert not main._scan_lock.locked()
    _run(go())


def test_release_memory_is_safe_anywhere():
    from memory_util import release_memory
    release_memory()  # no-op trim off glibc; must never raise
