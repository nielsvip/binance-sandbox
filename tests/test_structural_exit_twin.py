"""STRUCTURAL_EXIT_GATE live parity (USER 2026-10-21, top priority).

The vector ANDs STRUCTURAL_EXIT_GATE over _all_exit twice (v12:13606-30, :13643-73,
twin of wt_dc_delta.structural_exit_permitted); the live VEL lane had no veto and
exited into favorable moves (WLD long rising, ENS short falling, measured 2026-10-21).
These pin the shared scalar's contract: permit ONLY on LTF collapse or 1h/4h LH+LL
with price not running favorably; deny without structure data (matches the vector's
_have gate).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wt_dc_delta import structural_exit_permitted as permitted


def _ind(**kw):
    base = {
        "current_price": 100.0,
        "open_15m": 99.0,
        "close_15m": 100.0,
        "high_15m": 100.5,
        "high_15m_prev": 100.0,
        "low_15m": 99.0,
        "low_15m_prev": 98.5,
        "high_1h": 101.0,
        "high_1h_prev": 100.0,
        "low_1h": 99.0,
        "low_1h_prev": 98.0,
        "high_4h": 102.0,
        "high_4h_prev": 101.0,
        "low_4h": 99.5,
        "low_4h_prev": 98.5,
    }
    base.update(kw)
    return base


def test_long_rising_no_collapse_denied():
    ok, why = permitted(_ind(), True, {"rz_ltf_micro": "15m"})
    assert ok is False and why.startswith("STRUCT_VETO")


def test_long_collapse_and_falling_permitted():
    ind = _ind(
        open_15m=100.0,
        close_15m=96.5,
        high_15m=99.0,
        low_15m=97.0,
        low_15m_prev=98.0,
        current_price=96.5,
    )
    ok, why = permitted(ind, True, {"rz_ltf_micro": "15m"})
    assert ok is True and why.startswith("STRUCT_OK")


def test_long_collapse_but_still_rising_denied():
    # WLD shape 2026-10-21: collapse prints while the close still rises -> hold.
    ind = _ind(
        open_15m=99.0,
        close_15m=100.0,
        high_15m=99.0,
        low_15m=97.0,
        low_15m_prev=98.0,
        current_price=96.5,
    )
    ok, why = permitted(ind, True, {"rz_ltf_micro": "15m"})
    assert ok is False and "still" in why


def test_short_falling_no_collapse_denied():
    # ENS shape 2026-10-21: short with price still falling -> hold.
    ind = _ind(
        open_15m=101.0,
        close_15m=100.0,
        high_15m=100.5,
        high_15m_prev=100.0,
        low_15m=99.0,
        low_15m_prev=99.5,
        current_price=100.0,
    )
    ok, _ = permitted(ind, False, {"rz_ltf_micro": "15m"})
    assert ok is False


def test_short_ltf_collapse_permitted():
    ind = _ind(
        open_15m=100.0,
        close_15m=102.0,
        high_15m=102.5,
        high_15m_prev=101.0,
        low_15m=100.5,
        low_15m_prev=100.0,
        current_price=102.5,
        high_1h=101.0,
        high_1h_prev=100.0,
        low_1h=99.0,
        low_1h_prev=99.5,
        high_4h=102.0,
        high_4h_prev=101.0,
        low_4h=99.5,
        low_4h_prev=100.0,
    )
    ok, _ = permitted(ind, False, {"rz_ltf_micro": "15m"})
    assert ok is True


def test_missing_structure_data_denied():
    ok, why = permitted({"current_price": 100.0}, True, {"rz_ltf_micro": "15m"})
    assert ok is False and "no_structure_data" in why
