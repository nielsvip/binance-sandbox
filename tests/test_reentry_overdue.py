"""Unit tests for vec_decisions.reentry_overdue (TRADIER OVERDUE reentry bypass).

Pure predicate: no NPZ, no engine import. Live mirror: the reentry_monitor
OVERDUE BYPASS gate (tradier_manage.py SAFETY SWITCH 3) — 2h+ wait bypasses
stoch+WT on 15m-DC break OR GR HL/HH with WT agreement.
"""
from types import SimpleNamespace

from vec_decisions import reentry_overdue as R


def _cfg(**kw):
    return SimpleNamespace(**kw)


def _wts(w1, w2):
    return {"1h": (w1, w2), "4h": (w1, w2), "D": (w1, w2)}


def _hl(hi, hip, lo, lop):
    return (
        {"1h": hi, "4h": hi, "D": hi},
        {"1h": lo, "4h": lo, "D": lo},
        {"1h": hip, "4h": hip, "D": hip},
        {"1h": lop, "4h": lop, "D": lop},
    )


def test_disabled_by_default():
    assert R.enabled(_cfg()) is False
    assert R.enabled(_cfg(REENTRY_OVERDUE_MODEL_ENABLED=False)) is False
    assert R.enabled(_cfg(REENTRY_OVERDUE_MODEL_ENABLED=True)) is True


def test_too_young_refuses():
    h, l, hp, lp = _hl(110, 100, 102, 100)
    assert R.fire(_cfg(), True, 7, 111, 110, h, l, hp, lp, _wts(2, 1)) is False


def test_dc_break_fires_long():
    h, l, hp, lp = _hl(100, 100, 100, 100)
    assert R.fire(_cfg(), True, 8, 111, 110, h, l, hp, lp, _wts(0, 0)) is True


def test_gr_hh_with_wt_fires():
    h, l, hp, lp = _hl(110, 100, 100, 100)
    assert R.fire(_cfg(), True, 20, 90, 200, h, l, hp, lp, _wts(2, 1)) is True


def test_gr_without_wt_refuses():
    h, l, hp, lp = _hl(110, 100, 100, 100)
    assert R.fire(_cfg(), True, 20, 90, 200, h, l, hp, lp, _wts(1, 2)) is False


def test_short_mirror():
    h, l, hp, lp = _hl(100, 100, 90, 100)
    assert R.fire(_cfg(), False, 8, 89, 90, h, l, hp, lp, _wts(0, 0)) is True
    assert R.fire(_cfg(), False, 8, 91, 90, h, l, hp, lp, _wts(0, 0)) is False


def test_nan_and_garbage_fail_closed():
    h, l, hp, lp = _hl(110, 100, 102, 100)
    assert R.fire(_cfg(), True, 8, float("nan"), 110, h, l, hp, lp, _wts(2, 1)) is False
    assert R.fire(_cfg(), True, 8, 111, 110, {}, {}, {}, {}, {}) is False
    assert R.fire(None, True, 8, 111, 110, h, l, hp, lp, _wts(2, 1)) is False
