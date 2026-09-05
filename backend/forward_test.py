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
