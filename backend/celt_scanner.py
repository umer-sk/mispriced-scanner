"""
CELT (Crash Entry LEAP Trigger) scanner.

Identifies QQQ-universe stocks in crash / deep-correction mode where buying
deep-ITM LEAP calls provides asymmetric upside relative to a stock position,
at a premium that reflects the market's own elevated fear (high IV rank) —
not at "depressed" IV; elevated IV rank IS what capitulation looks like.
Being deep-ITM is what keeps the position's vega/theta exposure low despite
that, so the elevated-IV entry doesn't undermine the thesis.

Three scored signals:
  1. Price Damage    (max 1.0)
  2. Elevated HV     (max 1.0)
  3. Sentiment Capit.(max 1.2)
Qualifies if total >= 2.2.
"""
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone

import yfinance as yf

from catalyst import _fetch_earnings_date
from models import CeltSetup, OptionChainData, OptionContract
from occ import build_occ
from options_math import _single_leg_reward
from schwab_client import fetch_option_chain, _compute_hv30, iv_rank_from_decimal

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


# A deep-ITM LEAP is surfaced at its mid, but it is bought at the ask. Reject
# legs quoted wider than this: the edge CELT looks for is easily smaller than
# the round trip on a 20%-wide market.
LEAP_MAX_SPREAD_PCT = 15.0

# The minimum DTE that makes a contract a "LEAP" here. Named and exported
# (rather than a repeated literal) because forward_test.py imports this
# exact value for its own CELT DTE band, and it must match `_find_best_leap`
# exactly or the forward test would tier a position on a different DTE
# assumption than the scanner actually enforced.
LEAP_DTE_MIN = 270

# Reward model. ATR14 (a ~2-3 week realized-range measure) is the wrong
# timescale for a 270-760 DTE LEAP bought on a multi-quarter recovery
# thesis — unlike technical_scanner.py's momentum setups, there's no ATR
# forecast to reuse here. Instead: assume the stock mean-reverts this
# fraction of the way back toward its pre-drawdown 52-week high by the
# LEAP's expiry, and feed that as the drift assumption into the same
# expected-value model (options_math._single_leg_reward) the rest of the
# app uses for single-leg options. Verified numerically that even a FULL
# recovery (fraction=1.0) to the 52-week high scores well under 2.0 at
# representative deep-ITM/high-IV CELT inputs (~1.7) — the shared 2.0 bar
# built for 30-60 DTE structures is a category error here, the same reason
# the 200-week bounce (technical_scanner.py) needed its own lower bar.
# NOT YET a construction gate — see CELT_RR_MIN in forward_test.py, which
# feeds this into the tier system instead, so real ft_positions outcomes
# can calibrate a real threshold rather than committing to a guessed one
# that could silence the scanner entirely.
CELT_RECOVERY_FRACTION = 0.5

# Window used to read IV off the FRONT of the curve.
_FRONT_IV_DTE_MIN, _FRONT_IV_DTE_MAX = 20, 45
_FRONT_IV_DTE_MAX_FALLBACK = 75


def _front_month_iv(chain: OptionChainData) -> float:
    """Mean IV of near-ATM calls at the front of the curve, in decimal form.

    chain.iv30 cannot be used here. CELT fetches with days_out=730, and iv30 is
    an unweighted mean of ATM call IV across EVERY expiry in the fetched window
    — so for CELT it blends front weeklies with two-year LEAPs. In a crash the
    term structure inverts sharply: the front spikes while LEAP IV barely
    moves, so the blend lands far below the front-month IV that "capitulation"
    actually means, and the iv_rank gate below then rejects the very regime
    this scanner exists to catch.

    Returns 0.0 when no usable contract is found; the caller treats that as
    "cannot assess" rather than "low IV".
    """
    if chain.stock_price <= 0:
        return 0.0

    def _mean_iv(dte_max: int, band: float) -> float:
        ivs = [
            c.iv for c in chain.calls
            if _FRONT_IV_DTE_MIN <= c.dte <= dte_max
            and c.iv > 0
            and abs(c.strike - chain.stock_price) < chain.stock_price * band
        ]
        return sum(ivs) / len(ivs) if ivs else 0.0

    # Widen the DTE window first, then the strike band. A low-priced name on
    # wide strike spacing (spot 18.60, strikes 17.5/20.0) has nothing inside
    # +/-5%, and silently skipping it is the same failure this fix removes.
    return (
        _mean_iv(_FRONT_IV_DTE_MAX, 0.05)
        or _mean_iv(_FRONT_IV_DTE_MAX_FALLBACK, 0.05)
        or _mean_iv(_FRONT_IV_DTE_MAX_FALLBACK, 0.10)
    )


