"""_run_celt_scan / /celt-setups / /health: celt_last_attempt tracking.

CELT is a rare, high-conviction detector — the market usually has zero
qualifying setups. _run_celt_scan deliberately does NOT advance
celt_timestamp on an empty result (an empty result is more often a
yfinance/Schwab failure than a genuine "no crash" — see the comment in
main.py), which means celt_timestamp can stay None forever even after many
successful scan attempts. celt_last_attempt exists specifically so the
frontend can still tell "never scanned" apart from "scanned, found nothing".
"""
import asyncio
from types import SimpleNamespace

import main


def _reset_celt_cache():
    main._cache["celt_setups"] = []
    main._cache["celt_timestamp"] = None
    main._cache["celt_last_attempt"] = None
    main._cache["last_scan_note"] = None
    main._cache["last_scan_error"] = None


# QQQ below its own MA50 — the systemic-stress prerequisite scan_celt_setups
# itself enforces (see celt_scanner.py) — so these tests exercise the
# _run_celt_scan wiring/caching logic, not that gate. get_market_context is
# mocked so these stay network-free (the real one does a yfinance call).
_STRESSED_MARKET = SimpleNamespace(qqq_price=400.0, qqq_ma50=450.0)


def _mock_market_context(monkeypatch):
    monkeypatch.setattr(main, "get_market_context", lambda qqq_chain=None: _STRESSED_MARKET)


def test_empty_result_sets_last_attempt_but_not_timestamp(monkeypatch):
    _reset_celt_cache()
    _mock_market_context(monkeypatch)
    monkeypatch.setattr(main, "scan_celt_setups", lambda symbols, qqq_price, qqq_ma50: [])
    monkeypatch.setattr(main, "save_scan_results", lambda *a, **k: None)

    asyncio.run(main._run_celt_scan())

    assert main._cache["celt_last_attempt"] is not None
    assert main._cache["celt_timestamp"] is None
    assert main._cache["celt_setups"] == []


def test_nonempty_result_sets_both_timestamps_to_the_same_instant(monkeypatch):
    _reset_celt_cache()
    _mock_market_context(monkeypatch)
    fake_setup = {"symbol": "TEST", "signal_score": 3.0}
    monkeypatch.setattr(main, "scan_celt_setups", lambda symbols, qqq_price, qqq_ma50: [fake_setup])
    monkeypatch.setattr(main, "save_scan_results", lambda *a, **k: None)
    # fake_setup is a plain dict, not a real CeltSetup — snapshot_setups
    # would fail to normalise it; stub it out so this test stays focused on
    # the timestamp/caching behavior it's actually named for.
    monkeypatch.setattr(main, "snapshot_setups", lambda setups, source: 0)

    asyncio.run(main._run_celt_scan())

    assert main._cache["celt_last_attempt"] is not None
    assert main._cache["celt_timestamp"] is not None
    assert main._cache["celt_last_attempt"] == main._cache["celt_timestamp"]
    assert main._cache["celt_setups"] == [fake_setup]


def test_exception_during_scan_does_not_record_an_attempt(monkeypatch):
    # An attempt that raised never completed — recording it as a completed
    # attempt would tell the frontend "the market was checked" when it
    # wasn't, which is worse than the ambiguity this feature exists to fix.
    _reset_celt_cache()
    _mock_market_context(monkeypatch)

    def boom(symbols, qqq_price, qqq_ma50):
        raise RuntimeError("yfinance is down")

    monkeypatch.setattr(main, "scan_celt_setups", boom)

    asyncio.run(main._run_celt_scan())

    assert main._cache["celt_last_attempt"] is None
    assert main._cache["last_scan_error"] is not None


def test_celt_setups_endpoint_reports_last_attempt_even_when_empty(monkeypatch):
    _reset_celt_cache()
    _mock_market_context(monkeypatch)
    monkeypatch.setattr(main, "scan_celt_setups", lambda symbols, qqq_price, qqq_ma50: [])
    monkeypatch.setattr(main, "save_scan_results", lambda *a, **k: None)
    asyncio.run(main._run_celt_scan())

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    resp = client.get("/celt-setups")
    body = resp.json()

    assert body["scan_timestamp"] is None
    assert body["last_attempt"] is not None


def test_health_reports_celt_last_attempt(monkeypatch):
    _reset_celt_cache()
    _mock_market_context(monkeypatch)
    monkeypatch.setattr(main, "scan_celt_setups", lambda symbols, qqq_price, qqq_ma50: [])
    monkeypatch.setattr(main, "save_scan_results", lambda *a, **k: None)
    asyncio.run(main._run_celt_scan())

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    resp = client.get("/health")
    body = resp.json()

    assert body["celt_last_scan"] is None
    assert body["celt_last_attempt"] is not None
