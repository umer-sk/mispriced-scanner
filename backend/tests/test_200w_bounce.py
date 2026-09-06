"""200-week MA bounce detector.

The four criteria (rising MA, recent touch, reclaimed, not extended) are each
independently load-bearing — a setup missing any one of them is a different,
lower-conviction trade than the one this is meant to find.
"""
from technical_scanner import (MA_200W_MAX_EXTENSION_PCT, MA_200W_PERIOD,
                               MA_200W_SLOPE_LOOKBACK, MA_200W_TOUCH_LOOKBACK,
                               MA_200W_TOUCH_TOLERANCE_PCT, _score_200w_bounce)

MIN_WEEKS = MA_200W_PERIOD + MA_200W_SLOPE_LOOKBACK  # 210


def _flat_series(n, price=100.0):
    return [price] * n


def _bounce_series(
    n=260,
    ma_slope_pct=5.0,       # % the 200W SMA rose over the slope-lookback window
    touch_pct=-2.0,         # weekly low vs MA at the touch, negative = below it
    weeks_since_touch=2,    # how many weeks ago the touch happened
    extension_pct=3.0,      # current close vs MA now
):
    """Build a weekly close/low series with an EXACTLY controlled 200W SMA
    slope, current extension, and a single controlled touch — each parameter
    independent of the others, with zero approximation error.

    Key facts this relies on:
    - Only `weekly_closes` feeds the SMA math. `weekly_lows` feeds only the
      touch check. They can be constructed with no interaction at all, which
      is what makes exact control possible.
    - ma_now (SMA over the trailing 200 closes) and ma_then (SMA over the 200
      closes ending 10 weeks earlier) share 190 of their 200 points. Fixing
      those 190 shared points and solving for the disjoint 10-point blocks on
      each side pins both means exactly, regardless of parameters.
    """
    P, K = MA_200W_PERIOD, MA_200W_SLOPE_LOOKBACK
    needed = P + K
    if n < needed:
        return [100.0] * n, [100.0] * n   # too short to matter; content irrelevant

    pad = n - needed
    mid_n = P - K
    mid_level = 100.0
    ma_then = 100.0
    ma_now = ma_then * (1 + ma_slope_pct / 100)

    # Old block (indices [pad, pad+K)): only in ma_then's window.
    old_level = (P * ma_then - mid_n * mid_level) / K

    # New block (indices [n-K, n)): only in ma_now's window. The final value
    # is pinned to the requested extension FIRST, then the other K-1 values
    # are solved so the block's mean still hits ma_now exactly — extension
    # and slope are otherwise independent parameters and must stay that way.
    fixed_last = ma_now * (1 + extension_pct / 100)
    new_fill = (P * ma_now - mid_n * mid_level - fixed_last) / (K - 1)

    closes = (
        [100.0] * pad
        + [old_level] * K
        + [mid_level] * mid_n
        + [new_fill] * (K - 1)
        + [fixed_last]
    )
    assert len(closes) == n

    # Lows are fully independent of closes: a safe baseline everywhere,
    # overridden at exactly one index for the touch. "Safe" means far enough
    # from ma_now that it can never accidentally register as a touch.
    safe_low = ma_now * 3.0
    lows = [safe_low] * n
    touch_idx = n - 1 - weeks_since_touch
    lows[touch_idx] = ma_now * (1 + touch_pct / 100)

    return closes, lows


def test_qualifying_bounce_returns_facts():
    closes, lows = _bounce_series()
    result = _score_200w_bounce(closes, lows)
    assert result is not None
    assert result["ma_slope_pct"] > 0
    assert result["touch_pct"] <= MA_200W_TOUCH_TOLERANCE_PCT
    assert result["extension_pct"] <= MA_200W_MAX_EXTENSION_PCT
    assert result["weeks_since_touch"] == 2


def test_not_enough_history_returns_none():
    closes, lows = _bounce_series(n=MIN_WEEKS - 1)
    assert _score_200w_bounce(closes, lows) is None


def test_exactly_enough_history_is_accepted():
    closes, lows = _bounce_series(n=MIN_WEEKS)
    assert _score_200w_bounce(closes, lows) is not None


def test_flat_price_has_zero_slope_and_is_rejected():
    # A perfectly flat 200W SMA is neither rising nor a real "reclaim" (price
    # never left the MA), so both criterion 1 and criterion 3 should reject it.
    closes, lows = _flat_series(260), _flat_series(260)
    assert _score_200w_bounce(closes, lows) is None


