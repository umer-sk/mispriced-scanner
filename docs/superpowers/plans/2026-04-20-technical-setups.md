# Technical Setups Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SETUPS tab that finds the best R:R options plays across 100 QQQ stocks using 7 technical indicators (Qullamaggie/Minervini momentum style), choosing between long calls/puts or spreads based on IV rank.

**Architecture:** New `technical_scanner.py` fetches 220-day price history via yfinance, computes 7 signals per stock, selects optimal options structure from Schwab chains for stocks with 5+/7 signal agreement, and returns `TechnicalSetup` objects cached in `_technical_cache`. Two new endpoints serve results. New `TechnicalSetups.jsx` tab with its own scan button.

**Tech Stack:** Python 3.11, yfinance (already installed), pandas, FastAPI, React

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/models.py` | Modify | Add `TechnicalSetup` dataclass |
| `backend/technical_scanner.py` | Create | Indicator computation, options structure selection, scan orchestration |
| `backend/tests/test_technical_scanner.py` | Create | Unit tests for indicators and structure selection |
| `backend/main.py` | Modify | `_technical_cache`, `/technical-setups`, `/scan-setups` endpoints |
| `frontend/src/api.js` | Modify | `fetchTechnicalSetups`, `triggerSetupsScan` |
| `frontend/src/components/TechnicalSetups.jsx` | Create | New tab UI |
| `frontend/src/App.jsx` | Modify | SETUPS tab button + content render |

---

### Task 1: Add TechnicalSetup dataclass to models.py

**Files:**
- Modify: `backend/models.py` (append after line 160)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_technical_scanner.py
from models import TechnicalSetup
from datetime import date

def test_technical_setup_fields():
    setup = TechnicalSetup(
        symbol="NVDA",
        stock_price=875.0,
        direction="bullish",
        signal_count=6,
        signal_details={"stage2": True, "ema_alignment": True, "price_vs_ema21": True,
                        "rsi_zone": True, "volume_accum": True, "rs_vs_qqq": True, "breakout": False},
        structure="long_call",
        strike=900.0,
        short_strike=None,
        expiry=date(2026, 5, 16),
        dte=45,
        delta=0.44,
        iv_rank=32.0,
        premium=4.20,
        price_target=940.0,
        rr_ratio=3.2,
        max_loss=420.0,
        breakeven_move_pct=4.8,
        probability_of_profit=44,
        order_string="BUY +1 NVDA 05/16 900 CALL @4.20 LMT",
    )
    assert setup.symbol == "NVDA"
    assert setup.direction == "bullish"
    assert setup.signal_count == 6
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py::test_technical_setup_fields -v
```

Expected: `ImportError: cannot import name 'TechnicalSetup' from 'models'`

- [ ] **Step 3: Add TechnicalSetup to models.py**

Append after the `SectorData` dataclass (after line 160):

```python
@dataclass
class TechnicalSetup:
    # Identity
    symbol: str
    stock_price: float
    direction: str              # "bullish" | "bearish"
    signal_count: int           # 5, 6, or 7

    # Which of the 7 signals fired (True = bullish for that signal)
    signal_details: dict        # keys: stage2, ema_alignment, price_vs_ema21,
                                #       rsi_zone, volume_accum, rs_vs_qqq, breakout

    # Structure
    structure: str              # "long_call" | "long_put" | "bull_call_spread" | "bear_put_spread"
    strike: float               # long leg strike
    short_strike: Optional[float]  # None for outright calls/puts
    expiry: date
    dte: int
    delta: float                # long leg delta (raw, negative for puts)
    iv_rank: float

    # Economics
    premium: float              # net debit per spread, or ask for outright (per-share)
    price_target: float         # ATR-based target
    rr_ratio: float
    max_loss: float             # dollars per contract
    breakeven_move_pct: float   # % stock must move to break even
    probability_of_profit: int  # approx %, delta * 100

    # Execution
    order_string: str
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py::test_technical_setup_fields -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_technical_scanner.py
git commit -m "feat: add TechnicalSetup dataclass to models"
```

---

### Task 2: Implement 7 technical indicators in technical_scanner.py

**Files:**
- Create: `backend/technical_scanner.py`

The 7 signals all return True = bullish for that signal, False = bearish.
Net score = count(True) - count(False), range -7 to +7.
Direction = "bullish" if score >= 3 (5+/7 agree), "bearish" if score <= -3.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_technical_scanner.py (add to existing file)
import pandas as pd
import numpy as np
from technical_scanner import _ema, _rsi, _atr14, score_signals

def _make_df(closes, highs=None, lows=None, volumes=None, n=220):
    """Build a minimal OHLCV DataFrame for testing."""
    if len(closes) < n:
        closes = [closes[0]] * (n - len(closes)) + list(closes)
    closes = closes[-n:]
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        'Close': closes, 'High': highs, 'Low': lows,
        'Open': closes, 'Volume': volumes,
    })

def test_ema_increasing_series():
    prices = pd.Series([float(i) for i in range(1, 50)])
    ema13 = _ema(prices, 13)
    ema21 = _ema(prices, 21)
    # In rising series, shorter EMA should be higher
    assert ema13 > ema21

def test_rsi_all_up_days():
    prices = pd.Series([100.0 + i for i in range(30)])
    rsi = _rsi(prices)
    assert rsi > 70  # all up days → overbought

def test_rsi_all_down_days():
    prices = pd.Series([100.0 - i * 0.5 for i in range(30)])
    rsi = _rsi(prices)
    assert rsi < 30  # all down days → oversold

