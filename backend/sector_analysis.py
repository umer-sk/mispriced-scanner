import logging
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

try:
    from schwab_client import fetch_option_chain
except Exception:
    fetch_option_chain = None

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


def _safe(lst: list, i: int) -> float:
    return lst[max(i, -len(lst))]


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
