# Sector Rotation Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ROTATION column to the sector table showing a single arrow verdict (↑↑/↑/→/↓/↓↓) computed from RS momentum, volume accumulation/distribution, and Schwab options flow.

**Architecture:** Add `rotation: str` to `SectorData`, add signal helpers + Schwab flow function to `sector_analysis.py`, update `get_sector_analysis()` to compute all three signals and populate `rotation`, add one column to `SectorPanel.jsx`. Degrades gracefully if Schwab is unavailable (uses 2 yfinance signals instead of 3).

**Tech Stack:** Python/FastAPI, yfinance (price/volume), Schwab API (options OI), React

---

## File Structure

| File | Change |
|---|---|
| `backend/models.py` | Add `rotation: str = "→"` to `SectorData` |
| `backend/sector_analysis.py` | Add helpers, fix 2 pre-existing bugs, update `get_sector_analysis()` |
| `backend/conftest.py` | New — adds `backend/` to pytest sys.path |
| `backend/tests/__init__.py` | New — empty, marks tests as package |
| `backend/tests/test_sector_rotation.py` | New — unit tests for all signal helpers |
| `frontend/src/components/SectorPanel.jsx` | Add ROTATION header + cell |

---

### Task 1: Add `rotation` field to `SectorData` + test scaffolding

**Files:**
- Modify: `backend/models.py`
- Create: `backend/conftest.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_sector_rotation.py`

- [ ] **Step 1: Create test infrastructure**

```bash
mkdir -p backend/tests
touch backend/tests/__init__.py
```

Create `backend/conftest.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
```

- [ ] **Step 2: Write failing tests**

Create `backend/tests/test_sector_rotation.py`:

```python
from models import SectorData


def make_sector(**kwargs):
    defaults = dict(
        etf="XLK", name="Technology",
        return_1w=1.0, return_4w=2.0, return_12w=3.0,
        return_vs_spy_1w=0.5, return_vs_spy_4w=1.0, return_vs_spy_12w=1.5,
        rs_score=75.0, trend_direction="improving", classification="bullish",
    )
    defaults.update(kwargs)
    return SectorData(**defaults)


def test_sector_data_accepts_rotation_field():
    s = make_sector(rotation="↑↑")
    assert s.rotation == "↑↑"


def test_sector_data_rotation_defaults_to_neutral():
    s = make_sector()
    assert s.rotation == "→"
```

- [ ] **Step 3: Run to verify failure**

```bash
cd backend && python -m pytest tests/test_sector_rotation.py -v
```

Expected: `TypeError: SectorData.__init__() got an unexpected keyword argument 'rotation'`

- [ ] **Step 4: Add `rotation` field to `SectorData` in `backend/models.py`**

Find (lines 147–159):
```python
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

Replace with:
```python
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
    rotation: str = "→"        # ↑↑ / ↑ / → / ↓ / ↓↓ (3-signal agreement)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_sector_rotation.py -v
```

Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/conftest.py backend/tests/__init__.py backend/tests/test_sector_rotation.py
git commit -m "feat: add rotation field to SectorData"
```

---

### Task 2: Signal helper functions + tests

**Files:**
- Modify: `backend/sector_analysis.py`
- Modify: `backend/tests/test_sector_rotation.py`

The helpers to add: `_score_to_arrow`, `_rs_momentum_vote`, `_volume_vote`, `_get_sector_flow`. Also add a conditional import of `fetch_option_chain` at the top of `sector_analysis.py`.

- [ ] **Step 1: Write failing tests for all helpers**

Append to `backend/tests/test_sector_rotation.py`:

