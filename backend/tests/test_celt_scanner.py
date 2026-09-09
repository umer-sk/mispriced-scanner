"""CELT scanner: front-month IV and LEAP tradeability.

The two behaviours pinned here both had the same failure shape — the scanner
silently declining to fire, or firing on a leg nobody could actually buy.
"""
from datetime import date, datetime, timedelta, timezone

from celt_scanner import (LEAP_MAX_SPREAD_PCT, _find_best_leap,
                          _front_month_iv, _score_sentiment, _spread_pct)
from models import OptionChainData, OptionContract


def _c(strike, dte, iv=0.30, delta=0.75, bid=10.0, ask=10.2, oi=1000):
    return OptionContract(
        strike=strike, expiry=date.today() + timedelta(days=dte), dte=dte,
        bid=bid, ask=ask, mid=round((bid + ask) / 2, 2), last=(bid + ask) / 2,
        volume=100, open_interest=oi, iv=iv, delta=delta,
        gamma=0.01, theta=-0.02, vega=0.30,
        theoretical_value=(bid + ask) / 2, in_the_money=delta > 0.5,
    )


def _chain(calls, stock_price=100.0, iv30=0.60, iv_rank=58.0, puts=None):
    return OptionChainData(
        symbol="NVDA", stock_price=stock_price, iv30=iv30, hv30=0.55,
        iv_rank=iv_rank, iv_percentile=iv_rank,
        timestamp=datetime.now(timezone.utc),
        calls=calls, puts=puts or [], is_stale=False,
    )


# ─── front-month IV ──────────────────────────────────────────────────────────

def test_front_month_iv_ignores_the_leap_tail():
    """The bug: CELT fetches days_out=730, so chain.iv30 averages front weeklies
    with two-year LEAPs. In a crash the front spikes while LEAPs barely move,
    dragging the blend below the iv_rank gate — the scanner cannot fire in the
    exact regime it exists for."""
    calls = [
        _c(100, 30, iv=0.95),    # front month, crashing
        _c(100, 35, iv=0.92),
        _c(100, 400, iv=0.45),   # LEAP, barely moved
        _c(100, 700, iv=0.40),
    ]
    front = _front_month_iv(_chain(calls))
    assert abs(front - 0.935) < 0.001          # mean of the two front contracts
    # The blended value the old code used would have been ~0.68 — far lower.
    assert front > sum(c.iv for c in calls) / len(calls)


def test_front_month_iv_only_uses_near_the_money_strikes():
    calls = [
        _c(100, 30, iv=0.90),    # ATM
        _c(160, 30, iv=1.80),    # far OTM, high skew — must not drag the mean
    ]
    assert abs(_front_month_iv(_chain(calls)) - 0.90) < 0.001


def test_front_month_iv_widens_the_window_when_the_front_is_empty():
    # Some symbols have no 20-45 DTE expiry on a given day.
    calls = [_c(100, 60, iv=0.80)]
    assert abs(_front_month_iv(_chain(calls)) - 0.80) < 0.001


def test_front_month_iv_returns_zero_when_nothing_usable():
    # Must be distinguishable from "IV is low" — the caller skips the symbol
    # rather than scoring it as calm.
    assert _front_month_iv(_chain([_c(100, 700, iv=0.40)])) == 0.0
    assert _front_month_iv(_chain([], stock_price=0.0)) == 0.0


def test_front_month_iv_ignores_contracts_with_no_iv():
    calls = [_c(100, 30, iv=0.90), _c(100, 31, iv=0.0)]
    assert abs(_front_month_iv(_chain(calls)) - 0.90) < 0.001


# ─── the crash scenario end to end ───────────────────────────────────────────

def test_crash_regime_now_scores_instead_of_being_skipped():
    """A genuine crash: front IV 95%, LEAP IV 40%. The 730-day blend lands
    around 60 and used to fail the iv_rank >= 60 gate, so sentiment scored 0.0
    and the total capped at 2.0 against a 2.2 threshold — nothing could ever
    qualify. Scoring off the front-month rank fixes that."""
    blended_rank = 58.0        # what the old chain-level value looked like
    assert _score_sentiment(_chain([]), blended_rank)[0] == 0.0

    front_rank = 88.0          # front-month IV ranked against realised vol
    score, details = _score_sentiment(_chain([]), front_rank)
    assert score > 0.0
    assert details["iv_rank"] == front_rank


def test_sentiment_still_declines_a_genuinely_calm_tape():
    score, details = _score_sentiment(_chain([]), 30.0)
    assert score == 0.0
    assert details["skipped"] is True


