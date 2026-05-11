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