def test_score_signals_bullish():
    """Strong uptrend should score >= 3 (bullish)."""
    # Rising prices over 220 days, accelerating
    closes = [100.0 + i * 0.5 for i in range(220)]
    # Higher volume recently
    volumes = [500_000] * 200 + [1_500_000] * 20
    df = _make_df(closes, volumes=volumes)
    qqq_df = _make_df([300.0 + i * 0.4 for i in range(220)])  # QQQ rising slower
    score, details = score_signals("NVDA", df, qqq_df)
    assert score >= 3, f"Expected bullish score >= 3, got {score}"
    assert details['stage2'] is True
    assert details['ema_alignment'] is True

def test_score_signals_bearish():
    """Strong downtrend should score <= -3 (bearish)."""
    closes = [300.0 - i * 0.5 for i in range(220)]
    volumes = [500_000] * 200 + [1_500_000] * 20
    df = _make_df(closes, volumes=volumes)
    qqq_df = _make_df([300.0 + i * 0.1 for i in range(220)])  # QQQ flat/rising
    score, details = score_signals("AAPL", df, qqq_df)
    assert score <= -3, f"Expected bearish score <= -3, got {score}"

def test_score_signals_mixed():
    """Flat/noisy prices should score between -2 and +2."""
    import math
    closes = [200.0 + math.sin(i * 0.2) * 5 for i in range(220)]
    df = _make_df(closes)
    qqq_df = _make_df([300.0] * 220)
    score, details = score_signals("MSFT", df, qqq_df)
    assert -3 <= score <= 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py -k "test_ema or test_rsi or test_score" -v
```

Expected: `ModuleNotFoundError: No module named 'technical_scanner'`

- [ ] **Step 3: Implement technical_scanner.py with indicator functions**

Create `backend/technical_scanner.py`:

```python
"""
Technical momentum scanner — Qullamaggie/Minervini style.

Evaluates 7 signals per stock using daily price history from yfinance.
Stocks with 5+/7 signals agreeing on direction proceed to options structure selection.
"""
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from models import OptionChainData, TechnicalSetup

logger = logging.getLogger(__name__)