def test_sentiment_thresholds_and_weighting():
    # 1.2 weight; 0.7/0.5/0.3 bands.
    assert _score_sentiment(_chain([]), 85.0)[0] == round(0.7 * 1.2, 3)
    assert _score_sentiment(_chain([]), 70.0)[0] == round(0.5 * 1.2, 3)
    assert _score_sentiment(_chain([]), 60.0)[0] == round(0.3 * 1.2, 3)
    assert _score_sentiment(_chain([]), 59.9)[0] == 0.0


def test_put_call_skew_still_adds_on_top():
    puts = [_c(100, 400, oi=1600)]
    calls = [_c(100, 400, oi=1000)]
    score, details = _score_sentiment(_chain(calls, puts=puts), 88.0)
    assert details["leap_pc_ratio"] == 1.6
    assert score == round(min(1.0, 0.7 + 0.2) * 1.2, 3)


# ─── LEAP tradeability ───────────────────────────────────────────────────────

def test_spread_pct_is_a_percentage():
    assert _spread_pct(_c(100, 400, bid=9.5, ask=10.5)) == 10.0


def test_spread_pct_of_an_unpriceable_leg_is_maximally_wide():
    assert _spread_pct(_c(100, 400, bid=0.0, ask=0.0)) == 100.0


def test_wide_leap_is_rejected():
    """leap_mid is what the card shows, leap_ask is what you pay. A 20%-wide
    market makes the displayed entry meaningfully better than the real one."""
    wide = _c(100, 400, bid=9.0, ask=11.0)      # 20% of mid
    assert _spread_pct(wide) > LEAP_MAX_SPREAD_PCT
    assert _find_best_leap([wide], stock_price=120.0) is None


def test_tight_leap_is_accepted():
    tight = _c(100, 400, bid=9.9, ask=10.1)     # 2%
    assert _find_best_leap([tight], stock_price=120.0) is tight


def test_spread_gate_applies_to_the_relaxed_fallback_too():
    # OI 200 and delta 0.88 only match the relaxed branch; the spread gate
    # must still bite there, or the fallback becomes an escape hatch.
    wide = _c(100, 400, delta=0.88, oi=200, bid=9.0, ask=11.0)
    assert _find_best_leap([wide], stock_price=120.0) is None


def test_boundary_spread_is_accepted():
    at_limit = _c(100, 400, bid=9.25, ask=10.75)   # exactly 15.0% of mid 10.0
    assert _spread_pct(at_limit) == LEAP_MAX_SPREAD_PCT
    assert _find_best_leap([at_limit], stock_price=120.0) is at_limit


def test_deepest_itm_still_wins_among_tradeable_candidates():
    shallow = _c(100, 400, delta=0.68, bid=9.9, ask=10.1)
    deep = _c(80, 400, delta=0.83, bid=19.9, ask=20.1)
    assert _find_best_leap([shallow, deep], stock_price=120.0) is deep


def test_a_wide_deep_leg_does_not_beat_a_tight_shallow_one():
    # The old code would have picked the deep one on delta alone.
    tight_shallow = _c(100, 400, delta=0.68, bid=9.9, ask=10.1)
    wide_deep = _c(80, 400, delta=0.83, bid=18.0, ask=22.0)   # 20%
    assert _find_best_leap([tight_shallow, wide_deep], stock_price=120.0) is tight_shallow


def test_short_dated_calls_are_never_leaps():
    assert _find_best_leap([_c(100, 200, delta=0.75, bid=9.9, ask=10.1)], stock_price=120.0) is None


def test_shallow_itm_leap_rejected_despite_qualifying_delta():
    """A contract can sit inside the 0.65-0.85 delta band while barely being
    ITM at all when IV is high — verified in celt_scanner.py's comment on
    LEAP_MAX_MONEYNESS. The moneyness gate must reject it even though delta
    alone would accept it."""
    shallow = _c(98, 400, delta=0.70, bid=9.9, ask=10.1)  # 98/100 = 0.98, > 0.85
    assert _find_best_leap([shallow], stock_price=100.0) is None


def test_deep_itm_leap_within_moneyness_is_accepted():
    deep = _c(80, 400, delta=0.70, bid=19.9, ask=20.1)  # 80/100 = 0.80, within 0.85
    assert _find_best_leap([deep], stock_price=100.0) is deep


# ─── composition: the wiring, not just the helpers ───────────────────────────

def _synthetic_closes(n_calm=250, n_crash=20, seed=1):
    """A year of closes ending in a crash, so HV30 rises the way it really would."""
    import random
    rnd = random.Random(seed)
    closes = [100.0]
    for _ in range(n_calm):
        closes.append(closes[-1] * (1 + rnd.gauss(0, 0.015)))
    for _ in range(n_crash):
        closes.append(closes[-1] * (1 + rnd.gauss(-0.03, 0.05)))
    return closes