```python
from unittest.mock import patch, MagicMock
import sector_analysis
from sector_analysis import _score_to_arrow, _rs_momentum_vote, _volume_vote


# --- _score_to_arrow ---

def test_score_to_arrow_strong_up():
    assert _score_to_arrow(3) == "↑↑"
    assert _score_to_arrow(2) == "↑↑"

def test_score_to_arrow_up():
    assert _score_to_arrow(1) == "↑"

def test_score_to_arrow_neutral():
    assert _score_to_arrow(0) == "→"

def test_score_to_arrow_down():
    assert _score_to_arrow(-1) == "↓"

def test_score_to_arrow_strong_down():
    assert _score_to_arrow(-2) == "↓↓"
    assert _score_to_arrow(-3) == "↓↓"


# --- _rs_momentum_vote ---

def test_rs_momentum_vote_bullish():
    assert _rs_momentum_vote(70.0, 60.0) == 1   # delta +10 > 5

def test_rs_momentum_vote_bearish():
    assert _rs_momentum_vote(50.0, 62.0) == -1  # delta -12 < -5

def test_rs_momentum_vote_neutral():
    assert _rs_momentum_vote(55.0, 52.0) == 0   # delta +3, within ±5


# --- _volume_vote ---

def test_volume_vote_accumulation():
    # vol_5d=200, vol_20d=125 → ratio=1.6 > 1.3; price up
    volumes = [100] * 15 + [200] * 5
    prices = [100.0] * 5 + [110.0]   # prices[-6]=100, prices[-1]=110
    assert _volume_vote(volumes, prices) == 1

def test_volume_vote_distribution():
    # Same high volume, price down
    volumes = [100] * 15 + [200] * 5
    prices = [110.0] * 5 + [100.0]   # prices[-6]=110, prices[-1]=100
    assert _volume_vote(volumes, prices) == -1

def test_volume_vote_neutral_low_volume():
    volumes = [100] * 20              # ratio=1.0 < 1.3
    prices = [100.0] * 5 + [105.0]
    assert _volume_vote(volumes, prices) == 0

def test_volume_vote_insufficient_data():
    assert _volume_vote([100] * 10, [100.0] * 6) == 0  # <20 volume days
    assert _volume_vote([100] * 20, [100.0] * 3) == 0  # <6 price days


# --- _get_sector_flow ---

def _make_chain(call_oi: int, put_oi: int, dte: int = 45) -> MagicMock:
    call = MagicMock(dte=dte, open_interest=call_oi)
    put = MagicMock(dte=dte, open_interest=put_oi)
    chain = MagicMock(stock_price=100.0, calls=[call], puts=[put])
    return chain

def test_get_sector_flow_call_biased():
    # put/call ratio = 700/1000 = 0.7 < 0.8 → +1
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 700)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 1

def test_get_sector_flow_put_biased():
    # put/call ratio = 1300/1000 = 1.3 > 1.2 → -1
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 1300)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == -1

def test_get_sector_flow_neutral():
    # put/call ratio = 1.0 → 0
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 1000)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 0

def test_get_sector_flow_no_schwab():
    with patch.object(sector_analysis, 'fetch_option_chain', None):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes == {}

def test_get_sector_flow_exception_returns_zero():
    with patch('sector_analysis.fetch_option_chain', side_effect=Exception("timeout")):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd backend && python -m pytest tests/test_sector_rotation.py -k "arrow or momentum or volume or flow" -v
```

Expected: `ImportError: cannot import name '_score_to_arrow' from 'sector_analysis'`

- [ ] **Step 3: Add conditional import at top of `backend/sector_analysis.py`**

After `import yfinance as yf` (line 5), add:

```python
try:
    from schwab_client import fetch_option_chain
except Exception:
    fetch_option_chain = None
```

- [ ] **Step 4: Add helper functions to `backend/sector_analysis.py`**

After the `_safe` function (after line 65), add:

```python
def _score_to_arrow(total: int) -> str:
    if total >= 2:
        return "↑↑"
    if total == 1:
        return "↑"
    if total == -1:
        return "↓"
    if total <= -2:
        return "↓↓"
    return "→"


def _rs_momentum_vote(current_score: float, prior_score: float) -> int:
    """+1 if RS score accelerating by >5pts, -1 if decelerating, 0 if stable."""
    delta = current_score - prior_score
    if delta > 5:
        return 1
    if delta < -5:
        return -1
    return 0


def _volume_vote(volumes: list[float], prices: list[float]) -> int:
    """+1 for accumulation (high vol + rising price), -1 for distribution, 0 neutral."""
    if len(volumes) < 20 or len(prices) < 6:
        return 0
    vol_5d = sum(volumes[-5:]) / 5
    vol_20d = sum(volumes[-20:]) / 20
    if vol_20d == 0:
        return 0
    ratio = vol_5d / vol_20d
    if ratio < 1.3:
        return 0
    price_return = (prices[-1] - prices[-6]) / prices[-6] if prices[-6] != 0 else 0
    if price_return > 0:
        return 1
    if price_return < 0:
        return -1
    return 0


def _get_sector_flow(etfs: list[str]) -> dict[str, int]:
    """Fetch Schwab option chains for sector ETFs. Returns put/call vote per ETF.
    Returns empty dict if Schwab client unavailable."""
    if fetch_option_chain is None:
        return {}
    votes: dict[str, int] = {}
    for etf in etfs:
        try:
            chain = fetch_option_chain(etf)
            if chain.stock_price == 0:
                votes[etf] = 0
                continue
            call_oi = sum(c.open_interest for c in chain.calls if 30 <= c.dte <= 60)
            put_oi = sum(c.open_interest for c in chain.puts if 30 <= c.dte <= 60)
            if call_oi == 0:
                votes[etf] = 0
                continue
            pc_ratio = put_oi / call_oi
            if pc_ratio < 0.8:
                votes[etf] = 1    # call-biased → bullish
            elif pc_ratio > 1.2:
                votes[etf] = -1   # put-biased → bearish
            else:
                votes[etf] = 0
        except Exception as e:
            logger.warning("Sector flow fetch failed for %s: %s", etf, e)
            votes[etf] = 0
    return votes
```

