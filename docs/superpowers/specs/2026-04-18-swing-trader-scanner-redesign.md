# Swing Trader Scanner Redesign

**Date:** 2026-04-18
**Status:** Approved

## Overview

Expand the QQQ options scanner from a mispricing detection tool into a complete swing trader workflow: directional context per stock, bearish setups, sector heat map, score transparency, position sizing, and trade journal portability. All critical gaps identified in the design review are addressed.

## Scope

**In scope:**
- Expand holdings universe from 30 → 50 QQQ stocks
- Add EOD scan at 3:45PM ET (keep existing 8AM, 9:45AM, 11AM scans)
- Enforce min 30 DTE at scanner level (filter, not warn)
- Bearish setups via 4 new put mispricing detectors + bear put spread constructor
- Per-stock technical context (MA50, MA200, trend bias)
- Sector analysis strip using 11 SPDR ETFs with momentum direction
- Score transparency: tap-to-expand breakdown + structured label
- Position sizing panel in each opportunity card (frontend only)
- Trade journal CSV export/import
- Directional toggle: Bullish / Both / Bearish

**Out of scope (deferred):**
- Alerts (email, push notifications)
- Server-side trade journal persistence
- Historical signal performance / backtesting
- Exit signal recommendations

---

## Architecture

### Data Sources

| Data | Source | Auth |
|---|---|---|
| Options chains | Schwab API (schwab-py SDK) | OAuth token |
| Price history, MAs, sector ETFs | yfinance | None |

Schwab handles options data only — yfinance handles all price/technical data to avoid Schwab rate limit pressure (120 req/min across 50 stocks).

**Known limitation:** yfinance scrapes Yahoo Finance and can break silently on Yahoo API changes. Acceptable for a personal tool; monitor scan logs after Yahoo Finance updates.

### New Backend Modules

- **`technical_analysis.py`** — fetches price history via yfinance, computes MA50/MA200, % distance from each, trend label, directional bias per stock
- **`sector_analysis.py`** — fetches 11 sector ETF price history via yfinance, computes 1w/4w/12w returns, return vs SPY, RS score, momentum direction (improving/deteriorating/stable)

### Updated Backend Modules

- **`scanner.py`** — enforce min 30 DTE, add 4 bearish put detectors, add bear put spread constructor, consume `TechnicalContext` to bias setup direction
- **`schwab_client.py`** — expand `qqq_holdings` to 50 stocks
- **`main.py`** — add 3:45PM ET EOD scan, new `/sector-analysis` endpoint, pass `TechnicalContext` into scan results

### Data Flow

```
Schwab API          yfinance
    │                   │
    ▼                   ▼
Option chains      Price history (50 stocks + 11 ETFs)
    │                   │
    └────────┬──────────┘
             ▼
        scanner.py  ←── technical_analysis.py
             │               MA50, MA200, trend bias
             │
        sector_analysis.py
             │      11 ETF relative strength, momentum
             │
        main.py
               /opportunities (updated)
               /sector-analysis (new)
```

---

## Backend: Technical Analysis Module

**File:** `backend/technical_analysis.py`

Fetches 1-year daily price history per stock via yfinance. Computes:

- `ma50` — 50-day SMA
- `ma200` — 200-day SMA
- `pct_from_ma50` — (price - ma50) / ma50 × 100
- `pct_from_ma200` — (price - ma200) / ma200 × 100
- `trend` — `"uptrend"` (price > MA50 > MA200), `"downtrend"` (price < MA50 < MA200), `"mixed"` (otherwise)
- `bias` — `"bullish"` (uptrend), `"bearish"` (downtrend), `"neutral"` (mixed)

MA20 is excluded — too short for swing trading timeframes.

**Dataclass output:**
```python
@dataclass
class TechnicalContext:
    symbol: str
    price: float
    ma50: float
    ma200: float
    pct_from_ma50: float
    pct_from_ma200: float
    trend: str   # "uptrend" | "downtrend" | "mixed"
    bias: str    # "bullish" | "bearish" | "neutral"
```

**Bias feeds scanner direction:** bullish bias → call setups surfaced; bearish bias → put setups; neutral → both detectors run, best R:R setup returned.

---

## Backend: Sector Analysis Module

**File:** `backend/sector_analysis.py`

**ETFs tracked:** XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC

Fetches 3-month daily price history for all 11 ETFs + SPY via yfinance in a single batch call.

**Metrics per ETF:**
- `return_1w`, `return_4w`, `return_12w` — absolute price return
- `return_vs_spy_1w`, `return_vs_spy_4w`, `return_vs_spy_12w` — return minus SPY return over same period
- `rs_score` — 0–100 ranking of all 11 sectors by 4-week return vs SPY (most recent week weighted 2×)
- `trend_direction` — `"improving"` / `"deteriorating"` / `"stable"` based on RS score this week vs 4 weeks ago
- `classification` — `"bullish"` (rs_score > 60), `"bearish"` (rs_score < 40), `"neutral"` (otherwise)

**All timeframes returned in a single API response** — the frontend X-week slider recalculates rankings client-side without a refetch.

**Refresh schedule:** Once daily at 9:30AM ET, cached separately from options scan. Lightweight enough to not impact Schwab rate limits.

**Endpoint:** `GET /sector-analysis`
```json
{
  "sectors": [...],
  "as_of": "2026-04-18T09:30:00",
  "spy_return_4w": 2.1
}
```

---

## Backend: Bearish Detectors

**File:** `backend/scanner.py` — 4 new detectors added alongside existing 5

