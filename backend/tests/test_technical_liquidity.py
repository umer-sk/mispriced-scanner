from datetime import date, timedelta

from models import OptionContract, TechnicalSetup
from technical_scanner import _leg_liquidity


def _contract(bid, ask, oi):
    return OptionContract(
        strike=100.0, expiry=date.today() + timedelta(days=35), dte=35,
        bid=bid, ask=ask, mid=(bid + ask) / 2, last=(bid + ask) / 2,
        volume=200, open_interest=oi, iv=0.30, delta=0.45,
        gamma=0.01, theta=-0.05, vega=0.10,
        theoretical_value=(bid + ask) / 2, in_the_money=False,
    )


def test_spread_pct_is_a_percentage_not_a_fraction():
    # scanner.py stores these as 0-100, technical must match or the shared
    # tier gate would compare incompatible scales.
    long_leg = _contract(4.9, 5.1, 800)      # 0.2 / 5.0 = 4%
    oi_l, oi_s, sp_l, sp_s, ok = _leg_liquidity(long_leg, None)
    assert abs(sp_l - 4.0) < 0.01


def test_liquidity_ok_true_for_tight_liquid_legs():
    long_leg = _contract(4.9, 5.1, 800)
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is True


def test_liquidity_ok_false_on_low_open_interest():
    long_leg = _contract(4.9, 5.1, 50)       # below the 100 floor
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is False


def test_liquidity_ok_false_on_wide_spread():
    long_leg = _contract(4.0, 6.0, 800)      # 2.0 / 5.0 = 40%
    short_leg = _contract(2.45, 2.55, 600)
    *_, ok = _leg_liquidity(long_leg, short_leg)
    assert ok is False


def test_single_leg_ignores_absent_short_leg():
    long_leg = _contract(4.9, 5.1, 800)
    oi_l, oi_s, sp_l, sp_s, ok = _leg_liquidity(long_leg, None)
    assert oi_s == 0 and sp_s == 0.0 and ok is True


def test_zero_mid_is_illiquid_not_a_crash():
    long_leg = _contract(0.0, 0.0, 800)
    *_, ok = _leg_liquidity(long_leg, None)
    assert ok is False
