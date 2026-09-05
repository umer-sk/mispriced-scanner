from datetime import date

import pytest

from occ import build_occ


def test_build_occ_standard_call():
    # 6-char root left-justified space-padded, YYMMDD, C/P, strike*1000 in 8 digits
    assert build_occ("NVDA", date(2026, 10, 16), False, 190.0) == "NVDA  261016C00190000"


def test_build_occ_standard_put():
    assert build_occ("NVDA", date(2026, 10, 16), True, 190.0) == "NVDA  261016P00190000"


def test_build_occ_five_char_root_has_one_space_padding():
    assert build_occ("GOOGL", date(2026, 1, 16), False, 150.0) == "GOOGL 260116C00150000"


def test_build_occ_six_char_root_has_no_padding():
    assert build_occ("GOOGL2", date(2026, 1, 16), False, 150.0) == "GOOGL2260116C00150000"


def test_build_occ_fractional_strike():
    assert build_occ("F", date(2026, 3, 20), False, 12.5) == "F     260320C00012500"


def test_build_occ_high_strike():
    assert build_occ("AVGO", date(2026, 6, 18), True, 1750.0) == "AVGO  260618P01750000"


def test_build_occ_rejects_overlong_root():
    with pytest.raises(ValueError):
        build_occ("TOOLONG", date(2026, 6, 18), False, 10.0)


def test_build_occ_rejects_nonpositive_strike():
    with pytest.raises(ValueError):
        build_occ("NVDA", date(2026, 6, 18), False, 0.0)


def test_build_occ_rounds_float_noise():
    # 1.005 * 1000 is 1004.9999999999999 on IEEE-754 doubles, so a naive
    # int() truncates to 1004 and quotes a different contract. (Note 12.34
    # is NOT such a case — 12.34 * 1000 is exactly 12340.0.)
    assert build_occ("XYZ", date(2026, 6, 18), False, 1.005) == "XYZ   260618C00001005"


def test_build_occ_rejects_oversized_strike():
    with pytest.raises(ValueError):
        build_occ("QQQ", date(2026, 1, 1), False, 100000.0)
