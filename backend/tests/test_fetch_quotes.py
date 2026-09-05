from unittest.mock import MagicMock, patch

import schwab_client
from schwab_client import fetch_quotes


def _occ(i: int, right: str = "C") -> str:
    """A syntactically valid 21-char OCC symbol, unique per index."""
    return f"SYM{i:03d}261016{right}00190000"


def _client_returning(payloads: dict) -> MagicMock:
    """A fake Schwab client whose get_quotes(chunk) answers only for symbols
    present in `payloads`, echoing back exactly the requested chunk's keys."""
    client = MagicMock()

    def get_quotes(chunk):
        resp = MagicMock()
        resp.json.return_value = {
            sym: payloads[sym] for sym in chunk if sym in payloads
        }
        return resp

    client.get_quotes.side_effect = get_quotes
    return client


def test_request_response_key_identity():
    sym = _occ(0)
    client = _client_returning({sym: {"quote": {"bidPrice": 1.0, "askPrice": 2.0}}})
    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes([sym])
    assert sym in quotes
    assert list(quotes.keys()) == [sym]


def test_mid_is_bid_ask_average():
    sym = _occ(1)
    client = _client_returning({sym: {"quote": {"bidPrice": 4.0, "askPrice": 5.0}}})
    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes([sym])
    assert quotes[sym] == 4.5


def test_none_bid_or_ask_yields_no_entry():
    sym_missing_bid = _occ(2)
    sym_none_ask = _occ(3)
    payloads = {
        sym_missing_bid: {"quote": {"askPrice": 5.0}},  # bidPrice absent
        sym_none_ask: {"quote": {"bidPrice": 4.0, "askPrice": None}},
    }
    client = _client_returning(payloads)
    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes([sym_missing_bid, sym_none_ask])
    assert sym_missing_bid not in quotes
    assert sym_none_ask not in quotes
    assert quotes == {}


def test_chunk_boundary_two_calls_all_quotable_symbols_returned():
    symbols = [_occ(i) for i in range(26)]
    payloads = {s: {"quote": {"bidPrice": 1.0, "askPrice": 1.2}} for s in symbols}
    client = _client_returning(payloads)
    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes(symbols)
    assert client.get_quotes.call_count == 2
    call_sizes = sorted(len(c.args[0]) for c in client.get_quotes.call_args_list)
    assert call_sizes == [1, 25]
    assert set(quotes.keys()) == set(symbols)


def test_bad_chunk_does_not_lose_the_other_chunks_quotes():
    symbols = [_occ(i) for i in range(26)]
    good_payloads = {s: {"quote": {"bidPrice": 1.0, "askPrice": 1.2}} for s in symbols}

    calls = []

    def get_quotes(chunk):
        calls.append(list(chunk))
        if len(calls) == 1:
            raise RuntimeError("schwab 500")
        resp = MagicMock()
        resp.json.return_value = {s: good_payloads[s] for s in chunk}
        return resp

    client = MagicMock()
    client.get_quotes.side_effect = get_quotes

    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes(symbols)

    assert client.get_quotes.call_count == 2
    # The failing chunk contributed nothing, but the other chunk's quotes
    # still made it back.
    assert len(quotes) > 0
    assert set(quotes.keys()) <= set(symbols)
    assert set(quotes.keys()) == set(calls[1])


def test_occ_shape_rejection_never_sent_to_schwab():
    good = _occ(4)
    too_short = "SYM004261016C0019000"          # 20 chars
    no_c_or_p_at_12 = "SYM004261016X00190000"    # wrong char at index 12
    bad_symbols = [too_short, no_c_or_p_at_12]
    client = _client_returning({good: {"quote": {"bidPrice": 1.0, "askPrice": 1.2}}})

    with patch.object(schwab_client, "_get_client", return_value=client):
        quotes = fetch_quotes([good, *bad_symbols])

    assert good in quotes
    for bad in bad_symbols:
        assert bad not in quotes
    sent = {s for call in client.get_quotes.call_args_list for s in call.args[0]}
    assert bad_symbols[0] not in sent
    assert bad_symbols[1] not in sent


def test_empty_input_returns_empty_dict_without_calling_client():
    with patch.object(schwab_client, "_get_client") as get_client:
        quotes = fetch_quotes([])
    assert quotes == {}
    get_client.assert_not_called()


def test_get_client_raising_yields_empty_dict_not_propagated():
    with patch.object(schwab_client, "_get_client", side_effect=RuntimeError("no token")):
        quotes = fetch_quotes([_occ(5)])
    assert quotes == {}
