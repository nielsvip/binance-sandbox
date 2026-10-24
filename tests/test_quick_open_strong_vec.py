"""QUICK_OPEN_STRONG vectorized coverage (USER 2026-10-24: every path gets a switch
+ a vectorized version). Pins the shared decider and the sim-loop driver:
fires on strong momentum + low risk, silent flat/gated/tradier, scalar/vec agree."""
import numpy as np

import vec_decisions.quick_open_strong as qos


class _Cfg:
    MODE = "crypto"
    QUICK_OPEN_STRONG_VEC_ENABLED = True
    QUICK_OPEN_STRONG_SCORE_THRESHOLD = 80.0


def _pos(qty=1.0, peak=1.0, hist=()):
    return {"qty": qty, "peak_pnl_pct": peak, "_qos_gain_hist": list(hist)}


def test_fires_strong_momentum_low_risk():
    # mom=mean([20,24,28])=24 -> 50+12+18=80 >= 80 boundary fires
    ok, reason = qos.augment_fire_from_state(_Cfg(), _pos(peak=30.0, hist=[20.0, 24.0, 28.0, 30.0]), 30.0, True)
    assert ok and reason.startswith("QUICK_OPEN_STRONG_BUY")


def test_silent_flat_or_gated_or_tradier():
    assert qos.augment_fire_from_state(_Cfg(), None, 4.0, True) == (False, "")
    assert qos.augment_fire_from_state(_Cfg(), _pos(qty=0.0, hist=[9.0, 9.0]), 9.0, True) == (False, "")
    assert qos.augment_fire_from_state(_Cfg(), _pos(hist=[9.0]), 9.0, True) == (False, "")  # current-bar-only hist = no prior ticks
    _off = _Cfg()
    _off.QUICK_OPEN_STRONG_VEC_ENABLED = False
    assert qos.augment_fire_from_state(_off, _pos(hist=[9.0, 9.0, 9.0]), 9.0, True) == (False, "")
    _tr = _Cfg()
    _tr.MODE = "tradier"
    assert qos.augment_fire_from_state(_tr, _pos(hist=[9.0, 9.0, 9.0]), 9.0, True) == (False, "")


def test_drawdown_raises_risk_silences():
    ok, _ = qos.augment_fire_from_state(_Cfg(), _pos(peak=12.0, hist=[1.0, 1.0, 1.0]), 1.0, True)
    assert ok is False


def test_scalar_vec_agree():
    m = np.array([4.0, 0.0, -5.0])
    r = np.array([1.0, 1.0, 1.0])
    hv = np.array([False, False, False])
    mask = qos.check_quick_open_strong_vec(_Cfg(), m, r, hv)
    for k in range(3):
        ok, _ = qos.check_quick_open_strong(_Cfg(), {}, True, momentum=float(m[k]), risk_level=float(r[k]), high_vol=False)
        assert bool(mask[k]) == ok
