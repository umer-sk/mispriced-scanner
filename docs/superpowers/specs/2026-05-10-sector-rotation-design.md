# Sector Rotation Signals Design

**Goal:** Show a single conclusion per sector — where smart money is rotating — by combining three signals: RS momentum, volume accumulation/distribution, and options flow.

**Architecture:** Extend the existing daily sector analysis refresh to compute three signals per sector ETF and combine them into a single rotation verdict. Add one column to the existing SectorPanel table.

**Tech Stack:** yfinance (existing), Schwab API (existing), React (existing SectorPanel component)

---

## Signals

Three signals each vote +1 (bullish), -1 (bearish), or 0 (neutral). The sum determines the verdict.

### RS Momentum
Compute RS score at two timepoints within the existing yfinance download: full window and the window ending 5 days ago. Delta = current RS score minus RS score 5 days ago.
- Delta > +5 → +1 (accelerating)
- Delta < -5 → -1 (decelerating)
- Otherwise → 0

### Volume Signal
Compare 5-day average volume to 20-day average volume for the sector ETF. Use 5-day price return for direction.
- Volume ratio > 1.3 AND price return > 0 → +1 (accumulation)
- Volume ratio > 1.3 AND price return < 0 → -1 (distribution)
- Otherwise → 0

### Options Flow (Put/Call Ratio)
Fetch Schwab option chain for the sector ETF. Sum total call OI and put OI across all strikes in the 30–60 DTE range. Compute put/call ratio = total put OI / total call OI.
- Ratio < 0.8 → +1 (call-biased, bullish positioning)
- Ratio > 1.2 → -1 (put-biased, bearish/hedging)
- Otherwise → 0

---

## Verdict Scoring

| Signal Sum | Verdict |
|---|---|
| +3 or +2 | `↑↑` |
| +1 | `↑` |
| 0 | `→` |
| -1 | `↓` |
| -2 or -3 | `↓↓` |

---

## Backend Changes

### `models.py`
Add one field to `SectorData` dataclass:
```python
rotation: str = "→"  # ↑↑ / ↑ / → / ↓ / ↓↓
```

### `sector_analysis.py`

**Extend `get_sector_analysis()`:**
- After computing existing RS scores, also compute RS score for each sector using returns ending 5 days ago (slice the DataFrame). Compute delta → `rs_vote` per symbol.
- Compute 5-day and 20-day average volume from the existing yfinance download → `volume_vote` per symbol.
- Call `_get_sector_flow(symbols)` (internal helper below) to get `flow_vote` per symbol.
- Combine all three votes, compute arrow string, populate `rotation` field on each `SectorData`.

**New internal `_get_sector_flow(symbols: list[str]) -> dict[str, int]`:**
- For each symbol, calls `fetch_option_chain(symbol)` (existing Schwab client).
- Sums call OI and put OI across contracts with 30–60 DTE.
- Returns dict mapping symbol → flow_vote (+1 / -1 / 0).
- Wrapped in try/except: on any failure for a symbol, that symbol gets 0. If Schwab is entirely unavailable, returns empty dict (graceful degradation).

### `main.py`

**Extend `refresh_sector_analysis()`:**
```python
sectors = get_sector_analysis()   # handles all three signals internally
_cache["sector_analysis"] = sectors
```

No changes to the call site. All signal computation stays inside `sector_analysis.py`.

No new scheduler jobs. The existing 9:30 AM ET job calls `refresh_sector_analysis()`.

---

## Frontend Changes

### `SectorPanel.jsx`

Add a `ROTATION` column as the rightmost column in the sector table. Renders `sector.rotation` in monospace font. No color coding.

Example row:
```
XLK  Technology  +3.2%  72  ↑   ↑↑
```

`SectorStrip.jsx` is unchanged — tiles stay compact.

---

## Graceful Degradation

If Schwab is unavailable (token expired, API error), `get_sector_flow()` returns an empty dict. The rotation verdict is computed from 2 signals (RS momentum + volume) instead of 3. The verdict is still displayed — just with less conviction.

No error is surfaced to the user. The rotation column still populates.

---

## Scheduling & Refresh

- Runs once daily at 9:30 AM ET (existing `refresh_sector_analysis` job).
- On server startup, `asyncio.create_task(refresh_sector_analysis())` already fires immediately.
- No intraday refresh. Data is a daily snapshot.