| Detector | Signal | Logic |
|---|---|---|
| `put_iv_rank` | Put IV low relative to 52-week history | Same as `iv_rank` but computed on put-side IV; low rank = cheap entry for buyer |
| `skew_inversion` | Market underpricing downside | Call skew elevated, put skew flat relative to historical norms; puts cheap relative to calls |
| `put_parity` | Put-call parity violation favoring puts | Synthetic put (via parity) cheaper than actual put cost implies put underpricing |
| `downside_move` | Implied downside < historical downside vol | Put straddle implies smaller move than HV on the downside; market underestimating bearish risk |

**Bear put spread constructor:** mirrors existing bull call spread logic. Selects OTM put as long leg, further OTM put as short leg. Same DTE enforcement (min 30 DTE), same R:R threshold (≥ 2.0).

**Direction routing in scanner:**
- `bias = "bullish"` → run existing 5 call detectors only
- `bias = "bearish"` → run 4 put detectors only
- `bias = "neutral"` → run all 9, surface setup with highest score

---

## Backend: Scan Schedule Update

**File:** `backend/main.py`

Add 3:45PM ET scan to APScheduler alongside existing schedule:

```
8:00AM ET   — existing
9:45AM ET   — existing
11:00AM ET  — existing
3:45PM ET   — new (EOD swing trader scan)
```

All 4 scans call the same `scan_all()` function. EOD scan results overwrite cache same as intraday scans.

**Min DTE enforcement:** `scanner.py` filters any contract with DTE < 30 before scoring. No UI warning — silently excluded.

---

## Frontend: Sector Strip

**Location:** Pinned at the top of the Scanner tab, above the FilterBar. Always visible.

**Layout:** Horizontal scrollable row of 11 compact tiles.

**Each tile:**
```
[ XLK  ▲  +3.2% vs SPY ]
```
- Dominant text: return vs SPY (4-week default)
- Arrow: ▲ improving / ▼ deteriorating / — stable
- Color: green (bullish) / red (bearish) / grey (neutral)
- "View all →" link at end of row opens sector deep-dive panel

**Interaction:** Clicking a tile filters Scanner results to stocks in that sector. Active tile gets a highlighted border. Clicking again clears the filter.

**Sector deep-dive panel:** Slides down (not a new tab) showing full table:

| Sector | 1W vs SPY | 4W vs SPY | 12W vs SPY | RS Score | Trend |
|---|---|---|---|---|---|
| XLK | +1.2% | +3.2% | +8.1% | 82 | ▲ |

X-week selector (1W / 4W / 12W) above the table. Recalculates rankings client-side. RS score is a secondary column — actual return % is the primary data.

---

## Frontend: Score Transparency

**Score badge** — tap-to-expand inline breakdown:
```
IV Rank      +30
Skew         +25
Move         +18
─────────────────
Total         73   [tap to collapse]
```

**Structured label** — always visible directly under score badge:
```
IV Rank + Skew | Uptrend          ← bullish setup
Put Skew + Downside Move | Downtrend   ← bearish setup
```

Format: `[Fired detectors] | [Trend label]`. Short, no natural language generation.

---

## Frontend: Updated Opportunity Card

**Technical context block** — between card header and trade details:
```
NVDA  $887  ↑ Uptrend
━━━━━━━━━━━━━━━━━━━━━━━━━━━
MA50   $834  (+6.4%)
MA200  $743  (+19.4%)
```

Two MAs only (MA50 + MA200). MA20 excluded.

**Bearish card visual distinction:**
- Red left border (reinforcement)
- Setup type label (`Bear Put Spread`) prominent in subheader — primary direction signal

**Directional toggle** in FilterBar: `[ Bullish | Both | Bearish ]`, defaults to `Both`.

---

## Frontend: Position Sizing Panel

**Location:** Inside each opportunity card's ACTIONS section.

**Display:**
```
Risk per trade: [ 2.0 % ]   ← editable, persisted in localStorage
Account: $40,000

Suggested: 8 contracts  ($320 max loss)
```

**Formula:** `floor((40000 × risk%) / (debit_per_contract × 100))`

- Risk % input persisted in localStorage — set once, applies globally across all cards
- Account size ($40,000) stored as a settings constant in the frontend
- Pure client-side math, no backend involvement

---

## Frontend: Trade Journal CSV

**Location:** My Trades tab header row.

**Buttons:**
- **Export CSV** — downloads `trades_YYYY-MM-DD.csv`
- **Import CSV** — file picker, merges with existing entries, deduplicates by `id`

**CSV schema:**
```
id, symbol, setup_type, contracts, debit, date_saved, notes, status
```

`status` values: `Open` / `Closed` / `Expired` — trackable in the CSV itself without a backend.

localStorage remains the live store. CSV is backup and cross-device portability only.

---

## API Changes Summary

| Endpoint | Change |
|---|---|
| `GET /opportunities` | Returns `technical_context` per opportunity; respects min 30 DTE |
| `GET /sector-analysis` | New — returns all 11 ETFs with full metrics |
| `GET /scan` | Unchanged |
| `GET /health` | Unchanged |

---

## Open Questions / Risks

1. **yfinance reliability** — silent failures on Yahoo API changes. Mitigate by logging yfinance errors separately and falling back to showing "Technical data unavailable" on cards rather than crashing the scan.
2. **50-stock scan time** — 5 batches × 2s delay = ~10s minimum for Schwab calls. EOD at 3:45PM should complete before close. Monitor on first run.
3. **S/R levels** — explicitly excluded. MA50/MA200 distance is unambiguous; algorithmic S/R is not.
4. **Sector→stock mapping** — the 50 QQQ holdings need a hardcoded GICS sector mapping alongside `qqq_holdings.py` so the sector strip filter works. Update quarterly with the holdings list.
