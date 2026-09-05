"""
Forward test — records every surfaced setup and marks it to market until it
resolves against the +50% / +100% / -50% exit rules.

See docs/superpowers/specs/2026-09-05-forward-test-design.md.
"""
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

# Exit rules, as a percentage of entry debit measured on spread mid.
TARGET_1_PCT = 50.0
TARGET_2_PCT = 100.0
STOP_PCT = -50.0

# Tier A gates.
QUALITY_MIN = {"scanner": 60, "technical": 5}       # score / signal_count
QUALITY_NEAR = {"scanner": 10, "technical": 1}      # Tier B band below threshold
RR_MIN = 2.0
SPREAD_MAX_PCT = 6.0
DTE_MIN, DTE_MAX = 25, 45
BREAKEVEN_MAX_PCT = 3.5
BREAKEVEN_NEAR_PCT = 5.0

# Only these two gates have a meaningful "nearly"; failing any other gate is
# Tier C even when it is the sole failure.
NEAR_MISS_GATES = {"quality", "breakeven"}


def classify(norm: dict) -> tuple[str, list[str]]:
    """Return (tier, gates_failed) for a normalised setup."""
    source = norm["source"]
    failed: list[str] = []

    if norm["quality"] < QUALITY_MIN[source]:
        failed.append("quality")
    if norm["rr_ratio"] < RR_MIN:
        failed.append("rr")
    if not norm["liquidity_ok"] or \
            norm["long_leg_spread_pct"] > SPREAD_MAX_PCT or \
            norm["short_leg_spread_pct"] > SPREAD_MAX_PCT:
        failed.append("liquidity")
    if norm["earnings_in_window"]:
        failed.append("earnings")
    if not (DTE_MIN <= norm["dte_at_entry"] <= DTE_MAX):
        failed.append("dte")
    # Magnitude, not signed value: bear put spreads carry a positive
    # breakeven_move_pct meaning "the stock must fall this far".
    if abs(norm["breakeven_move_pct"]) > BREAKEVEN_MAX_PCT:
        failed.append("breakeven")
    if norm["trend_opposes"]:
        failed.append("trend")

    if not failed:
        return "A", []

    if len(failed) == 1 and failed[0] in NEAR_MISS_GATES:
        gate = failed[0]
        near = (
            gate == "quality"
            and norm["quality"] >= QUALITY_MIN[source] - QUALITY_NEAR[source]
        ) or (
            gate == "breakeven"
            and abs(norm["breakeven_move_pct"]) <= BREAKEVEN_NEAR_PCT
        )
        if near:
            return "B", failed

    return "C", failed


_BEARISH_STRUCTURES = {"bear_put_spread"}


def _direction_from_structure(structure: str) -> str:
    return "bearish" if structure in _BEARISH_STRUCTURES else "bullish"


def normalise(setup, source: str) -> dict:
    """Map a TradeSetup or TechnicalSetup onto one common shape.

    The two dataclasses share no base class and disagree on field names
    (long_strike/strike, net_debit/premium, score/signal_count), so every
    consumer would otherwise need to branch on source.
    """
    if source == "scanner":
        long_strike = setup.long_strike
        entry_debit = setup.net_debit
        quality = setup.score
        detector = setup.signal.detector
        direction = _direction_from_structure(setup.structure)
        earnings = bool(setup.catalyst.earnings_in_window)
        ctx = getattr(setup, "technical_context", None)
        bias = getattr(ctx, "bias", None) if ctx else None
        # Missing context counts as neutral — absence of a trend read is not
        # evidence against the trade.
        trend_opposes = bias is not None and bias != "neutral" and bias != direction
        long_occ = getattr(setup, "long_occ", "") or ""
        short_occ = getattr(setup, "short_occ", "") or ""
    elif source == "technical":
        long_strike = setup.strike
        entry_debit = setup.premium
        quality = setup.signal_count
        detector = None
        direction = setup.direction
        earnings = bool(setup.earnings_within_dte)
        # `direction` is itself the trend read for this source, so it can never
        # oppose itself. The gate is tautological here by construction.
        trend_opposes = False
        long_occ = setup.long_occ or ""
        short_occ = setup.short_occ or ""
    else:
        raise ValueError(f"unknown source {source!r}")

    short_strike = setup.short_strike
    dedup_key = "|".join([
        setup.symbol,
        detector or source,
        setup.structure,
        setup.expiry.isoformat(),
        str(float(long_strike)),
        str(float(short_strike)) if short_strike is not None else "",
    ])

    return {
        "source": source,
        "dedup_key": dedup_key,
        "symbol": setup.symbol,
        "detector": detector,
        "structure": setup.structure,
        "direction": direction,
        "expiry": setup.expiry,
        "dte_at_entry": setup.dte,
        "long_strike": float(long_strike),
        "short_strike": float(short_strike) if short_strike is not None else None,
        "long_occ": long_occ,
        "short_occ": short_occ,
        "entry_debit": float(entry_debit),
        "entry_stock_price": float(setup.stock_price),
        "quality": quality,
        "rr_ratio": float(setup.rr_ratio),
        "breakeven_move_pct": float(setup.breakeven_move_pct),
        "liquidity_ok": bool(setup.liquidity_ok),
        "long_leg_spread_pct": float(setup.long_leg_spread_pct),
        "short_leg_spread_pct": float(setup.short_leg_spread_pct),
        "earnings_in_window": earnings,
        "trend_opposes": trend_opposes,
    }
