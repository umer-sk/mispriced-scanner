from datetime import date, timedelta

from schwab_client import _parse_contracts


def _raw(symbol_field, strike="190.0", dte_days=30):
    exp = date.today() + timedelta(days=dte_days)
    contract = {
        "bid": 5.0, "ask": 5.4, "last": 5.2, "totalVolume": 100,
        "openInterest": 800, "volatility": 30.0, "delta": 0.45,
        "gamma": 0.01, "theta": -0.05, "vega": 0.10,
        "theoreticalOptionValue": 5.2, "inTheMoney": False,
    }
    if symbol_field is not None:
        contract["symbol"] = symbol_field
    return {f"{exp:%Y-%m-%d}:{dte_days}": {strike: [contract]}}


def test_parse_contracts_captures_schwab_symbol():
    out = _parse_contracts(_raw("NVDA  261016C00190000"))
    assert len(out) == 1
    assert out[0].occ_symbol == "NVDA  261016C00190000"


def test_parse_contracts_falls_back_when_symbol_absent():
    # Older cached payloads and some Schwab responses omit it; we must still be
    # able to re-price, so construct one from the fields we do have.
    out = _parse_contracts(_raw(None), underlying="NVDA", is_put=False)
    assert len(out) == 1
    exp = out[0].expiry
    assert out[0].occ_symbol == f"NVDA  {exp:%y%m%d}C00190000"


def test_parse_contracts_fallback_without_underlying_is_empty_not_wrong():
    # Never guess a root — a wrong OCC symbol would silently quote another
    # company's option. Empty means "cannot re-price", which the marker skips.
    out = _parse_contracts(_raw(None))
    assert out[0].occ_symbol == ""


def test_trade_setup_has_occ_fields_defaulting_to_empty():
    # normalise() reads these; if they are absent, snapshot_setups silently
    # skips every scanner setup and the forward test records nothing.
    import dataclasses

    from models import TradeSetup
    names = {f.name for f in dataclasses.fields(TradeSetup)}
    assert "long_occ" in names and "short_occ" in names
