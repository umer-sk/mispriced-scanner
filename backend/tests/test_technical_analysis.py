import pandas as pd
import pytest
from unittest.mock import patch
from technical_analysis import get_technical_context, _compute_bias


def _make_prices(close_prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": close_prices})


def test_bias_uptrend():
    assert _compute_bias(price=150, ma50=140, ma200=120) == "bullish"


def test_bias_downtrend():
    assert _compute_bias(price=110, ma50=120, ma200=140) == "bearish"


def test_bias_mixed():
    assert _compute_bias(price=130, ma50=135, ma200=120) == "neutral"


def test_pct_from_ma50():
    from technical_analysis import _pct_from
    assert abs(_pct_from(110, 100) - 10.0) < 0.01


@patch("technical_analysis.yf.download")
def test_get_technical_context_uptrend(mock_dl):
    # 250 prices trending up: price > ma50 > ma200
    prices = list(range(100, 350))  # 250 values, last=349
    mock_dl.return_value = _make_prices(prices)
    ctx = get_technical_context("NVDA")
    assert ctx.symbol == "NVDA"
    assert ctx.trend == "uptrend"
    assert ctx.bias == "bullish"
    assert ctx.price == 349


@patch("technical_analysis.yf.download")
def test_get_technical_context_returns_none_on_empty(mock_dl):
    mock_dl.return_value = pd.DataFrame({"Close": []})
    ctx = get_technical_context("NVDA")
    assert ctx is None