def test_crash_chain_composes_into_a_qualifying_sentiment_score():
    """The end-to-end path the earlier test only mimicked: a real crash chain
    through _front_month_iv -> iv_rank_from_decimal -> _score_sentiment.

    Pins BOTH fixes at once. Reverting the call site to chain.iv_rank fails
    here, and so does routing through _compute_iv_rank, whose legacy guard
    divides a 120% front IV by 100 and ranks it 0.
    """
    from schwab_client import iv_rank_from_decimal

    calls = [
        _c(100, 30, iv=1.20),    # front month at 120% — capitulation
        _c(100, 35, iv=1.15),
        _c(100, 400, iv=0.45),   # LEAPs barely moved
        _c(100, 700, iv=0.40),
    ]
    chain = _chain(calls, iv30=0.68, iv_rank=55.0)   # the misleading blend

    front_iv = _front_month_iv(chain)
    assert front_iv > 1.0, "front-month IV in a crash exceeds 100%"

    rank, _ = iv_rank_from_decimal(front_iv, _synthetic_closes())
    assert rank >= 60, f"front-month rank {rank} must clear the sentiment gate"

    score, details = _score_sentiment(chain, rank)
    assert score > 0.0
    assert details.get("skipped") is not True

    # And the blended value the old code used would still be rejected.
    assert _score_sentiment(chain, chain.iv_rank)[0] == 0.0


def test_iv_above_100pct_is_not_divided_by_100():
    """The exact regression: _compute_iv_rank's legacy guard treats any value
    over 1.0 as a whole-number percent. At 99% IV it ranks 100; at 101% it
    ranks 0. iv_rank_from_decimal must not have that cliff."""
    from schwab_client import _compute_iv_rank, iv_rank_from_decimal

    closes = _synthetic_closes()
    assert _compute_iv_rank(1.01, closes)[0] == 0.0        # the bug, still there by design
    assert iv_rank_from_decimal(1.01, closes)[0] > 60      # the fix
    assert iv_rank_from_decimal(1.50, closes)[0] > 60


def test_front_month_iv_widens_the_strike_band_for_wide_spacing():
    # Spot 18.60 with $2.50 strike spacing: nothing inside +/-5%.
    calls = [_c(17.5, 30, iv=0.85), _c(20.0, 30, iv=0.83)]
    assert _front_month_iv(_chain(calls, stock_price=18.60)) > 0.0


def _crash_closes():
    """A year ending in a deep drawdown below SMA200 with HV genuinely expanding.

    The crash tail needs real dispersion, not a smooth decline: a constant
    daily percentage move has zero variance, so HV30 computes to 0 and the
    volatility pre-screen rejects it before sentiment is ever reached.
    """
    import random
    rnd = random.Random(7)
    closes = [100.0]
    for _ in range(252):
        closes.append(closes[-1] * (1 + rnd.gauss(0.0012, 0.008)))    # calm climb
    for _ in range(30):
        closes.append(closes[-1] * (1 + rnd.gauss(-0.025, 0.035)))    # capitulation
    return closes


def test_scan_wires_front_month_iv_into_the_score(monkeypatch):
    """Pins the CALL SITE, not just the helpers.

    Routing this through _compute_iv_rank instead of iv_rank_from_decimal, or
    reverting to chain.iv_rank, must fail here — testing the helpers alone
    leaves the wiring free to regress silently.
    """
    import celt_scanner as cs

    closes = _crash_closes()
    spot = closes[-1]
    chain = _chain(
        calls=[
            _c(spot, 30, iv=1.20),                                  # front at 120%
            _c(spot, 35, iv=1.15),
            _c(spot * 0.6, 400, iv=0.45, delta=0.80,
               bid=spot * 0.42, ask=spot * 0.43, oi=2000),           # tradeable LEAP
        ],
        stock_price=spot, iv30=0.68, iv_rank=55.0,                   # misleading blend
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: None)

    setups = cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0)

    assert len(setups) == 1, "a 45% drawdown with 120% front IV must qualify"
    s = setups[0]
    assert s.iv_rank >= 60, f"setup carries the front-month rank, got {s.iv_rank}"
    assert s.sentiment_score > 0.0
    assert s.signal_score >= 2.2
    assert 0 <= s.leap_spread_pct <= LEAP_MAX_SPREAD_PCT


