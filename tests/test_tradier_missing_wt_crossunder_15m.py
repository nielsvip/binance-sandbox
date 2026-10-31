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


def _long_data():
    return {
        "wt1_15m": np.array([1.0, -1.0, -1.0]),
        "wt2_15m": np.array([0.0, 0.0, 0.0]),
        "wt1_1h": np.array([1.0, -1.0, -1.0]),
        "wt2_1h": np.array([0.0, 0.0, 0.0]),
        "wt1_4h": np.zeros(3),
        "wt2_4h": np.zeros(3),
        "wt1_D": np.zeros(3),
        "wt2_D": np.zeros(3),
    }


def test_long_exit_uses_native_directional_15m_and_htf_gates():
    data = _long_data()
    cfg = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True, PARABOLIC_PROTECTION_ENABLED=False)
    mask = wt_crossunder_15m_mask(data, 3, True, cfg, _safe)
    assert mask.tolist() == [False, True, True]
    get = lambda key, default=None: True if key == "WT_CROSSUNDER_FINAL_ENABLED" else default
    fire, reason = wt_crossunder_15m_live(get, True, {key: value[1] for key, value in data.items()}, 2.5, 45)
    assert fire is True
    assert reason == "WT_CROSSUNDER_FINAL_5m_15m_1hTrue_4hFalse_DFalse_g2.50%_hold45m_MANDATORY_REENTRY"


def test_short_exit_mirror_parabolic_veto_and_master_off():
    data = {
        "wt1_15m": np.array([-1.0, 1.0, 1.0]),
        "wt2_15m": np.zeros(3),
        "wt1_1h": np.array([-1.0, 1.0, 1.0]),
        "wt2_1h": np.zeros(3),
        "wt1_4h": np.zeros(3),
        "wt2_4h": np.zeros(3),
        "wt1_D": np.zeros(3),
        "wt2_D": np.zeros(3),
        "rsi_4h": np.array([50.0, 20.0, 50.0]),
        "rsi_1h": np.array([50.0, 30.0, 50.0]),
        "bb_pct_b_4h": np.array([0.5, 0.05, 0.5]),
    }
    on = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True, PARABOLIC_PROTECTION_ENABLED=True)
    off = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=False, PARABOLIC_PROTECTION_ENABLED=True)
    assert wt_crossunder_15m_mask(data, 3, False, on, _safe).tolist() == [False, False, True]
    assert not wt_crossunder_15m_mask(data, 3, False, off, _safe).any()


def test_missing_htf_data_fails_closed():
    cfg = SimpleNamespace(WT_CROSSUNDER_FINAL_ENABLED=True, PARABOLIC_PROTECTION_ENABLED=False)
    assert not wt_crossunder_15m_mask({}, 4, True, cfg, _safe).any()
