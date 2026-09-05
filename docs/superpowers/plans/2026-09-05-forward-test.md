# Forward Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically record every setup the scanner surfaces, mark it to market on the four existing scan ticks, and resolve it against the +50% / +100% / −50% exit rules — so win rate and realised R:R can be measured per detector.

**Architecture:** A new `backend/forward_test.py` with two entry points called from the existing scan functions. No new scheduler and no new cron: snapshotting and marking both ride the four ticks that `.github/workflows/scan.yml` already drives. Positions and their price series live in two new append-only Supabase tables; re-pricing quotes specific contracts by OCC symbol via `client.get_quotes()` rather than re-scanning chains.

**Tech Stack:** Python 3.12, FastAPI, schwab-py 1.5.1, Supabase (`supabase>=2.0.0`), pytest, React 18 + Vite.

**Spec:** [`docs/superpowers/specs/2026-09-05-forward-test-design.md`](../specs/2026-09-05-forward-test-design.md)

## Global Constraints

- **Never block or fail a scan.** Every forward-test call site wraps in `try/except`, logs, and continues. A Supabase outage must not stop scanning.
- **Never mutate entry state.** `entry_ts`, `entry_debit`, `entry_stock_price` are written once at first sighting and never touched again.
- **Dedup against open positions only.** A resolved setup recurring later is a new position.
- **All timestamps are timezone-aware UTC** (`datetime.now(timezone.utc)`). Never `utcnow()` — it is naive and deprecated, and mixing the two raises `TypeError` against Supabase `timestamptz` values.
- **One `now()` per write.** Compute a timestamp once and reuse it for cache and database in the same operation.
- **Run tests from `backend/`**: `python -m pytest tests/ -q`. `backend/conftest.py` puts `backend/` on `sys.path`, so modules import flat (`from scanner import ...`).
- **Local venv is incomplete** — it lacks `pandas`, `numpy`, `yfinance`. Tests that import those cannot run locally. Every test in this plan is written to avoid them.
- Exit thresholds, verbatim: first target **+50%**, second target **+100%**, stop **−50%**, all as a percentage of entry debit measured on spread mid.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/forward_test.py` | **New.** Tier classification, normalisation, snapshot, mark, resolve. The whole feature except persistence and wiring. |
| `backend/ft_store.py` | **New.** All Supabase reads/writes for the two tables. Isolated so `forward_test.py` is testable without a database. |
| `backend/occ.py` | **New.** OCC symbol construction. Tiny and pure — used by both the scanner and the marker. |
| `backend/models.py` | Add `occ_symbol` to `OptionContract`; add `long_occ`/`short_occ` to `TradeSetup` and `TechnicalSetup`; add liquidity fields to `TechnicalSetup`. |
| `backend/schwab_client.py` | Capture `symbol` in `_parse_contracts`; add `fetch_quotes()`. |
| `backend/technical_scanner.py` | Populate the new liquidity fields and apply the hard gates. |
| `backend/main.py` | Call snapshot + mark from the scan functions; two new endpoints. |
| `backend/requirements.txt` | Add `pytest`. |
| `docs/sql/forward_test.sql` | **New.** The DDL, checked in so the schema is not an out-of-repo assumption. |
| `frontend/src/components/ForwardTest.jsx` | **New.** The results tab. |
| `frontend/src/api.js`, `frontend/src/App.jsx` | Fetch helpers and tab wiring. |

---

## Task 1: Test infrastructure

`pytest` is used by 53 existing tests but is not in `requirements.txt` and is not installed. Nothing else in this plan can follow TDD until that is fixed.

**Files:**
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: a runnable `python -m pytest tests/ -q` from `backend/`.

- [ ] **Step 1: Add pytest to requirements**

In `backend/requirements.txt`, add after `supabase>=2.0.0`:

```
pytest>=8.0.0
```

- [ ] **Step 2: Install and confirm the existing suite collects**

```bash
cd backend && ./venv/bin/pip install -q pytest
./venv/bin/python -m pytest tests/test_bearish_detectors.py -q
```

Expected: `6 passed`. (The other four test files import `pandas`, which this venv lacks; they will error on collection. That is a pre-existing local environment gap, not a regression — do not try to fix it here.)

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: add pytest to requirements — 53 existing tests had no runner"
```

---

## Task 2: OCC symbol construction

Marking positions requires quoting exact contracts. `client.get_quotes()` accepts OCC option symbols, which avoids re-scanning chains — important because `strike_count=20` is ATM-centred, so a setup that moved deep ITM or OTM would fall out of a later chain, losing exactly the trades that moved most.

**Files:**
- Create: `backend/occ.py`
- Test: `backend/tests/test_occ.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `build_occ(symbol: str, expiry: date, is_put: bool, strike: float) -> str`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_occ.py`:

```python
from datetime import date

import pytest

from occ import build_occ


def test_build_occ_standard_call():
    # 6-char root left-justified space-padded, YYMMDD, C/P, strike*1000 in 8 digits
    assert build_occ("NVDA", date(2026, 10, 16), False, 190.0) == "NVDA  261016C00190000"


def test_build_occ_standard_put():
    assert build_occ("NVDA", date(2026, 10, 16), True, 190.0) == "NVDA  261016P00190000"


def test_build_occ_six_char_root_has_no_padding():
    assert build_occ("GOOGL", date(2026, 1, 16), False, 150.0) == "GOOGL 260116C00150000"


def test_build_occ_fractional_strike():
    assert build_occ("F", date(2026, 3, 20), False, 12.5) == "F     260320C00012500"


def test_build_occ_high_strike():
    assert build_occ("AVGO", date(2026, 6, 18), True, 1750.0) == "AVGO  260618P01750000"


def test_build_occ_rejects_overlong_root():
    with pytest.raises(ValueError):
        build_occ("TOOLONG", date(2026, 6, 18), False, 10.0)


def test_build_occ_rejects_nonpositive_strike():
    with pytest.raises(ValueError):
        build_occ("NVDA", date(2026, 6, 18), False, 0.0)


def test_build_occ_rounds_float_noise():
    # 12.34 * 1000 is 12339.999... in binary floating point; must not truncate to 12339
    assert build_occ("XYZ", date(2026, 6, 18), False, 12.34) == "XYZ   260618C00012340"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_occ.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'occ'`

- [ ] **Step 3: Write the implementation**

Create `backend/occ.py`:

```python
"""
OCC option symbol construction.

Format: 6-char underlying root (left-justified, space-padded), YYMMDD expiry,
'C' or 'P', then strike x 1000 as 8 zero-padded digits.

    NVDA  261016C00190000  ->  NVDA 2026-10-16 190 call

Schwab's quote endpoint accepts these directly, which lets us re-price a known
contract without re-fetching a chain.
"""
from datetime import date


def build_occ(symbol: str, expiry: date, is_put: bool, strike: float) -> str:
    root = symbol.strip().upper()
    if not root or len(root) > 6:
        raise ValueError(f"OCC root must be 1-6 characters, got {symbol!r}")
    if strike is None or strike <= 0:
        raise ValueError(f"OCC strike must be positive, got {strike!r}")

    # round() before int() — 12.34 * 1000 is 12339.999... in binary floating
    # point, and truncating would produce the wrong contract.
    thousandths = int(round(strike * 1000))
    return f"{root:<6}{expiry:%y%m%d}{'P' if is_put else 'C'}{thousandths:08d}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_occ.py -q
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/occ.py backend/tests/test_occ.py
git commit -m "feat: OCC option symbol construction for contract-level re-pricing"
```

---

## Task 3: Carry OCC symbols through the data model

Schwab's chain payload contains the contract symbol, but `_parse_contracts` discards it. Capture it, and fall back to construction when absent.

**Files:**
- Modify: `backend/models.py` (`OptionContract`)
- Modify: `backend/schwab_client.py` (`_parse_contracts`)
- Test: `backend/tests/test_occ_capture.py`

**Interfaces:**
- Consumes: `build_occ` from Task 2.
- Produces: `OptionContract.occ_symbol: str` — populated on every parsed contract.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_occ_capture.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_occ_capture.py -q
```

Expected: FAIL — `TypeError: _parse_contracts() got an unexpected keyword argument 'underlying'`

- [ ] **Step 3: Add the field to the model**

In `backend/models.py`, in the `OptionContract` dataclass, add as the **last** field (it needs a default so existing positional construction in tests keeps working):

```python
    occ_symbol: str = ""      # OCC symbol, e.g. "NVDA  261016C00190000"