def _score_sentiment(chain: OptionChainData, iv_rank: float) -> tuple[float, dict]:
    """Score based on IV rank and LEAP put/call OI skew.

    iv_rank is passed in rather than read off the chain — see _front_month_iv
    for why the chain-level value is unusable for CELT.
    """

    if iv_rank >= 85:
        score = 0.7
    elif iv_rank >= 70:
        score = 0.5
    elif iv_rank >= 60:
        score = 0.3
    else:
        return 0.0, {"iv_rank": iv_rank, "skipped": True}

    # LEAP put/call OI ratio
    leap_puts_oi = sum(p.open_interest for p in chain.puts if p.dte >= LEAP_DTE_MIN and p.open_interest > 0)
    leap_calls_oi = sum(c.open_interest for c in chain.calls if c.dte >= LEAP_DTE_MIN and c.open_interest > 0)
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

def _spread_pct(c: OptionContract) -> float:
    """Bid/ask spread as a percentage of mid, 0-100.

    Note scanner._spread_pct returns a FRACTION (0-1) and its callers multiply
    by 100; this one returns the percentage directly. Do not move a threshold
    constant between the two without converting.
    """
    if c.mid <= 0:
        return 100.0
    return round((c.ask - c.bid) / c.mid * 100, 1)



# A contract must be at least this far ITM by strike, not just by delta, to
# count as "deep-ITM stock replacement". Delta alone is not enough at the IV
# levels CELT requires (rank 60-85): at typical CELT operating conditions
# (T~1.1y, IV 40-80%), the 0.65-0.85 delta band alone admits strikes from
# roughly at-the-money down to ~34% ITM — a 0.65-delta LEAP bought at IV
# rank 85 can be essentially ATM, all extrinsic, the opposite of the
# low-theta/low-vega instrument the "buy the recovery cheaply" framing
# implies. 0.85 maps to strike ~0.85x spot fairly stably across that IV
# range, so this cuts the shallow end without fighting the delta band.
LEAP_MAX_MONEYNESS = 0.85


