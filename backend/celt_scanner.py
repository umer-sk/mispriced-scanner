"""
CELT (Crash Entry LEAP Trigger) scanner.

Identifies QQQ-universe stocks in crash / deep-correction mode where buying
deep-ITM LEAP calls provides asymmetric upside at depressed IV.

Three scored signals:
  1. Price Damage    (max 1.0)
  2. Elevated HV     (max 1.0)
  3. Sentiment Capit.(max 1.2)
Qualifies if total >= 2.2.
"""
import logging
import math
import time
from datetime import datetime, timezone

import yfinance as yf

from models import CeltSetup, OptionChainData, OptionContract
from schwab_client import fetch_option_chain, _compute_hv30

logger = logging.getLogger(__name__)

_BATCH_SIZE = 15


# ---------------------------------------------------------------------------
# Volatility helpers
# ---------------------------------------------------------------------------

def _compute_hv60(prices: list[float]) -> float:
    if len(prices) < 2:
        return 0.0
    window = prices[-61:]
    log_returns = [
        math.log(window[i] / window[i - 1])
        for i in range(1, len(window))
        if window[i - 1] > 0 and window[i] > 0
    ]
    if len(log_returns) < 2:
        return 0.0
    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _compute_hv_1yr_avg(closes: list[float]) -> float:
    """Average of rolling 30-day HV windows sampled every 5 days over the past year."""
    if len(closes) < 32:
        return 0.0
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(log_returns) < 30:
        return 0.0
    hvs = []
    for i in range(30, len(log_returns) + 1, 5):
        window = log_returns[i - 30:i]
        n = len(window)
        mean = sum(window) / n
        variance = sum((r - mean) ** 2 for r in window) / (n - 1) if n > 1 else 0.0
        hvs.append(math.sqrt(variance) * math.sqrt(252))
    return sum(hvs) / len(hvs) if hvs else 0.0


# ---------------------------------------------------------------------------
# Signal scorers
# ---------------------------------------------------------------------------

def _score_price_damage(closes: list[float], stock_price: float) -> tuple[float, dict]:
    """Score based on drawdown from 52-week high and position vs SMA200."""
    if len(closes) < 10:
        return 0.0, {}

    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    drawdown = (high_52w - stock_price) / high_52w if high_52w > 0 else 0.0

    if drawdown >= 0.40:
        score = 1.0
    elif drawdown >= 0.30:
        score = 0.8
    elif drawdown >= 0.20:
        score = 0.6
    elif drawdown >= 0.10:
        score = 0.3
    else:
        return 0.0, {"drawdown_pct": round(drawdown * 100, 1), "skipped": True}

    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
    below_sma200 = stock_price < sma200
    pct_from_200sma = (stock_price - sma200) / sma200 * 100

    if below_sma200:
        score = min(1.0, score + 0.1)

    return round(score, 3), {
        "drawdown_pct": round(drawdown * 100, 1),
        "high_52w": round(high_52w, 2),
        "below_200sma": below_sma200,
        "pct_from_200sma": round(pct_from_200sma, 1),
        "sma200": round(sma200, 2),
    }


def _score_volatility(closes: list[float]) -> tuple[float, dict]:
    """Score based on HV30 elevation vs 1-year average."""
    hv30 = _compute_hv30(closes[-31:])
    hv60 = _compute_hv60(closes)
    hv_1yr_avg = _compute_hv_1yr_avg(closes)

    if hv_1yr_avg == 0:
        return 0.0, {}

    ratio = hv30 / hv_1yr_avg

    if ratio < 1.5:
        return 0.0, {"hv30": round(hv30, 4), "hv60": round(hv60, 4),
                     "hv_ratio": round(ratio, 3), "skipped": True}

    if ratio >= 2.0:
        score = 1.0
    elif ratio >= 1.75:
        score = 0.8
    else:
        score = 0.6

    hv_expansion = hv30 / hv60 if hv60 > 0 else 0.0
    if hv_expansion >= 1.5:
        score = min(1.0, score + 0.2)

    return round(score, 3), {
        "hv30": round(hv30, 4),
        "hv60": round(hv60, 4),
        "hv_ratio": round(ratio, 3),
        "hv_expansion": round(hv_expansion, 3),
        "hv_1yr_avg": round(hv_1yr_avg, 4),
    }