```

- [ ] **Step 4: Capture it in the parser**

In `backend/schwab_client.py`, change the `_parse_contracts` signature and add the field. Replace the `def _parse_contracts(raw_map: dict) -> list[OptionContract]:` line with:

```python
def _parse_contracts(
    raw_map: dict,
    underlying: str | None = None,
    is_put: bool | None = None,
) -> list[OptionContract]:
```

Then inside the innermost `for c in contract_list:` loop, immediately before `contracts.append(OptionContract(`, insert:

```python
                # Schwab sends the OCC symbol; prefer it. Only construct one as
                # a fallback, and only when we know the root and the side —
                # never guess, because a wrong symbol quotes a different
                # company's option and the error would be invisible.
                occ_symbol = str(c.get("symbol") or "")
                if not occ_symbol and underlying and is_put is not None:
                    from occ import build_occ
                    try:
                        occ_symbol = build_occ(underlying, exp_date, is_put, strike)
                    except ValueError:
                        occ_symbol = ""
```

and add to the `OptionContract(...)` call, after `in_the_money=bool(c.get("inTheMoney", False)),`:

```python
                    occ_symbol=occ_symbol,
```

- [ ] **Step 5: Pass the underlying at both call sites**

In `backend/schwab_client.py`, find the two `_parse_contracts(...)` calls inside `fetch_option_chain` (one for calls, one for puts) and add the arguments:

```bash
cd backend && grep -n "_parse_contracts(" schwab_client.py
```

Change the calls parse to `_parse_contracts(<existing arg>, underlying=symbol, is_put=False)` and the puts parse to `_parse_contracts(<existing arg>, underlying=symbol, is_put=True)`.

- [ ] **Step 6: Add the leg symbols to `TradeSetup`**

Without these, `normalise` reads `""` for every scanner setup and `snapshot_setups` skips all of them — the main scanner would record nothing at all.

In `backend/models.py`, in the `TradeSetup` dataclass, add after `score_breakdown`:

```python
    long_occ: str = ""
    short_occ: str = ""
```

- [ ] **Step 7: Populate them in both spread constructors**

In `backend/scanner.py`, find the two `TradeSetup(` constructions:

```bash
cd backend && grep -n "return TradeSetup(\|TradeSetup(" scanner.py
```

In `construct_best_spread` (bull call) and `construct_bear_put_spread` (bear put), immediately before the `TradeSetup(` call, add — using `False` for the bull constructor and `True` for the bear one:

```python
    from occ import build_occ
    _is_put = <False for bull_call, True for bear_put>
    try:
        long_occ = long_leg.occ_symbol or build_occ(chain.symbol, long_leg.expiry, _is_put, long_leg.strike)
    except ValueError:
        long_occ = ""
    try:
        short_occ = short_leg.occ_symbol or build_occ(chain.symbol, short_leg.expiry, _is_put, short_leg.strike)
    except ValueError:
        short_occ = ""
```

and pass to the constructor:

```python
        long_occ=long_occ, short_occ=short_occ,
```

Adjust `chain.symbol` to whatever local holds the underlying symbol in each constructor.

- [ ] **Step 8: Write the test for it**

Append to `backend/tests/test_occ_capture.py`:

```python
def test_trade_setup_has_occ_fields_defaulting_to_empty():
    # normalise() reads these; if they are absent, snapshot_setups silently
    # skips every scanner setup and the forward test records nothing.
    import dataclasses

    from models import TradeSetup
    names = {f.name for f in dataclasses.fields(TradeSetup)}
    assert "long_occ" in names and "short_occ" in names
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_occ_capture.py tests/test_occ.py tests/test_bearish_detectors.py -q
```

Expected: `18 passed` — the bearish detector tests are included to confirm the new `OptionContract` and `TradeSetup` fields did not break existing positional construction.

- [ ] **Step 10: Commit**

```bash
git add backend/models.py backend/schwab_client.py backend/scanner.py backend/tests/test_occ_capture.py
git commit -m "feat: capture OCC symbols on contracts and both TradeSetup legs"
```

---

## Task 4: TechnicalSetup liquidity fields (spec prerequisite)

The spec's liquidity gate cannot be evaluated for technical setups because `TechnicalSetup` has no OI or spread fields, and `technical_scanner.py` never applies the OI ≥ 100 / spread ≤ 15% hard gates that `scanner.py` enforces. A technical setup can currently be built on a contract with OI 0 and a 40% bid/ask.

**Files:**
- Modify: `backend/models.py` (`TechnicalSetup`)
- Modify: `backend/technical_scanner.py`
- Test: `backend/tests/test_technical_liquidity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TechnicalSetup.long_leg_oi`, `.short_leg_oi`, `.long_leg_spread_pct`, `.short_leg_spread_pct`, `.liquidity_ok`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_technical_liquidity.py`:

```python
from datetime import date, timedelta

from models import OptionContract, TechnicalSetup
from technical_scanner import _leg_liquidity


def _contract(bid, ask, oi):
    return OptionContract(
        strike=100.0, expiry=date.today() + timedelta(days=35), dte=35,
        bid=bid, ask=ask, mid=(bid + ask) / 2, last=(bid + ask) / 2,
        volume=200, open_interest=oi, iv=0.30, delta=0.45,
        gamma=0.01, theta=-0.05, vega=0.10,
        theoretical_value=(bid + ask) / 2, in_the_money=False,
    )


def test_spread_pct_is_a_percentage_not_a_fraction():
    # scanner.py stores these as 0-100; technical must match or the shared
    # tier gate would compare incompatible scales.
    long_leg = _contract(4.9, 5.1, 800)      # 0.2 / 5.0 = 4%
    oi_l, oi_s, sp_l, sp_s, ok = _leg_liquidity(long_leg, None)
    assert abs(sp_l - 4.0) < 0.01


def test_liquidity_ok_true_for_tight_liquid_legs():
    long_leg = _contract(4.9, 5.1, 800)
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is True


def test_liquidity_ok_false_on_low_open_interest():
    long_leg = _contract(4.9, 5.1, 50)       # below the 100 floor
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is False


def test_liquidity_ok_false_on_wide_spread():
    long_leg = _contract(4.0, 6.0, 800)      # 2.0 / 5.0 = 40%
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is False


def test_single_leg_ignores_absent_short_leg():
    long_leg = _contract(4.9, 5.1, 800)
    oi_l, oi_s, sp_l, sp_s, ok = _leg_liquidity(long_leg, None)
    assert oi_s == 0 and sp_s == 0.0 and ok is True


def test_zero_mid_is_illiquid_not_a_crash():
    long_leg = _contract(0.0, 0.0, 800)
    *_, ok = _leg_liquidity(long_leg, None)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_technical_liquidity.py -q
```

Expected: FAIL — `ImportError: cannot import name '_leg_liquidity'`

- [ ] **Step 3: Add the fields to the model**

In `backend/models.py`, in the `TechnicalSetup` dataclass, add as the last fields:

```python
    long_leg_oi: int = 0
    short_leg_oi: int = 0
    long_leg_spread_pct: float = 0.0    # 0-100, matching TradeSetup's scale
    short_leg_spread_pct: float = 0.0
    liquidity_ok: bool = False
    long_occ: str = ""
    short_occ: str = ""
```

- [ ] **Step 4: Add the helper**

In `backend/technical_scanner.py`, after the imports, add:

```python
def _leg_liquidity(long_leg, short_leg):
    """Return (long_oi, short_oi, long_spread_pct, short_spread_pct, ok).

    Spread percentages are 0-100 to match scanner.py, which stores
    round(_spread_pct(c) * 100, 1). Mixing scales would make the shared
    forward-test liquidity gate compare a fraction against a percentage.
    """
    def pct(c):
        if c is None or c.mid <= 0:
            return 100.0          # unpriceable is maximally illiquid
        return round((c.ask - c.bid) / c.mid * 100, 1)

    long_oi = long_leg.open_interest if long_leg else 0
    short_oi = short_leg.open_interest if short_leg else 0
    long_sp = pct(long_leg)
    short_sp = pct(short_leg) if short_leg else 0.0

    ok = (
        long_oi >= 100
        and long_sp <= 10.0
        and (short_leg is None or (short_oi >= 100 and short_sp <= 10.0))
    )
    return long_oi, short_oi, long_sp, short_sp, ok
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_technical_liquidity.py -q
```

Expected: `6 passed`

- [ ] **Step 6: Populate the fields in both constructors**

In `backend/technical_scanner.py`, locate the two places that build a `TechnicalSetup` (the long-call and spread constructors):

```bash
cd backend && grep -n "TechnicalSetup(" technical_scanner.py
```

In each, before the `return TechnicalSetup(...)`, add:

```python
    from occ import build_occ
    oi_l, oi_s, sp_l, sp_s, liq_ok = _leg_liquidity(long_leg, short_leg)
    is_put = direction == "bearish"
    try:
        long_occ = long_leg.occ_symbol or build_occ(symbol, long_leg.expiry, is_put, long_leg.strike)
    except ValueError:
        long_occ = ""
    try:
        short_occ = (
            (short_leg.occ_symbol or build_occ(symbol, short_leg.expiry, is_put, short_leg.strike))
            if short_leg else ""
        )
    except ValueError:
        short_occ = ""
```

and add these keyword arguments to the `TechnicalSetup(...)` call:

```python
        long_leg_oi=oi_l, short_leg_oi=oi_s,
        long_leg_spread_pct=sp_l, short_leg_spread_pct=sp_s,
        liquidity_ok=liq_ok,
        long_occ=long_occ, short_occ=short_occ,
```

Adjust `long_leg` / `short_leg` / `symbol` / `direction` to whatever the surrounding local variables are actually called in each constructor.

- [ ] **Step 7: Apply the hard gate**

In each constructor, immediately after computing `liq_ok`, add:

```python
    # Match scanner.py's hard gate: reject outright rather than surfacing a
    # setup that cannot realistically be filled.
    if oi_l < 100 or sp_l > 15.0 or (short_leg is not None and (oi_s < 100 or sp_s > 15.0)):
        return None
```

- [ ] **Step 8: Verify nothing regressed**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_technical_liquidity.py tests/test_occ.py tests/test_occ_capture.py tests/test_bearish_detectors.py -q
```

Expected: `23 passed`

- [ ] **Step 9: Commit**

```bash
git add backend/models.py backend/technical_scanner.py backend/tests/test_technical_liquidity.py
git commit -m "feat: liquidity fields and hard gates on TechnicalSetup

technical_scanner never applied the OI >= 100 / spread <= 15% gates that
scanner.py enforces, so a setup could be built on a contract with OI 0 and a
40% bid/ask. Also a prerequisite for the forward test's liquidity tier gate,
which had no fields to read."
```

---

## Task 5: Database schema

**Files:**
- Create: `docs/sql/forward_test.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `ft_positions`, `ft_marks`.

- [ ] **Step 1: Write the DDL**

Create `docs/sql/forward_test.sql` with the exact contents of the "Data model" section of the spec (the two `create table` statements and both indexes).

- [ ] **Step 2: Apply it**

Run the file in the Supabase SQL editor for the project. Then confirm:

```sql
select table_name, column_name, data_type
from information_schema.columns
where table_name in ('ft_positions','ft_marks')
order by table_name, ordinal_position;
```

Expected: both tables listed, `ft_positions.id` is `uuid`, `entry_ts` is `timestamp with time zone`.

- [ ] **Step 3: Commit**

```bash
git add docs/sql/forward_test.sql
git commit -m "docs: forward-test table DDL

Checked in so the schema is not an out-of-repo assumption — scanner_cache's
shape currently exists only inside the Supabase project."
```

---

## Task 6: Tier classification

Pure functions over a normalised dict. No database, no network — the heart of the feature, fully unit-testable.

**Files:**
- Create: `backend/forward_test.py`
- Test: `backend/tests/test_forward_test_tiers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `normalise(setup, source: str) -> dict`
  - `classify(norm: dict) -> tuple[str, list[str]]` returning `(tier, gates_failed)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_forward_test_tiers.py`:

```python
from forward_test import classify


def _base(**over):
    """A setup passing every gate; override one field per test."""
    d = {
        "source": "scanner",
        "quality": 70,                 # score for scanner, signal_count for technical
        "rr_ratio": 3.0,
        "liquidity_ok": True,
        "long_leg_spread_pct": 4.0,
        "short_leg_spread_pct": 4.0,
        "earnings_in_window": False,
        "dte_at_entry": 35,
        "breakeven_move_pct": 2.0,
        "trend_opposes": False,
    }
    d.update(over)
    return d


def test_all_gates_pass_is_tier_a():
    tier, failed = classify(_base())
    assert tier == "A"
    assert failed == []


def test_narrow_quality_miss_is_tier_b():
    tier, failed = classify(_base(quality=55))
    assert tier == "B"
    assert failed == ["quality"]


def test_wide_quality_miss_is_tier_c():
    tier, failed = classify(_base(quality=30))
    assert tier == "C"


def test_narrow_breakeven_miss_is_tier_b():
    tier, failed = classify(_base(breakeven_move_pct=4.2))
    assert tier == "B"
    assert failed == ["breakeven"]


def test_wide_breakeven_miss_is_tier_c():
    tier, failed = classify(_base(breakeven_move_pct=8.0))
    assert tier == "C"


def test_breakeven_gate_uses_magnitude_so_bearish_is_not_penalised():
    # bear put spreads carry a positive breakeven_move_pct meaning "must fall";
    # a sign-sensitive comparison would wrongly pass or fail every bear setup.
    assert classify(_base(breakeven_move_pct=-2.0))[0] == "A"


def test_earnings_in_window_is_tier_c_even_as_sole_failure():
    # Binary gate — there is no "nearly no earnings".
    tier, failed = classify(_base(earnings_in_window=True))
    assert tier == "C"
    assert failed == ["earnings"]


def test_wide_spread_is_tier_c_even_as_sole_failure():
    tier, failed = classify(_base(long_leg_spread_pct=9.0))
    assert tier == "C"
    assert failed == ["liquidity"]


def test_two_failures_is_tier_c():
    tier, failed = classify(_base(quality=55, breakeven_move_pct=4.2))
    assert tier == "C"
    assert sorted(failed) == ["breakeven", "quality"]


def test_dte_outside_window_is_tier_c():
    assert classify(_base(dte_at_entry=12))[0] == "C"
    assert classify(_base(dte_at_entry=60))[0] == "C"


def test_technical_quality_uses_signal_count_threshold():
    # 5 of 7 signals is the technical analogue of score >= 60.
    assert classify(_base(source="technical", quality=5))[0] == "A"
    assert classify(_base(source="technical", quality=4))[0] == "B"
    assert classify(_base(source="technical", quality=2))[0] == "C"


def test_trend_opposition_is_tier_c():
    tier, failed = classify(_base(trend_opposes=True))
    assert tier == "C"
    assert failed == ["trend"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_tiers.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'forward_test'`

- [ ] **Step 3: Write the implementation**

Create `backend/forward_test.py`:

```python
"""
Forward test — records every surfaced setup and marks it to market until it
resolves against the +50% / +100% / -50% exit rules.

See docs/superpowers/specs/2026-09-05-forward-test-design.md.
"""
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

# Exit rules, as a percentage of entry debit measured on spread mid.
TARGET_1_PCT = 50.0
TARGET_2_PCT = 100.0
STOP_PCT = -50.0

# Tier A gates.
QUALITY_MIN = {"scanner": 60, "technical": 5}       # score / signal_count
QUALITY_NEAR = {"scanner": 10, "technical": 1}      # Tier B band below threshold
RR_MIN = 2.0
SPREAD_MAX_PCT = 6.0
DTE_MIN, DTE_MAX = 25, 45
BREAKEVEN_MAX_PCT = 3.5
BREAKEVEN_NEAR_PCT = 5.0

# Only these two gates have a meaningful "nearly"; failing any other gate is
# Tier C even when it is the sole failure.
NEAR_MISS_GATES = {"quality", "breakeven"}


def classify(norm: dict) -> tuple[str, list[str]]:
    """Return (tier, gates_failed) for a normalised setup."""
    source = norm["source"]
    failed: list[str] = []

    if norm["quality"] < QUALITY_MIN[source]:
        failed.append("quality")
    if norm["rr_ratio"] < RR_MIN:
        failed.append("rr")
    if not norm["liquidity_ok"] or \
            norm["long_leg_spread_pct"] > SPREAD_MAX_PCT or \
            norm["short_leg_spread_pct"] > SPREAD_MAX_PCT:
        failed.append("liquidity")
    if norm["earnings_in_window"]:
        failed.append("earnings")
    if not (DTE_MIN <= norm["dte_at_entry"] <= DTE_MAX):
        failed.append("dte")
    # Magnitude, not signed value: bear put spreads carry a positive
    # breakeven_move_pct meaning "the stock must fall this far".
    if abs(norm["breakeven_move_pct"]) > BREAKEVEN_MAX_PCT:
        failed.append("breakeven")
    if norm["trend_opposes"]:
        failed.append("trend")

    if not failed:
        return "A", []

    if len(failed) == 1 and failed[0] in NEAR_MISS_GATES:
        gate = failed[0]
        near = (
            gate == "quality"
            and norm["quality"] >= QUALITY_MIN[source] - QUALITY_NEAR[source]
        ) or (
            gate == "breakeven"
            and abs(norm["breakeven_move_pct"]) <= BREAKEVEN_NEAR_PCT
        )
        if near:
            return "B", failed

    return "C", failed
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_tiers.py -q
```

Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/forward_test.py backend/tests/test_forward_test_tiers.py
git commit -m "feat: forward-test tier classification"
```

---

## Task 7: Normalisation

`TradeSetup` and `TechnicalSetup` share no base class and disagree on field names. One function maps both to the shape `classify` expects.

**Files:**
- Modify: `backend/forward_test.py`
- Test: `backend/tests/test_forward_test_normalise.py`

**Interfaces:**
- Consumes: `classify` from Task 6.
- Produces: `normalise(setup, source: str) -> dict` with keys: `source`, `dedup_key`, `symbol`, `detector`, `structure`, `direction`, `expiry`, `dte_at_entry`, `long_strike`, `short_strike`, `long_occ`, `short_occ`, `entry_debit`, `entry_stock_price`, `quality`, `rr_ratio`, `breakeven_move_pct`, `liquidity_ok`, `long_leg_spread_pct`, `short_leg_spread_pct`, `earnings_in_window`, `trend_opposes`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_forward_test_normalise.py`:

```python
from datetime import date, datetime, timezone

from models import (CatalystContext, MispricingSignal, TechnicalContext,
                    TechnicalSetup, TradeSetup)
from forward_test import normalise


def _trade_setup(**over):
    sig = MispricingSignal(symbol="NVDA", detector="parity", description="",
                           confidence=0.8, raw_data={})
    cat = CatalystContext(earnings_date=None, earnings_dte=None,
                          earnings_in_window=False, iv_trend="STABLE",
                          iv_expansion_likely=False, recent_volume_spike=False,
                          catalyst_summary="")
    kwargs = dict(
        symbol="NVDA", stock_price=190.0, signal=sig, catalyst=cat,
        structure="bull_call_spread", long_strike=190.0, short_strike=200.0,
        expiry=date(2026, 10, 16), dte=35, net_debit=3.0, max_gain=7.0,
        max_loss=3.0, breakeven=193.0, breakeven_move_pct=1.6, rr_ratio=2.33,
        probability_of_profit=45.0, net_delta=0.3, net_theta=0.01,
        net_vega=0.05, long_leg_oi=800, short_leg_oi=600, long_leg_volume=300,
        long_leg_spread_pct=4.0, short_leg_spread_pct=4.0, liquidity_ok=True,
        scenarios_5d=[], scenarios_10d=[], scenarios_expiry=[], score=70,
        timestamp=datetime.now(timezone.utc), order_string="",
    )
    kwargs.update(over)
    return TradeSetup(**kwargs)


def _technical_setup(**over):
    kwargs = dict(
        symbol="AMD", stock_price=150.0, direction="bullish", signal_count=6,
        signal_details={}, structure="bull_call_spread", strike=150.0,
        short_strike=160.0, expiry=date(2026, 10, 16), dte=35, delta=0.45,
        iv_rank=40.0, premium=2.5, price_target=165.0, rr_ratio=3.0,
        max_loss=250.0, breakeven_move_pct=1.7, probability_of_profit=45.0,
        order_string="", earnings_within_dte=False,
        long_leg_oi=800, short_leg_oi=600, long_leg_spread_pct=4.0,
        short_leg_spread_pct=4.0, liquidity_ok=True,
        long_occ="AMD   261016C00150000", short_occ="AMD   261016C00160000",
    )
    kwargs.update(over)
    return TechnicalSetup(**kwargs)


def test_trade_setup_maps_net_debit_to_entry_debit():
    n = normalise(_trade_setup(), "scanner")
    assert n["entry_debit"] == 3.0
    assert n["quality"] == 70
    assert n["detector"] == "parity"


def test_technical_setup_maps_premium_and_strike():
    n = normalise(_technical_setup(), "technical")
    assert n["entry_debit"] == 2.5
    assert n["long_strike"] == 150.0
    assert n["quality"] == 6
    assert n["detector"] is None


def test_dedup_key_includes_strikes_and_expiry():
    n = normalise(_trade_setup(), "scanner")
    assert n["dedup_key"] == "NVDA|parity|bull_call_spread|2026-10-16|190.0|200.0"


def test_dedup_key_differs_when_strikes_move():
    a = normalise(_trade_setup(), "scanner")["dedup_key"]
    b = normalise(_trade_setup(long_strike=195.0), "scanner")["dedup_key"]
    assert a != b


def test_direction_derived_from_structure_for_trade_setup():
    assert normalise(_trade_setup(), "scanner")["direction"] == "bullish"
    assert normalise(_trade_setup(structure="bear_put_spread"),
                     "scanner")["direction"] == "bearish"


def test_technical_direction_taken_directly():
    assert normalise(_technical_setup(direction="bearish"),
                     "technical")["direction"] == "bearish"


def test_trend_opposes_when_bias_contradicts_structure():
    ctx = TechnicalContext(symbol="NVDA", price=190.0, ma50=180.0, ma200=170.0,
                           pct_from_ma50=5.0, pct_from_ma200=10.0,
                           trend="downtrend", bias="bearish")
    n = normalise(_trade_setup(technical_context=ctx), "scanner")
    assert n["trend_opposes"] is True


def test_missing_technical_context_counts_as_neutral():
    n = normalise(_trade_setup(technical_context=None), "scanner")
    assert n["trend_opposes"] is False


def test_technical_setups_never_oppose_their_own_trend_signal():
    # `direction` IS the trend read for this source, so the gate is tautological.
    assert normalise(_technical_setup(direction="bearish"),
                     "technical")["trend_opposes"] is False


def test_earnings_flag_reads_the_right_field_per_source():
    cat = CatalystContext(earnings_date=date(2026, 10, 1), earnings_dte=10,
                          earnings_in_window=True, iv_trend="RISING",
                          iv_expansion_likely=True, recent_volume_spike=False,
                          catalyst_summary="")
    assert normalise(_trade_setup(catalyst=cat), "scanner")["earnings_in_window"] is True
    assert normalise(_technical_setup(earnings_within_dte=True),
                     "technical")["earnings_in_window"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_normalise.py -q
```

Expected: FAIL — `ImportError: cannot import name 'normalise'`

- [ ] **Step 3: Write the implementation**

Append to `backend/forward_test.py`:

```python
_BEARISH_STRUCTURES = {"bear_put_spread"}


def _direction_from_structure(structure: str) -> str:
    return "bearish" if structure in _BEARISH_STRUCTURES else "bullish"


def normalise(setup, source: str) -> dict:
    """Map a TradeSetup or TechnicalSetup onto one common shape.

    The two dataclasses share no base class and disagree on field names
    (long_strike/strike, net_debit/premium, score/signal_count), so every
    consumer would otherwise need to branch on source.
    """
    if source == "scanner":
        long_strike = setup.long_strike
        entry_debit = setup.net_debit
        quality = setup.score
        detector = setup.signal.detector
        direction = _direction_from_structure(setup.structure)
        earnings = bool(setup.catalyst.earnings_in_window)
        ctx = getattr(setup, "technical_context", None)
        bias = getattr(ctx, "bias", None) if ctx else None
        # Missing context counts as neutral — absence of a trend read is not
        # evidence against the trade.
        trend_opposes = bias is not None and bias != "neutral" and bias != direction
        long_occ = getattr(setup, "long_occ", "") or ""
        short_occ = getattr(setup, "short_occ", "") or ""
    elif source == "technical":
        long_strike = setup.strike
        entry_debit = setup.premium
        quality = setup.signal_count
        detector = None
        direction = setup.direction
        earnings = bool(setup.earnings_within_dte)
        # `direction` is itself the trend read for this source, so it can never
        # oppose itself. The gate is tautological here by construction.
        trend_opposes = False
        long_occ = setup.long_occ or ""
        short_occ = setup.short_occ or ""
    else:
        raise ValueError(f"unknown source {source!r}")

    short_strike = setup.short_strike
    dedup_key = "|".join([
        setup.symbol,
        detector or source,
        setup.structure,
        setup.expiry.isoformat(),
        str(float(long_strike)),
        str(float(short_strike)) if short_strike is not None else "",
    ])

    return {
        "source": source,
        "dedup_key": dedup_key,
        "symbol": setup.symbol,
        "detector": detector,
        "structure": setup.structure,
        "direction": direction,
        "expiry": setup.expiry,
        "dte_at_entry": setup.dte,
        "long_strike": float(long_strike),
        "short_strike": float(short_strike) if short_strike is not None else None,
        "long_occ": long_occ,
        "short_occ": short_occ,
        "entry_debit": float(entry_debit),
        "entry_stock_price": float(setup.stock_price),
        "quality": quality,
        "rr_ratio": float(setup.rr_ratio),
        "breakeven_move_pct": float(setup.breakeven_move_pct),
        "liquidity_ok": bool(setup.liquidity_ok),
        "long_leg_spread_pct": float(setup.long_leg_spread_pct),
        "short_leg_spread_pct": float(setup.short_leg_spread_pct),
        "earnings_in_window": earnings,
        "trend_opposes": trend_opposes,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_normalise.py -q
```

Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/forward_test.py backend/tests/test_forward_test_normalise.py
git commit -m "feat: normalise TradeSetup and TechnicalSetup to one shape"
```

---

## Task 8: Outcome state machine

Given a position and a fresh mark, decide what changed. Pure logic — no database.

**Files:**
- Modify: `backend/forward_test.py`
- Test: `backend/tests/test_forward_test_outcomes.py`

**Interfaces:**
- Consumes: the module constants from Task 6.
- Produces:
  - `pnl_pct(spread_mid: float, entry_debit: float) -> float`
  - `apply_mark(position: dict, pnl: float, now: datetime, today: date) -> dict` returning the fields to update
  - `realized_pnl(position: dict, final_pnl: float | None) -> float`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_forward_test_outcomes.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest

from forward_test import apply_mark, pnl_pct, realized_pnl

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 10)
EXPIRY = date(2026, 10, 16)


def _pos(**over):
    d = {"entry_debit": 3.0, "expiry": EXPIRY, "status": "open",
         "t1_ts": None, "t2_ts": None, "stop_ts": None,
         "mfe_pct": 0.0, "mae_pct": 0.0, "mark_failures": 0}
    d.update(over)
    return d


def test_pnl_pct_basic():
    assert pnl_pct(4.5, 3.0) == 50.0
    assert pnl_pct(6.0, 3.0) == 100.0
    assert pnl_pct(1.5, 3.0) == -50.0
    assert pnl_pct(3.0, 3.0) == 0.0


def test_pnl_pct_zero_entry_is_not_a_division_error():
    assert pnl_pct(1.0, 0.0) == 0.0


def test_first_target_records_t1_but_stays_open():
    upd = apply_mark(_pos(), 55.0, NOW, TODAY)
    assert upd["t1_ts"] == NOW
    assert upd["status"] == "target1"
    assert upd["closed_ts"] is None      # second half still running


def test_second_target_closes():
    upd = apply_mark(_pos(status="target1", t1_ts=NOW), 105.0, NOW, TODAY)
    assert upd["t2_ts"] == NOW
    assert upd["status"] == "target2"
    assert upd["closed_ts"] == NOW


def test_a_single_mark_can_cross_both_targets():
    # A gap through +100% must not be recorded as only the first target.
    upd = apply_mark(_pos(), 120.0, NOW, TODAY)
    assert upd["t1_ts"] == NOW and upd["t2_ts"] == NOW
    assert upd["status"] == "target2"


def test_stop_closes():
    upd = apply_mark(_pos(), -55.0, NOW, TODAY)
    assert upd["stop_ts"] == NOW
    assert upd["status"] == "stopped"
    assert upd["closed_ts"] == NOW


def test_stop_after_first_target_still_closes():
    upd = apply_mark(_pos(status="target1", t1_ts=NOW), -60.0, NOW, TODAY)
    assert upd["status"] == "stopped"
    assert upd["closed_ts"] == NOW


def test_t1_is_not_overwritten_on_a_later_mark():
    earlier = NOW - timedelta(days=2)
    upd = apply_mark(_pos(status="target1", t1_ts=earlier), 60.0, NOW, TODAY)
    assert upd["t1_ts"] == earlier


def test_expiry_closes_using_the_last_mark():
    upd = apply_mark(_pos(expiry=date(2026, 9, 9)), -20.0, NOW, TODAY)
    assert upd["status"] == "expired"
    assert upd["closed_ts"] == NOW


def test_mfe_and_mae_track_extremes():
    upd = apply_mark(_pos(mfe_pct=30.0, mae_pct=-10.0), 45.0, NOW, TODAY)
    assert upd["mfe_pct"] == 45.0 and upd["mae_pct"] == -10.0
    upd = apply_mark(_pos(mfe_pct=30.0, mae_pct=-10.0), -25.0, NOW, TODAY)
    assert upd["mfe_pct"] == 30.0 and upd["mae_pct"] == -25.0


def test_realized_target1_then_target2_is_75():
    assert realized_pnl(_pos(status="target2", t1_ts=NOW, t2_ts=NOW), None) == 75.0


def test_realized_target1_then_stopped_is_zero():
    assert realized_pnl(_pos(status="stopped", t1_ts=NOW, stop_ts=NOW), None) == 0.0


def test_realized_stopped_without_target_is_minus_50():
    assert realized_pnl(_pos(status="stopped"), None) == -50.0


def test_realized_expired_without_target_is_the_final_mark():
    assert realized_pnl(_pos(status="expired"), -18.0) == -18.0


def test_realized_expired_after_target1_is_blended():
    assert realized_pnl(_pos(status="expired", t1_ts=NOW), 10.0) == 30.0


def test_realized_expired_with_no_final_mark_is_none():
    assert realized_pnl(_pos(status="expired"), None) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_outcomes.py -q
```

Expected: FAIL — `ImportError: cannot import name 'apply_mark'`

- [ ] **Step 3: Write the implementation**

Append to `backend/forward_test.py`:

```python
MAX_MARK_FAILURES = 8


def pnl_pct(spread_mid: float, entry_debit: float) -> float:
    if not entry_debit:
        return 0.0
    return (spread_mid - entry_debit) / entry_debit * 100.0


def apply_mark(position: dict, pnl: float, now: datetime, today: date) -> dict:
    """Return the position fields to update for one mark.

    Order matters: targets are checked before the stop so a mark that gapped
    through both is recorded as reaching the target, and both targets are
    checked on the same mark so a gap past +100% is not recorded as only +50%.
    """
    upd: dict = {
        "mfe_pct": max(position.get("mfe_pct") or 0.0, pnl),
        "mae_pct": min(position.get("mae_pct") or 0.0, pnl),
        "t1_ts": position.get("t1_ts"),
        "t2_ts": position.get("t2_ts"),
        "stop_ts": position.get("stop_ts"),
        "status": position.get("status", "open"),
        "closed_ts": None,
        "last_pnl_pct": pnl,
    }

    if pnl >= TARGET_1_PCT and upd["t1_ts"] is None:
        upd["t1_ts"] = now
        upd["status"] = "target1"

    if pnl >= TARGET_2_PCT and upd["t2_ts"] is None:
        upd["t2_ts"] = now
        upd["status"] = "target2"
        upd["closed_ts"] = now
        return upd

    if pnl <= STOP_PCT:
        upd["stop_ts"] = now
        upd["status"] = "stopped"
        upd["closed_ts"] = now
        return upd

    if today > position["expiry"]:
        upd["status"] = "expired"
        upd["closed_ts"] = now

    return upd


def realized_pnl(position: dict, final_pnl: float | None) -> float | None:
    """Blended scale-out: half the position off at each target."""
    status = position.get("status")
    hit_t1 = position.get("t1_ts") is not None

    if status == "target2":
        return (TARGET_1_PCT + TARGET_2_PCT) / 2      # +75%
    if status == "stopped":
        return (TARGET_1_PCT + STOP_PCT) / 2 if hit_t1 else STOP_PCT
    if status == "expired":
        if final_pnl is None:
            return None
        return (TARGET_1_PCT + final_pnl) / 2 if hit_t1 else final_pnl
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_outcomes.py -q
```

Expected: `16 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/forward_test.py backend/tests/test_forward_test_outcomes.py
git commit -m "feat: forward-test outcome state machine and blended scale-out P&L"
```

---

## Task 9: Persistence layer

All Supabase access for the two tables, isolated so the logic above stays testable without a database.

**Files:**
- Create: `backend/ft_store.py`
- Modify: `backend/supabase_client.py` (expose a public client accessor)
- Test: `backend/tests/test_ft_store.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()`.
- Produces:
  - `insert_position(row: dict) -> str | None`
  - `touch_position(position_id: str, now: datetime) -> None`
  - `find_open_by_dedup(keys: list[str]) -> dict[str, dict]`
  - `list_open_positions() -> list[dict]`
  - `insert_mark(position_id: str, ts: datetime, spread_mid: float, pnl: float, stock_price: float | None) -> None`
  - `update_position(position_id: str, fields: dict) -> None`
  - `fetch_all_positions(status: str | None, tier: str | None) -> list[dict]`

- [ ] **Step 1: Expose a public client accessor**

In `backend/supabase_client.py`, add after `_get_client`:

```python
def get_client() -> Client | None:
    """Public accessor. Returns None when Supabase is not configured, in which
    case every caller must degrade gracefully rather than raise."""
    return _get_client()
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_ft_store.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_ft_store.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ft_store'`

- [ ] **Step 4: Write the implementation**

Create `backend/ft_store.py`:

```python
"""
Supabase persistence for the forward test.

Every function degrades to a no-op when Supabase is unconfigured or erroring:
the forward test must never block or fail a scan.
"""
import logging
from datetime import date, datetime
from typing import Any

from supabase_client import get_client

logger = logging.getLogger(__name__)

POSITIONS = "ft_positions"
MARKS = "ft_marks"


def _encode(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(d: dict) -> dict:
    return {k: _encode(v) for k, v in d.items()}


def insert_position(row: dict) -> str | None:
    db = get_client()
    if db is None:
        return None
    try:
        resp = db.table(POSITIONS).insert(_row(row)).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as e:
        logger.error("forward test: insert_position failed: %s", e)
        return None


def touch_position(position_id: str, now: datetime) -> None:
    db = get_client()
    if db is None:
        return
    try:
        current = db.table(POSITIONS).select("times_seen").eq("id", position_id).execute()
        seen = (current.data[0]["times_seen"] if current.data else 0) + 1
        db.table(POSITIONS).update(
            {"times_seen": seen, "last_seen_ts": now.isoformat()}
        ).eq("id", position_id).execute()
    except Exception as e:
        logger.error("forward test: touch_position failed: %s", e)


def find_open_by_dedup(keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    db = get_client()
    if db is None:
        return {}
    try:
        resp = (db.table(POSITIONS).select("*")
                  .in_("dedup_key", keys).is_("closed_ts", "null").execute())
        return {r["dedup_key"]: r for r in (resp.data or [])}
    except Exception as e:
        logger.error("forward test: find_open_by_dedup failed: %s", e)
        return {}


def list_open_positions() -> list[dict]:
    db = get_client()
    if db is None:
        return []
    try:
        resp = db.table(POSITIONS).select("*").is_("closed_ts", "null").execute()
        return resp.data or []
    except Exception as e:
        logger.error("forward test: list_open_positions failed: %s", e)
        return []


def insert_mark(position_id: str, ts: datetime, spread_mid: float,
                pnl: float, stock_price: float | None) -> None:
    db = get_client()
    if db is None:
        return
    try:
        db.table(MARKS).upsert({
            "position_id": position_id, "ts": ts.isoformat(),
            "spread_mid": spread_mid, "pnl_pct": pnl, "stock_price": stock_price,
        }).execute()
    except Exception as e:
        logger.error("forward test: insert_mark failed: %s", e)


def update_position(position_id: str, fields: dict) -> None:
    db = get_client()
    if db is None:
        return
    try:
        db.table(POSITIONS).update(_row(fields)).eq("id", position_id).execute()
    except Exception as e:
        logger.error("forward test: update_position failed: %s", e)


def fetch_all_positions(status: str | None = None,
                        tier: str | None = None) -> list[dict]:
    db = get_client()
    if db is None:
        return []
    try:
        q = db.table(POSITIONS).select("*")
        if status:
            q = q.eq("status", status)
        if tier:
            q = q.eq("tier", tier)
        return q.order("entry_ts", desc=True).limit(1000).execute().data or []
    except Exception as e:
        logger.error("forward test: fetch_all_positions failed: %s", e)
        return []
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_ft_store.py -q
```

Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/ft_store.py backend/supabase_client.py backend/tests/test_ft_store.py
git commit -m "feat: forward-test persistence layer, degrading to no-op without Supabase"
```

---

## Task 10: Snapshot and mark

The two entry points the scans call.

**Files:**
- Modify: `backend/forward_test.py`
- Modify: `backend/schwab_client.py` (add `fetch_quotes`)
- Test: `backend/tests/test_forward_test_snapshot.py`

**Interfaces:**
- Consumes: `normalise`, `classify`, `apply_mark`, `realized_pnl`, `pnl_pct`, all of `ft_store`.
- Produces:
  - `snapshot_setups(setups: list, source: str) -> int` — count of new positions
  - `mark_open_positions() -> int` — count of positions marked
  - `schwab_client.fetch_quotes(symbols: list[str]) -> dict[str, float]` — OCC symbol → mid

- [ ] **Step 1: Add the quote fetcher**

In `backend/schwab_client.py`, append:

```python
def fetch_quotes(symbols: list[str]) -> dict[str, float]:
    """Quote specific contracts by OCC symbol; returns {symbol: mid}.

    Used to re-price tracked positions without re-fetching chains — chains are
    ATM-centred (strike_count=20), so a position that moved deep ITM or OTM
    would simply fall out of one.
    """
    if not symbols:
        return {}
    out: dict[str, float] = {}
    client = _get_schwab_client()
    if client is None:
        return out
    # Chunked: Schwab caps symbols per request, and one oversized request
    # failing would lose every quote rather than one chunk's worth.
    for i in range(0, len(symbols), 25):
        chunk = symbols[i:i + 25]
        try:
            resp = client.get_quotes(chunk)
            data = resp.json() if hasattr(resp, "json") else {}
            for sym, payload in (data or {}).items():
                q = payload.get("quote") or {}
                bid, ask = _safe_float(q.get("bidPrice")), _safe_float(q.get("askPrice"))
                if bid > 0 and ask > 0:
                    out[sym] = round((bid + ask) / 2, 4)
        except Exception as e:
            logger.error("fetch_quotes failed for %d symbols: %s", len(chunk), e)
    return out
```

Confirm the singleton accessor's real name and fix the call if it differs:

```bash
cd backend && grep -n "def _get_schwab_client\|_schwab_client" schwab_client.py | head -3
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_forward_test_snapshot.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_snapshot.py -q
```

Expected: FAIL — `AttributeError: module 'forward_test' has no attribute 'snapshot_setups'`

- [ ] **Step 4: Write the implementation**

Append to `backend/forward_test.py` (and add `import ft_store` plus `from schwab_client import fetch_quotes` to the imports at the top):

```python
def snapshot_setups(setups: list, source: str) -> int:
    """Record newly-seen setups. Returns the count of new positions.

    Never raises: a forward-test failure must not fail the scan that called it.
    """
    if not setups:
        return 0
    try:
        now = datetime.now(timezone.utc)
        norms = []
        for s in setups:
            try:
                norms.append(normalise(s, source))
            except Exception as e:
                logger.error("forward test: could not normalise a %s setup: %s", source, e)

        # A position with no OCC symbols could never be marked; recording it
        # would leave a permanent open row that only ever times out.
        norms = [n for n in norms if n["long_occ"]]
        if not norms:
            return 0

        existing = ft_store.find_open_by_dedup([n["dedup_key"] for n in norms])
        new_count = 0

        for n in norms:
            prior = existing.get(n["dedup_key"])
            if prior:
                # Entry price and timestamp are deliberately untouched.
                ft_store.touch_position(prior["id"], now)
                continue

            tier, gates_failed = classify(n)
            row = {
                "dedup_key": n["dedup_key"], "source": n["source"],
                "symbol": n["symbol"], "detector": n["detector"],
                "structure": n["structure"], "direction": n["direction"],
                "expiry": n["expiry"], "dte_at_entry": n["dte_at_entry"],
                "long_strike": n["long_strike"], "short_strike": n["short_strike"],
                "long_occ": n["long_occ"], "short_occ": n["short_occ"],
                "entry_ts": now, "entry_debit": n["entry_debit"],
                "entry_stock_price": n["entry_stock_price"],
                "score_at_entry": n["quality"], "rr_at_entry": n["rr_ratio"],
                "breakeven_move_pct": n["breakeven_move_pct"],
                "tier": tier, "gates_failed": gates_failed,
                "status": "open", "times_seen": 1, "last_seen_ts": now,
            }
            if ft_store.insert_position(row):
                new_count += 1

        logger.info("forward test: %d new positions from %d %s setups",
                    new_count, len(norms), source)
        return new_count
    except Exception as e:
        logger.exception("forward test: snapshot_setups failed: %s", e)
        return 0


def mark_open_positions(now: datetime | None = None) -> int:
    """Re-price every open position and advance its state. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        positions = ft_store.list_open_positions()
        if not positions:
            return 0

        symbols = sorted({
            occ for p in positions
            for occ in (p.get("long_occ"), p.get("short_occ")) if occ
        })
        quotes = fetch_quotes(symbols)

        marked = 0
        for p in positions:
            long_mid = quotes.get(p.get("long_occ"))
            short_occ = p.get("short_occ") or ""
            short_mid = quotes.get(short_occ) if short_occ else 0.0

            if long_mid is None or (short_occ and short_mid is None):
                # A missing quote is not a price of zero. Count the failure and
                # leave the position open.
                failures = (p.get("mark_failures") or 0) + 1
                if failures >= MAX_MARK_FAILURES:
                    ft_store.update_position(p["id"], {
                        "mark_failures": failures, "status": "unpriceable",
                        "closed_ts": now,
                    })
                else:
                    ft_store.update_position(p["id"], {"mark_failures": failures})
                continue

            spread_mid = round(long_mid - (short_mid or 0.0), 4)
            pnl = round(pnl_pct(spread_mid, p["entry_debit"]), 2)
            # stock_price is left null: quoting the underlying would add one
            # request per distinct symbol for a field nothing currently reads.
            # The column stays in the schema so it can be backfilled later
            # without a migration.
            ft_store.insert_mark(p["id"], now, spread_mid, pnl, None)

            expiry = p["expiry"]
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry)
            upd = apply_mark({**p, "expiry": expiry}, pnl, now, today)

            final = upd.pop("last_pnl_pct", None)
            if upd.get("closed_ts") is not None:
                upd["realized_pnl_pct"] = realized_pnl({**p, **upd}, final)
            else:
                upd.pop("closed_ts", None)

            if p.get("mark_failures"):
                upd["mark_failures"] = 0
            ft_store.update_position(p["id"], upd)
            marked += 1

        logger.info("forward test: marked %d/%d open positions", marked, len(positions))
        return marked
    except Exception as e:
        logger.exception("forward test: mark_open_positions failed: %s", e)
        return 0
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_snapshot.py -q
```

Expected: `8 passed`

- [ ] **Step 6: Run the whole forward-test suite**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_*.py tests/test_ft_store.py tests/test_occ*.py -q
```

Expected: `51 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/forward_test.py backend/schwab_client.py backend/tests/test_forward_test_snapshot.py
git commit -m "feat: forward-test snapshot and mark-to-market"
```

---

## Task 11: Wire into the scans

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `snapshot_setups`, `mark_open_positions`.
- Produces: positions recorded and marked on every scan tick.

- [ ] **Step 1: Import**

In `backend/main.py`, with the other local imports:

```python
from forward_test import mark_open_positions, snapshot_setups
```

- [ ] **Step 2: Snapshot and mark in the full scan**

In `_run_scan_inner`, immediately after `save_scan_results("opportunities", ...)`:

```python
        # After the chains_ok guard, so a failed scan never records positions.
        snapshot_setups(filtered, "scanner")
        mark_open_positions()
```

- [ ] **Step 3: Snapshot in the technical scan**

In `_run_technical_scan`, immediately after its `save_scan_results(...)`:

```python
            snapshot_setups(setups, "technical")
```

Marking is deliberately not repeated here — `_run_technical_scan` runs at 10:30 ET between two full scans, and marking four times a day is already the design's cadence.

- [ ] **Step 4: Verify the app still imports and routes register**

```bash
cd backend && SCHWAB_APP_KEY=x SCHWAB_APP_SECRET=y ./venv/bin/python -c "
import sys, types
from unittest.mock import MagicMock
for n in ['yfinance','supabase','pandas','numpy','scipy','scipy.stats']:
    m=MagicMock(); m.__spec__=types.SimpleNamespace(name=n); sys.modules[n]=m
import main
print(len([r for r in main.app.routes if hasattr(r,'methods')]), 'routes')
"
```

Expected: route count printed with no traceback.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat: record and mark forward-test positions on every scan tick"
```

---

## Task 12: API endpoints

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_forward_test_stats.py`

**Interfaces:**
- Consumes: `ft_store.fetch_all_positions`.
- Produces: `forward_test.aggregate(positions: list[dict]) -> dict`; `GET /forward-test`; `GET /forward-test/positions`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_forward_test_stats.py`:

```python
from forward_test import aggregate


def _p(**over):
    d = {"tier": "A", "detector": "parity", "source": "scanner",
         "status": "target2", "realized_pnl_pct": 75.0,
         "mfe_pct": 110.0, "mae_pct": -10.0}
    d.update(over)
    return d


def test_open_positions_are_counted_but_excluded_from_stats():
    out = aggregate([_p(), _p(status="open", realized_pnl_pct=None)])
    assert out["open_count"] == 1
    assert out["closed_count"] == 1
    assert out["overall"]["n"] == 1


def test_win_rate_counts_positive_realized_only():
    out = aggregate([_p(realized_pnl_pct=75.0), _p(realized_pnl_pct=-50.0),
                     _p(realized_pnl_pct=0.0)])
    assert out["overall"]["n"] == 3
    assert abs(out["overall"]["win_rate"] - 33.3) < 0.1     # 0% is not a win


def test_grouped_by_tier_and_detector():
    out = aggregate([_p(tier="A", detector="parity", realized_pnl_pct=75.0),
                     _p(tier="B", detector="skew", realized_pnl_pct=-50.0)])
    assert out["by_tier"]["A"]["n"] == 1
    assert out["by_tier"]["B"]["avg_pnl"] == -50.0
    assert out["by_detector"]["skew"]["n"] == 1


def test_unpriceable_positions_are_excluded_entirely():
    out = aggregate([_p(), _p(status="unpriceable", realized_pnl_pct=None)])
    assert out["closed_count"] == 1
    assert out["unpriceable_count"] == 1


def test_empty_input_does_not_divide_by_zero():
    out = aggregate([])
    assert out["overall"] == {"n": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                             "avg_mfe": 0.0, "avg_mae": 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_stats.py -q
```

Expected: FAIL — `ImportError: cannot import name 'aggregate'`

- [ ] **Step 3: Write the implementation**

Append to `backend/forward_test.py`:

```python
def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_pnl": 0.0, "avg_mfe": 0.0, "avg_mae": 0.0}
    pnls = [r["realized_pnl_pct"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)          # breakeven is not a win
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "avg_pnl": round(sum(pnls) / n, 1),
        "avg_mfe": round(sum((r.get("mfe_pct") or 0.0) for r in rows) / n, 1),
        "avg_mae": round(sum((r.get("mae_pct") or 0.0) for r in rows) / n, 1),
    }


def aggregate(positions: list[dict]) -> dict:
    """Summarise closed positions. Open ones are counted, never averaged in —
    including them would quietly dilute every number."""
    closed = [p for p in positions
              if p.get("status") in {"target1", "target2", "stopped", "expired"}
              and p.get("realized_pnl_pct") is not None]
    open_count = sum(1 for p in positions if p.get("status") in {"open", "target1"}
                     and p.get("realized_pnl_pct") is None)
    unpriceable = sum(1 for p in positions if p.get("status") == "unpriceable")

    def group(key):
        out: dict[str, list[dict]] = {}
        for p in closed:
            out.setdefault(str(p.get(key) or "—"), []).append(p)
        return {k: _stats(v) for k, v in sorted(out.items())}

    return {
        "overall": _stats(closed),
        "by_tier": group("tier"),
        "by_detector": group("detector"),
        "by_source": group("source"),
        "closed_count": len(closed),
        "open_count": open_count,
        "unpriceable_count": unpriceable,
    }
```

- [ ] **Step 4: Add the endpoints**

In `backend/main.py`, after the `/celt-setups` endpoint:

```python
@app.get("/forward-test")
@limiter.limit("20/minute")
async def get_forward_test(request: Request):
    import ft_store
    from forward_test import aggregate
    return JSONResponse(content=aggregate(ft_store.fetch_all_positions()))


@app.get("/forward-test/positions")
@limiter.limit("20/minute")
async def get_forward_test_positions(
    request: Request,
    status: Optional[str] = None,
    tier: Optional[str] = None,
):
    import ft_store
    return JSONResponse(content={
        "positions": ft_store.fetch_all_positions(status=status, tier=tier),
    })
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_forward_test_stats.py -q
```

Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/forward_test.py backend/main.py backend/tests/test_forward_test_stats.py
git commit -m "feat: forward-test aggregate stats and endpoints"
```

---

## Task 13: Frontend tab

**Files:**
- Create: `frontend/src/components/ForwardTest.jsx`
- Modify: `frontend/src/api.js`, `frontend/src/App.jsx`

**Interfaces:**
- Consumes: `GET /forward-test`, `GET /forward-test/positions`.
- Produces: a `FORWARD TEST` tab.

- [ ] **Step 1: Add the fetch helpers**

In `frontend/src/api.js`, following the existing pattern:

```javascript
export async function fetchForwardTest() {
  const r = await fetch(`${BASE}/forward-test`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

export async function fetchForwardTestPositions(params = {}) {
  const q = new URLSearchParams(params).toString()
  const r = await fetch(`${BASE}/forward-test/positions${q ? `?${q}` : ''}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}
```

Match the existing constant name for the base URL — check with:

```bash
cd frontend && head -12 src/api.js
```

- [ ] **Step 2: Build the component**

Create `frontend/src/components/ForwardTest.jsx`:

```jsx
import { useState, useEffect } from 'react'
import { fetchForwardTest, fetchForwardTestPositions } from '../api.js'

function StatRow({ label, s }) {
  return (
    <tr>
      <td style={styles.td}>{label}</td>
      <td style={styles.tdNum}>{s.n}</td>
      <td style={{ ...styles.tdNum, color: s.win_rate >= 50 ? '#00ffaa' : '#ff4444' }}>
        {s.n ? `${s.win_rate}%` : '—'}
      </td>
      <td style={{ ...styles.tdNum, color: s.avg_pnl >= 0 ? '#00ffaa' : '#ff4444' }}>
        {s.n ? `${s.avg_pnl > 0 ? '+' : ''}${s.avg_pnl}%` : '—'}
      </td>
      <td style={styles.tdNum}>{s.n ? `+${s.avg_mfe}%` : '—'}</td>
      <td style={styles.tdNum}>{s.n ? `${s.avg_mae}%` : '—'}</td>
    </tr>
  )
}

export default function ForwardTest() {
  const [stats, setStats] = useState(null)
  const [positions, setPositions] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchForwardTest(), fetchForwardTestPositions()])
      .then(([s, p]) => {
        if (cancelled) return
        setStats(s)
        setPositions(p.positions || [])
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  if (loading) return <div style={styles.note}>Loading…</div>
  if (error) return <div style={styles.error}>Could not load forward test: {error}</div>
  if (!stats || stats.closed_count === 0) {
    return (
      <div style={styles.note}>
        No resolved positions yet. {stats?.open_count ?? 0} open.
        Results appear as positions hit a target, stop, or expiry.
      </div>
    )
  }

  return (
    <div style={styles.wrap}>
      <div style={styles.caveat}>
        Measured on spread mid, not fills — real trading crosses the bid/ask and
        will underperform these numbers. {stats.open_count} still open
        {stats.unpriceable_count > 0 && `, ${stats.unpriceable_count} unpriceable`}.
      </div>

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>GROUP</th><th style={styles.thNum}>N</th>
            <th style={styles.thNum}>WIN%</th><th style={styles.thNum}>AVG P&L</th>
            <th style={styles.thNum}>AVG MFE</th><th style={styles.thNum}>AVG MAE</th>
          </tr>
        </thead>
        <tbody>
          <StatRow label="ALL" s={stats.overall} />
          {Object.entries(stats.by_tier).map(([k, s]) => <StatRow key={`t${k}`} label={`Tier ${k}`} s={s} />)}
          {Object.entries(stats.by_detector).map(([k, s]) => <StatRow key={`d${k}`} label={k} s={s} />)}
        </tbody>
      </table>

      <div style={styles.caveat}>
        N is the sample size. Treat any row with a small N as directional only.
      </div>

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>SYMBOL</th><th style={styles.th}>DETECTOR</th>
            <th style={styles.th}>TIER</th><th style={styles.th}>STATUS</th>
            <th style={styles.thNum}>ENTRY</th><th style={styles.thNum}>P&L</th>
            <th style={styles.thNum}>MFE</th><th style={styles.thNum}>MAE</th>
          </tr>
        </thead>
        <tbody>
          {positions.map(p => (
            <tr key={p.id}>
              <td style={styles.td}>{p.symbol}</td>
              <td style={styles.td}>{p.detector || p.source}</td>
              <td style={styles.td}>{p.tier}</td>
              <td style={styles.td}>{p.status}</td>
              <td style={styles.tdNum}>${Number(p.entry_debit).toFixed(2)}</td>
              <td style={{ ...styles.tdNum, color: (p.realized_pnl_pct ?? 0) >= 0 ? '#00ffaa' : '#ff4444' }}>
                {p.realized_pnl_pct == null ? '—' : `${p.realized_pnl_pct > 0 ? '+' : ''}${p.realized_pnl_pct}%`}
              </td>
              <td style={styles.tdNum}>{p.mfe_pct == null ? '—' : `+${p.mfe_pct}%`}</td>
              <td style={styles.tdNum}>{p.mae_pct == null ? '—' : `${p.mae_pct}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const styles = {
  wrap: { padding: '16px' },
  table: { width: '100%', borderCollapse: 'collapse', fontFamily: 'monospace',
           fontSize: '12px', marginBottom: '20px' },
  th: { textAlign: 'left', color: '#555', borderBottom: '1px solid #1a1a2e', padding: '6px' },
  thNum: { textAlign: 'right', color: '#555', borderBottom: '1px solid #1a1a2e', padding: '6px' },
  td: { padding: '6px', color: '#ccc', borderBottom: '1px solid #111' },
  tdNum: { padding: '6px', textAlign: 'right', color: '#ccc', borderBottom: '1px solid #111' },
  note: { padding: '32px 16px', color: '#555', fontFamily: 'monospace', fontSize: '13px' },
  error: { padding: '16px', color: '#ff4444', fontFamily: 'monospace', fontSize: '13px' },
  caveat: { color: '#555', fontFamily: 'monospace', fontSize: '11px', marginBottom: '12px' },
}
```

- [ ] **Step 3: Wire the tab**

In `frontend/src/App.jsx`: add `import ForwardTest from './components/ForwardTest.jsx'` with the other imports; add a button in the tab bar copying the existing pattern exactly, with key `forward`; and add `{tab === 'forward' && <ForwardTest />}` after the last conditional render.

- [ ] **Step 4: Build**

```bash
cd frontend && npm run build
```

Expected: `✓ built in …` with no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ForwardTest.jsx frontend/src/api.js frontend/src/App.jsx
git commit -m "feat: forward test tab"
```

---

## Task 14: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the feature**

Add a `### Forward Test` section under Architecture describing: the two tables, the two entry points and where they are called from, the tier gates, and — most importantly — that results are measured on mid rather than fills, and that sample sizes are small enough that per-detector rows need their N read alongside them.

- [ ] **Step 2: Full suite**

```bash
cd backend && ./venv/bin/python -m pytest tests/ -q --ignore=tests/test_sector_analysis.py --ignore=tests/test_sector_rotation.py --ignore=tests/test_technical_analysis.py --ignore=tests/test_technical_scanner.py
```

Expected: all pass. (The four ignored files need `pandas`, absent from this venv.)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: forward test"
```

---

## Verification after deployment

The first scan tick after deploy should produce log lines `forward test: N new positions from M scanner setups` and `forward test: marked N/M open positions`. Then:

```bash
curl -s "$BACKEND_URL/forward-test" | python3 -m json.tool
```

Expect `open_count` rising and `closed_count` at 0 for the first several days — nothing resolves until a position reaches a target, a stop, or expiry, and DTE at entry is 25–45 days.
