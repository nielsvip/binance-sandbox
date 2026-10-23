"""pv3 C3 REENTRY_MASK_ARM_<FAM>_ENABLED (USER 2026-10-23 v15_pending sweep):
per-family entry-mask arms for reentry fires. Defaults = today's behaviour."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from vec_decisions import reentry_mask_arm as R


def _cfg(**kw):
    return types.SimpleNamespace(**kw)


def test_defaults_are_identity():
    assert R.armed(_cfg()) == []
    assert R.armed_mask(None, 0, True, _cfg(), None, None) is None
    cfg = _cfg(REENTRY_MASK_ARM_DC_BREAK_ENABLED=True, DC_BREAK_FILTER_TF="OFF")
    assert R.armed_mask(None, 0, True, cfg, None, None) is None
    assert R.live_allows(lambda k, d=None: getattr(_cfg(), k, d), True, {}, 100.0) == (
        True,
        "",
    )


def test_armed_lists_families():
    cfg = _cfg(
        REENTRY_MASK_ARM_DC_BREAK_ENABLED=True,
        REENTRY_MASK_ARM_MOM3_ENABLED=True,
    )
    assert R.armed(cfg) == ["DC_BREAK", "MOM3"]


def test_mask_filters_break_only():
    n = 6
    close = np.array([100.0, 101.0, 102.0, 103.0, 90.0, 104.0])
    npz = {
        "close": close,
        "dc_high_15m_prev": np.full(n, 103.5),
        "dc_high_15m": np.full(n, 104.0),
    }
    import v12_quick_engine as V

    cfg = V.QuickConfig()
    cfg.REENTRY_MASK_ARM_DC_BREAK_ENABLED = True
    cfg.DC_BREAK_FILTER_TF = "15m"
    m = R.armed_mask(npz, n, True, cfg, close, V._safe)
    assert m is not None
    m = [bool(x) for x in m]
    assert m == [False, False, False, False, False, True]


def test_live_allows_dc_break_snapshot():
    get = lambda k, d=None: {
        "REENTRY_MASK_ARM_DC_BREAK_ENABLED": True,
        "DC_BREAK_FILTER_TF": "15m",
    }.get(k, d)
    ind = {"dc_high_15m_prev": 103.5}
    assert R.live_allows(get, True, ind, 104.0) == (True, "")
    ok, why = R.live_allows(get, True, ind, 100.0)
    assert ok is False and why == "REENTRY_MASK_ARM_DC_BREAK_TF15m"
    # missing keys fail open, like the vector's np.where(missing, True, ...)
    assert R.live_allows(get, True, {}, 100.0) == (True, "")


def test_live_master_gate_and_short_mirror():
    get = lambda k, d=None: {
        "REENTRY_MASK_ARM_BREAKOUT_RETEST_ENABLED": True,
        "BREAKOUT_RETEST_FILTER_TF": "15m",
    }.get(k, d)
    # master off -> family does nothing
    assert R.live_allows(get, True, {"dc_high_15m_prev": 10.0}, 99.0) == (True, "")
    get2 = lambda k, d=None: {
        "REENTRY_MASK_ARM_BREAKOUT_RETEST_ENABLED": True,
        "BREAKOUT_RETEST_FILTER_TF": "15m",
        "BREAKOUT_RETEST_ARMED_ENABLED": True,
    }.get(k, d)
    ind = {"dc_low_15m_prev": 100.0, "high_15m": 101.0}
    ok, _ = R.live_allows(get2, False, ind, 99.0)
    assert ok is True
    ok, why = R.live_allows(get2, False, ind, 102.0)
    assert ok is False and "BREAKOUT_RETEST" in why


def test_live_mom3_snapshot():
    get = lambda k, d=None: {
        "REENTRY_MASK_ARM_MOM3_ENABLED": True,
        "MOM3_FILTER_TF": "15m",
    }.get(k, d)
    ind = {"close_3bar_15m": 100.0}
    assert R.live_allows(get, True, ind, 98.0) == (True, "")
    ok, _ = R.live_allows(get, True, ind, 100.5)
    assert ok is False
