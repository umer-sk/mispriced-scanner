"""
5 mispricing detectors, spread constructor, and swing quality scorer.
"""
import logging
import math
from datetime import date, datetime
from typing import Optional

import numpy as np
from scipy import stats

from models import (
    CatalystContext,
    MispricingSignal,
    OptionChainData,
    OptionContract,
    PnLScenario,
    TradeSetup,
)

logger = logging.getLogger(__name__)

# Risk-free rate — update quarterly
RISK_FREE_RATE = 0.0525


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _atm_contract(contracts: list[OptionContract], stock_price: float, dte_min: int = 0, dte_max: int = 65) -> Optional[OptionContract]:
    """Find the contract closest to ATM within DTE range."""
    candidates = [c for c in contracts if dte_min <= c.dte <= dte_max and c.bid > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c.strike - stock_price))


def _contracts_for_expiry(contracts: list[OptionContract], expiry: date) -> list[OptionContract]:
    return [c for c in contracts if c.expiry == expiry]


def _call_put_volume_ratio(chain: OptionChainData) -> float:
    call_vol = sum(c.volume for c in chain.calls)
    put_vol = sum(c.volume for c in chain.puts)
    if put_vol == 0:
        return 2.0  # default bullish
    return call_vol / put_vol


def _spread_pct(contract: OptionContract) -> float:
    if contract.mid == 0:
        return 1.0
    return (contract.ask - contract.bid) / contract.mid


# ---------------------------------------------------------------------------
# Detector 1: IV Rank Underpricing
# ---------------------------------------------------------------------------

