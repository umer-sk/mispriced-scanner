# backend/tests/test_technical_scanner.py
from models import TechnicalSetup
from datetime import date

def test_technical_setup_fields():
    setup = TechnicalSetup(
        symbol="NVDA",
        stock_price=875.0,
        direction="bullish",
        signal_count=6,
        signal_details={"stage2": True, "ema_alignment": True, "price_vs_ema21": True,
                        "rsi_zone": True, "volume_accum": True, "rs_vs_qqq": True, "breakout": False},
        structure="long_call",
        strike=900.0,
        short_strike=None,
        expiry=date(2026, 5, 16),
        dte=45,
        delta=0.44,
        iv_rank=32.0,
        premium=4.20,
        price_target=940.0,
        rr_ratio=3.2,
        max_loss=420.0,
        breakeven_move_pct=4.8,
        probability_of_profit=44,
        order_string="BUY +1 NVDA 05/16 900 CALL @4.20 LMT",
    )
    assert setup.symbol == "NVDA"
    assert setup.direction == "bullish"
    assert setup.signal_count == 6
