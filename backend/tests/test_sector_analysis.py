import pytest
from sector_analysis import _rs_score, _classify, _trend_direction, _compute_return


def test_rs_score_highest():
    returns = {"XLK": 5.0, "XLF": 2.0, "XLE": -1.0}
    scores = _rs_score(returns)
    assert scores["XLK"] == 100.0


def test_rs_score_lowest():
    returns = {"XLK": 5.0, "XLF": 2.0, "XLE": -1.0}
    scores = _rs_score(returns)
    assert scores["XLE"] == 0.0


def test_rs_score_single_sector():
    returns = {"XLK": 3.0}
    scores = _rs_score(returns)
    assert scores["XLK"] == 50.0


def test_classify_bullish():
    assert _classify(70) == "bullish"


def test_classify_bearish():
    assert _classify(30) == "bearish"


def test_classify_neutral():
    assert _classify(50) == "neutral"


def test_trend_direction_improving():
    assert _trend_direction(current=65, prior=45) == "improving"


def test_trend_direction_deteriorating():
    assert _trend_direction(current=35, prior=55) == "deteriorating"


def test_trend_direction_stable():
    assert _trend_direction(current=50, prior=48) == "stable"


def test_compute_return():
    prices = [100.0, 105.0]
    assert abs(_compute_return(prices, 0, 1) - 5.0) < 0.01
