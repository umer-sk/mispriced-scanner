"""/position-quotes: thin passthrough to fetch_quotes for the trade journal.

The journal is client-side (localStorage) — the backend holds no state about
what a user has open, so this endpoint takes whatever OCC symbols the
frontend already has on a saved position and re-prices them live. No new
pricing logic; the value is entirely in fetch_quotes, already exercised by
the forward-test tracker.
"""
import main


def test_no_symbols_returns_empty_without_calling_schwab(monkeypatch):
    called = []
    monkeypatch.setattr(main, "fetch_quotes", lambda syms: called.append(syms) or {})

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    resp = client.get("/position-quotes")

    assert resp.json() == {"quotes": {}}
    assert called == []


def test_passes_deduped_symbols_through_to_fetch_quotes(monkeypatch):
    captured = {}

    def fake_fetch_quotes(symbols):
        captured["symbols"] = symbols
        return {s: 1.23 for s in symbols}

    monkeypatch.setattr(main, "fetch_quotes", fake_fetch_quotes)

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    occ_a = "NVDA  261016C00190000"
    occ_b = "NVDA  261016P00190000"
    resp = client.get("/position-quotes", params=[("occ", occ_a), ("occ", occ_b), ("occ", occ_a)])
    body = resp.json()

    assert captured["symbols"] == [occ_a, occ_b]  # de-duped, order preserved
    assert body["quotes"] == {occ_a: 1.23, occ_b: 1.23}


def test_caps_the_number_of_symbols_sent_to_schwab(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "fetch_quotes", lambda syms: captured.setdefault("symbols", syms) or {})
    monkeypatch.setattr(main, "MAX_POSITION_QUOTE_SYMBOLS", 3)

    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    occs = [f"SYM{i}   261016C00190000" for i in range(10)]
    client.get("/position-quotes", params=[("occ", o) for o in occs])

    assert len(captured["symbols"]) == 3
