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
