import logging
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


def _safe(lst: list, i: int) -> float:
    return lst[max(i, -len(lst))]


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

    spy_ret = {
        "1w": _compute_return([_safe(spy, -5), spy[-1]], 0, 1),
        "4w": _compute_return([_safe(spy, -20), spy[-1]], 0, 1),
        "12w": _compute_return([_safe(spy, -60), spy[-1]], 0, 1),
    }

    vs_spy_4w: dict[str, float] = {}
    sector_raw: dict[str, dict] = {}

    for etf in SECTOR_ETFS:
        if etf not in df.columns:
            continue
        prices = df[etf].dropna().tolist()
        if len(prices) < 5:
            continue

        abs_r1w  = _compute_return([_safe(prices, -5),  prices[-1]], 0, 1)
        abs_r4w  = _compute_return([_safe(prices, -20), prices[-1]], 0, 1)
        abs_r12w = _compute_return([_safe(prices, -60), prices[-1]], 0, 1)
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

    scores = _rs_score(vs_spy_4w)

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
