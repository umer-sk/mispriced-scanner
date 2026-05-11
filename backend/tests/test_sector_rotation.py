from models import SectorData


def make_sector(**kwargs):
    defaults = dict(
        etf="XLK", name="Technology",
        return_1w=1.0, return_4w=2.0, return_12w=3.0,
        return_vs_spy_1w=0.5, return_vs_spy_4w=1.0, return_vs_spy_12w=1.5,
        rs_score=75.0, trend_direction="improving", classification="bullish",
    )
    defaults.update(kwargs)
    return SectorData(**defaults)


def test_sector_data_accepts_rotation_field():
    s = make_sector(rotation="↑↑")
    assert s.rotation == "↑↑"


def test_sector_data_rotation_defaults_to_neutral():
    s = make_sector()
    assert s.rotation == "→"


from unittest.mock import patch, MagicMock
import sector_analysis
from sector_analysis import _score_to_arrow, _rs_momentum_vote, _volume_vote


# --- _score_to_arrow ---

def test_score_to_arrow_strong_up():
    assert _score_to_arrow(3) == "↑↑"
    assert _score_to_arrow(2) == "↑↑"

def test_score_to_arrow_up():
    assert _score_to_arrow(1) == "↑"

def test_score_to_arrow_neutral():
    assert _score_to_arrow(0) == "→"

def test_score_to_arrow_down():
    assert _score_to_arrow(-1) == "↓"

def test_score_to_arrow_strong_down():
    assert _score_to_arrow(-2) == "↓↓"
    assert _score_to_arrow(-3) == "↓↓"


# --- _rs_momentum_vote ---

def test_rs_momentum_vote_bullish():
    assert _rs_momentum_vote(70.0, 60.0) == 1   # delta +10 > 5

def test_rs_momentum_vote_bearish():
    assert _rs_momentum_vote(50.0, 62.0) == -1  # delta -12 < -5

def test_rs_momentum_vote_neutral():
    assert _rs_momentum_vote(55.0, 52.0) == 0   # delta +3, within ±5


# --- _volume_vote ---

def test_volume_vote_accumulation():
    # vol_5d=200, vol_20d=125 → ratio=1.6 > 1.3; price up
    volumes = [100] * 15 + [200] * 5
    prices = [100.0] * 5 + [110.0]   # prices[-6]=100, prices[-1]=110
    assert _volume_vote(volumes, prices) == 1

def test_volume_vote_distribution():
    # Same high volume, price down
    volumes = [100] * 15 + [200] * 5
    prices = [110.0] * 5 + [100.0]   # prices[-6]=110, prices[-1]=100
    assert _volume_vote(volumes, prices) == -1

def test_volume_vote_neutral_low_volume():
    volumes = [100] * 20              # ratio=1.0 < 1.3
    prices = [100.0] * 5 + [105.0]
    assert _volume_vote(volumes, prices) == 0

def test_volume_vote_insufficient_data():
    assert _volume_vote([100] * 10, [100.0] * 6) == 0  # <20 volume days
    assert _volume_vote([100] * 20, [100.0] * 3) == 0  # <6 price days


# --- _get_sector_flow ---

def _make_chain(call_oi: int, put_oi: int, dte: int = 45) -> MagicMock:
    call = MagicMock(dte=dte, open_interest=call_oi)
    put = MagicMock(dte=dte, open_interest=put_oi)
    chain = MagicMock(stock_price=100.0, calls=[call], puts=[put])
    return chain

def test_get_sector_flow_call_biased():
    # put/call ratio = 700/1000 = 0.7 < 0.8 → +1
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 700)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 1

def test_get_sector_flow_put_biased():
    # put/call ratio = 1300/1000 = 1.3 > 1.2 → -1
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 1300)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == -1

def test_get_sector_flow_neutral():
    # put/call ratio = 1.0 → 0
    with patch('sector_analysis.fetch_option_chain', return_value=_make_chain(1000, 1000)):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 0

def test_get_sector_flow_no_schwab():
    with patch.object(sector_analysis, 'fetch_option_chain', None):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes == {}

def test_get_sector_flow_exception_returns_zero():
    with patch('sector_analysis.fetch_option_chain', side_effect=Exception("timeout")):
        votes = sector_analysis._get_sector_flow(["XLK"])
    assert votes["XLK"] == 0
