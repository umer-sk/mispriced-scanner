"""
Forward test — records every surfaced setup and marks it to market until it
resolves against the +50% / +100% / -50% exit rules.

See docs/superpowers/specs/2026-09-05-forward-test-design.md.
"""
import logging
from datetime import date, datetime, timezone

import ft_store
from schwab_client import fetch_quotes

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


MAX_MARK_FAILURES = 8


def pnl_pct(spread_mid: float, entry_debit: float) -> float:
    if not entry_debit:
        return 0.0
    return (spread_mid - entry_debit) / entry_debit * 100.0


def apply_mark(position: dict, pnl: float, now: datetime, today: date) -> dict:
    """Return the position fields to update for one mark.

    Order matters: targets are checked before the stop so a mark that gapped
    through both is recorded as reaching the target, and both targets are
    checked on the same mark so a gap past +100% is not recorded as only +50%.

    `position["expiry"]` may be a `date` or an ISO-format `str` (Supabase
    `date` columns round-trip as strings) — a string is parsed before use.

    Return contract callers must respect:
    - `closed_ts` is always present in the returned dict. It is `None` when
      the position stays open on this mark — callers must not blindly write
      that `None` over an existing `closed_ts` already stored for the
      position; only a mark that actually closes the position sets it.
    - `last_pnl_pct` is transport-only: it reports this mark's P&L to the
      caller (e.g. as the final mark on an expiring position) but is not a
      column in `ft_positions` and must be popped from the dict before any
      database write.
    """
    expiry = position["expiry"]
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry)

    upd: dict = {
        "mfe_pct": max(position.get("mfe_pct") or 0.0, pnl),
        "mae_pct": min(position.get("mae_pct") or 0.0, pnl),
        "t1_ts": position.get("t1_ts"),
        "t2_ts": position.get("t2_ts"),
        "stop_ts": position.get("stop_ts"),
        "status": position.get("status", "open"),
        "closed_ts": None,
        "last_pnl_pct": pnl,
    }

    if pnl >= TARGET_1_PCT and upd["t1_ts"] is None:
        upd["t1_ts"] = now
        upd["status"] = "target1"

    if pnl >= TARGET_2_PCT and upd["t2_ts"] is None:
        upd["t2_ts"] = now
        upd["status"] = "target2"
        upd["closed_ts"] = now
        return upd

    if pnl <= STOP_PCT:
        upd["stop_ts"] = now
        upd["status"] = "stopped"
        upd["closed_ts"] = now
        return upd

    if today > expiry:
        upd["status"] = "expired"
        upd["closed_ts"] = now

    return upd


def realized_pnl(position: dict, final_pnl: float | None) -> float | None:
    """Blended scale-out: half the position off at each target."""
    status = position.get("status")
    hit_t1 = position.get("t1_ts") is not None

    if status == "target2":
        return (TARGET_1_PCT + TARGET_2_PCT) / 2      # +75%
    if status == "stopped":
        return (TARGET_1_PCT + STOP_PCT) / 2 if hit_t1 else STOP_PCT
    if status == "expired":
        if final_pnl is None:
            return None
        return (TARGET_1_PCT + final_pnl) / 2 if hit_t1 else final_pnl
    return None


def snapshot_setups(setups: list, source: str) -> int:
    """Record newly-seen setups. Returns the count of new positions.

    Never raises: a forward-test failure must not fail the scan that called it.
    """
    if not setups:
        return 0
    try:
        now = datetime.now(timezone.utc)
        norms = []
        for s in setups:
            try:
                norms.append(normalise(s, source))
            except Exception as e:
                logger.error("forward test: could not normalise a %s setup: %s", source, e)

        # A position with no OCC symbols could never be marked; recording it
        # would leave a permanent open row that only ever times out.
        norms = [n for n in norms if n["long_occ"]]
        if not norms:
            return 0

        existing = ft_store.find_open_by_dedup([n["dedup_key"] for n in norms])
        new_count = 0

        for n in norms:
            prior = existing.get(n["dedup_key"])
            if prior:
                # Entry price and timestamp are deliberately untouched.
                ft_store.touch_position(prior["id"], now)
                continue

            tier, gates_failed = classify(n)
            row = {
                "dedup_key": n["dedup_key"], "source": n["source"],
                "symbol": n["symbol"], "detector": n["detector"],
                "structure": n["structure"], "direction": n["direction"],
                "expiry": n["expiry"], "dte_at_entry": n["dte_at_entry"],
                "long_strike": n["long_strike"], "short_strike": n["short_strike"],
                "long_occ": n["long_occ"], "short_occ": n["short_occ"],
                "entry_ts": now, "entry_debit": n["entry_debit"],
                "entry_stock_price": n["entry_stock_price"],
                "score_at_entry": n["quality"], "rr_at_entry": n["rr_ratio"],
                "breakeven_move_pct": n["breakeven_move_pct"],
                "tier": tier, "gates_failed": gates_failed,
                "status": "open", "times_seen": 1, "last_seen_ts": now,
            }
            if ft_store.insert_position(row):
                new_count += 1

        logger.info("forward test: %d new positions from %d %s setups",
                    new_count, len(norms), source)
        return new_count
    except Exception as e:
        logger.exception("forward test: snapshot_setups failed: %s", e)
        return 0


def mark_open_positions(now: datetime | None = None) -> int:
    """Re-price every open position and advance its state. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        today = now.date()
        positions = ft_store.list_open_positions()
        if not positions:
            return 0

        symbols = sorted({
            occ for p in positions
            for occ in (p.get("long_occ"), p.get("short_occ")) if occ
        })
        quotes = fetch_quotes(symbols)

        marked = 0
        for p in positions:
            try:
                long_mid = quotes.get(p.get("long_occ"))
                short_occ = p.get("short_occ") or ""
                short_mid = quotes.get(short_occ) if short_occ else 0.0

                if long_mid is None or (short_occ and short_mid is None):
                    # A missing quote is not a price of zero. Count the failure
                    # and leave the position open.
                    failures = (p.get("mark_failures") or 0) + 1
                    if failures >= MAX_MARK_FAILURES:
                        ft_store.update_position(p["id"], {
                            "mark_failures": failures, "status": "unpriceable",
                            "closed_ts": now,
                        })
                    else:
                        ft_store.update_position(p["id"], {"mark_failures": failures})
                    continue

                spread_mid = round(long_mid - (short_mid or 0.0), 4)
                pnl = round(pnl_pct(spread_mid, p["entry_debit"]), 2)
                # stock_price is left null: quoting the underlying would add one
                # request per distinct symbol for a field nothing currently reads.
                # The column stays in the schema so it can be backfilled later
                # without a migration.
                ft_store.insert_mark(p["id"], now, spread_mid, pnl, None)

                expiry = p["expiry"]
                if isinstance(expiry, str):
                    expiry = date.fromisoformat(expiry)
                upd = apply_mark({**p, "expiry": expiry}, pnl, now, today)

                final = upd.pop("last_pnl_pct", None)
                if upd.get("closed_ts") is not None:
                    upd["realized_pnl_pct"] = realized_pnl({**p, **upd}, final)
                else:
                    upd.pop("closed_ts", None)

                if p.get("mark_failures"):
                    upd["mark_failures"] = 0
                ft_store.update_position(p["id"], upd)
                marked += 1
            except Exception as e:
                logger.error("forward test: could not mark position %s: %s",
                             p.get("id"), e)
                continue

        logger.info("forward test: marked %d/%d open positions", marked, len(positions))
        return marked
    except Exception as e:
        logger.exception("forward test: mark_open_positions failed: %s", e)
        return 0
