"""
Forward test — records every surfaced setup and marks it to market until it
resolves against the +50% / +100% / -50% exit rules.

See docs/superpowers/specs/2026-09-05-forward-test-design.md.
"""
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import ft_store
from celt_scanner import LEAP_MAX_SPREAD_PCT
from celt_scanner import LEAP_DTE_MIN as CELT_DTE_MIN
from schwab_client import fetch_quotes
from technical_scanner import BOUNCE_RR_MIN

logger = logging.getLogger(__name__)

# Expiry is a market-calendar fact, so the "is it past expiry" comparison must
# be made on the exchange's date. A UTC date rolls over at 19:00/20:00 ET, so a
# manual scan late in the evening would otherwise see tomorrow and expire a
# position a day early.
ET = ZoneInfo("America/New_York")

# Exit rules, as a percentage of entry debit measured on spread mid.
TARGET_1_PCT = 50.0
TARGET_2_PCT = 100.0
STOP_PCT = -50.0

# Tier A gates.
QUALITY_MIN = {"scanner": 60, "technical": 5, "celt": 60}   # score / signal_count / confidence
QUALITY_NEAR = {"scanner": 10, "technical": 1, "celt": 15}  # Tier B band below threshold
RR_MIN = 2.0
SPREAD_MAX_PCT = 6.0
# 30-60, not the wider 25-100 a first pass might suggest: this exactly covers
# both sources' actual achievable ranges — scanner.py's constructor picks
# 30-60 DTE (tightened alongside this band, see scanner.py:851) and technical
# consensus's _find_delta_contract is exactly 30-60. Widening further would
# make BOUNCE_DTE_MIN/MAX's own override vacuous.
DTE_MIN, DTE_MAX = 30, 60
# The 200W MA bounce is deliberately longer-dated (60-100 DTE, see
# technical_scanner._construct_200w_bounce_long_call) than every other
# structure this classifies. Without its own band, every bounce position
# fails the "dte" gate unconditionally and permanently — it can never be
# Tier A or B regardless of everything else about the trade.
BOUNCE_DTE_MIN, BOUNCE_DTE_MAX = 60, 100
# The bounce is also gated at construction on its own, separately-calibrated
# BOUNCE_RR_MIN (1.5, not the shared 2.0) — see technical_scanner.py for the
# calibration reasoning. Without this override every bounce position (rr
# typically 1.5-2.0) fails the "rr" gate here unconditionally, and since "rr"
# is not in NEAR_MISS_GATES that means permanent Tier C regardless of
# everything else about the trade — the same failure mode the DTE override
# above exists to prevent, just on a different gate.
#
# The bounce's 4 qualifying criteria (technical_scanner._score_200w_bounce)
# are ALL load-bearing for the setup to exist at all — unlike the 7-signal
# consensus's signal_count, there's no "5 of 7" fraction here, so a quality
# gate keyed off QUALITY_MIN["technical"]=5 would forever read 4 < 5 as a
# near-miss and cap the bounce at Tier B. This override makes the quality
# check tautologically pass for the bounce (4 >= 4), matching what the DTE
# and RR overrides above already do for their own gates.
BOUNCE_QUALITY_MIN, BOUNCE_QUALITY_NEAR = 4, 1

# CELT's LEAP is 270-760 DTE (see celt_scanner.LEAP_DTE_MIN — imported, not
# redefined, so this can't silently drift from what celt_scanner actually
# enforces; 760 gives headroom over the ~730-day chain fetch), single-leg,
# and deep-ITM at high IV rank — none of the shared bands fit it:
CELT_DTE_MAX = 760
# See celt_scanner.CELT_RECOVERY_FRACTION for the reward-model reasoning:
# even a full recovery to the 52-week high scores ~1.7 at representative
# inputs, well under the shared structures' 2.0. This is deliberately NOT a
# construction gate yet (celt_scanner.py never rejects on it) — feeding it
# in here lets the tier system do the discriminating (Tier C for setups that
# fire but don't clear it) so real ft_positions outcomes can calibrate a
# real threshold instead of committing to a guessed one that could silence
# CELT's own scanner entirely.
CELT_RR_MIN = 0.5
# Deep-ITM LEAPs genuinely quote 8-15% wide (celt_scanner.LEAP_MAX_SPREAD_PCT
# = 15.0 at construction) — the shared 6.0 would fail every CELT position on
# liquidity alone, with no near-miss band to soften it. 12.0 is tighter than
# the construction ceiling, so it still discriminates the wider third.
CELT_SPREAD_MAX_PCT = 12.0

