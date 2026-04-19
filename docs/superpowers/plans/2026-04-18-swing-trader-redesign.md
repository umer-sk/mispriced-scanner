# Swing Trader Scanner Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the QQQ options scanner into a complete swing trader workflow with directional context, bearish setups, sector heat map, score transparency, position sizing, and trade journal CSV portability.

**Architecture:** Two new backend modules (`technical_analysis.py`, `sector_analysis.py`) use yfinance for price/sector data, keeping Schwab exclusively for options chains. The scanner gains 4 bearish put detectors and a bear put spread constructor. The frontend gains a persistent sector strip, per-card technical context, tap-to-expand score breakdowns, a directional toggle, position sizing, and CSV export/import.

**Tech Stack:** Python/FastAPI (backend), yfinance (price data), React/Vite (frontend), localStorage + CSV (trade journal)

---

## File Map

**Create:**
- `backend/technical_analysis.py` — MA50/MA200, trend label, bias per stock
- `backend/sector_analysis.py` — 11 sector ETF RS scores, returns, momentum
- `backend/tests/__init__.py` — empty
- `backend/tests/test_technical_analysis.py`
- `backend/tests/test_sector_analysis.py`
- `backend/tests/test_bearish_detectors.py`
- `frontend/src/components/SectorStrip.jsx` — horizontal scrollable sector tiles
- `frontend/src/components/SectorPanel.jsx` — deep-dive slide-down table

**Modify:**
- `backend/requirements.txt` — add yfinance
- `backend/qqq_holdings.py` — expand to 50 stocks, add sector map
- `backend/models.py` — add `TechnicalContext`, `SectorData`; update `TradeSetup`
- `backend/scanner.py` — min 30 DTE, 4 bearish detectors, bear put spread, direction routing, score breakdown
- `backend/main.py` — EOD scan (3:45PM ET), `/sector-analysis` endpoint, wire TechnicalContext
- `frontend/src/api.js` — add `fetchSectorAnalysis()`
- `frontend/src/components/FilterBar.jsx` — directional toggle, 4 new detector labels
- `frontend/src/components/OpportunityCard.jsx` — technical context block, score breakdown, bearish styling, structure label fix
- `frontend/src/components/TradeJournal.jsx` — CSV export/import buttons

---

## Task 1: Add yfinance and expand holdings to 50 stocks

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/qqq_holdings.py`

- [ ] **Step 1: Add yfinance to requirements**

```
# backend/requirements.txt — add this line:
yfinance>=0.2.40
```

- [ ] **Step 2: Replace qqq_holdings.py with 50 stocks + sector map**

```python
# Top 50 QQQ holdings by weight — update quarterly
QQQ_TOP50 = [
    "NVDA", "AAPL", "MSFT", "AMZN", "META",
    "AVGO", "TSLA", "GOOGL", "GOOG", "COST",
    "NFLX", "AMD", "ADBE", "QCOM", "INTC",
    "AMGN", "CSCO", "TXN", "INTU", "ISRG",
    "MU",   "AMAT", "LRCX", "MRVL", "KLAC",
    "PLTR", "CRWD", "PANW", "FTNT", "CDNS",
    "PYPL", "MELI", "DXCM", "SNPS", "ABNB",
    "WDAY", "TEAM", "DDOG", "ZS",   "NET",
    "COIN", "DASH", "TTWO", "MNST", "VRSK",
    "ODFL", "KDP",  "EXC",  "AEP",  "CSGP",
]

# Map each holding to its SPDR sector ETF — update quarterly
SECTOR_MAP: dict[str, str] = {
    # XLK — Technology
    "NVDA": "XLK", "AAPL": "XLK", "MSFT": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "ADBE": "XLK", "QCOM": "XLK", "INTC": "XLK", "CSCO": "XLK", "TXN": "XLK",
    "INTU": "XLK", "MU": "XLK",   "AMAT": "XLK", "LRCX": "XLK", "MRVL": "XLK",
    "KLAC": "XLK", "CDNS": "XLK", "SNPS": "XLK", "PLTR": "XLK", "CRWD": "XLK",
    "PANW": "XLK", "FTNT": "XLK", "ZS": "XLK",   "NET": "XLK",  "DDOG": "XLK",
    "WDAY": "XLK", "TEAM": "XLK",
    # XLC — Communication Services
    "META": "XLC", "GOOGL": "XLC", "GOOG": "XLC", "NFLX": "XLC",
    "TTWO": "XLC", "DASH": "XLC",
    # XLY — Consumer Discretionary
    "AMZN": "XLY", "TSLA": "XLY", "COST": "XLY", "ABNB": "XLY", "MELI": "XLY",
    # XLV — Health Care
    "AMGN": "XLV", "ISRG": "XLV", "DXCM": "XLV",
    # XLF — Financials
    "PYPL": "XLF", "COIN": "XLF", "VRSK": "XLF",
    # XLI — Industrials
    "ODFL": "XLI",
    # XLP — Consumer Staples
    "MNST": "XLP", "KDP": "XLP",
    # XLU — Utilities
    "EXC": "XLU", "AEP": "XLU",
    # XLRE — Real Estate
    "CSGP": "XLRE",
}
```

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt backend/qqq_holdings.py
git commit -m "feat: expand holdings to 50 QQQ stocks with sector map"
```

---

## Task 2: Add data models

**Files:**
- Modify: `backend/models.py`

- [ ] **Step 1: Add TechnicalContext and SectorData dataclasses, update TradeSetup**

Add after the existing imports at the top of `models.py`:

```python
@dataclass
class TechnicalContext:
    symbol: str
    price: float
    ma50: float
    ma200: float
    pct_from_ma50: float    # (price - ma50) / ma50 * 100
    pct_from_ma200: float   # (price - ma200) / ma200 * 100
    trend: str              # "uptrend" | "downtrend" | "mixed"
    bias: str               # "bullish" | "bearish" | "neutral"


@dataclass
class SectorData:
    etf: str                    # e.g. "XLK"
    name: str                   # e.g. "Technology"
    return_1w: float
    return_4w: float
    return_12w: float
    return_vs_spy_1w: float
    return_vs_spy_4w: float
    return_vs_spy_12w: float
    rs_score: float             # 0–100 relative to other sectors
    trend_direction: str        # "improving" | "deteriorating" | "stable"
    classification: str         # "bullish" | "bearish" | "neutral"
```

Add two fields to `TradeSetup` (after the `timestamp` field):

```python
    # Technical context (populated by scanner when yfinance data is available)
    technical_context: Optional["TechnicalContext"] = None
    # Score breakdown for UI transparency
    score_breakdown: list[dict] = field(default_factory=list)
```

Also update the `structure` field comment to include `"bear_put_spread"`:

```python
    structure: str              # "bull_call_spread" | "bear_put_spread" | "calendar" | "long_call"
```

- [ ] **Step 2: Verify imports are correct** — `field` is already imported via `from dataclasses import dataclass, field` and `Optional` via `from typing import Optional`. No new imports needed.

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: add TechnicalContext, SectorData models; update TradeSetup"
```

---

## Task 3: Technical analysis module

**Files:**
- Create: `backend/technical_analysis.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_technical_analysis.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_technical_analysis.py
import pandas as pd
import pytest
from unittest.mock import patch
from technical_analysis import get_technical_context, _compute_bias