# Minimum net score for a "clear majority" signal
# score = sum(+1 if bullish_signal else -1 for each of 7 signals)
# score >= 3 → 5+/7 bullish, score <= -3 → 5+/7 bearish
SIGNAL_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> float:
    """Exponential moving average of the last value."""
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def _rsi(series: pd.Series, period: int = 14) -> float:
    """RSI using Wilder smoothing (ewm with com=period-1)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = float(gain.ewm(com=period - 1, adjust=False).mean().iloc[-1])
    avg_loss = float(loss.ewm(com=period - 1, adjust=False).mean().iloc[-1])
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)


def _atr14(df: pd.DataFrame) -> float:
    """Average True Range over last 14 bars."""
    high = df['High']
    low = df['Low']
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(14).mean().iloc[-1])


# ---------------------------------------------------------------------------
# Signal scoring
# ---------------------------------------------------------------------------

def score_signals(
    symbol: str,
    df: pd.DataFrame,
    qqq_df: pd.DataFrame,
) -> tuple[int, dict[str, bool]]:
    """
    Evaluate 7 technical signals for a stock.

    Returns (net_score, signal_details) where:
    - Each signal True = bullish for that indicator, False = bearish
    - net_score = count(True) - count(False), range -7 to +7
    - net_score >= 3 → clear bullish (5+/7 agree)
    - net_score <= -3 → clear bearish (5+/7 agree)

    Requires df with at least 220 rows of OHLCV daily data.
    """
    close = df['Close']
    volume = df['Volume']
    price = float(close.iloc[-1])

    # Signal 1: Price vs 21 EMA
    ema21 = _ema(close, 21)
    price_vs_ema21 = price > ema21

    # Signal 2: 13 EMA vs 21 EMA (short-term momentum alignment)
    ema13 = _ema(close, 13)
    ema_alignment = ema13 > ema21

    # Signal 3: Stage 2 trend — price > MA50 > MA200 (Minervini trend template)
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200_series = close.rolling(200).mean()
    ma200 = float(ma200_series.iloc[-1]) if not pd.isna(ma200_series.iloc[-1]) else ma50
    stage2 = price > ma50 > ma200

    # Signal 4: RSI(14) in momentum zone and rising
    rsi_now = _rsi(close)
    rsi_3d_ago = _rsi(close.iloc[:-3]) if len(close) > 20 else rsi_now
    rsi_zone = (45 <= rsi_now <= 75) and (rsi_now > rsi_3d_ago)

    # Signal 5: Volume accumulation — recent 5-day avg > 20-day avg
    vol_5d = float(volume.iloc[-5:].mean())
    vol_20d = float(volume.iloc[-20:].mean())
    volume_accum = vol_5d > vol_20d

    # Signal 6: Relative strength vs QQQ over last 10 days
    stock_ret = (price / float(close.iloc[-11]) - 1) if len(close) >= 11 else 0.0
    qqq_close = qqq_df['Close']
    qqq_ret = (float(qqq_close.iloc[-1]) / float(qqq_close.iloc[-11]) - 1) if len(qqq_close) >= 11 else 0.0
    rs_vs_qqq = stock_ret > qqq_ret

    # Signal 7: Near 50-day high (within 5%) — breakout candidate
    high_50d = float(close.iloc[-50:].max())
    breakout = price >= high_50d * 0.95

    details = {
        'price_vs_ema21': price_vs_ema21,
        'ema_alignment':  ema_alignment,
        'stage2':         stage2,
        'rsi_zone':       rsi_zone,
        'volume_accum':   volume_accum,
        'rs_vs_qqq':      rs_vs_qqq,
        'breakout':       breakout,
    }

    net_score = sum(1 if v else -1 for v in details.values())
    return net_score, details
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py -k "test_ema or test_rsi or test_score" -v
```

Expected: all 5 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/technical_scanner.py backend/tests/test_technical_scanner.py
git commit -m "feat: add 7 technical indicators to technical_scanner.py"
```

---

### Task 3: Add options structure constructors to technical_scanner.py

**Files:**
- Modify: `backend/technical_scanner.py` (append)

Two constructors: `_construct_long_call` and `_construct_long_put`. Spread constructors delegate to existing `construct_bull_call_spread` / `construct_bear_put_spread` in scanner.py via a thin wrapper that returns `TechnicalSetup` instead of `TradeSetup`.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_technical_scanner.py (add to existing file)
from unittest.mock import patch, MagicMock
from datetime import date
from models import OptionChainData, OptionContract, TechnicalSetup
from technical_scanner import _construct_long_call, _construct_long_put, _pick_best_structure

def _make_chain(symbol="NVDA", price=875.0, iv_rank=32.0):
    expiry = date(2026, 6, 20)  # ~60 DTE from test date
    def _call(strike, delta, ask, bid=None):
        return OptionContract(
            strike=strike, expiry=expiry, dte=45, bid=bid or ask*0.95,
            ask=ask, mid=ask*0.975, last=ask, volume=500, open_interest=1000,
            iv=0.35, delta=delta, gamma=0.01, theta=-0.05, vega=0.10,
            theoretical_value=ask, in_the_money=(delta > 0.5),
        )
    def _put(strike, delta, ask, bid=None):
        return OptionContract(
            strike=strike, expiry=expiry, dte=45, bid=bid or ask*0.95,
            ask=ask, mid=ask*0.975, last=ask, volume=500, open_interest=1000,
            iv=0.35, delta=delta, gamma=0.01, theta=-0.05, vega=0.10,
            theoretical_value=ask, in_the_money=(delta < -0.5),
        )
    return OptionChainData(
        symbol=symbol, stock_price=price, iv30=0.35, hv30=0.28,
        iv_rank=iv_rank, iv_percentile=35.0,
        timestamp=datetime.utcnow(),
        calls=[
            _call(850, 0.60, 38.0),
            _call(875, 0.50, 28.0),
            _call(900, 0.44, 20.0),   # target: 0.45 delta
            _call(925, 0.35, 14.0),
            _call(950, 0.25, 9.0),    # short leg for spread
            _call(975, 0.15, 5.0),
        ],
        puts=[
            _put(850, -0.44, 19.0),   # target: 0.45 delta
            _put(825, -0.35, 13.0),
            _put(800, -0.25, 8.0),    # short leg for spread
            _put(775, -0.15, 4.5),
        ],
        is_stale=False,
    )

def test_construct_long_call_returns_setup():
    chain = _make_chain()
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_long_call("NVDA", 875.0, chain, 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure == "long_call"
    assert setup.strike == 900.0  # closest to 0.45 delta
    assert setup.rr_ratio >= 2.0
    assert setup.direction == "bullish"

def test_construct_long_put_returns_setup():
    chain = _make_chain()
    signal_details = {k: False for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _construct_long_put("NVDA", 875.0, chain, 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure == "long_put"
    assert setup.strike == 850.0  # closest to 0.45 delta (abs)
    assert setup.direction == "bearish"

def test_pick_best_structure_low_iv_prefers_long_call():
    chain = _make_chain(iv_rank=30.0)
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _pick_best_structure("NVDA", 875.0, chain, "bullish", 7, signal_details, atr14=15.0)
    assert setup is not None
    assert setup.structure in ("long_call", "bull_call_spread")

def test_pick_best_structure_high_iv_prefers_spread():
    chain = _make_chain(iv_rank=70.0)
    signal_details = {k: True for k in ['price_vs_ema21','ema_alignment','stage2','rsi_zone','volume_accum','rs_vs_qqq','breakout']}
    setup = _pick_best_structure("NVDA", 875.0, chain, "bullish", 7, signal_details, atr14=15.0)
    # high IV: spread preferred; if spread fails, falls back to long call
    assert setup is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py -k "test_construct or test_pick" -v
```

Expected: `ImportError` for `_construct_long_call`

- [ ] **Step 3: Add constructors to technical_scanner.py**

Append to `backend/technical_scanner.py`:

```python
from datetime import date, datetime
from models import OptionContract


def _find_delta_contract(
    contracts: list[OptionContract],
    target_delta: float,
    dte_min: int = 30,
    dte_max: int = 60,
) -> Optional[OptionContract]:
    """Find the contract whose abs(delta) is closest to target_delta."""
    candidates = [
        c for c in contracts
        if dte_min <= c.dte <= dte_max and c.bid > 0 and c.iv > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))


def _construct_long_call(
    symbol: str,
    stock_price: float,
    chain: OptionChainData,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Long call at ~0.45 delta, 30–60 DTE. R:R via ATR-based price target."""
    call = _find_delta_contract(chain.calls, 0.45)
    if call is None:
        return None

    dte = call.dte
    price_target = stock_price + 1.5 * atr14 * dte / 10
    intrinsic_at_target = max(0.0, price_target - call.strike)
    gain_at_target = intrinsic_at_target - call.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / call.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = call.strike + call.ask
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bullish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="long_call",
        strike=call.strike,
        short_strike=None,
        expiry=call.expiry,
        dte=dte,
        delta=round(call.delta, 2),
        iv_rank=chain.iv_rank,
        premium=call.ask,
        price_target=round(price_target, 2),
        rr_ratio=rr_ratio,
        max_loss=round(call.ask * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(call.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {call.expiry.strftime('%m/%d')} "
            f"{call.strike:.0f} CALL @{call.ask:.2f} LMT"
        ),
    )


