"""Worker FILTER_TF enforcement + vec direction conformance (USER 2026-10-24 exact
same lanes). Pins: policy exposure, veto/refusal strings, direction semantics,
absent-key skip parity with live."""
import numpy as np

from ez_manage import TradingPolicy, _parity_filter_tf_gates
from vec_decisions import generic_filter_tf as gftf


def test_policy_exposes_shared_gate():
    assert callable(TradingPolicy.parity_filter_tf_gates)
    ind = {"close": 100.0}
    assert TradingPolicy.parity_filter_tf_gates(ind, True, 100.0) == _parity_filter_tf_gates(ind, True, 100.0)


def test_gate_blocks_and_names_filter():
    ind = {"close": 100.0, "dc_high_D_prev": 110.0}
    import ez_manage as ezm
    ok, why = TradingPolicy.parity_filter_tf_gates(ind, True, 100.0)
    assert isinstance(ok, bool) and isinstance(why, str)


def test_vec_direction_side_semantics():
    n = 8
    d = {"bar_direction_D": np.array([1, 1, -1, 0, 1, -1, 0, 1], dtype=float)}
    safe = lambda npz, k, nn, default=0.0: np.asarray(npz[k], dtype=float)
    m = gftf._cond("bar_pattern_side", d, n, "D", True, np.zeros(n), safe)
    assert list(map(bool, m)) == [True, True, False, False, True, False, False, True]
    m2 = gftf._cond("bar_pattern_side", d, n, "D", False, np.zeros(n), safe)
    assert list(map(bool, m2)) == [False, False, True, False, False, True, False, False]


def test_vec_absent_key_skips_like_live():
    n = 4
    d = {}
    safe = lambda npz, k, nn, default=0.0: np.asarray(npz.get(k, np.full(nn, default)), dtype=float)
    assert gftf._cond("bar_pattern_side", d, n, "D", True, np.zeros(n), safe) is None
    ok, _ = _parity_filter_tf_gates({"close": 100.0}, True, 100.0)
    assert ok is True


def test_atr_trail_15m_inert_like_vec_twin():
    # VEC-TRUTH 2026-10-24: wirec_batch1_gates.atr_trail_filter_tf_allow returns
    # all-allow unless tf in (1h,4h,D,W). The veto must not invent 15m refusals.
    ind = {"close": 100.0, "wt1_15m": -15.0, "wt2_15m": -8.0}
    ok, why = _parity_filter_tf_gates(ind, True, 100.0)
    assert ok is True, why
    ind1h = {"close": 100.0, "wt1_1h": -15.0, "wt2_1h": -8.0}
    get1h = lambda n, d: {"ATR_TRAIL_FILTER_TF": "1h"}.get(n, d)
    ok, why = _parity_filter_tf_gates(ind1h, True, 100.0, cfg_get=get1h)
    assert ok is False and "ATR_TRAIL_TF_1h_BLOCK" in why


def test_circuit_sharpe_15m_inert_like_vec_twin():
    ind = {"close": 100.0, "wt1_15m": -15.0, "wt2_15m": -8.0}
    ok, why = _parity_filter_tf_gates(ind, True, 100.0)
    assert ok is True, why


def test_cfg_get_resolver_binds_board_values():
    # Per-sym resolver overrides the global default (production board values).
    ind = {"close": 100.0, "wt1_1h": -15.0, "wt2_1h": -8.0}
    get_off = lambda n, d: {"ATR_TRAIL_FILTER_TF": "OFF"}.get(n, d)
    ok, _ = _parity_filter_tf_gates(ind, True, 100.0, cfg_get=get_off)
    assert ok is True
    get_on = lambda n, d: {"ATR_TRAIL_FILTER_TF": "1h"}.get(n, d)
    ok, why = _parity_filter_tf_gates(ind, True, 100.0, cfg_get=get_on)
    assert ok is False and "ATR_TRAIL_TF_1h_BLOCK" in why


def test_family_lane_tags_kzone_fires_per_sym():
    # Shared helper: KZONE fires at k=7 with the board switch on, silent when off.
    from ez_positions_quick import _family_lane_tags
    ind = {"k_15m": 7.27, "close": 0.0047}
    get_on = lambda n, d: {"K_ZONE_ENTRY_ENABLED": True}.get(n, d)
    assert "B_KZONE_FAMILY" in _family_lane_tags(ind, 0.0047, True, get_on)
    get_off = lambda n, d: {"K_ZONE_ENTRY_ENABLED": False}.get(n, d)
    assert "B_KZONE_FAMILY" not in _family_lane_tags(ind, 0.0047, True, get_off)