def _make_prices(close_prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": close_prices})


def test_bias_uptrend():
    assert _compute_bias(price=150, ma50=140, ma200=120) == "bullish"


def test_bias_downtrend():
    assert _compute_bias(price=110, ma50=120, ma200=140) == "bearish"


def test_bias_mixed():
    assert _compute_bias(price=130, ma50=135, ma200=120) == "neutral"


def test_pct_from_ma50():
    from technical_analysis import _pct_from
    assert abs(_pct_from(110, 100) - 10.0) < 0.01


@patch("technical_analysis.yf.download")
def test_get_technical_context_uptrend(mock_dl):
    # 250 prices trending up: price > ma50 > ma200
    prices = list(range(100, 350))  # 250 values, last=349
    mock_dl.return_value = _make_prices(prices)
    ctx = get_technical_context("NVDA")
    assert ctx.symbol == "NVDA"
    assert ctx.trend == "uptrend"
    assert ctx.bias == "bullish"
    assert ctx.price == 349


@patch("technical_analysis.yf.download")
def test_get_technical_context_returns_none_on_empty(mock_dl):
    mock_dl.return_value = pd.DataFrame({"Close": []})
    ctx = get_technical_context("NVDA")
    assert ctx is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/test_technical_analysis.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'technical_analysis'`

- [ ] **Step 3: Create technical_analysis.py**

```python
# backend/technical_analysis.py
import logging
from typing import Optional

import yfinance as yf

from models import TechnicalContext

logger = logging.getLogger(__name__)


def _pct_from(price: float, ma: float) -> float:
    if ma == 0:
        return 0.0
    return round((price - ma) / ma * 100, 2)


def _compute_bias(price: float, ma50: float, ma200: float) -> str:
    if price > ma50 > ma200:
        return "bullish"
    if price < ma50 < ma200:
        return "bearish"
    return "neutral"


def get_technical_context(symbol: str) -> Optional[TechnicalContext]:
    """Fetch 1-year daily price history and compute MA50/MA200 context."""
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            logger.warning("Insufficient price history for %s", symbol)
            return None

        closes = df["Close"].dropna()
        price = float(closes.iloc[-1])
        ma50 = float(closes.rolling(50).mean().iloc[-1])
        ma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else ma50

        bias = _compute_bias(price, ma50, ma200)
        if price > ma50 > ma200:
            trend = "uptrend"
        elif price < ma50 < ma200:
            trend = "downtrend"
        else:
            trend = "mixed"

        return TechnicalContext(
            symbol=symbol,
            price=round(price, 2),
            ma50=round(ma50, 2),
            ma200=round(ma200, 2),
            pct_from_ma50=_pct_from(price, ma50),
            pct_from_ma200=_pct_from(price, ma200),
            trend=trend,
            bias=bias,
        )
    except Exception as e:
        logger.warning("Technical analysis failed for %s: %s", symbol, e)
        return None


def get_technical_contexts(symbols: list[str]) -> dict[str, Optional[TechnicalContext]]:
    """Fetch technical context for multiple symbols. Returns dict keyed by symbol."""
    results = {}
    for symbol in symbols:
        results[symbol] = get_technical_context(symbol)
    return results
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
pip install yfinance
python -m pytest tests/test_technical_analysis.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/technical_analysis.py backend/tests/__init__.py backend/tests/test_technical_analysis.py
git commit -m "feat: add technical analysis module (MA50/MA200, trend bias)"
```

---

## Task 4: Sector analysis module

**Files:**
- Create: `backend/sector_analysis.py`
- Create: `backend/tests/test_sector_analysis.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sector_analysis.py
import pytest
from sector_analysis import _rs_score, _classify, _trend_direction, _compute_return


def test_rs_score_highest():
    # Symbol with highest return_vs_spy_4w gets score 100
    returns = {"XLK": 5.0, "XLF": 2.0, "XLE": -1.0}
    scores = _rs_score(returns)
    assert scores["XLK"] == 100.0


def test_rs_score_lowest():
    returns = {"XLK": 5.0, "XLF": 2.0, "XLE": -1.0}
    scores = _rs_score(returns)
    assert scores["XLE"] == 0.0


def test_rs_score_single_sector():
    returns = {"XLK": 3.0}
    scores = _rs_score(returns)
    assert scores["XLK"] == 50.0  # Only one sector — neutral


def test_classify_bullish():
    assert _classify(70) == "bullish"


def test_classify_bearish():
    assert _classify(30) == "bearish"


def test_classify_neutral():
    assert _classify(50) == "neutral"


def test_trend_direction_improving():
    assert _trend_direction(current=65, prior=45) == "improving"


def test_trend_direction_deteriorating():
    assert _trend_direction(current=35, prior=55) == "deteriorating"


def test_trend_direction_stable():
    assert _trend_direction(current=50, prior=48) == "stable"


def test_compute_return():
    prices = [100.0, 105.0]
    assert abs(_compute_return(prices, 0, 1) - 5.0) < 0.01
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/test_sector_analysis.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'sector_analysis'`

- [ ] **Step 3: Create sector_analysis.py**

```python
# backend/sector_analysis.py
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

from models import SectorData

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLC": "Communication",
    "XLY": "Consumer Discret.",
    "XLP": "Consumer Staples",
    "XLV": "Health Care",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}


def _compute_return(prices: list[float], start_idx: int, end_idx: int) -> float:
    if prices[start_idx] == 0:
        return 0.0
    return round((prices[end_idx] - prices[start_idx]) / prices[start_idx] * 100, 2)


def _rs_score(return_vs_spy: dict[str, float]) -> dict[str, float]:
    """Rank sectors 0–100 by return vs SPY. Single sector → 50."""
    if len(return_vs_spy) <= 1:
        return {k: 50.0 for k in return_vs_spy}
    values = list(return_vs_spy.values())
    min_v, max_v = min(values), max(values)
    if max_v == min_v:
        return {k: 50.0 for k in return_vs_spy}
    return {
        k: round((v - min_v) / (max_v - min_v) * 100, 1)
        for k, v in return_vs_spy.items()
    }


def _classify(rs_score: float) -> str:
    if rs_score > 60:
        return "bullish"
    if rs_score < 40:
        return "bearish"
    return "neutral"


def _trend_direction(current: float, prior: float) -> str:
    delta = current - prior
    if delta > 10:
        return "improving"
    if delta < -10:
        return "deteriorating"
    return "stable"


def get_sector_analysis() -> list[SectorData]:
    """Fetch 3-month daily history for all 11 sector ETFs + SPY."""
    tickers = list(SECTOR_ETFS.keys()) + ["SPY"]
    try:
        df = yf.download(tickers, period="3mo", interval="1d", progress=False, auto_adjust=True)["Close"]
        if df.empty:
            logger.warning("Sector analysis: empty data from yfinance")
            return []
    except Exception as e:
        logger.error("Sector analysis fetch failed: %s", e)
        return []

    spy = df["SPY"].dropna().tolist()
    if len(spy) < 5:
        return []

    # Index positions for 1w (~5 days), 4w (~20 days), 12w (~60 days)
    idx = {"now": -1, "1w": -5, "4w": -20, "12w": -60, "prior_4w": -40}

    def safe_idx(prices, i):
        if abs(i) >= len(prices):
            return prices[0]
        return prices[i]

    spy_ret = {
        "1w": _compute_return(spy, idx["1w"], idx["now"]),
        "4w": _compute_return(spy, idx["4w"], idx["now"]),
        "12w": _compute_return(spy, idx["12w"], idx["now"]),
    }

    # Compute return_vs_spy_4w for RS scoring
    vs_spy_4w: dict[str, float] = {}
    sector_raw: dict[str, dict] = {}

    for etf in SECTOR_ETFS:
        if etf not in df.columns:
            continue
        prices = df[etf].dropna().tolist()
        if len(prices) < 5:
            continue
        r1w = _compute_return(prices, safe_idx(prices, idx["1w"]), prices[-1]) - spy_ret["1w"]
        r4w = _compute_return(prices, safe_idx(prices, idx["4w"]), prices[-1]) - spy_ret["4w"]
        r12w = _compute_return(prices, safe_idx(prices, idx["12w"]), prices[-1]) - spy_ret["12w"]
        vs_spy_4w[etf] = r4w
        sector_raw[etf] = {
            "return_1w": _compute_return(prices, safe_idx(prices, idx["1w"]), prices[-1]),
            "return_4w": _compute_return(prices, safe_idx(prices, idx["4w"]), prices[-1]),
            "return_12w": _compute_return(prices, safe_idx(prices, idx["12w"]), prices[-1]),
            "return_vs_spy_1w": round(r1w, 2),
            "return_vs_spy_4w": round(r4w, 2),
            "return_vs_spy_12w": round(r12w, 2),
            # Prior RS for trend direction (4w ago vs 8w ago)
            "prior_vs_spy_4w": _compute_return(prices, safe_idx(prices, idx["12w"]), safe_idx(prices, idx["4w"])) - \
                               _compute_return(spy, safe_idx(spy, idx["12w"]), safe_idx(spy, idx["4w"])),
        }

    scores = _rs_score(vs_spy_4w)

    # Prior RS scores for trend_direction
    prior_vs_spy = {etf: sector_raw[etf]["prior_vs_spy_4w"] for etf in sector_raw}
    prior_scores = _rs_score(prior_vs_spy)

    results = []
    for etf, name in SECTOR_ETFS.items():
        if etf not in sector_raw:
            continue
        raw = sector_raw[etf]
        score = scores.get(etf, 50.0)
        prior = prior_scores.get(etf, 50.0)
        results.append(SectorData(
            etf=etf,
            name=name,
            return_1w=raw["return_1w"],
            return_4w=raw["return_4w"],
            return_12w=raw["return_12w"],
            return_vs_spy_1w=raw["return_vs_spy_1w"],
            return_vs_spy_4w=raw["return_vs_spy_4w"],
            return_vs_spy_12w=raw["return_vs_spy_12w"],
            rs_score=score,
            trend_direction=_trend_direction(score, prior),
            classification=_classify(score),
        ))

    results.sort(key=lambda s: s.rs_score, reverse=True)
    return results
```

- [ ] **Step 4: Fix the `safe_idx` usage** — the `safe_idx` helper above is called incorrectly. Replace the `safe_idx` calls in `get_sector_analysis` with direct index bounds:

```python
        def _safe_price(prices, idx):
            i = idx if idx >= 0 else max(idx, -len(prices))
            return prices[i]

        r1w = _compute_return([_safe_price(prices, -5), prices[-1]], 0, 1) - spy_ret["1w"]
        r4w = _compute_return([_safe_price(prices, -20), prices[-1]], 0, 1) - spy_ret["4w"]
        r12w = _compute_return([_safe_price(prices, -60), prices[-1]], 0, 1) - spy_ret["12w"]
