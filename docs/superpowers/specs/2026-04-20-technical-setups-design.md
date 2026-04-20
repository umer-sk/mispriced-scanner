# Technical Setups Tab — Design Spec

## Goal

Add a **SETUPS** tab to the QQQ options scanner that finds the best R:R options plays across all 100 QQQ holdings using technical analysis (Qullamaggie/Minervini momentum style), independent of the mispricing scanner.

## Architecture

```
Frontend "SETUPS" tab
  → GET /technical-setups?direction=both&min_rr=2.0
      → technical_scanner.py
          → fetch 220 days price history per stock (Schwab)
          → compute 7 indicators → majority vote (5+/7 = clear signal)
          → if signal: fetch option chain → pick best structure by IV rank
          → return ranked list
  → POST /scan-setups  (triggers fresh scan, caches result)
```

**New files:**
- `backend/technical_scanner.py` — indicator logic + options structure selection
- `frontend/src/components/TechnicalSetups.jsx` — new tab UI

**Reuses:**
- `schwab_client.py` — price history + option chains
- `scanner.py` — `construct_bull_call_spread`, `construct_bear_put_spread`
- `models.py` — extend or add `TechnicalSetup` dataclass

**Cache:** Separate from mispriced scanner cache (`_technical_cache` dict in `main.py`).

---

## Technical Indicators (7 signals)

Each returns `+1` (bullish), `-1` (bearish), or `0` (neutral).  
A stock appears only when |sum| ≥ 5 (clear directional majority).

| # | Indicator | Bullish (+1) | Bearish (-1) | Data needed |
|---|-----------|-------------|-------------|-------------|
| 1 | Price vs 21 EMA | Price > 21 EMA | Price < 21 EMA | 21 days |
| 2 | 13 EMA vs 21 EMA | 13 EMA > 21 EMA | 13 EMA < 21 EMA | 21 days |
| 3 | Stage 2 trend | Price > MA50 > MA200 | Price < MA50 < MA200 | 200 days |
| 4 | RSI(14) momentum zone | 45 ≤ RSI ≤ 75 and rising | 25 ≤ RSI ≤ 55 and falling | 28 days |
| 5 | Volume accumulation | 5-day avg vol > 20-day avg vol | 5-day avg vol > 20-day avg vol on net down days | 20 days |
| 6 | Relative strength vs QQQ | Stock return > QQQ return last 10 days | Stock return < QQQ return last 10 days | 10 days + QQQ |
| 7 | Near 50-day high/low | Price within 5% of 50-day high | Price within 5% of 50-day low | 50 days |

**Minimum history required:** 220 daily candles (to compute MA200 with buffer).  
**QQQ price history** fetched once per scan for relative strength calculation.

---

## Options Structure Selection

**Earnings filter:** Skip stocks with earnings within 21 days.  
**IV rank gate:** Used to choose structure type.

| IV Rank | Bullish structure | Bearish structure |
|---------|------------------|------------------|
| < 50 | Long call (0.45Δ) | Long put (0.45Δ) |
| 50–65 | Best R:R of long call vs bull call spread | Best R:R of long put vs bear put spread |
| > 65 | Bull call spread | Bear put spread |

**Strike selection:**
- Long call/put: nearest strike to 0.45 delta, 30–60 DTE
- Bull call spread: long at 0.45Δ, short at 0.25Δ, same expiry
- Bear put spread: long at 0.45Δ, short at 0.25Δ, same expiry

**R:R calculation for outright calls/puts:**
- Price target = current price + (1.5 × ATR14 × DTE / 10)
- R:R = (price target gain on option) / premium paid
- Minimum R:R to surface: 2.0

**Skip if:** no valid expiry 30–60 DTE, R:R < 2.0, IV rank > 65 and no valid spread.

---

## Data Model

```python
@dataclass
class TechnicalSetup:
    symbol: str
    stock_price: float
    direction: str                    # "bullish" | "bearish"
    signal_count: int                 # 5, 6, or 7
    signal_details: dict[str, bool]   # {stage2: True, ema_alignment: False, ...}
    structure: str                    # "long_call" | "long_put" | "bull_call_spread" | "bear_put_spread"
    strike: float
    expiry: date
    dte: int
    delta: float
    iv_rank: float
    premium: float                    # cost per contract / 100
    price_target: float
    rr_ratio: float
    max_loss: float                   # dollars per contract
    breakeven_move_pct: float
    probability_of_profit: float
    order_string: str
    earnings_within_dte: bool         # always False (filtered out), kept for transparency
```

---

## API Endpoints

```
GET /technical-setups
  Params: direction (both|bullish|bearish), min_rr (float, default 2.0)
  Returns: { setups: TechnicalSetup[], scan_timestamp, symbols_scanned }

GET /scan-setups
  Triggers fresh technical scan in background, returns immediately
  Returns: { status: "scanning", message: "..." }
```

---

## Frontend — SETUPS Tab

**Tab bar:** SCANNER | SETUPS | MY TRADES

**Header:**
```
TECHNICAL SETUPS    {n} setups    ○/● MARKET STATUS    [▶ SCAN SETUPS]
```
- SCAN SETUPS button triggers `GET /scan-setups`, then polls `/technical-setups` after 45s
- Shows "⟳ SCANNING…" while in progress

**Filters:**
```
DIRECTION  [Both] [▲ Bullish] [▼ Bearish]    MIN R:R [slider 1.0–5.0]    SORT [R:R] [PoP]
```

**Result card:**
```
NVDA  $875.20                              6/7 BULLISH    LONG CALL
May 16  $900C  45 DTE  Δ0.44  IV Rank 32

R:R 3.2:1   Max loss $420   Breakeven +4.8%   PoP 44%
Price target $940 (+7.4%)

Signals: Stage 2 ✓  EMA align ✓  Volume ✓  RS vs QQQ ✓  Breakout ✓  RSI ✓  · 13/21 ✗
```

- Green left border for bullish, red for bearish (matching mispriced scanner style)
- Sorted by R:R descending by default; toggle to sort by PoP
- No save-to-journal (discovery tab only)
- Copy order string button

---

## Scan Performance

- 100 symbols × 1 price history request (220 candles) + 1 option chain request = 200 Schwab API calls
- Batched in groups of 10 with 2s delay = ~40s total scan time
- QQQ price history fetched once, reused for all RS calculations
- Results cached in `_technical_cache`; stale after 90 minutes

---

## Out of Scope

- Save technical setups to journal (user navigates to scanner tab for that)
- Intraday signals (daily candles only)
- Backtesting
- Alerts or notifications