BREAKEVEN_MAX_PCT = 3.5
BREAKEVEN_NEAR_PCT = 5.0
# Breakeven for a single-leg long option is extrinsic/spot exactly (breakeven
# = strike + premium for a call), so this gate doubles as an extrinsic-value
# gate for CELT (see celt_scanner.py's moneyness gate, which is deliberately
# NOT an extrinsic gate — this is where that check actually lives). Measured
# breakevens for a 0.45Δ consensus long call run 4.3-9.2% across realistic
# IV — the shared 3.5% band mis-tiers those as Tier C regardless of quality;
# 7.0 sits at the IV≈40% point of that curve (passing the low/mid-IV regime
# _pick_best_structure actually picks a long leg in) and 10.0 matches
# scanner.py's own hard-reject bound, so Tier B reads as "borderline but
# tradeable by the constructors' own standard". The 200W bounce needs no
# separate entry — its breakeven (0.65Δ, 60-100 DTE) already falls inside
# this band. Keyed on structure, not source: both technical consensus and
# the bounce emit structure="long_call".
_BREAKEVEN_BANDS = {
    "long_call": (7.0, 10.0),
    "long_put": (7.0, 10.0),
    "celt_leap": (15.0, 20.0),
}

# Only these two gates have a meaningful "nearly"; failing any other gate is
# Tier C even when it is the sole failure.
NEAR_MISS_GATES = {"quality", "breakeven"}


def classify(norm: dict) -> tuple[str, list[str]]:
    """Return (tier, gates_failed) for a normalised setup."""
    source = norm["source"]
    failed: list[str] = []

    quality_min = norm.get("quality_min", QUALITY_MIN[source])
    quality_near = norm.get("quality_near", QUALITY_NEAR[source])
    if norm["quality"] < quality_min:
        failed.append("quality")
    rr_min = norm.get("rr_min", RR_MIN)
    if norm["rr_ratio"] < rr_min:
        failed.append("rr")
    spread_max = norm.get("spread_max", SPREAD_MAX_PCT)
    if not norm["liquidity_ok"] or \
            norm["long_leg_spread_pct"] > spread_max or \
            norm["short_leg_spread_pct"] > spread_max:
        failed.append("liquidity")
    if norm["earnings_in_window"]:
        failed.append("earnings")
    dte_min = norm.get("dte_min", DTE_MIN)
    dte_max = norm.get("dte_max", DTE_MAX)
    if not (dte_min <= norm["dte_at_entry"] <= dte_max):
        failed.append("dte")
    breakeven_max = norm.get("breakeven_max", BREAKEVEN_MAX_PCT)
    breakeven_near = norm.get("breakeven_near", BREAKEVEN_NEAR_PCT)
    # Magnitude, not signed value: bear put spreads carry a positive
    # breakeven_move_pct meaning "the stock must fall this far".
    if abs(norm["breakeven_move_pct"]) > breakeven_max:
        failed.append("breakeven")
    if norm["trend_opposes"]:
        failed.append("trend")

    if not failed:
        return "A", []

    if len(failed) == 1 and failed[0] in NEAR_MISS_GATES:
        gate = failed[0]
        near = (
            gate == "quality"
            and norm["quality"] >= quality_min - quality_near
        ) or (
            gate == "breakeven"
            and abs(norm["breakeven_move_pct"]) <= breakeven_near
        )
        if near:
            return "B", failed

    return "C", failed


_BEARISH_STRUCTURES = {"bear_put_spread"}


def _direction_from_structure(structure: str) -> str:
    return "bearish" if structure in _BEARISH_STRUCTURES else "bullish"