def detect_iv_rank_cheap(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Fire when IV rank < 25%, IV30 < HV30, and net call flow is bullish.
    """
    if chain.stock_price == 0 or chain.iv30 == 0:
        return None

    ivr = chain.iv_rank
    if ivr >= 25:
        return None
    if chain.iv30 >= chain.hv30:
        return None

    cp_ratio = _call_put_volume_ratio(chain)
    if cp_ratio <= 1.3:
        return None

    if ivr <= 10:
        confidence = 0.95
    elif ivr <= 18:
        confidence = 0.85
    else:
        confidence = 0.70

    return MispricingSignal(
        symbol=chain.symbol,
        detector="iv_rank",
        description=(
            f"IV rank at {ivr:.0f}% — options pricing {chain.iv30:.1%} vol vs "
            f"{chain.hv30:.1%} historical. Market underpricing realized movement."
        ),
        confidence=confidence,
        raw_data={
            "iv_rank": ivr,
            "iv30": chain.iv30,
            "hv30": chain.hv30,
            "call_put_volume_ratio": round(cp_ratio, 2),
        },
    )


# ---------------------------------------------------------------------------
# Detector 2: Volatility Skew Anomaly
# ---------------------------------------------------------------------------

def detect_skew_anomaly(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Detect when calls are mispriced relative to puts via skew curve analysis.
    """
    if chain.stock_price == 0:
        return None

    # Find 10-delta contracts in the 21–60 DTE window
    def find_delta_contract(contracts: list[OptionContract], target_delta: float) -> Optional[OptionContract]:
        candidates = [c for c in contracts if 21 <= c.dte <= 60 and c.open_interest > 200 and c.iv > 0]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))

    put_10d = find_delta_contract(chain.puts, 0.10)
    call_10d = find_delta_contract(chain.calls, 0.10)

    if put_10d is None or call_10d is None:
        return None

    raw_skew = put_10d.iv - call_10d.iv

    # Anomaly Type A — flat skew (calls underpriced vs puts)
    if raw_skew < 0.03:
        return MispricingSignal(
            symbol=chain.symbol,
            detector="skew",
            description=(
                f"Skew at {raw_skew:.1%} — calls significantly cheaper than puts. "
                f"Normal equity skew is 5–15%. Calls underpriced vs skew curve."
            ),
            confidence=0.75,
            raw_data={
                "raw_skew": round(raw_skew, 4),
                "put_10d_iv": round(put_10d.iv, 4),
                "call_10d_iv": round(call_10d.iv, 4),
            },
        )

    # Anomaly Type B — specific strike mispriced via quadratic curve fit
    # Use a single expiry with sufficient strikes
    all_expiries = sorted(set(c.expiry for c in chain.calls if 21 <= c.dte <= 60))
    if not all_expiries:
        return None

    best_signal = None
    best_deviation = 0.0

    for expiry in all_expiries[:3]:  # Check first 3 expiries
        expiry_calls = [
            c for c in chain.calls
            if c.expiry == expiry and c.open_interest > 200 and c.iv > 0 and c.bid > 0
        ]
        if len(expiry_calls) < 5:
            continue

        strikes = np.array([c.strike for c in expiry_calls])
        ivs = np.array([c.iv for c in expiry_calls])

        # Fit quadratic
        coeffs = np.polyfit(strikes, ivs, 2)
        fitted = np.polyval(coeffs, strikes)
        residuals = ivs - fitted

        # R-squared
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((ivs - np.mean(ivs)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        if r_squared < 0.75:
            continue

        # Find strike with largest negative deviation (underpriced call)
        min_idx = int(np.argmin(residuals))
        min_deviation = residuals[min_idx]

        if min_deviation < -0.03 and abs(min_deviation) > abs(best_deviation):
            best_deviation = min_deviation
            mispriced_contract = expiry_calls[min_idx]
            best_signal = MispricingSignal(
                symbol=chain.symbol,
                detector="skew",
                description=(
                    f"${mispriced_contract.strike:.0f} call IV {min_deviation:.1%} below skew curve "
                    f"(DTE {mispriced_contract.dte}). Quadratic fit R²={r_squared:.2f}."
                ),
                confidence=min(0.90, 0.65 + abs(min_deviation) * 5),
                raw_data={
                    "mispriced_strike": mispriced_contract.strike,
                    "deviation": round(float(min_deviation), 4),
                    "r_squared": round(float(r_squared), 3),
                    "expiry": str(expiry),
                    "raw_skew": round(raw_skew, 4),
                },
            )

    return best_signal


# ---------------------------------------------------------------------------
# Detector 3: Put-Call Parity Violation
# ---------------------------------------------------------------------------

def detect_parity_violation(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Mathematical arbitrage detection using put-call parity.
    C - P = S - K * e^(-rT)
    """
    if chain.stock_price == 0:
        return None

    S = chain.stock_price
    r = RISK_FREE_RATE
    best_violation = 0.0
    best_signal = None

    # Group calls and puts by (strike, expiry)
    call_map = {(c.strike, c.expiry): c for c in chain.calls if 14 <= c.dte <= 60}
    put_map = {(p.strike, p.expiry): p for p in chain.puts if 14 <= p.dte <= 60}

    common_keys = set(call_map) & set(put_map)

    for key in common_keys:
        call = call_map[key]
        put = put_map[key]
        K = call.strike
        T = call.dte / 365.0

        # Liquidity gates
        if call.open_interest < 100 or put.open_interest < 100:
            continue
        if _spread_pct(call) > 0.05 or _spread_pct(put) > 0.05:
            continue

        theoretical_call = put.mid + S - K * math.exp(-r * T)
        if theoretical_call <= 0:
            continue

        violation_pct = (theoretical_call - call.mid) / theoretical_call

        # Only flag when call is underpriced (positive violation = call too cheap)
        if violation_pct > 0.02 and violation_pct > best_violation:
            best_violation = violation_pct
            confidence = min(0.98, 0.70 + violation_pct * 5)
            best_signal = MispricingSignal(
                symbol=chain.symbol,
                detector="parity",
                description=(
                    f"Put-call parity violation: ${K:.0f} call trading "
                    f"{violation_pct:.1%} below theoretical value (DTE {call.dte}). "
                    f"Mathematical underpricing, not opinion."
                ),
                confidence=confidence,
                raw_data={
                    "strike": K,
                    "expiry": str(call.expiry),
                    "dte": call.dte,
                    "call_mid": call.mid,
                    "theoretical_call": round(theoretical_call, 2),
                    "violation_pct": round(violation_pct, 4),
                    "put_mid": put.mid,
                    "stock_price": S,
                },
            )

    return best_signal


# ---------------------------------------------------------------------------
# Detector 4: Term Structure Anomaly
# ---------------------------------------------------------------------------

def detect_term_structure_gap(
    chain: OptionChainData,
    earnings_date: Optional[date] = None,
) -> Optional[MispricingSignal]:
    """
    Detect backwardation in term structure NOT explained by earnings.
    """
    if chain.stock_price == 0:
        return None

    S = chain.stock_price
    expiries = sorted(set(c.expiry for c in chain.calls if 7 <= c.dte <= 60))
    if len(expiries) < 2:
        return None

    expiry_1 = expiries[0]
    expiry_2 = expiries[1]

    atm_1 = _atm_contract(chain.calls, S, dte_min=0, dte_max=(expiry_1 - date.today()).days + 1)
    atm_2 = _atm_contract(chain.calls, S, dte_min=(expiry_1 - date.today()).days + 1, dte_max=65)

    if atm_1 is None or atm_2 is None:
        return None
    if atm_1.iv == 0 or atm_2.iv == 0:
        return None

    backwardation = atm_1.iv - atm_2.iv
    if backwardation <= 0.03:  # Need > 3 IV points of backwardation
        return None

    dte_1 = (expiry_1 - date.today()).days

    # Check if earnings explain the near-term spike
    if earnings_date is not None and (earnings_date - date.today()).days <= dte_1:
        return None  # Earnings within expiry_1 — expected behavior

    return MispricingSignal(
        symbol=chain.symbol,
        detector="term",
        description=(
            f"Term structure backwardation: near-term IV {atm_1.iv:.1%} vs "
            f"far-term {atm_2.iv:.1%} ({backwardation:.1%} gap). "
            f"No earnings to explain it — near-term overpriced."
        ),
        confidence=min(0.85, 0.60 + backwardation * 3),
        raw_data={
            "expiry_1": str(expiry_1),
            "expiry_2": str(expiry_2),
            "iv_near": round(atm_1.iv, 4),
            "iv_far": round(atm_2.iv, 4),
            "backwardation": round(backwardation, 4),
        },
    )


# ---------------------------------------------------------------------------
# Detector 5: Straddle vs Historical Move
# ---------------------------------------------------------------------------

def detect_move_underpricing(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Compare implied move (ATM straddle) vs expected historical move.
    """
    if chain.stock_price == 0 or chain.hv30 == 0:
        return None

    S = chain.stock_price

    # Find nearest expiry ATM straddle (21–60 DTE)
    atm_call = _atm_contract(chain.calls, S, dte_min=21, dte_max=60)
    atm_put = _atm_contract(chain.puts, S, dte_min=21, dte_max=60)

    if atm_call is None or atm_put is None:
        return None
    if atm_call.ask <= 0 or atm_put.ask <= 0:
        return None

    # Use same expiry for both legs
    if atm_call.expiry != atm_put.expiry:
        # Find closest matching put
        candidates = [
            p for p in chain.puts
            if p.expiry == atm_call.expiry and abs(p.strike - atm_call.strike) < 0.01
        ]
        if not candidates:
            return None
        atm_put = candidates[0]

    straddle_cost = atm_call.ask + atm_put.ask
    implied_move_pct = straddle_cost / S

    dte = atm_call.dte
    hv30_daily = chain.hv30 / math.sqrt(252)
    expected_move_pct = hv30_daily * math.sqrt(dte)

    if expected_move_pct == 0:
        return None

    ratio = implied_move_pct / expected_move_pct
    if ratio >= 0.80:
        return None

    if ratio < 0.65:
        confidence = 0.90
    elif ratio < 0.75:
        confidence = 0.75
    else:
        confidence = 0.60

    return MispricingSignal(
        symbol=chain.symbol,
        detector="move",
        description=(
            f"ATM straddle implies {implied_move_pct:.1%} move over {dte} days, "
            f"but historical norms suggest {expected_move_pct:.1%}. "
            f"Options pricing {(1-ratio):.0%} less movement than history."
        ),
        confidence=confidence,
        raw_data={
            "straddle_cost": round(straddle_cost, 2),
            "implied_move_pct": round(implied_move_pct, 4),
            "expected_move_pct": round(expected_move_pct, 4),
            "underpricing_ratio": round(ratio, 3),
            "dte": dte,
            "hv30": round(chain.hv30, 4),
        },
    )


# ---------------------------------------------------------------------------
# Bearish Detector 1: Put IV Rank Cheap
# ---------------------------------------------------------------------------

def detect_put_iv_rank_cheap(chain: OptionChainData) -> Optional[MispricingSignal]:
    """Fire when IV rank < 25%, iv30 < hv30, and put flow dominates (bearish)."""
    if chain.stock_price == 0 or chain.iv30 == 0:
        return None
    if chain.iv_rank >= 25:
        return None
    if chain.iv30 >= chain.hv30:
        return None

    put_vol = sum(c.volume for c in chain.puts)
    call_vol = sum(c.volume for c in chain.calls)
    if call_vol == 0 or put_vol / max(call_vol, 1) <= 1.3:
        return None

    ivr = chain.iv_rank
    confidence = 0.95 if ivr <= 10 else (0.85 if ivr <= 18 else 0.70)

    return MispricingSignal(
        symbol=chain.symbol,
        detector="put_iv_rank",
        description=(
            f"IV rank at {ivr:.0f}% — puts pricing {chain.iv30:.1%} vol vs "
            f"{chain.hv30:.1%} historical. Bearish flow dominant; puts cheap."
        ),
        confidence=confidence,
        raw_data={
            "iv_rank": ivr, "iv30": chain.iv30, "hv30": chain.hv30,
            "put_call_volume_ratio": round(put_vol / max(call_vol, 1), 2),
        },
    )


# ---------------------------------------------------------------------------
# Bearish Detector 2: Skew Inversion (Puts Cheap vs Historical Skew)
# ---------------------------------------------------------------------------

def detect_skew_inversion(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Fire when put/call skew is flat (< 0.03) — puts should be more expensive
    than calls in normal equity markets. Flat skew = cheap downside protection.
    """
    if chain.stock_price == 0:
        return None

    def find_delta_contract(contracts, target_delta):
        candidates = [c for c in contracts if 21 <= c.dte <= 60 and c.open_interest > 200 and c.iv > 0]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))

    put_10d = find_delta_contract(chain.puts, 0.10)
    call_10d = find_delta_contract(chain.calls, 0.10)
    if put_10d is None or call_10d is None:
        return None

    raw_skew = put_10d.iv - call_10d.iv
    if raw_skew >= 0.03:
        return None

    return MispricingSignal(
        symbol=chain.symbol,
        detector="skew_inversion",
        description=(
            f"Put/call skew at {raw_skew:.1%} — nearly flat vs normal 5–15%. "
            f"Market underpricing downside protection. Puts cheap relative to calls."
        ),
        confidence=0.75,
        raw_data={
            "raw_skew": round(raw_skew, 4),
            "put_10d_iv": round(put_10d.iv, 4),
            "call_10d_iv": round(call_10d.iv, 4),
        },
    )


# ---------------------------------------------------------------------------
# Bearish Detector 3: Put-Call Parity Violation (Put Underpriced)
# ---------------------------------------------------------------------------

def detect_put_parity_violation(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    P = C - S + K * e^(-rT). When actual put < theoretical put, put is underpriced.
    """
    if chain.stock_price == 0:
        return None

    S = chain.stock_price
    r = RISK_FREE_RATE
    best_violation = 0.0
    best_signal = None

    call_map = {(c.strike, c.expiry): c for c in chain.calls if 30 <= c.dte <= 60}
    put_map  = {(p.strike, p.expiry): p for p in chain.puts  if 30 <= p.dte <= 60}
    common_keys = set(call_map) & set(put_map)

    for key in common_keys:
        call = call_map[key]
        put  = put_map[key]
        K = call.strike
        T = call.dte / 365.0

        if call.open_interest < 100 or put.open_interest < 100:
            continue
        if _spread_pct(call) > 0.10 or _spread_pct(put) > 0.10:
            continue

        theoretical_put = call.mid - S + K * math.exp(-r * T)
        if theoretical_put <= 0:
            continue

        violation_pct = (theoretical_put - put.mid) / theoretical_put
        if violation_pct > 0.02 and violation_pct > best_violation:
            best_violation = violation_pct
            confidence = min(0.98, 0.70 + violation_pct * 5)
            best_signal = MispricingSignal(
                symbol=chain.symbol,
                detector="put_parity",
                description=(
                    f"Put-call parity violation: ${K:.0f} put trading "
                    f"{violation_pct:.1%} below theoretical value (DTE {call.dte}). "
                    f"Put underpriced mathematically."
                ),
                confidence=confidence,
                raw_data={
                    "strike": K, "expiry": str(call.expiry), "dte": call.dte,
                    "put_mid": put.mid, "theoretical_put": round(theoretical_put, 2),
                    "violation_pct": round(violation_pct, 4),
                    "call_mid": call.mid, "stock_price": S,
                },
            )

    return best_signal


# ---------------------------------------------------------------------------
# Bearish Detector 4: Downside Move Underpricing
# ---------------------------------------------------------------------------

def detect_downside_move_underpricing(chain: OptionChainData) -> Optional[MispricingSignal]:
    """
    Compare ATM put cost (implied downside) vs expected historical downside.
    """
    if chain.stock_price == 0 or chain.hv30 == 0:
        return None

    S = chain.stock_price
    atm_put = _atm_contract(chain.puts, S, dte_min=30, dte_max=60)
    if atm_put is None or atm_put.ask <= 0:
        return None

    implied_downside_pct = atm_put.ask / S
    dte = atm_put.dte
    hv30_daily = chain.hv30 / math.sqrt(252)
    expected_downside_pct = hv30_daily * math.sqrt(dte)

    if expected_downside_pct == 0:
        return None

    ratio = implied_downside_pct / expected_downside_pct
    if ratio >= 0.80:
        return None

    confidence = 0.90 if ratio < 0.65 else (0.75 if ratio < 0.75 else 0.60)

    return MispricingSignal(
        symbol=chain.symbol,
        detector="downside_move",
        description=(
            f"ATM put implies {implied_downside_pct:.1%} downside over {dte} days, "
            f"but historical norms suggest {expected_downside_pct:.1%}. "
            f"Options pricing {(1-ratio):.0%} less downside than history."
        ),
        confidence=confidence,
        raw_data={
            "put_cost": round(atm_put.ask, 2),
            "implied_downside_pct": round(implied_downside_pct, 4),
            "expected_downside_pct": round(expected_downside_pct, 4),
            "underpricing_ratio": round(ratio, 3),
            "dte": dte, "hv30": round(chain.hv30, 4),
        },
    )


# ---------------------------------------------------------------------------
# P&L Scenario Calculator
# ---------------------------------------------------------------------------

def _calc_pnl_scenarios(
    long_leg: OptionContract,
    short_leg: Optional[OptionContract],
    net_debit: float,
    stock_price: float,
    days: int,
    at_expiry: bool = False,
) -> list[PnLScenario]:
    """Calculate P&L across 7 price scenarios using simplified greeks model."""
    price_moves = [-0.05, -0.02, 0.0, 0.03, 0.05, 0.08, 0.10]
    scenarios = []

    for move in price_moves:
        new_price = stock_price * (1 + move)
        label_pct = f"{move:+.0%}" if move != 0 else "flat"
        label = f"Stock {label_pct} in {days}d" if not at_expiry else f"Stock ${new_price:.0f}"

        if at_expiry:
            # Intrinsic value at expiry
            long_val = max(0.0, new_price - long_leg.strike) * 100
            short_val = max(0.0, new_price - short_leg.strike) * 100 if short_leg else 0.0
            pnl = long_val - short_val - net_debit * 100
        else:
            # Simplified greeks approximation
            price_change = new_price - stock_price
            long_pnl = (
                long_leg.delta * price_change
                - abs(long_leg.theta) * days
            ) * 100

            short_pnl = 0.0
            if short_leg:
                short_pnl = (
                    short_leg.delta * price_change
                    - abs(short_leg.theta) * days
                ) * 100
                # Short leg profit when it loses value
                short_pnl = -short_pnl

            pnl = long_pnl + short_pnl - net_debit * 100 + net_debit * 100
            # Correct: net position value change
            pnl = long_pnl - (-short_pnl if short_leg else 0)

        pnl_pct = (pnl / (net_debit * 100)) * 100 if net_debit > 0 else 0.0

        scenarios.append(PnLScenario(
            label=label,
            stock_price=round(new_price, 2),
            pnl=round(pnl, 0),
            pnl_pct=round(pnl_pct, 0),
        ))

    return scenarios


# ---------------------------------------------------------------------------
# Spread Constructor
# ---------------------------------------------------------------------------

def construct_best_spread(
    signal: MispricingSignal,
    chain: OptionChainData,
    catalyst: CatalystContext,
) -> Optional[TradeSetup]:
    """
    7-step process to build optimal bull call spread from a mispricing signal.
    Returns None if quality or liquidity gates fail.
    """
    S = chain.stock_price
    if S == 0:
        return None

    # STEP 1 — Select expiry (28–50 DTE preferred, never < 21 or > 60)
    # If earnings within window: prefer expiry 7–14 days AFTER earnings
    candidate_expiries = sorted(set(c.expiry for c in chain.calls if 21 <= c.dte <= 60))
    if not candidate_expiries:
        return None

    selected_expiry = None
    for exp in candidate_expiries:
        dte = (exp - date.today()).days
        if catalyst.earnings_date and catalyst.earnings_in_window:
            days_after_earnings = (exp - catalyst.earnings_date).days
            if 7 <= days_after_earnings <= 14:
                selected_expiry = exp
                break
        if 28 <= dte <= 50:
            selected_expiry = exp
            break

    if selected_expiry is None:
        selected_expiry = candidate_expiries[0]  # Fallback to nearest valid

    dte = (selected_expiry - date.today()).days

    # STEP 2 — Select strikes for bull call spread
    expiry_calls = sorted(
        [c for c in chain.calls if c.expiry == selected_expiry and c.bid > 0],
        key=lambda c: c.strike,
    )
    if len(expiry_calls) < 2:
        return None

    # Long leg: closest ATM at or just below stock price
    atm_candidates = [c for c in expiry_calls if c.strike <= S * 1.02]
    if not atm_candidates:
        return None
    long_leg = min(atm_candidates, key=lambda c: abs(c.strike - S))

    # Short leg: target delta 0.20–0.25 (typically 8–15% OTM)
    short_candidates = [
        c for c in expiry_calls
        if c.strike > long_leg.strike and 0.15 <= abs(c.delta) <= 0.30
    ]
    if not short_candidates:
        # Fallback: pick strike 8–15% OTM
        short_candidates = [
            c for c in expiry_calls
            if S * 1.06 <= c.strike <= S * 1.18
        ]
    if not short_candidates:
        return None
    short_leg = short_candidates[0]

    # Spread economics
    spread_width = short_leg.strike - long_leg.strike
    if spread_width <= 0:
        return None

    net_debit = round(long_leg.ask - short_leg.bid, 2)
    if net_debit <= 0:
        return None

    # Debit must be < 35% of spread width for minimum R:R of 1.86:1
    if net_debit > spread_width * 0.35:
        # Try wider short strike
        wider = [c for c in expiry_calls if c.strike > short_leg.strike and c.bid > 0]
        if wider:
            short_leg = wider[0]
            spread_width = short_leg.strike - long_leg.strike
            net_debit = round(long_leg.ask - short_leg.bid, 2)
        if net_debit <= 0 or net_debit > spread_width * 0.35:
            return None

    max_gain = round(spread_width - net_debit, 2)
    max_loss = net_debit
    breakeven = round(long_leg.strike + net_debit, 2)
    breakeven_move_pct = round((breakeven - S) / S * 100, 2)
    rr_ratio = round(max_gain / max_loss, 2) if max_loss > 0 else 0.0
    prob_profit = round(abs(long_leg.delta) * 100, 1)

    # STEP 3 — Greeks
    net_delta = round(long_leg.delta - short_leg.delta, 3)
    net_theta = round(long_leg.theta - short_leg.theta, 3)
    net_vega = round(long_leg.vega - short_leg.vega, 3)

    # STEP 4 — P&L scenarios
    scenarios_5d = _calc_pnl_scenarios(long_leg, short_leg, net_debit, S, days=5)
    scenarios_10d = _calc_pnl_scenarios(long_leg, short_leg, net_debit, S, days=10)
    scenarios_expiry = _calc_pnl_scenarios(long_leg, short_leg, net_debit, S, days=dte, at_expiry=True)

    # STEP 5 — Liquidity check (hard gate)
    long_spread_pct = round(_spread_pct(long_leg) * 100, 1)
    short_spread_pct = round(_spread_pct(short_leg) * 100, 1)
    liquidity_ok = (
        long_leg.open_interest >= 100
        and short_leg.open_interest >= 100
        and long_leg.volume >= 50
        and long_spread_pct <= 10.0
        and short_spread_pct <= 10.0
    )
    if not liquidity_ok:
        return None

    # STEP 6 — Quality gate (hard gate)
    if rr_ratio < 2.0:
        return None
    if net_debit > 8.0:
        return None
    if breakeven_move_pct > 10.0:
        return None
    if not (21 <= dte <= 60):
        return None

    # STEP 7 — Broker order string
    expiry_str = selected_expiry.strftime("%d %b %y").upper()
    order_string = (
        f"BUY +1 VERTICAL {chain.symbol} 100 {expiry_str} "
        f"{long_leg.strike:.0f}/{short_leg.strike:.0f} CALL @{net_debit:.2f} LMT"
    )

    return TradeSetup(
        symbol=chain.symbol,
        stock_price=S,
        signal=signal,
        catalyst=catalyst,
        structure="bull_call_spread",
        long_strike=long_leg.strike,
        short_strike=short_leg.strike,
        expiry=selected_expiry,
        dte=dte,
        net_debit=net_debit,
        max_gain=max_gain,
        max_loss=max_loss,
        breakeven=breakeven,
        breakeven_move_pct=breakeven_move_pct,
        rr_ratio=rr_ratio,
        probability_of_profit=prob_profit,
        net_delta=net_delta,
        net_theta=net_theta,
        net_vega=net_vega,
        long_leg_oi=long_leg.open_interest,
        short_leg_oi=short_leg.open_interest,
        long_leg_volume=long_leg.volume,
        long_leg_spread_pct=long_spread_pct,
        short_leg_spread_pct=short_spread_pct,
        liquidity_ok=liquidity_ok,
        scenarios_5d=scenarios_5d,
        scenarios_10d=scenarios_10d,
        scenarios_expiry=scenarios_expiry,
        score=0,  # filled by caller
        timestamp=datetime.utcnow(),
        order_string=order_string,
    )


# ---------------------------------------------------------------------------
# Swing Quality Scorer
# ---------------------------------------------------------------------------

def score_swing_quality(setup: TradeSetup) -> int:
    """Score 0–100. Only surface setups scoring >= 55."""
    score = 0

    # Catalyst quality (most important for swing — 45 pts max)
    if setup.catalyst.earnings_in_window:
        score += 25
    if setup.catalyst.iv_expansion_likely:
        score += 20

    # Mispricing quality (35 pts max)
    if setup.signal.detector == "parity":
        score += 20
    if setup.signal.iv_rank < 20 if hasattr(setup.signal, "iv_rank") else False:
        score += 15
    if setup.signal.detector == "skew":
        score += 10
    if setup.signal.detector == "move":
        score += 10

    # IV rank from chain data
    if setup.signal.raw_data.get("iv_rank", 100) < 20:
        score += 15

    # Trade structure quality (30 pts max)
    if setup.rr_ratio >= 3.0:
        score += 20
    elif setup.rr_ratio >= 2.0:
        score += 10
    if setup.breakeven_move_pct < 5.0:
        score += 15
    if setup.net_debit <= 3.00:
        score += 10

    # Liquidity confidence (15 pts max)
    if min(setup.long_leg_oi, setup.short_leg_oi) >= 500:
        score += 10
    if setup.long_leg_volume >= 200:
        score += 5

    # DTE fit for swing (10 pts max)
    if 28 <= setup.dte <= 50:
        score += 10

    # Penalties
    if setup.dte < 21:
        score -= 20
    if setup.rr_ratio < 2.0:
        score -= 30
    if setup.net_debit > 8.00:
        score -= 15
    if setup.long_leg_spread_pct > 8.0:
        score -= 10

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Run all detectors on a chain
# ---------------------------------------------------------------------------

def run_all_detectors(
    chain: OptionChainData,
    catalyst: "CatalystContext",
) -> list[TradeSetup]:
    """
    Run all 5 detectors on a single chain.
    Returns list of valid, scored TradeSetup objects.
    """
    detectors = [
        detect_iv_rank_cheap(chain),
        detect_skew_anomaly(chain),
        detect_parity_violation(chain),
        detect_term_structure_gap(chain, earnings_date=catalyst.earnings_date),
        detect_move_underpricing(chain),
    ]

    setups = []
    for signal in detectors:
        if signal is None:
            continue
        setup = construct_best_spread(signal, chain, catalyst)
        if setup is None:
            continue
        setup.score = score_swing_quality(setup)
        setups.append(setup)
        logger.info(
            f"{chain.symbol} [{signal.detector}] → score={setup.score} "
            f"rr={setup.rr_ratio} debit=${setup.net_debit}"
        )

    return setups
