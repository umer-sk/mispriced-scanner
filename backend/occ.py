"""
OCC option symbol construction.

Format: 6-char underlying root (left-justified, space-padded), YYMMDD expiry,
'C' or 'P', then strike x 1000 as 8 zero-padded digits.

    NVDA  261016C00190000  ->  NVDA 2026-10-16 190 call

Schwab's quote endpoint accepts these directly, which lets us re-price a known
contract without re-fetching a chain.
"""
from datetime import date


def build_occ(symbol: str, expiry: date, is_put: bool, strike: float) -> str:
    root = symbol.strip().upper()
    if not root or len(root) > 6:
        raise ValueError(f"OCC root must be 1-6 characters, got {symbol!r}")
    if strike is None or strike <= 0:
        raise ValueError(f"OCC strike must be positive, got {strike!r}")

    # round() before int() — 12.34 * 1000 is 12339.999... in binary floating
    # point, and truncating would produce the wrong contract.
    thousandths = int(round(strike * 1000))
    return f"{root:<6}{expiry:%y%m%d}{'P' if is_put else 'C'}{thousandths:08d}"