def normalise(setup, source: str) -> dict:
    """Map a TradeSetup, TechnicalSetup, or CeltSetup onto one common shape.

    The three dataclasses share no base class and disagree on field names
    (long_strike/strike/leap_strike, net_debit/premium/leap_ask,
    score/signal_count/confidence, and CeltSetup has no structure/expiry/dte/
    liquidity_ok/spread-pct fields at all), so every consumer would otherwise
    need to branch on source. Gate overrides (dte_min/max, rr_min,
    quality_min/near, spread_max, breakeven_max/near) are ALWAYS emitted
    here — even when equal to the shared default — so classify() has one
    uniform `.get()` read pattern regardless of source.
    """
    quality_min_override = quality_near_override = None
    rr_min_override = None
    dte_min_override = dte_max_override = None
    spread_max_override = None

    if source == "scanner":
        structure = setup.structure
        expiry = setup.expiry
        dte = setup.dte
        long_strike = setup.long_strike
        short_strike = setup.short_strike
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
        liquidity_ok = bool(setup.liquidity_ok)
        long_leg_spread_pct = float(setup.long_leg_spread_pct)
        short_leg_spread_pct = float(setup.short_leg_spread_pct)
    elif source == "technical":
        structure = setup.structure
        expiry = setup.expiry
        dte = setup.dte
        long_strike = setup.strike
        short_strike = setup.short_strike
        entry_debit = setup.premium
        quality = setup.signal_count
        # 200W bounce is a distinct, independently-evaluated setup type (see
        # technical_scanner._score_200w_bounce) — tagging it lets the
        # aggregate stats separate its win rate from the 7-signal consensus's,
        # which is the whole point of tracking it in the first place. Every
        # other technical setup keeps detector=None ("—" in the UI), matching
        # existing behaviour.
        is_bounce = getattr(setup, "setup_type", "consensus") == "200w_bounce"
        detector = "200w_bounce" if is_bounce else None
        direction = setup.direction
        earnings = bool(setup.earnings_within_dte)
        # `direction` is itself the trend read for this source, so it can never
        # oppose itself. The gate is tautological here by construction.
        trend_opposes = False
        long_occ = setup.long_occ or ""
        short_occ = setup.short_occ or ""
        liquidity_ok = bool(setup.liquidity_ok)
        long_leg_spread_pct = float(setup.long_leg_spread_pct)
        short_leg_spread_pct = float(setup.short_leg_spread_pct)
        if is_bounce:
            dte_min_override, dte_max_override = BOUNCE_DTE_MIN, BOUNCE_DTE_MAX
            rr_min_override = BOUNCE_RR_MIN
            quality_min_override, quality_near_override = BOUNCE_QUALITY_MIN, BOUNCE_QUALITY_NEAR
    elif source == "celt":
        structure = "celt_leap"
        expiry = setup.leap_expiry
        dte = setup.leap_dte
        long_strike = setup.leap_strike
        short_strike = None
        entry_debit = setup.leap_ask
        quality = setup.confidence
        detector = None
        direction = "bullish"           # CELT only ever builds long calls
        # Not a flag to read here — scan_celt_setups already discards a
        # symbol outright when earnings falls within the next 7 days (its
        # own near-term check; a DTE-window check is meaningless at 270+
        # days), so nothing that reaches this function can have failed it.
        earnings = False
        trend_opposes = False
        long_occ = setup.leap_occ or ""
        short_occ = ""
        liquidity_ok = setup.leap_spread_pct >= 0 and setup.leap_spread_pct <= LEAP_MAX_SPREAD_PCT and setup.leap_oi >= 100
        long_leg_spread_pct = float(setup.leap_spread_pct) if setup.leap_spread_pct >= 0 else 100.0
        short_leg_spread_pct = 0.0
        dte_min_override, dte_max_override = CELT_DTE_MIN, CELT_DTE_MAX
        rr_min_override = CELT_RR_MIN
        spread_max_override = CELT_SPREAD_MAX_PCT
    else:
        raise ValueError(f"unknown source {source!r}")

    dte_min = dte_min_override if dte_min_override is not None else DTE_MIN
    dte_max = dte_max_override if dte_max_override is not None else DTE_MAX
    rr_min = rr_min_override if rr_min_override is not None else RR_MIN
    quality_min = quality_min_override if quality_min_override is not None else QUALITY_MIN[source]
    quality_near = quality_near_override if quality_near_override is not None else QUALITY_NEAR[source]
    spread_max = spread_max_override if spread_max_override is not None else SPREAD_MAX_PCT
    breakeven_max, breakeven_near = _BREAKEVEN_BANDS.get(structure, (BREAKEVEN_MAX_PCT, BREAKEVEN_NEAR_PCT))

    # net_debit / premium / leap_ask is the worst-case fill; every mark is
    # mid-to-mid. Carrying the entry mid makes a like-for-like series
    # recoverable later. None where the setup has no mid — never a guessed
    # value.
    entry_mid = getattr(setup, "entry_mid", None)

    dedup_key = "|".join([
        setup.symbol,
        detector or source,
        structure,
        expiry.isoformat(),
        str(float(long_strike)),
        str(float(short_strike)) if short_strike is not None else "",
    ])

    return {
        "source": source,
        "dedup_key": dedup_key,
        "symbol": setup.symbol,
        "detector": detector,
        "structure": structure,
        "direction": direction,
        "expiry": expiry,
        "dte_at_entry": dte,
        "dte_min": dte_min,
        "dte_max": dte_max,
        "rr_min": rr_min,
        "quality_min": quality_min,
        "quality_near": quality_near,
        "spread_max": spread_max,
        "breakeven_max": breakeven_max,
        "breakeven_near": breakeven_near,
        "long_strike": float(long_strike),
        "short_strike": float(short_strike) if short_strike is not None else None,
        "long_occ": long_occ,
        "short_occ": short_occ,
        "entry_debit": float(entry_debit),
        "entry_mid": float(entry_mid) if entry_mid is not None else None,
        "entry_stock_price": float(setup.stock_price),
        "quality": quality,
        "rr_ratio": float(setup.rr_ratio),
        "breakeven_move_pct": float(setup.breakeven_move_pct),
        "liquidity_ok": bool(liquidity_ok),
        "long_leg_spread_pct": float(long_leg_spread_pct),
        "short_leg_spread_pct": float(short_leg_spread_pct),
        "earnings_in_window": earnings,
        "trend_opposes": trend_opposes,
    }


