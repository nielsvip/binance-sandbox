"""Native tiered DC-breakout wiring (USER 2026-10-24 VEC-LAST catch-up).

The vector sweeps DC_BREAKOUT_ENTRY via the shared tiered core; the tradier
live lane must fire the same predicate through its own path (not via the
twin), gated on the existing DC_BREAKOUT_ENTRY_ENABLED switch.
"""
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Cfg:
    _DEFAULTS = {
        "DC_BREAKOUT_OVERBOUGHT_K15M_CAP": 95.0,
        "DC_BREAKOUT_ALLOW_15M": False,
        "DC_BREAKOUT_ALLOW_3M": False,
        "USE_1M_3M_SIGNALS_ENABLED": False,
    }

    def __init__(self, ov=None):
        self._ov = dict(ov or {})

    def __getattr__(self, k):
        if k.startswith("_"):
            raise AttributeError(k)
        if k in self._ov:
            return self._ov[k]
        return self._DEFAULTS.get(k, False)


def _breakout_inds(long=True):
    px = 105.0 if long else 85.0
    hi, lo = (100.0, 90.0) if long else (100.0, 90.0)
    return {
        "dc_high_4h": hi, "dc_high_1h": hi, "dc_high_15m": hi, "dc_high_3m": hi,
        "dc_low_4h": lo, "dc_low_1h": lo, "dc_low_15m": lo, "dc_low_3m": lo,
        "stoch_k_15m": 50.0,
        "wt1_3m": 10.0 if long else -10.0, "wt2_3m": 5.0 if long else -5.0,
        "wt1_15m": 10.0 if long else -10.0, "wt2_15m": 5.0 if long else -5.0,
        "_px": px,
    }


def test_shared_core_fires_breakout_both_sides():
    from vec_decisions import check_entry_candidates_crypto__dc_breakout_tiered as T
    for long in (True, False):
        inds = _breakout_inds(long)
        px = inds.pop("_px")
        fires, mult, rsn = T.check_dc_breakout_tiered(_Cfg(), inds, px, long)
        assert fires is True, (long, mult, rsn)
        assert "DC_BREAKOUT" in rsn, rsn


def test_shared_core_silent_without_breakout():
    from vec_decisions import check_entry_candidates_crypto__dc_breakout_tiered as T
    inds = _breakout_inds(True)
    inds.pop("_px")
    inds["dc_high_4h"] = inds["dc_high_1h"] = inds["dc_high_15m"] = inds["dc_high_3m"] = 200.0
    fires, mult, rsn = T.check_dc_breakout_tiered(_Cfg(), inds, 105.0, True)
    assert fires is False, (mult, rsn)


def test_tradier_lane_calls_shared_core():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "check_dc_breakout_tiered(" in src
    assert "TRADIER_DC_BREAKOUT_" in src and "_TIERED_" in src
    assert "DC_BREAKOUT_ENTRY_ENABLED" in src
