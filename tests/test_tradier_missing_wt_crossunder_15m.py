from types import SimpleNamespace

import numpy as np

from vec_decisions.tradier_missing_wt_crossunder_15m import (
    wt_crossunder_15m_live,
    wt_crossunder_15m_mask,
)


def _safe(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default, dtype=float)
    return np.asarray(value, dtype=float)


def test_long_exit_uses_causal_15m_cross_and_stoch_threshold():
    data = {
        "wt1_15m": np.array([1.0, -1.0, -1.0]),
        "wt2_15m": np.array([0.0, 0.0, 0.0]),
        "stoch_k_15m": np.array([80.0, 80.0, 80.0]),
    }
    cfg = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True)
    assert wt_crossunder_15m_mask(data, 3, True, cfg, _safe).tolist() == [False, True, False]
    get = lambda key, default=None: True if key == "WT_CROSSUNDER_FINAL_ENABLED" else default
    live = []
    for i in range(3):
        prev = data["wt1_15m"][max(0, i - 1)]
        fire, _ = wt_crossunder_15m_live(
            get,
            True,
            {
                "wt1_15m": data["wt1_15m"][i],
                "wt2_15m": data["wt2_15m"][i],
                "wt1_15m_prev": prev,
                "stoch_k_15m": data["stoch_k_15m"][i],
            },
        )
        live.append(fire)
    assert live == [False, True, False]


def test_short_exit_mirror_and_master_off_are_zero():
    data = {
        "wt1_15m": np.array([-1.0, 1.0, 1.0]),
        "wt2_15m": np.array([0.0, 0.0, 0.0]),
        "stoch_k_15m": np.array([20.0, 20.0, 20.0]),
    }
    on = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True)
    off = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=False)
    assert wt_crossunder_15m_mask(data, 3, False, on, _safe).tolist() == [False, True, False]
    assert not wt_crossunder_15m_mask(data, 3, False, off, _safe).any()


def test_missing_npz_keys_fail_closed():
    cfg = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True)
    assert not wt_crossunder_15m_mask({}, 4, True, cfg, _safe).any()