```

Actually, replace the entire computation block in `get_sector_analysis` after `spy_ret` with:

```python
    def _safe(lst, i):
        return lst[max(i, -len(lst))]

    for etf in SECTOR_ETFS:
        if etf not in df.columns:
            continue
        prices = df[etf].dropna().tolist()
        if len(prices) < 5:
            continue
        abs_r1w  = _compute_return([_safe(prices, -5),  prices[-1]], 0, 1)
        abs_r4w  = _compute_return([_safe(prices, -20), prices[-1]], 0, 1)
        abs_r12w = _compute_return([_safe(prices, -60), prices[-1]], 0, 1)
        # Prior period: 8w ago → 4w ago (for trend direction)
        prior_r4w = _compute_return([_safe(prices, -60), _safe(prices, -20)], 0, 1)
        spy_prior = _compute_return([_safe(spy, -60), _safe(spy, -20)], 0, 1)

        vs_spy_4w[etf] = round(abs_r4w - spy_ret["4w"], 2)
        sector_raw[etf] = {
            "return_1w":          abs_r1w,
            "return_4w":          abs_r4w,
            "return_12w":         abs_r12w,
            "return_vs_spy_1w":   round(abs_r1w  - spy_ret["1w"],  2),
            "return_vs_spy_4w":   round(abs_r4w  - spy_ret["4w"],  2),
            "return_vs_spy_12w":  round(abs_r12w - spy_ret["12w"], 2),
            "prior_vs_spy_4w":    round(prior_r4w - spy_prior, 2),
        }
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/test_sector_analysis.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/sector_analysis.py backend/tests/test_sector_analysis.py
git commit -m "feat: add sector analysis module (11 ETF RS scores, momentum)"
```

---

## Task 5: Add 4 bearish put detectors

**Files:**
- Modify: `backend/scanner.py`
- Create: `backend/tests/test_bearish_detectors.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_bearish_detectors.py
import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from models import OptionChainData, OptionContract, MispricingSignal


def _make_chain(symbol="AAPL", stock_price=150.0, iv30=0.25, hv30=0.30,
                iv_rank=20.0, calls=None, puts=None):
    return OptionChainData(
        symbol=symbol, stock_price=stock_price,
        iv30=iv30, hv30=hv30, iv_rank=iv_rank,
        iv_percentile=20.0, timestamp=__import__("datetime").datetime.utcnow(),
        calls=calls or [], puts=puts or [],
    )


def _make_contract(strike, dte, iv, delta, bid, ask, oi=500, volume=200, is_put=False):
    expiry = date.today() + timedelta(days=dte)
    return OptionContract(
        strike=strike, expiry=expiry, dte=dte,
        bid=bid, ask=ask, mid=(bid + ask) / 2, last=(bid + ask) / 2,
        volume=volume, open_interest=oi, iv=iv,
        delta=delta if not is_put else -delta,
        gamma=0.01, theta=-0.05, vega=0.10,
        theoretical_value=(bid + ask) / 2,
        in_the_money=False,
    )


# ─── put_iv_rank ──────────────────────────────────────────────────────────────

def test_put_iv_rank_fires_when_iv_low_and_bearish_flow():
    from scanner import detect_put_iv_rank_cheap
    puts = [_make_contract(148, 35, 0.20, 0.45, 1.0, 1.2, is_put=True) for _ in range(5)]
    calls = [_make_contract(152, 35, 0.20, 0.45, 0.5, 0.7) for _ in range(2)]
    # Put volume >> call volume → bearish flow
    for p in puts:
        p.volume = 300
    for c in calls:
        c.volume = 100
    chain = _make_chain(iv_rank=18, iv30=0.20, hv30=0.30, puts=puts, calls=calls)
    signal = detect_put_iv_rank_cheap(chain)
    assert signal is not None
    assert signal.detector == "put_iv_rank"


def test_put_iv_rank_no_fire_when_iv_high():
    from scanner import detect_put_iv_rank_cheap
    chain = _make_chain(iv_rank=50)
    signal = detect_put_iv_rank_cheap(chain)
    assert signal is None


# ─── skew_inversion ───────────────────────────────────────────────────────────

def test_skew_inversion_fires_when_put_skew_flat():
    from scanner import detect_skew_inversion
    expiry = date.today() + timedelta(days=35)
    # put_10d_iv ≈ call_10d_iv (flat skew = underpriced downside)
    puts  = [OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.22, -0.10,
                             0.01, -0.05, 0.10, 1.0, False)]
    calls = [OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.21, 0.10,
                             0.01, -0.05, 0.10, 1.0, False)]
    puts[0].strike = 135; calls[0].strike = 165
    chain = _make_chain(stock_price=150, puts=puts, calls=calls)
    signal = detect_skew_inversion(chain)
    assert signal is not None
    assert signal.detector == "skew_inversion"


def test_skew_inversion_no_fire_when_normal_skew():
    from scanner import detect_skew_inversion
    expiry = date.today() + timedelta(days=35)
    puts  = [OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.35, -0.10,
                             0.01, -0.05, 0.10, 1.0, False)]
    calls = [OptionContract("", expiry, 35, 0.8, 1.2, 1.0, 1.0, 200, 500, 0.20, 0.10,
                             0.01, -0.05, 0.10, 1.0, False)]
    puts[0].strike = 135; calls[0].strike = 165
    chain = _make_chain(stock_price=150, puts=puts, calls=calls)
    signal = detect_skew_inversion(chain)
    assert signal is None


# ─── put_parity ───────────────────────────────────────────────────────────────

def test_put_parity_fires_when_put_underpriced():
    from scanner import detect_put_parity_violation
    expiry = date.today() + timedelta(days=35)
    S, K, r, T = 150.0, 150.0, 0.0525, 35 / 365
    call_mid = 4.0
    theoretical_put = call_mid - S + K * math.exp(-r * T)  # ≈ 3.72
    # Actual put trades below theoretical
    put = OptionContract(K, expiry, 35, theoretical_put * 0.85, theoretical_put * 0.95,
                         theoretical_put * 0.90, theoretical_put * 0.90,
                         200, 500, 0.25, -0.48, 0.01, -0.05, 0.10, theoretical_put * 0.90, False)
    call = OptionContract(K, expiry, 35, call_mid - 0.1, call_mid + 0.1, call_mid, call_mid,
                          200, 500, 0.25, 0.52, 0.01, -0.05, 0.10, call_mid, False)
    chain = _make_chain(stock_price=S, puts=[put], calls=[call])
    signal = detect_put_parity_violation(chain)
    assert signal is not None
    assert signal.detector == "put_parity"


# ─── downside_move ────────────────────────────────────────────────────────────

def test_downside_move_fires_when_implied_less_than_historical():
    from scanner import detect_downside_move_underpricing
    expiry = date.today() + timedelta(days=35)
    S = 150.0
    # ATM put with cheap premium (implied move < HV)
    put = OptionContract(S, expiry, 35, 2.0, 2.5, 2.25, 2.25,
                         300, 600, 0.25, -0.50, 0.01, -0.05, 0.10, 2.25, False)
    chain = _make_chain(stock_price=S, iv30=0.25, hv30=0.50, puts=[put])
    signal = detect_downside_move_underpricing(chain)
    assert signal is not None
    assert signal.detector == "downside_move"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/test_bearish_detectors.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'detect_put_iv_rank_cheap' from 'scanner'`

- [ ] **Step 3: Add the 4 bearish detectors to scanner.py**

Add after `detect_move_underpricing` (around line 405), before the P&L section:

```python
# ---------------------------------------------------------------------------
# Bearish Detector 1: Put IV Rank Cheap
# ---------------------------------------------------------------------------

def detect_put_iv_rank_cheap(chain: OptionChainData) -> Optional[MispricingSignal]:
    """Fire when IV rank < 25%, iv30 < hv30, and put flow dominates (bearish)."""
    if chain.stock_price == 0 or chain.iv30 == 0:
        return None
    if chain.iv_rank >= 25:
        return None
    if chain.iv30 >= chain.hv30:
        return None

    put_vol = sum(c.volume for c in chain.puts)
    call_vol = sum(c.volume for c in chain.calls)
    if call_vol == 0 or put_vol / max(call_vol, 1) <= 1.3:
        return None

    ivr = chain.iv_rank
    confidence = 0.95 if ivr <= 10 else (0.85 if ivr <= 18 else 0.70)

    return MispricingSignal(
        symbol=chain.symbol,
        detector="put_iv_rank",
        description=(
            f"IV rank at {ivr:.0f}% — puts pricing {chain.iv30:.1%} vol vs "
            f"{chain.hv30:.1%} historical. Bearish flow dominant; puts cheap."
        ),
        confidence=confidence,
        raw_data={
            "iv_rank": ivr, "iv30": chain.iv30, "hv30": chain.hv30,
            "put_call_volume_ratio": round(put_vol / max(call_vol, 1), 2),
        },
    )


# ---------------------------------------------------------------------------
# Bearish Detector 2: Skew Inversion (Puts Cheap vs Historical Skew)
# ---------------------------------------------------------------------------