def _score_sentiment(chain: OptionChainData) -> tuple[float, dict]:
    """Score based on IV rank and LEAP put/call OI skew."""
    iv_rank = chain.iv_rank

    if iv_rank >= 85:
        score = 0.7
    elif iv_rank >= 70:
        score = 0.5
    elif iv_rank >= 60:
        score = 0.3
    else:
        return 0.0, {"iv_rank": iv_rank, "skipped": True}

    # LEAP put/call OI ratio
    leap_puts_oi = sum(p.open_interest for p in chain.puts if p.dte >= 270 and p.open_interest > 0)
    leap_calls_oi = sum(c.open_interest for c in chain.calls if c.dte >= 270 and c.open_interest > 0)
    pc_ratio = leap_puts_oi / leap_calls_oi if leap_calls_oi > 0 else 0.0

    if pc_ratio >= 1.5:
        score += 0.2
    elif pc_ratio >= 1.3:
        score += 0.1

    score = min(1.0, score)
    # Sentiment weight is 1.2
    weighted = round(score * 1.2, 3)

    return weighted, {
        "iv_rank": iv_rank,
        "leap_put_oi": leap_puts_oi,
        "leap_call_oi": leap_calls_oi,
        "leap_pc_ratio": round(pc_ratio, 3),
        "pre_weight_score": round(score, 3),
    }


# ---------------------------------------------------------------------------
# LEAP contract finder
# ---------------------------------------------------------------------------

def _find_best_leap(calls: list[OptionContract]) -> OptionContract | None:
    """Find highest-delta deep-ITM LEAP call with dte>=270."""
    candidates = [
        c for c in calls
        if c.dte >= 270 and 0.65 <= c.delta <= 0.85 and c.open_interest >= 500 and c.bid > 0
    ]
    if not candidates:
        # Relaxed fallback
        candidates = [
            c for c in calls
            if c.dte >= 270 and 0.60 <= c.delta <= 0.90 and c.open_interest >= 100 and c.bid > 0
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.delta)


# ---------------------------------------------------------------------------
# Price history download
# ---------------------------------------------------------------------------