def _find_best_leap(calls: list[OptionContract], stock_price: float) -> OptionContract | None:
    """Find highest-delta deep-ITM LEAP call with dte>=270."""
    def _tradeable(c: OptionContract) -> bool:
        # Open interest alone is not liquidity. Deep-ITM LEAPs routinely quote
        # 10-20% wide, and the setup is surfaced at leap_mid while it is bought
        # at leap_ask, so a wide leg silently overstates the entry.
        return c.bid > 0 and _spread_pct(c) <= LEAP_MAX_SPREAD_PCT

    def _deep_itm(c: OptionContract) -> bool:
        return stock_price > 0 and c.strike <= stock_price * LEAP_MAX_MONEYNESS

    candidates = [
        c for c in calls
        if c.dte >= LEAP_DTE_MIN and 0.65 <= c.delta <= 0.85 and c.open_interest >= 500
        and _tradeable(c) and _deep_itm(c)
    ]
    if not candidates:
        # Relaxed fallback
        candidates = [
            c for c in calls
            if c.dte >= LEAP_DTE_MIN and 0.60 <= c.delta <= 0.90 and c.open_interest >= 100
            and _tradeable(c) and _deep_itm(c)
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

def scan_celt_setups(tickers: list[str], qqq_price: float, qqq_ma50: float) -> list[CeltSetup]:
    """
    Scan tickers for CELT setups.
    Pre-screens on signals 1+2 (price data only) before fetching LEAP chains.
    Returns list sorted by signal_score descending.

    qqq_price/qqq_ma50: a coarse, cheap systemic-stress prerequisite —
    without it, nothing here distinguishes a genuine market-wide crash from
    a single-name blowup (bad earnings, fraud, a guidance cut), and every
    per-name gate (HV elevation, IV rank) can be satisfied by an
    idiosyncratic event alone. Required (not defaulted) so a caller can't
    silently disable the gate. Deliberately QQQ price vs its own 50-day MA
    — a trend check — rather than market_context's volatility-based RISK_OFF
    regime, which would have read false through most of a real grinding
    bear market and partly re-tests what the per-name HV/IV-rank gates
    below already check at the single-name level.
    """
    if qqq_ma50 <= 0.0:
        # A degraded yfinance fetch (market_context._fetch_index_mas
        # returns ma50=0.0 when it has fewer than 50 days of cached
        # closes) must not silently disable this gate — a naive `<`
        # comparison against 0.0 is always False, which would let CELT
        # fire completely unguarded on exactly a bad-data day.
        logger.warning("CELT: QQQ MA50 unavailable — cannot confirm market-wide stress, skipping scan")
        return []
    if qqq_price >= qqq_ma50:
        logger.info("CELT: QQQ (%.2f) not below its own 50-day MA (%.2f) — no market-wide stress, skipping scan",
                    qqq_price, qqq_ma50)
        return []

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
            # strike_count=40, not the default 20: a crashed name's older,
            # higher-struck puts (needed for the P/C OI ratio below) fall
            # outside a narrow ATM-centred window, and the wider window also
            # gives more strike choices for deep-ITM selection on tightly-
            # spaced names. OI itself stays a slow/structural signal, not
            # real-time flow — this widens the window, it doesn't fix that.
            chain = fetch_option_chain(sym, days_out=730, strike_count=40)
            if chain.stock_price == 0:
                continue

            # Read IV off the front of the curve, not the 730-day blend, and
            # rank it against this symbol's own realised-vol history. `closes`
            # is already in hand from the pre-screen, so this costs no extra
            # API call.
            front_iv = _front_month_iv(chain)
            if front_iv <= 0:
                logger.debug("CELT: %s — no usable front-month IV, skipping", sym)
                continue
            # iv_rank_from_decimal, NOT _compute_iv_rank: the latter's legacy
            # guard divides anything above 1.0 by 100, and a crash front-month
            # IV of 120% is exactly that — it would rank 0 instead of 100 and
            # re-create the bug this function exists to fix.
            front_iv_rank, _ = iv_rank_from_decimal(front_iv, closes)
            sent_score, sent_details = _score_sentiment(chain, front_iv_rank)
            sent_details["front_month_iv"] = round(front_iv, 4)
            sent_details["blended_chain_iv30"] = round(chain.iv30, 4)
            total = round(pd_score + vol_score + sent_score, 3)
            if total < 2.2:
                continue

            leap = _find_best_leap(chain.calls, stock_price=chain.stock_price)
            if leap is None:
                # Separate the two causes. A market-wide spread blowout silences
                # CELT on exactly the day it matters, and that must not look
                # identical in the logs to a chain with no qualifying strikes.
                shaped = [c for c in chain.calls
                          if c.dte >= LEAP_DTE_MIN and 0.60 <= c.delta <= 0.90
                          and c.open_interest >= 100 and c.bid > 0]
                if shaped:
                    spreads = sorted(_spread_pct(c) for c in shaped)
                    logger.info(
                        "CELT: %s qualifies (%.2f) but all %d LEAP candidates exceed "
                        "%.0f%% spread (tightest %.1f%%, widest %.1f%%) — skipping",
                        sym, total, len(shaped), LEAP_MAX_SPREAD_PCT, spreads[0], spreads[-1],
                    )
                else:
                    logger.debug(
                        "CELT: %s qualifies (%.2f) but no LEAP matched delta/OI/DTE — skipping",
                        sym, total,
                    )
                continue

            # Not a DTE-window exclusion (nonsensical at 270+ days — multiple
            # earnings reports during the hold are inherent to a LEAP-length
            # thesis, not a flaw to filter out). This is an entry-timing
            # question instead: avoid entering right before a fresh catalyst
            # that could extend the crash further; hold through subsequent
            # reports once in the position. 7 days, not fewer, absorbs
            # yfinance's earnings-date parsing occasionally landing a day off.
            earnings_date = _fetch_earnings_date(sym)
            if earnings_date is not None and date.today() <= earnings_date <= date.today() + timedelta(days=7):
                logger.info("CELT: %s qualifies (%.2f) but earnings %s is within 7 days — skipping",
                            sym, total, earnings_date)
                continue

            stock_price = chain.stock_price
            sma200 = pd_details.get("sma200", stock_price)
            pct_from_200sma = (stock_price - sma200) / sma200 * 100 if sma200 else 0.0

            details = {"pd": pd_details, "vol": vol_details, "sent": sent_details}
            confidence = _compute_confidence(total)
            entry_notes = _build_entry_notes(pd_score, vol_score, sent_score, details)
            try:
                leap_occ = leap.occ_symbol or build_occ(sym, leap.expiry, False, leap.strike)
            except ValueError:
                leap_occ = ""

            high_52w = pd_details.get("high_52w", stock_price)
            price_target = stock_price + CELT_RECOVERY_FRACTION * (high_52w - stock_price)
            rr_ratio = round(_single_leg_reward(
                stock_price, leap.strike, leap.dte, leap.iv, price_target, leap.ask, is_put=False,
            ), 2)
            breakeven = round(leap.strike + leap.ask, 2)
            breakeven_move_pct = round((breakeven - stock_price) / stock_price * 100, 1) if stock_price else 0.0

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
                iv_rank=front_iv_rank,
                leap_put_call_oi_ratio=sent_details.get("leap_pc_ratio", 0.0),
                leap_strike=leap.strike,
                leap_expiry=leap.expiry,
                leap_dte=leap.dte,
                leap_delta=round(leap.delta, 3),
                leap_ask=leap.ask,
                leap_bid=leap.bid,
                leap_mid=leap.mid,
                leap_spread_pct=_spread_pct(leap),
                leap_oi=leap.open_interest,
                leap_iv=round(leap.iv, 4),
                confidence=confidence,
                entry_notes=entry_notes,
                leap_occ=leap_occ,
                price_target=round(price_target, 2),
                rr_ratio=rr_ratio,
                max_loss=round(leap.ask * 100, 2),
                breakeven=breakeven,
                breakeven_move_pct=breakeven_move_pct,
            )
            setups.append(setup)
            logger.info("CELT: %s score=%.2f drawdown=%.0f%% IVR=%.0f LEAP %.0f %s",
                        sym, total, pd_details.get("drawdown_pct", 0),
                        front_iv_rank, leap.strike, leap.expiry)

        except Exception as e:
            logger.error("CELT: error processing %s: %s", sym, e)

    setups.sort(key=lambda s: s.signal_score, reverse=True)
    logger.info("CELT scan complete: %d setups from %d pre-screened", len(setups), len(qualifying))
    return setups