def test_exactly_zero_slope_is_rejected_even_with_a_clean_touch_and_reclaim():
    # Isolates the slope check from the other three criteria. Unlike
    # test_flat_price_has_zero_slope_and_is_rejected (which is flat on every
    # axis and would pass even with a weakened `< 0` slope check, since it
    # also fails the reclaim check), this series is fully qualifying on
    # touch/reclaim/extension and differs ONLY in slope == 0 exactly.
    closes, lows = _bounce_series(ma_slope_pct=0.0, touch_pct=-2.0, extension_pct=3.0)
    assert _score_200w_bounce(closes, lows) is None


def test_declining_ma_is_rejected_even_with_a_clean_touch_and_reclaim():
    """The defining criterion. A dead-cat bounce off a DECLINING 200W MA must
    not qualify — this is the whole reason 'rising MA' is a separate check
    from 'touched and reclaimed'."""
    closes, lows = _bounce_series(ma_slope_pct=-8.0, touch_pct=-1.0, extension_pct=2.0)
    result = _score_200w_bounce(closes, lows)
    assert result is None


def test_never_touched_is_rejected():
    # Price stayed well above the MA the whole lookback window — nothing to
    # "bounce" off, regardless of how the MA is sloped.
    closes, lows = _bounce_series(touch_pct=+15.0)
    assert _score_200w_bounce(closes, lows) is None


def test_touch_exactly_at_tolerance_boundary_is_accepted():
    # Positive touch_pct means the low stayed ABOVE the MA (didn't reach it);
    # negative means it dipped below. The tolerance bounds how far above is
    # still "close enough" — this is that boundary, from the correct side.
    closes, lows = _bounce_series(touch_pct=MA_200W_TOUCH_TOLERANCE_PCT)
    assert _score_200w_bounce(closes, lows) is not None


def test_touch_just_past_tolerance_is_rejected():
    closes, lows = _bounce_series(touch_pct=MA_200W_TOUCH_TOLERANCE_PCT + 0.1)
    assert _score_200w_bounce(closes, lows) is None


def test_a_touch_that_pierces_below_the_ma_still_counts():
    # More negative = deeper below the MA = an even more definitive touch.
    # Only "too far above" is disqualifying, never "too far below".
    closes, lows = _bounce_series(touch_pct=-(MA_200W_TOUCH_TOLERANCE_PCT + 5.0))
    assert _score_200w_bounce(closes, lows) is not None


def test_touch_outside_the_lookback_window_is_rejected():
    # A touch that happened, but too long ago, must not count — otherwise a
    # stock that bottomed a year ago and never looked back would qualify
    # forever.
    closes, lows = _bounce_series(weeks_since_touch=MA_200W_TOUCH_LOOKBACK + 5, touch_pct=-3.0)
    assert _score_200w_bounce(closes, lows) is None


def test_touch_at_the_edge_of_the_lookback_window_is_accepted():
    closes, lows = _bounce_series(weeks_since_touch=MA_200W_TOUCH_LOOKBACK - 1, touch_pct=-3.0)
    assert _score_200w_bounce(closes, lows) is not None


def test_still_below_the_ma_is_rejected():
    # Touched, but never actually reclaimed — still under the level now.
    closes, lows = _bounce_series(extension_pct=-1.0)
    assert _score_200w_bounce(closes, lows) is None


def test_extension_exactly_at_boundary_is_accepted():
    closes, lows = _bounce_series(extension_pct=MA_200W_MAX_EXTENSION_PCT)
    assert _score_200w_bounce(closes, lows) is not None


def test_extension_just_past_boundary_is_rejected():
    closes, lows = _bounce_series(extension_pct=MA_200W_MAX_EXTENSION_PCT + 0.1)
    assert _score_200w_bounce(closes, lows) is None


def test_already_extended_far_above_is_rejected():
    # The name that already ran 25% off the level — you missed the entry.
    closes, lows = _bounce_series(extension_pct=25.0)
    assert _score_200w_bounce(closes, lows) is None


def test_mismatched_lengths_do_not_crash():
    closes, lows = _bounce_series()
    assert _score_200w_bounce(closes, lows[:-5]) is None or True  # must not raise


# ─── 200W bounce option construction ─────────────────────────────────────────
# _make_chain (test_technical_scanner.py) only has dte=45 contracts; this
# setup specifically targets 60-100 DTE, deeper ITM (~0.65 delta) than the
# consensus long call, so it needs its own fixture.