- [ ] **Step 5: Run all tests**

```bash
cd backend && python -m pytest tests/test_sector_rotation.py -v
```

Expected: `19 passed` (2 from Task 1 + 17 new helpers)

- [ ] **Step 6: Commit**

```bash
git add backend/sector_analysis.py backend/tests/test_sector_rotation.py
git commit -m "feat: add _score_to_arrow, _rs_momentum_vote, _volume_vote, _get_sector_flow helpers"
```

---

### Task 3: Update `get_sector_analysis()` to compute rotation

**Files:**
- Modify: `backend/sector_analysis.py` (`get_sector_analysis` function only)

This task replaces the entire `get_sector_analysis` function. It also fixes two pre-existing bugs:
1. `spy_prior` (undefined variable) → `prior_spy`
2. `results.sort(...)` was indented inside the for loop — moved outside

- [ ] **Step 1: Replace `get_sector_analysis()` entirely**

Find and replace the entire `get_sector_analysis` function (from `def get_sector_analysis()` through the final `return results`):

```python
def get_sector_analysis() -> list[SectorData]:
    """Fetch 3-month daily history for all 11 sector ETFs + SPY, compute rotation signals."""
    tickers = list(SECTOR_ETFS.keys()) + ["SPY"]
    try:
        raw = yf.download(tickers, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if raw.empty:
            logger.warning("Sector analysis: empty data from yfinance")
            return []
        df = raw["Close"]
        vol_df = raw["Volume"]
    except Exception as e:
        logger.error("Sector analysis fetch failed: %s", e)
        return []

    spy = df["SPY"].dropna().tolist()
    if len(spy) < 5:
        return []

    spy_ret = {
        "1w":  _compute_return([_safe(spy, -5),  spy[-1]], 0, 1),
        "4w":  _compute_return([_safe(spy, -20), spy[-1]], 0, 1),
        "12w": _compute_return([_safe(spy, -60), spy[-1]], 0, 1),
    }
    prior_spy = _compute_return([_safe(spy, -60), _safe(spy, -20)], 0, 1)
    spy_4w_5d_ago = _compute_return([_safe(spy, -25), _safe(spy, -5)], 0, 1)

    vs_spy_4w: dict[str, float] = {}
    vs_spy_4w_5d_ago: dict[str, float] = {}
    sector_raw: dict[str, dict] = {}

    for etf in SECTOR_ETFS:
        if etf not in df.columns:
            continue
        prices = df[etf].dropna().tolist()
        if len(prices) < 5:
            continue

        abs_r1w   = _compute_return([_safe(prices, -5),  prices[-1]], 0, 1)
        abs_r4w   = _compute_return([_safe(prices, -20), prices[-1]], 0, 1)
        abs_r12w  = _compute_return([_safe(prices, -60), prices[-1]], 0, 1)
        prior_r4w = _compute_return([_safe(prices, -60), _safe(prices, -20)], 0, 1)
        r4w_5d_ago = _compute_return([_safe(prices, -25), _safe(prices, -5)], 0, 1)

        vs_spy_4w[etf] = round(abs_r4w - spy_ret["4w"], 2)
        vs_spy_4w_5d_ago[etf] = round(r4w_5d_ago - spy_4w_5d_ago, 2)

        volumes = vol_df[etf].dropna().tolist() if etf in vol_df.columns else []

        sector_raw[etf] = {
            "return_1w":         abs_r1w,
            "return_4w":         abs_r4w,
            "return_12w":        abs_r12w,
            "return_vs_spy_1w":  round(abs_r1w  - spy_ret["1w"],  2),
            "return_vs_spy_4w":  round(abs_r4w  - spy_ret["4w"],  2),
            "return_vs_spy_12w": round(abs_r12w - spy_ret["12w"], 2),
            "prior_vs_spy_4w":   round(prior_r4w - prior_spy, 2),
            "volumes":           volumes,
            "prices":            prices,
        }

    scores = _rs_score(vs_spy_4w)
    scores_5d_ago = _rs_score(vs_spy_4w_5d_ago)
    prior_vs_spy = {etf: sector_raw[etf]["prior_vs_spy_4w"] for etf in sector_raw}
    prior_scores = _rs_score(prior_vs_spy)

    flow_votes = _get_sector_flow(list(SECTOR_ETFS.keys()))

    results = []
    for etf, name in SECTOR_ETFS.items():
        if etf not in sector_raw:
            continue
        d = sector_raw[etf]
        score = scores.get(etf, 50.0)
        prior = prior_scores.get(etf, 50.0)
        score_5d_ago = scores_5d_ago.get(etf, 50.0)

        rs_vote  = _rs_momentum_vote(score, score_5d_ago)
        vol_vote = _volume_vote(d["volumes"], d["prices"])
        flow_vote = flow_votes.get(etf, 0)
        rotation = _score_to_arrow(rs_vote + vol_vote + flow_vote)

        results.append(SectorData(
            etf=etf,
            name=name,
            return_1w=d["return_1w"],
            return_4w=d["return_4w"],
            return_12w=d["return_12w"],
            return_vs_spy_1w=d["return_vs_spy_1w"],
            return_vs_spy_4w=d["return_vs_spy_4w"],
            return_vs_spy_12w=d["return_vs_spy_12w"],
            rs_score=score,
            trend_direction=_trend_direction(score, prior),
            classification=_classify(score),
            rotation=rotation,
        ))

    results.sort(key=lambda s: s.rs_score, reverse=True)
    return results
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
cd backend && python -c "from sector_analysis import get_sector_analysis; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run all tests**

```bash
cd backend && python -m pytest tests/test_sector_rotation.py -v
```

Expected: `19 passed`

- [ ] **Step 4: Commit**

```bash
git add backend/sector_analysis.py
git commit -m "feat: wire rotation signals into get_sector_analysis (RS momentum + volume + options flow)"
```

---

### Task 4: Add ROTATION column to SectorPanel.jsx

**Files:**
- Modify: `frontend/src/components/SectorPanel.jsx`

- [ ] **Step 1: Add ROTATION header**

Find in `frontend/src/components/SectorPanel.jsx`:
```jsx
                <div style={styles.headerRow}>
                    <span style={{ ...styles.cell, width: 60 }}>ETF</span>
                    <span style={{ ...styles.cell, flex: 1 }}>Sector</span>
                    <span style={{ ...styles.cell, width: 90, textAlign: 'right' }}>vs SPY ({period})</span>
                    <span style={{ ...styles.cell, width: 70, textAlign: 'right' }}>RS Score</span>
                    <span style={{ ...styles.cell, width: 60, textAlign: 'center' }}>Trend</span>
                </div>
