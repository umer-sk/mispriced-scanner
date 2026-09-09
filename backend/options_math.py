"""
Shared option-pricing math: expected-value reward for single-leg options, and
real-world probability of finishing beyond a breakeven. Used by scanner.py,
technical_scanner.py, and celt_scanner.py — kept here, not in any one of them,
so none of the three scanners has to import from another.
"""
import math
from typing import Optional

from scipy.stats import norm


def _expected_option_value(S: float, K: float, T: float, sigma: float, mu: float, is_put: bool) -> float:
    """E[payoff] of a European option at expiry, undiscounted, where the
    underlying is lognormal with E[S_T] = S * exp(mu*T) and volatility sigma.

    This is the actuarial ("real-world") expectation, not a risk-neutral
    price: same closed form as Black-Scholes with r -> mu and no discounting,
    since we want expected P&L at the horizon, not a present value.

    Why this replaces "intrinsic value at one point target": that approach
    throws away the whole probability distribution above (for a call) or
    below (for a put) the target, which is exactly where a long option's
    convexity pays off. It also cannot distinguish a setup with real edge
    from one with none — a flat, no-edge forecast (mu=0) still shows a
    positive number under intrinsic-at-target once the target sits far
    enough out, because the point-target model has no way to express "on
    average, nothing happens." Under this formula, mu=0 always gives exactly
    the option's own fair value at that vol, and expected_gain = 0.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, K - S) if is_put else max(0.0, S - K)
    d1 = (math.log(S / K) + (mu + sigma**2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    forward = S * math.exp(mu * T)
    if is_put:
        return K * norm.cdf(-d2) - forward * norm.cdf(-d1)
    return forward * norm.cdf(d1) - K * norm.cdf(d2)


def _single_leg_reward(
    stock_price: float, strike: float, dte: int, iv: float,
    price_target: float, premium: float, is_put: bool,
) -> float:
    """Expected-value R:R for a single-leg long option (see
    _expected_option_value for why intrinsic-at-a-point is the wrong metric).

    `price_target` supplies the drift assumption via mu = ln(target/S)/T —
    an externally-computed forecast (an ATR projection, a mean-reversion
    target, whatever the caller's thesis implies), evaluated properly across
    the whole outcome distribution instead of at one point. sigma is the
    CONTRACT'S OWN IV: this deliberately measures whether the forecast move
    is large relative to what the option's own pricing assumes, not whether
    IV itself is rich or cheap (that question belongs to a mispricing
    detector, not a reward model).

    Note: mu=0 gives EXACTLY rr=0 only when `premium` equals the option's
    theoretical fair value at (sigma, r=0). In production `premium` is the
    real market ask, which differs from that fair value (bid/ask spread,
    r>0 in the real pricing model, skew) — so a genuinely no-edge input
    gives rr close to but not exactly 0. Harmless against any reasonable
    gate, but "exactly 0" is a property of _expected_option_value in
    isolation, not of this function fed real market data.
    """
    # price_target <= 0 is reachable in production: a bearish target can go
    # negative if the drift assumption is large relative to price, and a bad
    # upstream data point (e.g. an unadjusted split) can inflate a volatility
    # input that far. math.log() on a non-positive argument raises ValueError,
    # which callers must not let take down an entire batch over one symbol.
    if iv <= 0 or dte <= 0 or premium <= 0 or stock_price <= 0 or price_target <= 0:
        return 0.0
    T = dte / 365.0
    mu = math.log(price_target / stock_price) / T
    ev_payoff = _expected_option_value(stock_price, strike, T, iv, mu, is_put)
    return (ev_payoff - premium) / premium


def probability_of_profit(
    stock_price: float, breakeven: float, dte: int, iv: float,
    is_put: bool, mu: float = 0.0,
) -> Optional[float]:
    """Real-world P(finish beyond breakeven) at expiry, 0.0-1.0.

    Replaces the common `|delta| * 100` shortcut, which is P(finish ITM at
    all) — not P(finish beyond breakeven), which is always a stricter bar for
    a purchased option (breakeven = strike + premium, or strike - premium for
    a put). Delta overstates true POP for exactly that reason. mu=0 (the
    default) makes no directional claim beyond current IV; pass a real
    forecast (e.g. an ATR or mean-reversion target's implied drift) when the
    caller has one.

    Returns None when the inputs can't support an answer (unpriceable option,
    non-positive vol/time) — never a guessed value.
    """
    if stock_price <= 0 or breakeven <= 0 or dte <= 0 or iv <= 0:
        return None
    T = dte / 365.0
    d = (math.log(stock_price / breakeven) + (mu - iv**2 / 2) * T) / (iv * math.sqrt(T))
    return float(norm.cdf(-d) if is_put else norm.cdf(d))
