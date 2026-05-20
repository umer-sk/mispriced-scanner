import gc
import logging
from typing import Optional

import pandas as pd
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


_BATCH_SIZE = 15


def get_technical_contexts(symbols: list[str]) -> dict[str, Optional[TechnicalContext]]:
    """
    Fetch technical context for all symbols, downloading in batches of _BATCH_SIZE
    to keep peak DataFrame memory low on constrained hosts.
    """
    results: dict[str, Optional[TechnicalContext]] = {}
    batches = [symbols[i:i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        if batch_idx > 0:
            import time; time.sleep(1)
        try:
            raw = yf.download(batch, period="1y", interval="1d", auto_adjust=True, progress=False)
        except Exception as e:
            logger.warning("Batch %d download failed: %s — falling back per-symbol", batch_idx, e)
            for symbol in batch:
                results[symbol] = get_technical_context(symbol)
            continue

        for symbol in batch:
            try:
                if len(batch) == 1:
                    closes = raw["Close"].dropna()
                elif isinstance(raw.columns, pd.MultiIndex):
                    closes = raw["Close"][symbol].dropna()
                else:
                    closes = raw["Close"].dropna()

                if len(closes) < 50:
                    logger.warning("Insufficient price history for %s", symbol)
                    results[symbol] = None
                    continue

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

                results[symbol] = TechnicalContext(
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
                results[symbol] = None

        del raw
        gc.collect()

    return results