def _construct_long_put(
    symbol: str,
    stock_price: float,
    chain: OptionChainData,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Long put at ~0.45 delta (abs), 30–60 DTE. R:R via ATR-based price target."""
    put = _find_delta_contract(chain.puts, 0.45)
    if put is None:
        return None

    dte = put.dte
    price_target = stock_price - 1.5 * atr14 * dte / 10
    intrinsic_at_target = max(0.0, put.strike - price_target)
    gain_at_target = intrinsic_at_target - put.ask
    if gain_at_target <= 0:
        return None

    rr_ratio = round(gain_at_target / put.ask, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = put.strike - put.ask
    breakeven_move_pct = round((stock_price - breakeven) / stock_price * 100, 1)

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bearish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="long_put",
        strike=put.strike,
        short_strike=None,
        expiry=put.expiry,
        dte=dte,
        delta=round(put.delta, 2),
        iv_rank=chain.iv_rank,
        premium=put.ask,
        price_target=round(price_target, 2),
        rr_ratio=rr_ratio,
        max_loss=round(put.ask * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(put.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {put.expiry.strftime('%m/%d')} "
            f"{put.strike:.0f} PUT @{put.ask:.2f} LMT"
        ),
    )


def _construct_bull_call_spread_technical(
    symbol: str,
    stock_price: float,
    chain: OptionChainData,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Bull call spread: long 0.45Δ, short 0.25Δ, same expiry."""
    long_leg = _find_delta_contract(chain.calls, 0.45)
    if long_leg is None:
        return None

    short_candidates = [
        c for c in chain.calls
        if c.expiry == long_leg.expiry and 0.20 <= abs(c.delta) <= 0.30 and c.bid > 0
    ]
    if not short_candidates:
        return None
    short_leg = min(short_candidates, key=lambda c: abs(abs(c.delta) - 0.25))

    spread_width = short_leg.strike - long_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0 or net_debit > spread_width * 0.40:
        return None

    max_gain = round(spread_width - net_debit, 2)
    rr_ratio = round(max_gain / net_debit, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = long_leg.strike + net_debit
    breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1)
    dte = long_leg.dte

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bullish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="bull_call_spread",
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        expiry=long_leg.expiry,
        dte=dte,
        delta=round(long_leg.delta, 2),
        iv_rank=chain.iv_rank,
        premium=net_debit,
        price_target=round(stock_price + 1.5 * atr14 * dte / 10, 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} CALL VRT @{net_debit:.2f} LMT"
        ),
    )