def test_leap_occ_is_built_when_the_contract_carries_no_occ_symbol(monkeypatch):
    """CeltSetup.leap_occ lets a position saved to the trade journal be
    re-priced later via schwab_client.fetch_quotes — the same gap
    TradeSetup.long_occ / TechnicalSetup.long_occ already closed for their
    own structures. Recomputes the expected value via build_occ rather than
    hardcoding the string, so it survives an unrelated format change."""
    import celt_scanner as cs
    from occ import build_occ

    closes = _crash_closes()
    spot = closes[-1]
    leap_strike = spot * 0.6
    leap = _c(leap_strike, 400, iv=0.45, delta=0.80,
              bid=spot * 0.42, ask=spot * 0.43, oi=2000)
    chain = _chain(
        calls=[_c(spot, 30, iv=1.20), _c(spot, 35, iv=1.15), leap],
        stock_price=spot, iv30=0.68, iv_rank=55.0,
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: None)

    setups = cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0)

    assert len(setups) == 1
    expected = build_occ("NVDA", leap.expiry, False, leap.strike)
    assert setups[0].leap_occ == expected


def test_scan_returns_nothing_when_qqq_is_not_below_its_own_ma50(monkeypatch):
    """Even a perfectly qualifying single-name crash must not fire when the
    broader market isn't stressed — this is the systemic-vs-idiosyncratic
    gate, verified against the exact chain that qualifies in
    test_scan_wires_front_month_iv_into_the_score."""
    import celt_scanner as cs

    closes = _crash_closes()
    spot = closes[-1]
    chain = _chain(
        calls=[
            _c(spot, 30, iv=1.20), _c(spot, 35, iv=1.15),
            _c(spot * 0.6, 400, iv=0.45, delta=0.80,
               bid=spot * 0.42, ask=spot * 0.43, oi=2000),
        ],
        stock_price=spot, iv30=0.68, iv_rank=55.0,
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: None)

    # QQQ above its own MA50 — no market-wide stress.
    assert cs.scan_celt_setups(["NVDA"], qqq_price=460.0, qqq_ma50=450.0) == []
    # Confirm the SAME chain does qualify once QQQ is below its MA50.
    assert len(cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0)) == 1


def test_scan_fails_closed_when_qqq_ma50_is_unavailable(monkeypatch):
    """qqq_ma50=0.0 signals a degraded fetch (see market_context._fetch_index_mas).
    A naive `qqq_price < qqq_ma50` comparison is always False against 0.0,
    which would let CELT fire completely unguarded on a bad-data day — this
    must fail closed instead, even with an otherwise-qualifying chain."""
    import celt_scanner as cs

    closes = _crash_closes()
    spot = closes[-1]
    chain = _chain(
        calls=[
            _c(spot, 30, iv=1.20), _c(spot, 35, iv=1.15),
            _c(spot * 0.6, 400, iv=0.45, delta=0.80,
               bid=spot * 0.42, ask=spot * 0.43, oi=2000),
        ],
        stock_price=spot, iv30=0.68, iv_rank=55.0,
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: None)

    assert cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=0.0) == []


def test_scan_skips_a_symbol_with_earnings_in_the_next_7_days(monkeypatch):
    """Not a DTE-window exclusion (that's nonsensical at 270+ days — see the
    comment at the call site) — a near-term-only check against a fresh
    catalyst that could extend the crash right as you're entering."""
    import celt_scanner as cs
    from datetime import date, timedelta

    closes = _crash_closes()
    spot = closes[-1]
    chain = _chain(
        calls=[
            _c(spot, 30, iv=1.20), _c(spot, 35, iv=1.15),
            _c(spot * 0.6, 400, iv=0.45, delta=0.80,
               bid=spot * 0.42, ask=spot * 0.43, oi=2000),
        ],
        stock_price=spot, iv30=0.68, iv_rank=55.0,
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)

    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: date.today() + timedelta(days=3))
    assert cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0) == []

    # Same chain, earnings well past the 7-day window — must qualify.
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: date.today() + timedelta(days=30))
    assert len(cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0)) == 1


def test_scan_drops_the_symbol_when_every_leap_is_too_wide(monkeypatch):
    """A market-wide spread blowout must not be silently indistinguishable
    from 'no qualifying strikes'."""
    import celt_scanner as cs

    closes = _crash_closes()
    spot = closes[-1]
    chain = _chain(
        calls=[
            _c(spot, 30, iv=1.20),
            _c(spot * 0.6, 400, iv=0.45, delta=0.80,
               bid=spot * 0.36, ask=spot * 0.48, oi=2000),           # ~28% wide
        ],
        stock_price=spot, iv30=0.68,
    )
    monkeypatch.setattr(cs, "_fetch_closes", lambda syms: {"NVDA": closes})
    monkeypatch.setattr(cs, "fetch_option_chain", lambda sym, days_out=105, strike_count=20: chain)
    monkeypatch.setattr(cs, "_fetch_earnings_date", lambda sym: None)

    assert cs.scan_celt_setups(["NVDA"], qqq_price=400.0, qqq_ma50=450.0) == []
