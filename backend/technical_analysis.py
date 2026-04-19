import logging
from typing import Optional

import yfinance as yf

from models import TechnicalContext

logger = logging.getLogger(__name__)


def _pct_from(price: float, ma: float) -> float:
    if ma == 0:
        return 0.0
    return round((price - ma) / ma * 100, 2)


def _compute_bias(price: float, ma50: float, ma200: float) -> str:
    if price > ma50 > ma200:
        return "bullish"
    if price < ma50 < ma200:
        return "bearish"
    return "neutral"


def get_technical_context(symbol: str) -> Optional[TechnicalContext]:
    """Fetch 1-year daily price history and compute MA50/MA200 context."""
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            logger.warning("Insufficient price history for %s", symbol)
            return None

        closes = df["Close"].dropna()
        price = float(closes.iloc[-1])
        ma50 = float(closes.rolling(50).mean().iloc[-1])
        ma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else ma50

        bias = _compute_bias(price, ma50, ma200)
        if price > ma50 > ma200:
            trend = "uptrend"
        elif price < ma50 < ma200:
            trend = "downtrend"
        else:
            trend = "mixed"

        return TechnicalContext(
            symbol=symbol,
            price=round(price, 2),
            ma50=round(ma50, 2),
            ma200=round(ma200, 2),
            pct_from_ma50=_pct_from(price, ma50),
            pct_from_ma200=_pct_from(price, ma200),
            trend=trend,
            bias=bias,
        )
    except Exception as e:
        logger.warning("Technical analysis failed for %s: %s", symbol, e)
        return None


def get_technical_contexts(symbols: list[str]) -> dict[str, Optional[TechnicalContext]]:
    """Fetch technical context for multiple symbols. Returns dict keyed by symbol."""
    results = {}
    for symbol in symbols:
        results[symbol] = get_technical_context(symbol)
    return results