MAX_MARK_FAILURES = 8


def _as_date(value) -> date | None:
    """Coerce a stored expiry to a `date`. Supabase `date` columns round-trip
    as ISO strings, so both forms reach here."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_datetime(value) -> datetime | None:
    """Coerce a stored timestamp to a `datetime`. Supabase `timestamptz`
    columns round-trip as ISO strings, so both forms reach here."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


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
    - `last_pnl_pct` is transport-only *in this dict*: it reports this mark's
      P&L to the caller and must be popped before the dict is used as a row
      update. `ft_positions` does have a `last_pnl_pct` column, but the caller
      writes it deliberately from the mark it just took — do not conflate the
      two, or a future non-column transport field will be written blindly.
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
                "entry_mid": n["entry_mid"],
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
        # ET, not UTC: see the ET constant above.
        today = now.astimezone(ET).date()
        positions = ft_store.list_open_positions()
        if not positions:
            return 0

        symbols = sorted({
            occ for p in positions
            for occ in (p.get("long_occ"), p.get("short_occ")) if occ
        })
        quotes = fetch_quotes(symbols)

        marked = 0
        resolved_at_expiry = 0
        for p in positions:
            try:
                long_mid = quotes.get(p.get("long_occ"))
                short_occ = p.get("short_occ") or ""
                short_mid = quotes.get(short_occ) if short_occ else 0.0

                if long_mid is None or (short_occ and short_mid is None):
                    expiry = _as_date(p.get("expiry"))
                    if expiry is not None and today > expiry:
                        # An expired option cannot be quoted, so the mark that
                        # would have closed this position never arrives. Check
                        # expiry BEFORE the failure counter: otherwise every
                        # expiring position accrues failures and closes as
                        # `unpriceable`, which is excluded from every statistic
                        # — and the excluded population is not random (spreads
                        # that ground sideways, and winners that hit T1 then
                        # faded), so the win rate silently censors itself.
                        last = p.get("last_pnl_pct")
                        if last is None:
                            # Never successfully marked in its whole life —
                            # there is no P&L to close it with, so this really
                            # is the spec's `unpriceable` case, not a censored
                            # outcome. Recording it as expired-with-null would
                            # leave a row counted in no bucket at all.
                            ft_store.update_position(p["id"], {
                                "status": "unpriceable", "closed_ts": now,
                            })
                            resolved_at_expiry += 1
                            continue
                        upd = {"status": "expired", "closed_ts": now}
                        upd["realized_pnl_pct"] = realized_pnl({**p, **upd}, last)
                        ft_store.update_position(p["id"], upd)
                        resolved_at_expiry += 1
                        continue

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

                upd = apply_mark({**p, "expiry": _as_date(p.get("expiry"))},
                                 pnl, now, today)

                # Transport-only key, popped so it is never written blindly.
                final = upd.pop("last_pnl_pct", None)
                # Persisted deliberately, as a real column: an expiring position
                # gets no final quote, so this is the only value left to close
                # it with.
                upd["last_pnl_pct"] = pnl
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

        if marked == 0 and resolved_at_expiry == 0:
            # Not one open position could be priced. The realistic cause is a
            # symbol-format mismatch between what we request and what Schwab
            # echoes back, which raises no exception anywhere and would quietly
            # close the entire dataset as `unpriceable` in MAX_MARK_FAILURES
            # ticks. Make it visible in the logs instead.
            logger.error(
                "forward test: 0 of %d open positions could be marked — every "
                "quote lookup missed. Check OCC symbol round-tripping in "
                "fetch_quotes; unfixed, the whole dataset closes as "
                "unpriceable within %d ticks.", len(positions), MAX_MARK_FAILURES)

        logger.info("forward test: marked %d/%d open positions (%d resolved at expiry)",
                    marked, len(positions), resolved_at_expiry)
        return marked
    except Exception as e:
        logger.exception("forward test: mark_open_positions failed: %s", e)
        return 0


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_pnl": 0.0, "avg_mfe": 0.0, "avg_mae": 0.0, "avg_hold_days": 0.0}
    pnls = [r["realized_pnl_pct"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)          # breakeven is not a win

    # Contextualises any win-rate comparison across structures with very
    # different natural holding periods — a 30-60 DTE spread and a 270-760
    # DTE CELT LEAP sitting in the same aggregate without this looks like
    # they're being compared on edge, when they're really being compared on
    # how long they were held.
    hold_days: list[float] = []
    for r in rows:
        entry = _as_datetime(r.get("entry_ts"))
        closed = _as_datetime(r.get("closed_ts"))
        if entry is not None and closed is not None:
            try:
                hold_days.append((closed - entry).total_seconds() / 86400)
            except TypeError:
                continue  # naive/aware mismatch on a malformed row

    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "avg_pnl": round(sum(pnls) / n, 1),
        "avg_mfe": round(sum((r.get("mfe_pct") or 0.0) for r in rows) / n, 1),
        "avg_mae": round(sum((r.get("mae_pct") or 0.0) for r in rows) / n, 1),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0.0,
    }


def aggregate(positions: list[dict]) -> dict:
    """Summarise closed positions. Open ones are counted, never averaged in —
    including them would quietly dilute every number."""
    closed = [p for p in positions
              if p.get("status") in {"target2", "stopped", "expired"}
              and p.get("realized_pnl_pct") is not None]
    open_count = sum(1 for p in positions if p.get("status") in {"open", "target1"}
                     and p.get("realized_pnl_pct") is None)
    unpriceable = sum(1 for p in positions if p.get("status") == "unpriceable")

    def group(key):
        out: dict[str, list[dict]] = {}
        for p in closed:
            out.setdefault(str(p.get(key) or "—"), []).append(p)
        return {k: _stats(v) for k, v in sorted(out.items())}

    return {
        "overall": _stats(closed),
        "by_tier": group("tier"),
        "by_detector": group("detector"),
        "by_source": group("source"),
        "closed_count": len(closed),
        "open_count": open_count,
        "unpriceable_count": unpriceable,
    }