def detect_skew_inversion(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Fire when put/call skew is flat (< 0.03) — puts should be more expensive
    than calls in normal equity markets. Flat skew = cheap downside protection.
    """
    if chain.stock_price == 0:
        return None

    def find_delta_contract(contracts, target_delta):
        candidates = [c for c in contracts if 21 <= c.dte <= 60 and c.open_interest > 200 and c.iv > 0]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))

    put_10d = find_delta_contract(chain.puts, 0.10)
    call_10d = find_delta_contract(chain.calls, 0.10)
    if put_10d is None or call_10d is None:
        return None

    raw_skew = put_10d.iv - call_10d.iv
    if raw_skew >= 0.03:
        return None

    return MispricingSignal(
        symbol=chain.symbol,
        detector="skew_inversion",
        description=(
            f"Put/call skew at {raw_skew:.1%} — nearly flat vs normal 5–15%. "
            f"Market underpricing downside protection. Puts cheap relative to calls."
        ),
        confidence=0.75,
        raw_data={
            "raw_skew": round(raw_skew, 4),
            "put_10d_iv": round(put_10d.iv, 4),
            "call_10d_iv": round(call_10d.iv, 4),
        },
    )


# ---------------------------------------------------------------------------
# Bearish Detector 3: Put-Call Parity Violation (Put Underpriced)
# ---------------------------------------------------------------------------

def detect_put_parity_violation(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    P = C - S + K * e^(-rT). When actual put < theoretical put, put is underpriced.
    """
    if chain.stock_price == 0:
        return None

    S = chain.stock_price
    r = RISK_FREE_RATE
    best_violation = 0.0
    best_signal = None

    call_map = {(c.strike, c.expiry): c for c in chain.calls if 30 <= c.dte <= 60}
    put_map  = {(p.strike, p.expiry): p for p in chain.puts  if 30 <= p.dte <= 60}
    common_keys = set(call_map) & set(put_map)

    for key in common_keys:
        call = call_map[key]
        put  = put_map[key]
        K = call.strike
        T = call.dte / 365.0

        if call.open_interest < 100 or put.open_interest < 100:
            continue
        if _spread_pct(call) > 0.05 or _spread_pct(put) > 0.05:
            continue

        theoretical_put = call.mid - S + K * math.exp(-r * T)
        if theoretical_put <= 0:
            continue

        violation_pct = (theoretical_put - put.mid) / theoretical_put
        if violation_pct > 0.02 and violation_pct > best_violation:
            best_violation = violation_pct
            confidence = min(0.98, 0.70 + violation_pct * 5)
            best_signal = MispricingSignal(
                symbol=chain.symbol,
                detector="put_parity",
                description=(
                    f"Put-call parity violation: ${K:.0f} put trading "
                    f"{violation_pct:.1%} below theoretical value (DTE {call.dte}). "
                    f"Put underpriced mathematically."
                ),
                confidence=confidence,
                raw_data={
                    "strike": K, "expiry": str(call.expiry), "dte": call.dte,
                    "put_mid": put.mid, "theoretical_put": round(theoretical_put, 2),
                    "violation_pct": round(violation_pct, 4),
                    "call_mid": call.mid, "stock_price": S,
                },
            )

    return best_signal


# ---------------------------------------------------------------------------
# Bearish Detector 4: Downside Move Underpricing
# ---------------------------------------------------------------------------

def detect_downside_move_underpricing(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Compare ATM put cost (implied downside) vs expected historical downside.
    """
    if chain.stock_price == 0 or chain.hv30 == 0:
        return None

    S = chain.stock_price
    atm_put = _atm_contract(chain.puts, S, dte_min=30, dte_max=60)
    if atm_put is None or atm_put.ask <= 0:
        return None

    implied_downside_pct = atm_put.ask / S
    dte = atm_put.dte
    hv30_daily = chain.hv30 / math.sqrt(252)
    expected_downside_pct = hv30_daily * math.sqrt(dte)

    if expected_downside_pct == 0:
        return None

    ratio = implied_downside_pct / expected_downside_pct
    if ratio >= 0.80:
        return None

    confidence = 0.90 if ratio < 0.65 else (0.75 if ratio < 0.75 else 0.60)

    return MispricingSignal(
        symbol=chain.symbol,
        detector="downside_move",
        description=(
            f"ATM put implies {implied_downside_pct:.1%} downside over {dte} days, "
            f"but historical norms suggest {expected_downside_pct:.1%}. "
            f"Options pricing {(1-ratio):.0%} less downside than history."
        ),
        confidence=confidence,
        raw_data={
            "put_cost": round(atm_put.ask, 2),
            "implied_downside_pct": round(implied_downside_pct, 4),
            "expected_downside_pct": round(expected_downside_pct, 4),
            "underpricing_ratio": round(ratio, 3),
            "dte": dte, "hv30": round(chain.hv30, 4),
        },
    )
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/test_bearish_detectors.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scanner.py backend/tests/test_bearish_detectors.py
git commit -m "feat: add 4 bearish put mispricing detectors"
```

---

## Task 6: Bear put spread constructor + min DTE enforcement

**Files:**
- Modify: `backend/scanner.py`

- [ ] **Step 1: Update min DTE from 21 to 30 throughout scanner.py**

In `construct_best_spread`, change:
- Line ~485: `candidate_expiries = sorted(set(c.expiry for c in chain.calls if 21 <= c.dte <= 60))`
  → `candidate_expiries = sorted(set(c.expiry for c in chain.calls if 30 <= c.dte <= 60))`
- Line ~497: `if 28 <= dte <= 50:` stays (preferred window still 28–50)
- Line ~592: `if not (21 <= dte <= 60):` → `if not (30 <= dte <= 60):`
- Line ~686 (score penalty): `if setup.dte < 21:` → `if setup.dte < 30:`

Also update `detect_move_underpricing` and `detect_downside_move_underpricing` DTE filters:
- `detect_move_underpricing`: `atm_call = _atm_contract(chain.calls, S, dte_min=21, dte_max=60)` → `dte_min=30`
- `detect_move_underpricing`: `atm_put = _atm_contract(chain.puts, S, dte_min=21, dte_max=60)` → `dte_min=30`

- [ ] **Step 2: Add bear put spread constructor**

Add after `construct_best_spread` function (around line 635):

```python
def construct_bear_put_spread(
    signal: MispricingSignal,
    chain: OptionChainData,
    catalyst: CatalystContext,
) -> Optional[TradeSetup]:
    """
    Build optimal bear put spread from a bearish mispricing signal.
    Long higher-strike put, short lower-strike put. Same quality gates as bull call spread.
    """
    S = chain.stock_price
    if S == 0:
        return None

    # STEP 1 — Select expiry (30–50 DTE preferred)
    candidate_expiries = sorted(set(c.expiry for c in chain.puts if 30 <= c.dte <= 60))
    if not candidate_expiries:
        return None

    selected_expiry = None
    for exp in candidate_expiries:
        dte = (exp - date.today()).days
        if 30 <= dte <= 50:
            selected_expiry = exp
            break
    if selected_expiry is None:
        selected_expiry = candidate_expiries[0]

    dte = (selected_expiry - date.today()).days

    # STEP 2 — Select strikes
    expiry_puts = sorted(
        [c for c in chain.puts if c.expiry == selected_expiry and c.bid > 0],
        key=lambda c: c.strike,
        reverse=True,  # descending for puts
    )
    if len(expiry_puts) < 2:
        return None

    # Long leg: ATM or slightly OTM put (strike just at or below S)
    atm_candidates = [c for c in expiry_puts if c.strike >= S * 0.98]
    if not atm_candidates:
        return None
    long_leg = min(atm_candidates, key=lambda c: abs(c.strike - S))

    # Short leg: further OTM put, delta 0.15–0.30 (lower strike)
    short_candidates = [
        c for c in expiry_puts
        if c.strike < long_leg.strike and 0.15 <= abs(c.delta) <= 0.30
    ]
    if not short_candidates:
        short_candidates = [
            c for c in expiry_puts
            if S * 0.82 <= c.strike <= S * 0.94
        ]
    if not short_candidates:
        return None
    short_leg = short_candidates[0]

    spread_width = long_leg.strike - short_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0:
        return None
    if net_debit > spread_width * 0.35:
        return None

    max_gain = round(spread_width - net_debit, 2)
    max_loss = net_debit
    breakeven = round(long_leg.strike - net_debit, 2)
    breakeven_move_pct = round((S - breakeven) / S * 100, 2)  # how far down to breakeven
    rr_ratio = round(max_gain / max_loss, 2) if max_loss > 0 else 0.0
    prob_profit = round(abs(long_leg.delta) * 100, 1)

    # STEP 3 — Greeks
    net_delta = round(long_leg.delta - short_leg.delta, 3)   # both negative for puts
    net_theta = round(long_leg.theta - short_leg.theta, 3)
    net_vega  = round(long_leg.vega  - short_leg.vega,  3)

    # STEP 4 — P&L scenarios (bearish: price moves down)
    price_moves = [-0.15, -0.10, -0.08, -0.05, -0.02, 0.0, 0.03]

    def _bear_pnl_scenarios(days: int, at_expiry: bool = False) -> list:
        out = []
        for move in price_moves:
            new_price = S * (1 + move)
            label = f"Stock {move:+.0%} in {days}d" if not at_expiry else f"Stock ${new_price:.0f}"
            if at_expiry:
                long_val = max(0.0, long_leg.strike - new_price) * 100
                short_val = max(0.0, short_leg.strike - new_price) * 100
                pnl = long_val - short_val - net_debit * 100
            else:
                price_change = new_price - S
                long_pnl = (long_leg.delta * price_change - abs(long_leg.theta) * days) * 100
                short_pnl = -(short_leg.delta * price_change - abs(short_leg.theta) * days) * 100
                pnl = long_pnl - short_pnl
            pnl_pct = (pnl / (net_debit * 100)) * 100 if net_debit > 0 else 0.0
            out.append(PnLScenario(
                label=label, stock_price=round(new_price, 2),
                pnl=round(pnl, 0), pnl_pct=round(pnl_pct, 0),
            ))
        return out

    scenarios_5d      = _bear_pnl_scenarios(5)
    scenarios_10d     = _bear_pnl_scenarios(10)
    scenarios_expiry  = _bear_pnl_scenarios(dte, at_expiry=True)

    # STEP 5 — Liquidity check
    long_spread_pct  = round(_spread_pct(long_leg)  * 100, 1)
    short_spread_pct = round(_spread_pct(short_leg) * 100, 1)
    liquidity_ok = (
        long_leg.open_interest  >= 100 and short_leg.open_interest >= 100
        and long_leg.volume >= 50
        and long_spread_pct  <= 10.0 and short_spread_pct <= 10.0
    )
    if not liquidity_ok:
        return None

    # STEP 6 — Quality gates
    if rr_ratio < 2.0 or net_debit > 8.0 or breakeven_move_pct > 10.0:
        return None
    if not (30 <= dte <= 60):
        return None

    # STEP 7 — Order string
    expiry_str = selected_expiry.strftime("%d %b %y").upper()
    order_string = (
        f"BUY +1 VERTICAL {chain.symbol} 100 {expiry_str} "
        f"{long_leg.strike:.0f}/{short_leg.strike:.0f} PUT @{net_debit:.2f} LMT"
    )

    return TradeSetup(
        symbol=chain.symbol, stock_price=S, signal=signal, catalyst=catalyst,
        structure="bear_put_spread",
        long_strike=long_leg.strike, short_strike=short_leg.strike,
        expiry=selected_expiry, dte=dte,
        net_debit=net_debit, max_gain=max_gain, max_loss=max_loss,
        breakeven=breakeven, breakeven_move_pct=breakeven_move_pct,
        rr_ratio=rr_ratio, probability_of_profit=prob_profit,
        net_delta=net_delta, net_theta=net_theta, net_vega=net_vega,
        long_leg_oi=long_leg.open_interest, short_leg_oi=short_leg.open_interest,
        long_leg_volume=long_leg.volume,
        long_leg_spread_pct=long_spread_pct, short_leg_spread_pct=short_spread_pct,
        liquidity_ok=liquidity_ok,
        scenarios_5d=scenarios_5d, scenarios_10d=scenarios_10d,
        scenarios_expiry=scenarios_expiry,
        score=0, timestamp=datetime.utcnow(), order_string=order_string,
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/scanner.py
git commit -m "feat: add bear put spread constructor, enforce min 30 DTE"
```

---

## Task 7: Score breakdown + direction-routing in run_all_detectors

**Files:**
- Modify: `backend/scanner.py`

- [ ] **Step 1: Add score_breakdown computation function**

Add after `score_swing_quality` (around line 695):

```python
def compute_score_breakdown(setup: TradeSetup) -> list[dict]:
    """Return itemized list of score contributions for UI transparency."""
    items = []

    if setup.catalyst.earnings_in_window:
        items.append({"label": "Earnings in window", "pts": 25})
    if setup.catalyst.iv_expansion_likely:
        items.append({"label": "IV expansion likely", "pts": 20})
    if setup.signal.detector == "parity":
        items.append({"label": "Parity violation", "pts": 20})
    if setup.signal.detector == "put_parity":
        items.append({"label": "Put parity violation", "pts": 20})
    if setup.signal.raw_data.get("iv_rank", 100) < 20:
        items.append({"label": "IV rank < 20", "pts": 15})
    if setup.signal.detector in ("skew", "skew_inversion"):
        items.append({"label": "Skew anomaly", "pts": 10})
    if setup.signal.detector in ("move", "downside_move"):
        items.append({"label": "Move underpricing", "pts": 10})
    if setup.rr_ratio >= 3.0:
        items.append({"label": "R:R ≥ 3.0", "pts": 20})
    elif setup.rr_ratio >= 2.0:
        items.append({"label": "R:R ≥ 2.0", "pts": 10})
    if setup.breakeven_move_pct < 5.0:
        items.append({"label": "Tight breakeven", "pts": 15})
    if setup.net_debit <= 3.00:
        items.append({"label": "Low debit", "pts": 10})
    if min(setup.long_leg_oi, setup.short_leg_oi) >= 500:
        items.append({"label": "High OI", "pts": 10})
    if setup.long_leg_volume >= 200:
        items.append({"label": "Good volume", "pts": 5})
    if 28 <= setup.dte <= 50:
        items.append({"label": "DTE 28–50", "pts": 10})

    return items
```

- [ ] **Step 2: Update run_all_detectors to accept TechnicalContext and direction**

Replace the existing `run_all_detectors` function:

```python
def run_all_detectors(
    chain: OptionChainData,
    catalyst: "CatalystContext",
    technical_context: Optional["TechnicalContext"] = None,
    direction: str = "both",
) -> list[TradeSetup]:
    """
    Run detectors based on direction:
      - "bullish": call detectors only → bull call spread
      - "bearish": put detectors only → bear put spread
      - "both": all 9 detectors, each routed to its spread type
    direction defaults to "both"; overridden by technical_context.bias when provided.
    """
    from models import TechnicalContext as TC

    effective_direction = direction
    if technical_context is not None and direction == "both":
        if technical_context.bias == "bullish":
            effective_direction = "bullish"
        elif technical_context.bias == "bearish":
            effective_direction = "bearish"

    bullish_detectors = [
        detect_iv_rank_cheap(chain),
        detect_skew_anomaly(chain),
        detect_parity_violation(chain),
        detect_term_structure_gap(chain, earnings_date=catalyst.earnings_date),
        detect_move_underpricing(chain),
    ]
    bearish_detectors = [
        detect_put_iv_rank_cheap(chain),
        detect_skew_inversion(chain),
        detect_put_parity_violation(chain),
        detect_downside_move_underpricing(chain),
    ]

    if effective_direction == "bullish":
        signals_with_constructor = [(s, construct_best_spread) for s in bullish_detectors if s]
    elif effective_direction == "bearish":
        signals_with_constructor = [(s, construct_bear_put_spread) for s in bearish_detectors if s]
    else:
        signals_with_constructor = (
            [(s, construct_best_spread)    for s in bullish_detectors if s] +
            [(s, construct_bear_put_spread) for s in bearish_detectors if s]
        )

    setups = []
    for signal, constructor in signals_with_constructor:
        setup = constructor(signal, chain, catalyst)
        if setup is None:
            continue
        setup.score = score_swing_quality(setup)
        setup.score_breakdown = compute_score_breakdown(setup)
        if technical_context is not None:
            setup.technical_context = technical_context
        setups.append(setup)
        logger.info(
            "%s [%s] → score=%d rr=%.2f debit=$%.2f",
            chain.symbol, signal.detector, setup.score, setup.rr_ratio, setup.net_debit,
        )

    return setups
```

- [ ] **Step 3: Run existing tests to confirm nothing broken**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/scanner.py
git commit -m "feat: add score breakdown, direction routing in run_all_detectors"
```

---

## Task 8: Update main.py — EOD scan, sector endpoint, wire TechnicalContext

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add imports and sector cache**

At the top of `main.py`, add to the existing imports:

```python
from sector_analysis import get_sector_analysis
from technical_analysis import get_technical_contexts
from qqq_holdings import QQQ_TOP50, SECTOR_MAP  # replace QQQ_TOP30 import
```

Replace `from qqq_holdings import QQQ_TOP30` with the line above.

Add to the `_cache` dict:

```python
_cache: dict = {
    "opportunities": [],
    "market_context": None,
    "scan_timestamp": None,
    "symbols_scanned": 0,
    "sector_analysis": [],       # list[SectorData]
    "sector_timestamp": None,    # datetime
}
```

- [ ] **Step 2: Update _run_scan to use QQQ_TOP50 and inject TechnicalContext**

In `_run_scan`, change:
- `logger.info("Starting full scan of %d symbols", len(QQQ_TOP30))` → `len(QQQ_TOP50)`
- `chains = await fetch_all_chains(QQQ_TOP30)` → `fetch_all_chains(QQQ_TOP50)`
- `_cache["symbols_scanned"] = len(QQQ_TOP30)` → `len(QQQ_TOP50)`

Before the chain loop, fetch technical contexts in a thread executor:

```python
        # Fetch technical context for all symbols
        tech_contexts = await asyncio.get_event_loop().run_in_executor(
            None, get_technical_contexts, QQQ_TOP50
        )
```

Inside the chain loop, pass technical context:

```python
        for chain in chains:
            if chain.stock_price == 0:
                continue
            catalyst = get_catalyst_context(chain.symbol, chain, trade_dte=35)
            tech_ctx = tech_contexts.get(chain.symbol)
            setups = run_all_detectors(chain, catalyst, technical_context=tech_ctx)
            all_setups.extend(setups)
```

- [ ] **Step 3: Add sector refresh function**

Add after `scan_all`:

```python
async def refresh_sector_analysis() -> None:
    """Refresh sector ETF data once daily. Does not require Schwab auth."""
    if not _is_weekday():
        return
    try:
        logger.info("Refreshing sector analysis")
        sectors = await asyncio.get_event_loop().run_in_executor(
            None, get_sector_analysis
        )
        _cache["sector_analysis"] = sectors
        _cache["sector_timestamp"] = datetime.utcnow()
        logger.info("Sector analysis updated: %d sectors", len(sectors))
    except Exception as e:
        logger.exception("Sector analysis refresh failed: %s", e)
```

- [ ] **Step 4: Add EOD scan and sector scheduler job**

In the scheduler section, add two new jobs:

```python
scheduler.add_job(scan_all, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET))
scheduler.add_job(refresh_sector_analysis, CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))
```

In `startup`, trigger sector analysis on startup:

```python
    asyncio.create_task(refresh_sector_analysis())
```

- [ ] **Step 5: Add /sector-analysis endpoint and update /opportunities**

Add the new endpoint:

```python
@app.get("/sector-analysis")
@limiter.limit("10/minute")
async def get_sector_analysis_endpoint(request: Request):
    return JSONResponse(content={
        "sectors": [_serialize(s) for s in _cache["sector_analysis"]],
        "as_of": _cache["sector_timestamp"].isoformat() if _cache["sector_timestamp"] else None,
    })
```

In `/opportunities`, update the `QQQ_TOP30` reference in the `symbols_scanned` field (already handled in Step 2). Also add `direction` filter param:

```python
@app.get("/opportunities")
@limiter.limit("10/minute")
async def get_opportunities(
    request: Request,
    min_rr: float = 2.0,
    max_debit: float = 8.0,
    min_score: int = 55,
    detector: str = "all",
    direction: str = "both",   # "bullish" | "bearish" | "both"
):
    opps = _cache["opportunities"]
    filtered = [
        s for s in opps
        if s.rr_ratio >= min_rr
        and s.net_debit <= max_debit
        and s.score >= min_score
        and (detector == "all" or s.signal.detector == detector)
        and (direction == "both" or (
            direction == "bullish" and s.structure in ("bull_call_spread", "calendar", "long_call") or
            direction == "bearish" and s.structure == "bear_put_spread"
        ))
    ]
    # rest unchanged
```

- [ ] **Step 6: Import SectorData in models import**

In `main.py` imports, add:

```python
from models import MarketContext, SectorData, TradeSetup
```

- [ ] **Step 7: Smoke test the server starts**

```bash
cd /Users/umer/Documents/work/mispriced-scanner/backend
uvicorn main:app --port 8000 --reload 2>&1 | head -20
```

Expected: `Scheduler started`, `Refreshing sector analysis` log lines, no errors.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py
git commit -m "feat: add EOD scan (3:45PM ET), /sector-analysis endpoint, TechnicalContext in scan"
```

---

## Task 9: Frontend — api.js and FilterBar updates

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/FilterBar.jsx`

- [ ] **Step 1: Add fetchSectorAnalysis to api.js**

```javascript
// frontend/src/api.js — add after fetchHealth:

export async function fetchSectorAnalysis() {
  const res = await fetch(`${BASE_URL}/sector-analysis`)
  if (!res.ok) throw new Error(`Sector analysis failed: ${res.status}`)
  return res.json()
}
```

Also update `fetchOpportunities` to pass `direction`:

```javascript
export async function fetchOpportunities(filters = {}) {
  const params = new URLSearchParams({
    min_rr:    filters.minRR      ?? 2.0,
    max_debit: filters.maxDebit   ?? 8.0,
    min_score: filters.minScore   ?? 55,
    detector:  filters.detector   ?? 'all',
    direction: filters.direction  ?? 'both',
  })
  const res = await fetch(`${BASE_URL}/opportunities?${params}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Add directional toggle + 4 new detector labels to FilterBar.jsx**

Update the `DETECTORS` array:

```javascript
const DETECTORS = [
  { key: 'all',           label: 'All' },
  { key: 'iv_rank',       label: 'IV Rank' },
  { key: 'skew',          label: 'Skew' },
  { key: 'parity',        label: 'Parity' },
  { key: 'term',          label: 'Term' },
  { key: 'move',          label: 'Move' },
  { key: 'put_iv_rank',   label: 'Put IV Rank' },
  { key: 'skew_inversion',label: 'Skew Inv.' },
  { key: 'put_parity',    label: 'Put Parity' },
  { key: 'downside_move', label: 'Downside' },
]

const DIRECTIONS = [
  { key: 'both',    label: 'Both' },
  { key: 'bullish', label: '▲ Bullish' },
  { key: 'bearish', label: '▼ Bearish' },
]
```

Add a direction row before the detector tabs row. Inside the `return` JSX:

```jsx
      {/* Direction toggle */}
      <div style={styles.row}>
        <span style={styles.label}>DIRECTION</span>
        <div style={styles.tabs}>
          {DIRECTIONS.map(d => (
            <button
              key={d.key}
              style={{
                ...styles.tab,
                ...(filters.direction === d.key ? {
                  ...styles.tabActive,
                  ...(d.key === 'bearish' ? { borderColor: '#ff4444', color: '#ff4444', background: '#1a0a0a' } : {}),
                } : {}),
              }}
              onClick={() => set('direction', d.key)}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js frontend/src/components/FilterBar.jsx
git commit -m "feat: add direction toggle and 4 bearish detector labels to filter bar"
```

---

## Task 10: Frontend — SectorStrip and SectorPanel components

**Files:**
- Create: `frontend/src/components/SectorStrip.jsx`
- Create: `frontend/src/components/SectorPanel.jsx`

- [ ] **Step 1: Create SectorStrip.jsx**

```jsx
// frontend/src/components/SectorStrip.jsx
import { useState } from 'react'
import SectorPanel from './SectorPanel.jsx'

function trendArrow(dir) {
  if (dir === 'improving') return '▲'
  if (dir === 'deteriorating') return '▼'
  return '—'
}

function tileColor(classification) {
  if (classification === 'bullish') return '#00ffaa'
  if (classification === 'bearish') return '#ff4444'
  return '#555'
}

export default function SectorStrip({ sectors, activeSector, onSectorClick }) {
  const [panelOpen, setPanelOpen] = useState(false)

  if (!sectors || sectors.length === 0) return null

  return (
    <div style={styles.wrapper}>
      <div style={styles.strip}>
        <span style={styles.stripLabel}>SECTORS</span>
        <div style={styles.tiles}>
          {sectors.map(s => {
            const isActive = activeSector === s.etf
            const color = tileColor(s.classification)
            return (
              <button
                key={s.etf}
                style={{
                  ...styles.tile,
                  borderColor: isActive ? color : '#2a2a3e',
                  background: isActive ? (s.classification === 'bullish' ? '#0a1a0f' : s.classification === 'bearish' ? '#1a0a0a' : '#0a0a14') : 'none',
                }}
                onClick={() => onSectorClick(isActive ? null : s.etf)}
              >
                <span style={{ ...styles.etf, color }}>{s.etf}</span>
                <span style={{ ...styles.arrow, color }}>{trendArrow(s.trend_direction)}</span>
                <span style={{ ...styles.ret, color }}>
                  {s.return_vs_spy_4w >= 0 ? '+' : ''}{s.return_vs_spy_4w.toFixed(1)}%
                </span>
              </button>
            )
          })}
        </div>
        <button style={styles.viewAll} onClick={() => setPanelOpen(v => !v)}>
          {panelOpen ? 'Close ✕' : 'View all →'}
        </button>
      </div>

      {panelOpen && <SectorPanel sectors={sectors} />}
    </div>
  )
}

const styles = {
  wrapper: { background: '#080810', borderBottom: '1px solid #1a1a2e' },
  strip: {
    display: 'flex', alignItems: 'center', gap: '8px',
    padding: '6px 16px', overflowX: 'auto',
  },
  stripLabel: {
    fontFamily: 'monospace', fontSize: '10px', color: '#555',
    letterSpacing: '0.08em', whiteSpace: 'nowrap',
  },
  tiles: { display: 'flex', gap: '6px', flex: 1 },
  tile: {
    display: 'flex', alignItems: 'center', gap: '4px',
    padding: '4px 8px', border: '1px solid #2a2a3e',
    cursor: 'pointer', borderRadius: '3px', whiteSpace: 'nowrap',
  },
  etf: { fontFamily: 'monospace', fontSize: '11px', fontWeight: 'bold' },
  arrow: { fontFamily: 'monospace', fontSize: '10px' },
  ret: { fontFamily: 'monospace', fontSize: '11px' },
  viewAll: {
    padding: '4px 10px', background: 'none', border: '1px solid #2a2a3e',
    color: '#555', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px',
    borderRadius: '3px', whiteSpace: 'nowrap', marginLeft: 'auto',
  },
}
```

- [ ] **Step 2: Create SectorPanel.jsx**

```jsx
// frontend/src/components/SectorPanel.jsx
import { useState } from 'react'

const PERIODS = ['1W', '4W', '12W']

function arrow(dir) {
  if (dir === 'improving') return '▲'
  if (dir === 'deteriorating') return '▼'
  return '—'
}

function retColor(val) {
  return val >= 0 ? '#00ffaa' : '#ff4444'
}

export default function SectorPanel({ sectors }) {
  const [period, setPeriod] = useState('4W')

  const getReturn = (s) => {
    if (period === '1W') return s.return_vs_spy_1w
    if (period === '12W') return s.return_vs_spy_12w
    return s.return_vs_spy_4w
  }

  const sorted = [...sectors].sort((a, b) => getReturn(b) - getReturn(a))

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <span style={styles.title}>SECTOR BREAKDOWN</span>
        <div style={styles.periodTabs}>
          <span style={styles.label}>vs SPY:</span>
          {PERIODS.map(p => (
            <button
              key={p}
              style={{ ...styles.tab, ...(period === p ? styles.tabActive : {}) }}
              onClick={() => setPeriod(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div style={styles.table}>
        <div style={styles.headerRow}>
          <span style={{ ...styles.cell, width: 60 }}>ETF</span>
          <span style={{ ...styles.cell, flex: 1 }}>Sector</span>
          <span style={{ ...styles.cell, width: 90, textAlign: 'right' }}>vs SPY ({period})</span>
          <span style={{ ...styles.cell, width: 70, textAlign: 'right' }}>RS Score</span>
          <span style={{ ...styles.cell, width: 60, textAlign: 'center' }}>Trend</span>
        </div>
        {sorted.map(s => {
          const ret = getReturn(s)
          return (
            <div key={s.etf} style={styles.row}>
              <span style={{ ...styles.cell, width: 60, color: '#fff', fontWeight: 'bold' }}>{s.etf}</span>
              <span style={{ ...styles.cell, flex: 1, color: '#888' }}>{s.name}</span>
              <span style={{ ...styles.cell, width: 90, textAlign: 'right', color: retColor(ret) }}>
                {ret >= 0 ? '+' : ''}{ret.toFixed(1)}%
              </span>
              <span style={{ ...styles.cell, width: 70, textAlign: 'right', color: '#aaa' }}>
                {s.rs_score.toFixed(0)}
              </span>
              <span style={{
                ...styles.cell, width: 60, textAlign: 'center',
                color: s.trend_direction === 'improving' ? '#00ffaa' : s.trend_direction === 'deteriorating' ? '#ff4444' : '#555',
              }}>
                {arrow(s.trend_direction)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles = {
  panel: { padding: '12px 16px', borderTop: '1px solid #1a1a2e', background: '#080810' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' },
  title: { fontFamily: 'monospace', fontSize: '10px', color: '#555', letterSpacing: '0.08em' },
  periodTabs: { display: 'flex', alignItems: 'center', gap: '6px' },
  label: { fontFamily: 'monospace', fontSize: '10px', color: '#555' },
  tab: {
    padding: '3px 8px', background: 'none', border: '1px solid #2a2a3e',
    color: '#666', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px', borderRadius: '3px',
  },
  tabActive: { borderColor: '#00ffaa', color: '#00ffaa', background: '#0a1a0f' },
  table: { display: 'flex', flexDirection: 'column', gap: '2px' },
  headerRow: {
    display: 'flex', padding: '4px 0',
    borderBottom: '1px solid #1a1a2e', marginBottom: '4px',
  },
  row: {
    display: 'flex', padding: '5px 0', borderBottom: '1px solid #0f0f1a',
  },
  cell: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SectorStrip.jsx frontend/src/components/SectorPanel.jsx
git commit -m "feat: add SectorStrip and SectorPanel components"
```

---

## Task 11: Wire sector strip into App.jsx + Dashboard.jsx

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Read App.jsx to understand current structure**

```bash
cat frontend/src/App.jsx
```

- [ ] **Step 2: Add sector state, fetch, and strip to App.jsx**

Add to imports:

```jsx
import { fetchSectorAnalysis } from './api.js'
import SectorStrip from './components/SectorStrip.jsx'
```

Add state in the component body (alongside existing state):

```jsx
const [sectors, setSectors] = useState([])
const [activeSector, setActiveSector] = useState(null)
```

Add `direction` to initial filters state (wherever `filters` is initialized):

```jsx
// Add direction: 'both' to the existing filters object
const [filters, setFilters] = useState({
  minRR: 2.0, maxDebit: 8.0, minScore: 55,
  detector: 'all', sort: 'score', minOI: false, direction: 'both',
})
```

Fetch sectors on mount (add alongside existing fetch logic):

```jsx
useEffect(() => {
  fetchSectorAnalysis()
    .then(data => setSectors(data.sectors || []))
    .catch(err => console.warn('Sector fetch failed:', err))
}, [])
```

- [ ] **Step 3: Add sector strip above the Scanner content and apply sector filter**

In the Scanner tab render, add SectorStrip above Dashboard (or wherever the scanner content renders):

```jsx
<SectorStrip
  sectors={sectors}
  activeSector={activeSector}
  onSectorClick={setActiveSector}
/>
```

Pass `activeSector` to Dashboard (or filter opportunities client-side before passing):

```jsx
// In the opportunities filtering logic, add:
// Import SECTOR_MAP equivalent — store it in a frontend constant
const SECTOR_MAP = { /* copy from qqq_holdings.py */ }

// Filter by active sector:
const visibleOpps = activeSector
  ? opportunities.filter(o => SECTOR_MAP[o.symbol] === activeSector)
  : opportunities
```

Add the SECTOR_MAP constant at the top of `App.jsx`:

```jsx
const SECTOR_MAP = {
  NVDA:'XLK', AAPL:'XLK', MSFT:'XLK', AVGO:'XLK', AMD:'XLK',
  ADBE:'XLK', QCOM:'XLK', INTC:'XLK', CSCO:'XLK', TXN:'XLK',
  INTU:'XLK', MU:'XLK', AMAT:'XLK', LRCX:'XLK', MRVL:'XLK',
  KLAC:'XLK', CDNS:'XLK', SNPS:'XLK', PLTR:'XLK', CRWD:'XLK',
  PANW:'XLK', FTNT:'XLK', ZS:'XLK', NET:'XLK', DDOG:'XLK',
  WDAY:'XLK', TEAM:'XLK',
  META:'XLC', GOOGL:'XLC', GOOG:'XLC', NFLX:'XLC', TTWO:'XLC', DASH:'XLC',
  AMZN:'XLY', TSLA:'XLY', COST:'XLY', ABNB:'XLY', MELI:'XLY',
  AMGN:'XLV', ISRG:'XLV', DXCM:'XLV',
  PYPL:'XLF', COIN:'XLF', VRSK:'XLF',
  ODFL:'XLI',
  MNST:'XLP', KDP:'XLP',
  EXC:'XLU', AEP:'XLU',
  CSGP:'XLRE',
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: wire sector strip into scanner tab with sector filter"
```

---

## Task 12: Update OpportunityCard — technical context, score breakdown, bearish styling

**Files:**
- Modify: `frontend/src/components/OpportunityCard.jsx`

- [ ] **Step 1: Read the full OpportunityCard.jsx**

```bash
cat frontend/src/components/OpportunityCard.jsx
```

- [ ] **Step 2: Add DETECTOR_LABELS for new bearish detectors**

Update the `DETECTOR_LABELS` object:

```jsx
const DETECTOR_LABELS = {
  iv_rank:       'IV Rank',
  skew:          'Skew Anomaly',
  parity:        'Parity Violation',
  term:          'Term Structure',
  move:          'Move Underpricing',
  put_iv_rank:   'Put IV Rank',
  skew_inversion:'Skew Inversion',
  put_parity:    'Put Parity',
  downside_move: 'Downside Move',
}
```

- [ ] **Step 3: Add score breakdown state and structured label helper**

Add inside the component, after the existing `useState` calls:

```jsx
const [scoreExpanded, setScoreExpanded] = useState(false)

const isBearish = setup.structure === 'bear_put_spread'

const structureLabel = {
  bull_call_spread: 'Bull Call Spread',
  bear_put_spread:  'Bear Put Spread',
  calendar:         'Calendar Spread',
  long_call:        'Long Call',
}[setup.structure] ?? setup.structure

const scoreLabel = (() => {
  const detectors = signal.detector
    ? (DETECTOR_LABELS[signal.detector] || signal.detector)
    : '—'
  const trend = setup.technical_context?.trend
  const trendStr = trend ? ` | ${trend.charAt(0).toUpperCase() + trend.slice(1)}` : ''
  return `${detectors}${trendStr}`
})()
```

- [ ] **Step 4: Update the card's left border and header**

Update the `styles.card` to use a dynamic border based on `isBearish`:

In the JSX, replace the static card container with:

```jsx
<div style={{ ...styles.card, borderLeft: `3px solid ${isBearish ? '#ff4444' : '#00ffaa'}` }}>
```

- [ ] **Step 5: Replace the score display with tap-to-expand breakdown**

Find the score span in the header and replace it with:

```jsx
<span
  style={{ ...styles.score, color: scoreColor(score), cursor: 'pointer' }}
  onClick={e => { e.stopPropagation(); setScoreExpanded(v => !v) }}
>
  Score: {score}/100 {scoreExpanded ? '▲' : '▼'}
</span>
```

Add the breakdown below the score span (inside `headerLeft`):

```jsx
{scoreExpanded && setup.score_breakdown && (
  <div style={styles.breakdown}>
    {setup.score_breakdown.map((item, i) => (
      <div key={i} style={styles.breakdownRow}>
        <span style={styles.breakdownLabel}>{item.label}</span>
        <span style={styles.breakdownPts}>+{item.pts}</span>
      </div>
    ))}
    <div style={{ ...styles.breakdownRow, borderTop: '1px solid #2a2a3e', marginTop: '4px', paddingTop: '4px' }}>
      <span style={styles.breakdownLabel}>Total</span>
      <span style={{ ...styles.breakdownPts, color: scoreColor(score) }}>{score}</span>
    </div>
  </div>
)}
```

Add below the score (in the always-visible header area, not inside expanded):

```jsx
<span style={styles.scoreLabel}>{scoreLabel}</span>
```

- [ ] **Step 6: Add technical context block**

In the expanded body, after the `<hr />` and before the WHY THIS EXISTS section, add:

```jsx
{setup.technical_context && (
  <div style={styles.techBlock}>
    <div style={styles.techHeader}>
      <span style={styles.techSymbol}>{symbol}</span>
      <span style={styles.techPrice}>${setup.technical_context.price?.toFixed(2)}</span>
      <span style={{
        ...styles.techTrend,
        color: setup.technical_context.trend === 'uptrend' ? '#00ffaa'
             : setup.technical_context.trend === 'downtrend' ? '#ff4444' : '#ffaa00',
      }}>
        {setup.technical_context.trend === 'uptrend' ? '↑' : setup.technical_context.trend === 'downtrend' ? '↓' : '↔'}{' '}
        {setup.technical_context.trend}
      </span>
    </div>
    <div style={styles.techMAs}>
      <span style={styles.techMA}>
        MA50 ${setup.technical_context.ma50?.toFixed(2)}
        <span style={{ color: setup.technical_context.pct_from_ma50 >= 0 ? '#00ffaa' : '#ff4444' }}>
          {' '}({setup.technical_context.pct_from_ma50 >= 0 ? '+' : ''}{setup.technical_context.pct_from_ma50?.toFixed(1)}%)
        </span>
      </span>
      <span style={styles.techMA}>
        MA200 ${setup.technical_context.ma200?.toFixed(2)}
        <span style={{ color: setup.technical_context.pct_from_ma200 >= 0 ? '#00ffaa' : '#ff4444' }}>
          {' '}({setup.technical_context.pct_from_ma200 >= 0 ? '+' : ''}{setup.technical_context.pct_from_ma200?.toFixed(1)}%)
        </span>
      </span>
    </div>
  </div>
)}
```

- [ ] **Step 7: Fix the hardcoded "Bull Call Spread" in the subheader**

Find this line:
```jsx
Bull Call Spread · {expiryStr} ${long_strike?.toFixed(0)}/{short_strike?.toFixed(0)} · DTE {dte}
```

Replace with:
```jsx
{structureLabel} · {expiryStr} ${long_strike?.toFixed(0)}/{short_strike?.toFixed(0)} · DTE {dte}
```

- [ ] **Step 8: Add new styles**

Add to the `styles` object:

```jsx
  scoreLabel: {
    fontFamily: 'monospace', fontSize: '10px', color: '#666',
    marginLeft: '8px', letterSpacing: '0.04em',
  },
  breakdown: {
    background: '#0a0a14', border: '1px solid #1a1a2e', borderRadius: '3px',
    padding: '6px 10px', marginTop: '4px', minWidth: '200px',
  },
  breakdownRow: {
    display: 'flex', justifyContent: 'space-between', gap: '16px',
    fontFamily: 'monospace', fontSize: '11px', padding: '1px 0',
  },
  breakdownLabel: { color: '#888' },
  breakdownPts: { color: '#00ffaa' },
  techBlock: {
    background: '#0a0a14', border: '1px solid #1a1a2e', borderRadius: '3px',
    padding: '8px 12px', marginBottom: '12px',
  },
  techHeader: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' },
  techSymbol: { fontFamily: 'monospace', fontSize: '13px', color: '#fff', fontWeight: 'bold' },
  techPrice: { fontFamily: 'monospace', fontSize: '13px', color: '#aaa' },
  techTrend: { fontFamily: 'monospace', fontSize: '12px' },
  techMAs: { display: 'flex', gap: '20px' },
  techMA: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
```

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/OpportunityCard.jsx
git commit -m "feat: add technical context block, score breakdown, bearish card styling"
```

---

## Task 13: Position sizing panel + Trade Journal CSV

**Files:**
- Modify: `frontend/src/components/OpportunityCard.jsx`
- Modify: `frontend/src/components/TradeJournal.jsx`

- [ ] **Step 1: Add position sizing panel to OpportunityCard**

Add state at the top of the component:

```jsx
const ACCOUNT_SIZE = 40000
const [riskPct, setRiskPct] = useState(() => {
  return parseFloat(localStorage.getItem('qqq_risk_pct') || '2.0')
})

const suggestedContracts = net_debit > 0
  ? Math.max(1, Math.floor((ACCOUNT_SIZE * riskPct / 100) / (net_debit * 100)))
  : 0
const maxLossDollars = suggestedContracts * net_debit * 100
```

In the expanded ACTIONS section (find the existing actions buttons), add BEFORE the save/copy buttons:

```jsx
<div style={styles.sizingPanel}>
  <div style={styles.sizingRow}>
    <span style={styles.sizingLabel}>RISK PER TRADE</span>
    <input
      type="number" min="0.5" max="10" step="0.5"
      value={riskPct}
      onChange={e => {
        const v = parseFloat(e.target.value)
        if (!isNaN(v)) {
          setRiskPct(v)
          localStorage.setItem('qqq_risk_pct', String(v))
        }
      }}
      style={styles.riskInput}
    />
    <span style={styles.sizingLabel}>% of $40k</span>
  </div>
  <div style={styles.sizingResult}>
    <span style={styles.sizingContracts}>{suggestedContracts} contracts</span>
    <span style={styles.sizingLoss}>(${maxLossDollars.toLocaleString()} max loss)</span>
  </div>
</div>
```

Add styles:

```jsx
  sizingPanel: {
    background: '#0a0a14', border: '1px solid #1a1a2e', borderRadius: '3px',
    padding: '8px 12px', marginBottom: '10px',
  },
  sizingRow: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' },
  sizingLabel: { fontFamily: 'monospace', fontSize: '10px', color: '#555' },
  riskInput: {
    width: '50px', background: '#0f0f1a', border: '1px solid #2a2a3e',
    color: '#00ffaa', fontFamily: 'monospace', fontSize: '12px',
    padding: '2px 6px', borderRadius: '3px', textAlign: 'center',
  },
  sizingResult: { display: 'flex', alignItems: 'baseline', gap: '8px' },
  sizingContracts: { fontFamily: 'monospace', fontSize: '16px', color: '#00ffaa', fontWeight: 'bold' },
  sizingLoss: { fontFamily: 'monospace', fontSize: '11px', color: '#888' },
```

- [ ] **Step 2: Add CSV export/import to TradeJournal.jsx**

Read the full TradeJournal to find the heading section:

```bash
grep -n "MY TRADES\|heading\|return" frontend/src/components/TradeJournal.jsx | head -20
```

Add export/import functions inside `TradeJournal`:

```jsx
function exportCSV() {
  const headers = ['id','symbol','structure','contracts','entry_debit','dte',
                   'date_saved','notes','status','exit_date','exit_credit',
                   'pnl_dollars','pnl_pct']
  const rows = trades.map(t =>
    headers.map(h => JSON.stringify(t[h] ?? '')).join(',')
  )
  const csv = [headers.join(','), ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `trades_${new Date().toISOString().split('T')[0]}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function importCSV(e) {
  const file = e.target.files[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = ev => {
    const lines = ev.target.result.trim().split('\n')
    const headers = lines[0].split(',')
    const imported = lines.slice(1).map(line => {
      const vals = line.split(',').map(v => {
        try { return JSON.parse(v) } catch { return v }
      })
      return Object.fromEntries(headers.map((h, i) => [h, vals[i]]))
    })
    // Merge: deduplicate by id, imported wins on conflict
    const existing = trades.filter(t => !imported.find(i => i.id === t.id))
    save([...existing, ...imported])
  }
  reader.readAsText(file)
  e.target.value = ''  // reset input
}
```

In the JSX, add export/import buttons next to the "MY TRADES" heading:

```jsx
<div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
  <div style={styles.heading}>MY TRADES</div>
  <div style={{ display: 'flex', gap: '8px' }}>
    <button style={styles.csvBtn} onClick={exportCSV}>Export CSV</button>
    <label style={styles.csvBtn}>
      Import CSV
      <input type="file" accept=".csv" onChange={importCSV} style={{ display: 'none' }} />
    </label>
  </div>
</div>
```

Add style:

```jsx
  csvBtn: {
    padding: '4px 12px', background: 'none', border: '1px solid #2a2a3e',
    color: '#666', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px',
    borderRadius: '3px',
  },
```

Remove the standalone `<div style={styles.heading}>MY TRADES</div>` that existed before (replace it with the flex container above).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/OpportunityCard.jsx frontend/src/components/TradeJournal.jsx
git commit -m "feat: add position sizing panel and CSV export/import to trade journal"
```

---

## Self-Review Checklist

After all tasks complete, verify:

- [ ] `python -m pytest backend/tests/ -v` — all tests pass
- [ ] `cd frontend && npm run build` — no TypeScript/lint errors
- [ ] Backend starts: `uvicorn backend.main:app --port 8000` — no import errors
- [ ] `/sector-analysis` returns 11 sectors
- [ ] `/opportunities` with `direction=bearish` returns only `bear_put_spread` structures
- [ ] Sector strip renders and clicking a tile filters opportunities
- [ ] Score tap-to-expand works on mobile (touch events)
- [ ] CSV export downloads a valid file; re-importing merges without duplicates
- [ ] Position sizing updates across cards when risk % changes