```

Replace with:
```jsx
                <div style={styles.headerRow}>
                    <span style={{ ...styles.cell, width: 60 }}>ETF</span>
                    <span style={{ ...styles.cell, flex: 1 }}>Sector</span>
                    <span style={{ ...styles.cell, width: 90, textAlign: 'right' }}>vs SPY ({period})</span>
                    <span style={{ ...styles.cell, width: 70, textAlign: 'right' }}>RS Score</span>
                    <span style={{ ...styles.cell, width: 60, textAlign: 'center' }}>Trend</span>
                    <span style={{ ...styles.cell, width: 70, textAlign: 'center' }}>Rotation</span>
                </div>
```

- [ ] **Step 2: Add ROTATION cell to data rows**

Find:
```jsx
                            <span style={{
                                ...styles.cell, width: 60, textAlign: 'center',
                                color: s.trend_direction === 'improving' ? '#00ffaa' : s.trend_direction === 'deteriorating' ? '#ff4444' : '#555',
                            }}>
                                {arrow(s.trend_direction)}
                            </span>
                        </div>
```

Replace with:
```jsx
                            <span style={{
                                ...styles.cell, width: 60, textAlign: 'center',
                                color: s.trend_direction === 'improving' ? '#00ffaa' : s.trend_direction === 'deteriorating' ? '#ff4444' : '#555',
                            }}>
                                {arrow(s.trend_direction)}
                            </span>
                            <span style={{ ...styles.cell, width: 70, textAlign: 'center' }}>
                                {s.rotation ?? '→'}
                            </span>
                        </div>
```

- [ ] **Step 3: Verify the frontend builds**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SectorPanel.jsx
git commit -m "feat: add ROTATION column to sector panel"
```