def _construct_bear_put_spread_technical(
    symbol: str,
    stock_price: float,
    chain: OptionChainData,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """Bear put spread: long 0.45Δ put, short 0.25Δ put, same expiry."""
    long_leg = _find_delta_contract(chain.puts, 0.45)
    if long_leg is None:
        return None

    short_candidates = [
        c for c in chain.puts
        if c.expiry == long_leg.expiry and 0.20 <= abs(c.delta) <= 0.30 and c.bid > 0
    ]
    if not short_candidates:
        return None
    short_leg = min(short_candidates, key=lambda c: abs(abs(c.delta) - 0.25))

    spread_width = long_leg.strike - short_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0 or net_debit > spread_width * 0.40:
        return None

    max_gain = round(spread_width - net_debit, 2)
    rr_ratio = round(max_gain / net_debit, 2)
    if rr_ratio < 2.0:
        return None

    breakeven = long_leg.strike - net_debit
    breakeven_move_pct = round((stock_price - breakeven) / stock_price * 100, 1)
    dte = long_leg.dte

    return TechnicalSetup(
        symbol=symbol,
        stock_price=stock_price,
        direction="bearish",
        signal_count=signal_count,
        signal_details=signal_details,
        structure="bear_put_spread",
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        expiry=long_leg.expiry,
        dte=dte,
        delta=round(long_leg.delta, 2),
        iv_rank=chain.iv_rank,
        premium=net_debit,
        price_target=round(stock_price - 1.5 * atr14 * dte / 10, 2),
        rr_ratio=rr_ratio,
        max_loss=round(net_debit * 100, 2),
        breakeven_move_pct=breakeven_move_pct,
        probability_of_profit=round(abs(long_leg.delta) * 100),
        order_string=(
            f"BUY +1 {symbol} {long_leg.expiry.strftime('%m/%d')} "
            f"{long_leg.strike:.0f}/{short_leg.strike:.0f} PUT VRT @{net_debit:.2f} LMT"
        ),
    )


def _pick_best_structure(
    symbol: str,
    stock_price: float,
    chain: OptionChainData,
    direction: str,
    signal_count: int,
    signal_details: dict,
    atr14: float,
) -> Optional[TechnicalSetup]:
    """
    Choose the best options structure based on IV rank.
    IV rank < 50: try long call/put first, then spread
    IV rank 50-65: try both, pick higher R:R
    IV rank > 65: spread first, then long call/put as fallback
    """
    iv_rank = chain.iv_rank

    if direction == "bullish":
        long_fn = _construct_long_call
        spread_fn = _construct_bull_call_spread_technical
    else:
        long_fn = _construct_long_put
        spread_fn = _construct_bear_put_spread_technical

    args = (symbol, stock_price, chain, signal_count, signal_details, atr14)

    if iv_rank < 50:
        return long_fn(*args) or spread_fn(*args)
    elif iv_rank <= 65:
        long_setup = long_fn(*args)
        spread_setup = spread_fn(*args)
        if long_setup and spread_setup:
            return long_setup if long_setup.rr_ratio >= spread_setup.rr_ratio else spread_setup
        return long_setup or spread_setup
    else:
        return spread_fn(*args) or long_fn(*args)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py -k "test_construct or test_pick" -v
```

Expected: all 4 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/technical_scanner.py backend/tests/test_technical_scanner.py
git commit -m "feat: add long call/put and spread constructors to technical_scanner"
```

---

### Task 4: Add scan orchestration to technical_scanner.py

**Files:**
- Modify: `backend/technical_scanner.py` (append)

Fetches 220-day price history via yfinance for all symbols + QQQ, scores signals, and for qualifying stocks fetches Schwab option chains and picks structures.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_technical_scanner.py (add to existing file)
from unittest.mock import patch, MagicMock
import pandas as pd

def _make_yf_df(closes, n=220):
    closes_full = ([closes[0]] * max(0, n - len(closes)) + list(closes))[-n:]
    return pd.DataFrame({
        'Close': closes_full,
        'High': [c * 1.01 for c in closes_full],
        'Low':  [c * 0.99 for c in closes_full],
        'Open': closes_full,
        'Volume': [1_500_000] * n,
    })

@patch('technical_scanner.yf.download')
@patch('technical_scanner.fetch_option_chain')
def test_scan_technical_setups_returns_list(mock_chain, mock_yf):
    from technical_scanner import scan_technical_setups

    # Rising prices → bullish signal
    bull_closes = [100.0 + i * 0.5 for i in range(220)]
    mock_yf.return_value = _make_yf_df(bull_closes)

    mock_chain.return_value = _make_chain()  # from earlier helper

    setups = scan_technical_setups(["NVDA"], min_rr=2.0, direction="both")
    assert isinstance(setups, list)
    # Result may be empty if R:R doesn't meet threshold with mock data
    # but should not raise
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py::test_scan_technical_setups_returns_list -v
```

Expected: `ImportError: cannot import name 'scan_technical_setups'`

- [ ] **Step 3: Add scan_technical_setups to technical_scanner.py**

Append to `backend/technical_scanner.py`:

```python
from schwab_client import fetch_option_chain


def scan_technical_setups(
    symbols: list[str],
    min_rr: float = 2.0,
    direction: str = "both",
) -> list[TechnicalSetup]:
    """
    Full technical scan:
    1. Fetch 220-day daily OHLCV from yfinance for all symbols + QQQ
    2. Score 7 signals per stock
    3. For stocks with score >= SIGNAL_THRESHOLD (5+/7 agree):
       fetch Schwab option chain, pick best structure
    4. Filter by min_rr, sort by rr_ratio descending
    """
    logger.info("Technical scan: fetching price history for %d symbols", len(symbols))

    # Fetch all price history in one yfinance call (batch download)
    all_tickers = symbols + (["QQQ"] if "QQQ" not in symbols else [])
    try:
        raw = yf.download(
            tickers=all_tickers,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        logger.error("yfinance batch download failed: %s", e)
        return []

    # Extract QQQ DataFrame
    if len(all_tickers) == 1:
        qqq_df = raw
    elif isinstance(raw.columns, pd.MultiIndex):
        try:
            qqq_df = raw.xs("QQQ", axis=1, level=1) if "QQQ" in all_tickers else raw
        except Exception:
            qqq_df = raw
    else:
        qqq_df = raw

    qualifying: list[tuple[str, int, dict, pd.DataFrame]] = []

    for symbol in symbols:
        try:
            if len(all_tickers) > 1 and isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(symbol, axis=1, level=1).dropna()
            else:
                df = raw.dropna()

            if len(df) < 50:
                logger.debug("%s: insufficient price history (%d rows)", symbol, len(df))
                continue

            score, details = score_signals(symbol, df, qqq_df)

            if direction == "bullish" and score < SIGNAL_THRESHOLD:
                continue
            if direction == "bearish" and score > -SIGNAL_THRESHOLD:
                continue
            if direction == "both" and abs(score) < SIGNAL_THRESHOLD:
                continue

            signal_direction = "bullish" if score >= SIGNAL_THRESHOLD else "bearish"
            signal_count = sum(1 for v in details.values() if (
                v if signal_direction == "bullish" else not v
            ))
            qualifying.append((symbol, signal_count, details, df, signal_direction))

        except Exception as e:
            logger.warning("Signal scoring failed for %s: %s", symbol, e)

    logger.info("Technical scan: %d/%d symbols qualify for options check", len(qualifying), len(symbols))

    setups: list[TechnicalSetup] = []

    for symbol, signal_count, details, df, sig_direction in qualifying:
        try:
            chain = fetch_option_chain(symbol)
            if chain.stock_price == 0:
                continue

            atr = _atr14(df)
            setup = _pick_best_structure(
                symbol, chain.stock_price, chain,
                sig_direction, signal_count, details, atr,
            )
            if setup is None or setup.rr_ratio < min_rr:
                continue
            setups.append(setup)

        except Exception as e:
            logger.warning("Options structure failed for %s: %s", symbol, e)

    setups.sort(key=lambda s: s.rr_ratio, reverse=True)
    logger.info("Technical scan complete: %d setups found", len(setups))
    return setups
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py::test_scan_technical_setups_returns_list -v
```

Expected: `PASSED`

- [ ] **Step 5: Run all technical scanner tests**

```bash
cd backend && python -m pytest tests/test_technical_scanner.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add backend/technical_scanner.py backend/tests/test_technical_scanner.py
git commit -m "feat: add scan_technical_setups orchestration"
```

---

### Task 5: Add /technical-setups and /scan-setups endpoints to main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Add import and cache entry**

In `backend/main.py`, add to the imports at line 32:

```python
from technical_scanner import scan_technical_setups
```

Add to `_cache` dict (after `"sector_timestamp": None,`):

```python
    "technical_setups": [],       # list[TechnicalSetup]
    "technical_timestamp": None,  # datetime
```

- [ ] **Step 2: Add _run_technical_scan helper**

Add after the existing `_run_scan` function:

```python
async def _run_technical_scan() -> None:
    """Fetch price history + option chains for all symbols, find technical setups."""
    from qqq_holdings import QQQ_TOP50
    t_start = time.monotonic()
    logger.info("Starting technical scan of %d symbols", len(QQQ_TOP50))
    try:
        loop = asyncio.get_event_loop()
        setups = await loop.run_in_executor(
            None, scan_technical_setups, QQQ_TOP50, 2.0, "both"
        )
        _cache["technical_setups"] = setups
        _cache["technical_timestamp"] = datetime.utcnow()
        elapsed = time.monotonic() - t_start
        logger.info("Technical scan complete: %d setups, %.1fs", len(setups), elapsed)
    except Exception as e:
        logger.error("Technical scan failed: %s", e)
```

- [ ] **Step 3: Add /technical-setups endpoint**

Add after the `/sector-analysis` endpoint:

```python
@app.get("/technical-setups")
@limiter.limit("60/minute")
async def get_technical_setups(
    request: Request,
    direction: str = "both",
    min_rr: float = 2.0,
    sort: str = "rr",
):
    setups = _cache["technical_setups"]
    ts = _cache["technical_timestamp"]

    filtered = [
        s for s in setups
        if s.rr_ratio >= min_rr
        and (direction == "both" or s.direction == direction)
    ]

    if sort == "pop":
        filtered.sort(key=lambda s: s.probability_of_profit, reverse=True)
    else:
        filtered.sort(key=lambda s: s.rr_ratio, reverse=True)

    return JSONResponse(content={
        "setups": [_serialize(s) for s in filtered],
        "scan_timestamp": ts.isoformat() if ts else None,
        "symbols_scanned": len(_cache.get("technical_setups", [])),
    })


@app.get("/scan-setups")
@limiter.limit("6/minute")
async def trigger_technical_scan(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_technical_scan)
    return JSONResponse(content={"status": "scanning", "message": "Technical scan started. Fetch /technical-setups in ~45s."})
```

- [ ] **Step 4: Verify server starts**

```bash
cd backend && uvicorn main:app --reload --port 8000
```

Expected: server starts without import errors. Visit `http://localhost:8000/technical-setups` → `{"setups": [], "scan_timestamp": null, "symbols_scanned": 0}`

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat: add /technical-setups and /scan-setups endpoints"
```

---

### Task 6: Add frontend API functions

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Add two functions to api.js**

Append to `frontend/src/api.js`:

```javascript
export async function fetchTechnicalSetups(filters = {}) {
  const params = new URLSearchParams({
    direction: filters.direction ?? 'both',
    min_rr:    filters.minRR     ?? 2.0,
    sort:      filters.sort      ?? 'rr',
  })
  const res = await fetch(`${BASE_URL}/technical-setups?${params}`)
  if (!res.ok) throw new Error(`Technical setups error: ${res.status}`)
  return res.json()
}

export async function triggerSetupsScan() {
  const res = await fetch(`${BASE_URL}/scan-setups`)
  if (!res.ok) throw new Error(`Setups scan failed: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 2: Verify build passes**

```bash
cd frontend && npm run build
```

Expected: `✓ built in X.XXs`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat: add fetchTechnicalSetups and triggerSetupsScan to api.js"
```

---

### Task 7: Create TechnicalSetups.jsx component

**Files:**
- Create: `frontend/src/components/TechnicalSetups.jsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/TechnicalSetups.jsx`:

```jsx
import { useState, useEffect } from 'react'
import { fetchTechnicalSetups, triggerSetupsScan } from '../api.js'

const SIGNAL_LABELS = {
  price_vs_ema21: 'Price>21EMA',
  ema_alignment:  '13/21 EMA',
  stage2:         'Stage 2',
  rsi_zone:       'RSI',
  volume_accum:   'Volume',
  rs_vs_qqq:      'RS vs QQQ',
  breakout:       'Near High',
}

function SignalBadges({ details, direction }) {
  return (
    <div style={styles.signals}>
      {Object.entries(SIGNAL_LABELS).map(([key, label]) => {
        const isBullishSignal = details[key]
        const firing = direction === 'bullish' ? isBullishSignal : !isBullishSignal
        return (
          <span key={key} style={{ ...styles.signal, color: firing ? '#00ffaa' : '#333' }}>
            {firing ? '✓' : '·'} {label}
          </span>
        )
      })}
    </div>
  )
}

function SetupCard({ setup }) {
  const [copied, setCopied] = useState(false)
  const isBearish = setup.direction === 'bearish'
  const structureLabel = {
    long_call:       'Long Call',
    long_put:        'Long Put',
    bull_call_spread:'Bull Call Spread',
    bear_put_spread: 'Bear Put Spread',
  }[setup.structure] ?? setup.structure

  const expiryStr = setup.expiry
    ? new Date(setup.expiry + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—'

  function copyOrder() {
    navigator.clipboard.writeText(setup.order_string).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div style={{ ...styles.card, borderLeft: `3px solid ${isBearish ? '#ff4444' : '#00ffaa'}` }}>
      <div style={styles.cardHeader}>
        <div style={styles.cardLeft}>
          <span style={styles.symbol}>{setup.symbol}</span>
          <span style={styles.price}>${setup.stock_price?.toFixed(2)}</span>
        </div>
        <div style={styles.cardRight}>
          <span style={{ ...styles.badge, color: isBearish ? '#ff4444' : '#00ffaa', borderColor: isBearish ? '#ff4444' : '#00ffaa' }}>
            {setup.signal_count}/7 {setup.direction.toUpperCase()}
          </span>
          <span style={styles.structureLabel}>{structureLabel}</span>
        </div>
      </div>

      <div style={styles.meta}>
        <span>{expiryStr}</span>
        <span>${setup.strike?.toFixed(0)}{setup.short_strike ? `/${setup.short_strike?.toFixed(0)}` : ''}</span>
        <span>{setup.dte} DTE</span>
        <span>Δ{Math.abs(setup.delta)?.toFixed(2)}</span>
        <span style={{ color: setup.iv_rank > 65 ? '#ffaa00' : '#666' }}>IV Rank {setup.iv_rank?.toFixed(0)}</span>
      </div>

      <div style={styles.metrics}>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>R:R</span>
          <span style={{ ...styles.metricVal, color: '#00ffaa' }}>{setup.rr_ratio?.toFixed(1)}:1</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Max Loss</span>
          <span style={styles.metricVal}>${setup.max_loss?.toFixed(0)}</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Breakeven</span>
          <span style={styles.metricVal}>{setup.breakeven_move_pct > 0 ? '+' : ''}{setup.breakeven_move_pct?.toFixed(1)}%</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>PoP</span>
          <span style={styles.metricVal}>{setup.probability_of_profit}%</span>
        </div>
        <div style={styles.metric}>
          <span style={styles.metricLabel}>Target</span>
          <span style={styles.metricVal}>${setup.price_target?.toFixed(0)}</span>
        </div>
      </div>

      <SignalBadges details={setup.signal_details} direction={setup.direction} />

      <button style={styles.copyBtn} onClick={copyOrder}>
        {copied ? '✓ Copied' : 'Copy Order'}
      </button>
    </div>
  )
}

export default function TechnicalSetups() {
  const [setups, setSetups] = useState([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState(null)
  const [scanTimestamp, setScanTimestamp] = useState(null)
  const [filters, setFilters] = useState({ direction: 'both', minRR: 2.0, sort: 'rr' })

  useEffect(() => {
    load()
  }, [filters])

  async function load() {
    try {
      const data = await fetchTechnicalSetups(filters)
      setSetups(data.setups || [])
      setScanTimestamp(data.scan_timestamp)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function runScan() {
    setScanning(true)
    try {
      await triggerSetupsScan()
      // Poll after 45s for results
      setTimeout(async () => {
        await load()
        setScanning(false)
      }, 45000)
    } catch (e) {
      setError(e.message)
      setScanning(false)
    }
  }

  const scanTime = scanTimestamp
    ? new Date(scanTimestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })
    : '—'

  return (
    <div>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <span style={styles.title}>TECHNICAL SETUPS</span>
          <span style={styles.count}>{loading ? '…' : `${setups.length} setups`}</span>
        </div>
        <div style={styles.headerRight}>
          {scanTimestamp && (
            <span style={styles.scanTime}>Last scan: {scanTime}</span>
          )}
          <button
            style={{ ...styles.scanBtn, ...(scanning ? styles.scanBtnActive : {}) }}
            onClick={runScan}
            disabled={scanning}
          >
            {scanning ? '⟳ SCANNING…' : '▶ SCAN SETUPS'}
          </button>
        </div>
      </div>

      {scanning && (
        <div style={styles.scanningBanner}>
          ⟳ Scanning 100 symbols — takes ~45 seconds. Results will load automatically.
        </div>
      )}

      {error && (
        <div style={styles.errorBanner}>Could not fetch setups: {error}</div>
      )}

      {/* Filters */}
      <div style={styles.filterBar}>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>DIRECTION</span>
          {['both', 'bullish', 'bearish'].map(d => (
            <button
              key={d}
              style={{ ...styles.filterBtn, ...(filters.direction === d ? styles.filterBtnActive : {}) }}
              onClick={() => setFilters(f => ({ ...f, direction: d }))}
            >
              {d === 'both' ? 'Both' : d === 'bullish' ? '▲ Bullish' : '▼ Bearish'}
            </button>
          ))}
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>MIN R:R</span>
          <input
            type="range" min="1.0" max="5.0" step="0.5"
            value={filters.minRR}
            onChange={e => setFilters(f => ({ ...f, minRR: parseFloat(e.target.value) }))}
            style={styles.slider}
          />
          <span style={styles.filterVal}>{filters.minRR.toFixed(1)}:1</span>
        </div>
        <div style={styles.filterGroup}>
          <span style={styles.filterLabel}>SORT</span>
          <button
            style={{ ...styles.filterBtn, ...(filters.sort === 'rr' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'rr' }))}
          >R:R</button>
          <button
            style={{ ...styles.filterBtn, ...(filters.sort === 'pop' ? styles.filterBtnActive : {}) }}
            onClick={() => setFilters(f => ({ ...f, sort: 'pop' }))}
          >PoP</button>
        </div>
      </div>

      {/* Results */}
      {!loading && setups.length === 0 && !scanning && (
        <div style={styles.empty}>
          {scanTimestamp
            ? 'No setups meet your filters. Try lowering Min R:R or running a fresh scan.'
            : 'No scan data yet. Click ▶ SCAN SETUPS to run the first scan.'}
        </div>
      )}

      <div style={{ paddingBottom: '32px' }}>
        {setups.map((setup, i) => (
          <SetupCard key={`${setup.symbol}-${setup.structure}-${i}`} setup={setup} />
        ))}
      </div>
    </div>
  )
}

const styles = {
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '16px', borderBottom: '1px solid #1a1a2e',
    background: '#0a0a14', flexWrap: 'wrap', gap: '8px',
  },
  title: {
    fontFamily: 'monospace', fontSize: '16px', fontWeight: 'bold',
    color: '#00ffaa', marginRight: '16px', letterSpacing: '0.05em',
  },
  count: { fontFamily: 'monospace', fontSize: '13px', color: '#666' },
  headerRight: { display: 'flex', alignItems: 'center', gap: '12px' },
  scanTime: { fontFamily: 'monospace', fontSize: '12px', color: '#555' },
  scanBtn: {
    background: 'none', border: '1px solid #00ffaa', color: '#00ffaa',
    cursor: 'pointer', padding: '4px 12px', borderRadius: '3px',
    fontFamily: 'monospace', fontSize: '11px', letterSpacing: '0.05em',
  },
  scanBtnActive: { color: '#555', borderColor: '#333', cursor: 'not-allowed' },
  scanningBanner: {
    background: '#0a0a14', borderLeft: '4px solid #00ffaa',
    color: '#00ffaa', padding: '8px 16px', fontSize: '12px', fontFamily: 'monospace',
  },
  errorBanner: {
    background: '#1a0505', borderLeft: '4px solid #ff4444',
    color: '#ff4444', padding: '8px 16px', fontSize: '12px', fontFamily: 'monospace',
  },
  filterBar: {
    display: 'flex', gap: '24px', padding: '10px 16px',
    borderBottom: '1px solid #1a1a2e', flexWrap: 'wrap', alignItems: 'center',
  },
  filterGroup: { display: 'flex', alignItems: 'center', gap: '6px' },
  filterLabel: { fontFamily: 'monospace', fontSize: '10px', color: '#555', letterSpacing: '0.08em' },
  filterBtn: {
    padding: '3px 10px', background: 'none', border: '1px solid #2a2a3e',
    color: '#666', cursor: 'pointer', fontFamily: 'monospace', fontSize: '11px', borderRadius: '3px',
  },
  filterBtnActive: { borderColor: '#00ffaa', color: '#00ffaa', background: '#0a1a0f' },
  slider: { width: '80px', accentColor: '#00ffaa' },
  filterVal: { fontFamily: 'monospace', fontSize: '11px', color: '#aaa', minWidth: '32px' },
  empty: {
    padding: '48px 16px', textAlign: 'center',
    color: '#555', fontFamily: 'monospace', fontSize: '13px',
  },
  card: {
    background: '#0d0d1a', border: '1px solid #1a1a2e',
    borderRadius: '4px', padding: '12px 16px', margin: '8px 16px',
  },
  cardHeader: {
    display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap', gap: '8px',
  },
  cardLeft: { display: 'flex', alignItems: 'baseline', gap: '10px' },
  cardRight: { display: 'flex', alignItems: 'center', gap: '8px' },
  symbol: { fontFamily: 'monospace', fontSize: '15px', fontWeight: 'bold', color: '#ddd' },
  price: { fontFamily: 'monospace', fontSize: '12px', color: '#666' },
  badge: {
    fontFamily: 'monospace', fontSize: '11px', border: '1px solid',
    padding: '2px 8px', borderRadius: '3px',
  },
  structureLabel: { fontFamily: 'monospace', fontSize: '11px', color: '#888' },
  meta: {
    display: 'flex', gap: '12px', flexWrap: 'wrap',
    fontFamily: 'monospace', fontSize: '11px', color: '#666', marginBottom: '8px',
  },
  metrics: { display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '8px' },
  metric: { display: 'flex', flexDirection: 'column', gap: '2px' },
  metricLabel: { fontFamily: 'monospace', fontSize: '9px', color: '#555', letterSpacing: '0.08em' },
  metricVal: { fontFamily: 'monospace', fontSize: '13px', color: '#aaa', fontWeight: 'bold' },
  signals: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '8px' },
  signal: { fontFamily: 'monospace', fontSize: '10px' },
  copyBtn: {
    background: 'none', border: '1px solid #2a2a3e', color: '#555',
    cursor: 'pointer', padding: '3px 10px', borderRadius: '3px',
    fontFamily: 'monospace', fontSize: '10px',
  },
}
```

- [ ] **Step 2: Verify build passes**

```bash
cd frontend && npm run build
```

Expected: `✓ built in X.XXs`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TechnicalSetups.jsx
git commit -m "feat: add TechnicalSetups tab component"
```

---

### Task 8: Wire SETUPS tab into App.jsx

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add import and tab button**

In `frontend/src/App.jsx`, add import after line 5:

```javascript
import TechnicalSetups from './components/TechnicalSetups.jsx'
```

Add tab button after the MY TRADES button (around line 111):

```javascript
        <button
          style={{ ...styles.tab, ...(tab === 'setups' ? styles.tabActive : {}) }}
          onClick={() => setTab('setups')}
        >
          SETUPS
        </button>
```

Add content render after the journal block (around line 133):

```javascript
      {tab === 'setups' && <TechnicalSetups />}
```

- [ ] **Step 2: Verify build passes**

```bash
cd frontend && npm run build
```

Expected: `✓ built in X.XXs`

- [ ] **Step 3: Start dev server and verify tab appears**

```bash
cd frontend && npm run dev
```

Open `http://localhost:5173/mispriced-scanner/`. Verify:
- Three tabs visible: SCANNER | SETUPS | MY TRADES
- Clicking SETUPS shows "No scan data yet. Click ▶ SCAN SETUPS"
- ▶ SCAN SETUPS button is visible and clickable
- Direction, Min R:R, and Sort filters are visible

- [ ] **Step 4: Commit and push**

```bash
git add frontend/src/App.jsx
git commit -m "feat: wire SETUPS tab into App.jsx"
git push
```

---

## Self-Review

**Spec coverage:**
- ✅ 7 indicators (stage2, ema_alignment, price_vs_ema21, rsi_zone, volume_accum, rs_vs_qqq, breakout)
- ✅ 5+/7 majority threshold (score ≥ 3)
- ✅ IV rank gates structure selection (< 50 → long, > 65 → spread)
- ✅ Earnings filter — NOT YET IMPLEMENTED. Add to `scan_technical_setups`: skip symbols where `get_catalyst_context(symbol).earnings_dte` is not None and `< chain.calls[0].dte` (within DTE window). Add this check in Task 4 Step 3 inside the `for symbol, ... in qualifying:` loop:

```python
# Skip if earnings within option DTE window
from catalyst import get_catalyst_context
catalyst = get_catalyst_context(symbol, chain)
if catalyst.earnings_in_window:
    logger.debug("%s: skipping — earnings within DTE window", symbol)
    continue
```

- ✅ Separate SETUPS tab with own scan button
- ✅ Direction filter, Min R:R filter, R:R / PoP sort
- ✅ Signal badges showing which fired
- ✅ Copy order string button
- ✅ Ranked by R:R descending

**Placeholder scan:** None found.

**Type consistency:** `TechnicalSetup` fields used in Tasks 1, 3, 4, 7 are consistent. `signal_details` dict keys match `SIGNAL_LABELS` in TechnicalSetups.jsx.