def _fetch_closes(symbols: list[str]) -> dict[str, list[float]]:
    """Batch-download 1 year of daily closes via yfinance."""
    result: dict[str, list[float]] = {}
    batches = [symbols[i:i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]

    for i, batch in enumerate(batches):
        if i > 0:
            time.sleep(1)
        try:
            raw = yf.download(
                batch,
                period="1y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if raw.empty:
                continue

            close_col = raw["Close"] if "Close" in raw.columns else raw.get("close")
            if close_col is None:
                continue

            if len(batch) == 1:
                sym = batch[0]
                closes = close_col.dropna().tolist()
                if closes:
                    result[sym] = [float(v) for v in closes]
            else:
                for sym in batch:
                    if sym in close_col.columns:
                        closes = close_col[sym].dropna().tolist()
                        if closes:
                            result[sym] = [float(v) for v in closes]
        except Exception as e:
            logger.warning("yfinance batch download failed for %s: %s", batch, e)

    return result


# ---------------------------------------------------------------------------
# Confidence calc
# ---------------------------------------------------------------------------

def _compute_confidence(total_score: float) -> int:
    if total_score >= 3.0:
        return 90
    if total_score >= 2.8:
        return 75
    if total_score >= 2.5:
        return 60
    return 45


def _build_entry_notes(pd_score: float, vol_score: float, sent_score: float, details: dict) -> str:
    notes = []
    if pd_score >= 0.8:
        notes.append(f"{details.get('pd', {}).get('drawdown_pct', 0):.0f}% drawdown from 52W high")
    if details.get('pd', {}).get('below_200sma'):
        notes.append("below 200 SMA")
    if vol_score >= 0.8:
        notes.append(f"HV elevated {details.get('vol', {}).get('hv_ratio', 0):.1f}x avg")
    if sent_score >= 0.6:
        notes.append(f"IV rank {details.get('sent', {}).get('iv_rank', 0):.0f}")
    return "; ".join(notes) if notes else "CELT setup"


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan_celt_setups(tickers: list[str]) -> list[CeltSetup]:
    """
    Scan tickers for CELT setups.
    Pre-screens on signals 1+2 (price data only) before fetching LEAP chains.
    Returns list sorted by signal_score descending.
    """
    logger.info("CELT scan: fetching 1yr closes for %d symbols", len(tickers))
    closes_map = _fetch_closes(tickers)

    # Pre-screen: need price damage AND volatility signals before pulling Schwab chains
    qualifying: list[tuple[str, list[float], float, dict, float, dict]] = []
    for sym in tickers:
        closes = closes_map.get(sym)
        if not closes or len(closes) < 32:
            continue
        stock_price = closes[-1]
        pd_score, pd_details = _score_price_damage(closes, stock_price)
        if pd_score == 0.0:
            continue
        vol_score, vol_details = _score_volatility(closes)
        if vol_score == 0.0:
            continue
        if pd_score + vol_score < 1.0:
            continue
        qualifying.append((sym, closes, pd_score, pd_details, vol_score, vol_details))

    logger.info("CELT: %d/%d pre-screened (price+vol signal)", len(qualifying), len(tickers))

    setups: list[CeltSetup] = []
    for sym, closes, pd_score, pd_details, vol_score, vol_details in qualifying:
        try:
            chain = fetch_option_chain(sym, days_out=730)
            if chain.stock_price == 0:
                continue

            sent_score, sent_details = _score_sentiment(chain)
            total = round(pd_score + vol_score + sent_score, 3)
            if total < 2.2:
                continue

            leap = _find_best_leap(chain.calls)
            if leap is None:
                logger.debug("CELT: %s qualifies (%.2f) but no LEAP found — skipping", sym, total)
                continue

            stock_price = chain.stock_price
            sma200 = pd_details.get("sma200", stock_price)
            pct_from_200sma = (stock_price - sma200) / sma200 * 100 if sma200 else 0.0

            details = {"pd": pd_details, "vol": vol_details, "sent": sent_details}
            confidence = _compute_confidence(total)
            entry_notes = _build_entry_notes(pd_score, vol_score, sent_score, details)

            setup = CeltSetup(
                symbol=sym,
                stock_price=round(stock_price, 2),
                timestamp=datetime.now(timezone.utc),
                signal_score=total,
                price_damage_score=pd_score,
                volatility_score=vol_score,
                sentiment_score=sent_score,
                drawdown_pct=pd_details.get("drawdown_pct", 0.0),
                below_200sma=pd_details.get("below_200sma", False),
                pct_from_200sma=round(pct_from_200sma, 1),
                hv30=vol_details.get("hv30", 0.0),
                hv60=vol_details.get("hv60", 0.0),
                hv_ratio=vol_details.get("hv_ratio", 0.0),
                hv_expansion=vol_details.get("hv_expansion", 0.0),
                iv_rank=chain.iv_rank,
                leap_put_call_oi_ratio=sent_details.get("leap_pc_ratio", 0.0),
                leap_strike=leap.strike,
                leap_expiry=leap.expiry,
                leap_dte=leap.dte,
                leap_delta=round(leap.delta, 3),
                leap_ask=leap.ask,
                leap_bid=leap.bid,
                leap_mid=leap.mid,
                leap_oi=leap.open_interest,
                leap_iv=round(leap.iv, 4),
                confidence=confidence,
                entry_notes=entry_notes,
            )
            setups.append(setup)
            logger.info("CELT: %s score=%.2f drawdown=%.0f%% IVR=%.0f LEAP %.0f %s",
                        sym, total, pd_details.get("drawdown_pct", 0),
                        chain.iv_rank, leap.strike, leap.expiry)

        except Exception as e:
            logger.error("CELT: error processing %s: %s", sym, e)

    setups.sort(key=lambda s: s.signal_score, reverse=True)
    logger.info("CELT scan complete: %d setups from %d pre-screened", len(setups), len(qualifying))
    return setups
