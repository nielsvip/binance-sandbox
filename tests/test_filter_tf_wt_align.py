import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_filter_tf_gates as F


def _g(**kw):
    base = {"DELTA_ENGINE_FILTER_TF": "OFF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF": "OFF", "CIRCUIT_SHARPE_GATES_FILTER_TF": "OFF", "BT_WT_CROSS_LADDER_FILTER_TF": "OFF"}
    base.update(kw)
    return lambda k, d: base.get(k, d)


def test_all_off_is_noop():
    assert F.check_filter_tf_wt_align(_g(), {"wt1_15m": -5, "wt2_15m": 5}, True, "OPEN") == (False, "")
    assert F.check_filter_tf_wt_align(lambda k, d: d, {"wt1_15m": -5, "wt2_15m": 5}, True, "OPEN") == (False, "")
    assert F.check_filter_tf_wt_align(_g(DELTA_ENGINE_FILTER_TF="off"), {"wt1_15m": -5, "wt2_15m": 5}, True, "OPEN") == (False, "")


def test_long_short_block_and_allow():
    g = _g(DELTA_ENGINE_FILTER_TF="15m")
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": 5, "wt2_15m": -5}, True, "OPEN") == (False, "")
    blk, why = F.check_filter_tf_wt_align(g, {"wt1_15m": -5, "wt2_15m": 5}, True, "OPEN")
    assert blk is True and "DELTA_ENGINE_FILTER_TF_15m" in why
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": -5, "wt2_15m": 5}, False, "OPEN") == (False, "")
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": 5, "wt2_15m": -5}, False, "OPEN")[0] is True


def test_multi_leg_and():
    g = _g(DELTA_ENGINE_FILTER_TF="15m", DC_MOMENTUM_BOTA_SCORER_FILTER_TF="1h", CIRCUIT_SHARPE_GATES_FILTER_TF="4h")
    ind = {"wt1_15m": 5, "wt2_15m": -5, "wt1_1h": 5, "wt2_1h": -5, "wt1_4h": -5, "wt2_4h": 5}
    blk, why = F.check_filter_tf_wt_align(g, ind, True, "OPEN")
    assert blk is True and "CIRCUIT_SHARPE_GATES_FILTER_TF_4h" in why
    ind["wt1_4h"], ind["wt2_4h"] = 5, -5
    assert F.check_filter_tf_wt_align(g, ind, True, "OPEN") == (False, "")


def test_both_zero_fail_open_and_nan_blocks():
    g = _g(DELTA_ENGINE_FILTER_TF="15m")
    assert F.check_filter_tf_wt_align(g, {}, True, "OPEN") == (False, "")
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": 0.0, "wt2_15m": 0.0}, True, "OPEN") == (False, "")
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": float("nan"), "wt2_15m": 1.0}, True, "OPEN")[0] is True


def test_unknown_tf_inert_and_action_scope():
    g = _g(DELTA_ENGINE_FILTER_TF="W")
    assert F.check_filter_tf_wt_align(g, {"wt1_15m": -5, "wt2_15m": 5}, True, "OPEN") == (False, "")
    g2 = _g(DELTA_ENGINE_FILTER_TF="15m")
    ind = {"wt1_15m": -5, "wt2_15m": 5}
    assert F.check_filter_tf_wt_align(g2, ind, True, "CLOSE") == (False, "")
    assert F.check_filter_tf_wt_align(g2, ind, True, "REDUCE") == (False, "")
    assert F.check_filter_tf_wt_align(g2, ind, True, "AUGMENT") == (False, "")
    assert F.check_filter_tf_wt_align(g2, ind, True, "BUY")[0] is True


def test_matches_vec_cond_on_grid():
    import numpy as np
    import vec_decisions.generic_filter_tf as GM
    def safe(npz, k, n, fill):
        a = np.asarray(npz.get(k, [fill] * n), dtype=float)
        return a if len(a) == n else np.full(n, fill)
    vals = [-60.0, -5.0, 0.0, 5.0, 60.0, float("nan")]
    for is_long in (True, False):
        for w1 in vals:
            for w2 in vals:
                npz = {"wt1_15m": np.array([w1]), "wt2_15m": np.array([w2])}
                vec_pass = bool(GM._cond("wt_cross_side", npz, 1, "15m", is_long, np.array([1.0]), safe)[0])
                g = _g(DELTA_ENGINE_FILTER_TF="15m")
                blk, _ = F.check_filter_tf_wt_align(g, {"wt1_15m": w1, "wt2_15m": w2}, is_long, "OPEN")
                assert blk == (not vec_pass), (is_long, w1, w2, blk, vec_pass)


def test_tradier_caller_wired():
    t = Path("tradier_manage.py").read_text()
    assert "check_filter_tf_wt_align" in t and "FILTER_TF_WT_ALIGN" in t and "_mandatory_reentry_qta" in t