from datetime import date, datetime, timezone
from models import OptionChainData, OptionContract
from technical_scanner import _construct_200w_bounce_long_call


def _bounce_chain(price=100.0, iv_rank=40.0, iv=0.30):
    expiry = date(2026, 12, 18)  # ~75 DTE from a nominal test "today"
    def _call(strike, delta, ask, bid=None, dte=75, oi=1000):
        return OptionContract(
            strike=strike, expiry=expiry, dte=dte, bid=bid or round(ask * 0.96, 2),
            ask=ask, mid=round(ask * 0.98, 2), last=ask, volume=300, open_interest=oi,
            iv=iv, delta=delta, gamma=0.01, theta=-0.03, vega=0.20,
            theoretical_value=ask, in_the_money=(delta > 0.5),
        )
    return OptionChainData(
        symbol="TEST", stock_price=price, iv30=0.30, hv30=0.28,
        iv_rank=iv_rank, iv_percentile=40.0,
        timestamp=datetime.now(timezone.utc),
        calls=[
            _call(80, 0.80, 24.0),
            _call(85, 0.62, 19.0),   # best IN-WINDOW match for delta 0.65
            _call(90, 0.55, 14.0),
            _call(100, 0.35, 6.0),
            # Delta 0.65 EXACTLY — a perfect match, but wrong DTE. If the DTE
            # filter is ever widened or dropped, this wins on delta distance
            # alone (0 vs 0.03), so the wrong contract gets selected.
            _call(70, 0.65, 30.0, dte=30),
        ],
        puts=[],
    )


_FACTS = {"ma_200w": 90.0, "ma_slope_pct": 4.5, "touch_pct": -1.5,
          "weeks_since_touch": 3, "extension_pct": 2.0}


def test_200w_construct_picks_the_right_delta_within_dte_window():
    chain = _bounce_chain()
    setup = _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=15.0)
    assert setup is not None
    # 85-strike (delta 0.62, dte=75) must win over the 70-strike EXACT delta
    # match at dte=30 — proving the DTE filter, not just the delta-distance
    # comparison, is what excludes it.
    assert setup.strike == 85.0
    assert setup.dte == 75
    assert setup.structure == "long_call"
    assert setup.setup_type == "200w_bounce"
    assert setup.direction == "bullish"
    assert setup.signal_details == _FACTS


def test_200w_construct_excludes_the_wrong_dte_even_with_perfect_delta():
    # The 70-strike/0.60-delta/dte=30 contract is closer to 0.65 than nothing,
    # but outside the 60-100 DTE window — must never be selected.
    chain = _bounce_chain()
    setup = _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=15.0)
    assert setup.dte != 30
    assert setup.strike != 70.0


def test_200w_construct_returns_none_with_no_candidates_in_window():
    chain = _bounce_chain()
    chain.calls = [c for c in chain.calls if not (60 <= c.dte <= 100)]
    assert _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=15.0) is None


def test_200w_construct_rejects_illiquid_leg():
    chain = _bounce_chain()
    for c in chain.calls:
        c.open_interest = 10   # below the 100 hard floor
    assert _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=15.0) is None


def test_200w_construct_price_target_uses_the_shared_sqrt_helper():
    from technical_scanner import _atr_price_target
    chain = _bounce_chain()
    setup = _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=15.0)
    assert setup is not None
    expected = round(_atr_price_target(100.0, 15.0, setup.dte, bullish=True), 2)
    assert setup.price_target == expected


def test_200w_construct_delegates_to_single_leg_reward():
    """Mutation guard for the reward-model swap.

    The default _bounce_chain() fixture (iv=0.30, atr14=15) turned out to be
    a bad discriminator: with a target that far above the strike relative to
    that IV, N(d1) and N(d2) in the EV formula both saturate to 1.0 in float
    precision, at which point EV[payoff] = target - K EXACTLY — identical to
    intrinsic-at-target, not just numerically close. A reversion to the old
    formula would be invisible there even to an independent recomputation.

    iv=1.00/atr14=10.0 sits in a regime verified NOT to saturate: the old
    model gives rr=1.952 (fails the >=2.0 gate, no setup), the new model
    gives rr=2.122 (passes) — the two models disagree on whether a setup
    exists at all, which a reversion cannot survive.
    """
    chain = _bounce_chain(iv=1.00)
    setup = _construct_200w_bounce_long_call("TEST", 100.0, chain, _FACTS, atr14=10.0)
    assert setup is not None
    assert setup.rr_ratio == 2.12
