import sys

import numpy as np

sys.path.insert(0, "tests")

import v12_quick_engine as v12
from test_tradier_reviewed_exit_ledgers import _base_npz
from vec_decisions import lane_vec_mopup3
from vec_decisions.asr_mts_provider import analyze_mts_state_array


def test_v12_entry_signal_route_consumes_full_native_mts_scores(monkeypatch):
    npz = _base_npz()
    n = len(npz["close"])
    rng = np.random.default_rng(901)
    for tf in ("1m", "3m", "15m", "1h", "4h", "D"):
        npz[f"k_{tf}"] = rng.uniform(0.0, 100.0, n)
        npz[f"d_{tf}"] = rng.uniform(0.0, 100.0, n)
        npz[f"k_{tf}_prev"] = rng.uniform(0.0, 100.0, n)
        npz[f"wt1_{tf}"] = rng.normal(0.0, 30.0, n)
        npz[f"wt2_{tf}"] = rng.normal(0.0, 30.0, n)
        npz[f"wt_cross_{tf}"] = rng.choice([-1, 0, 1], n).astype(np.int8)
        npz[f"wt_cross_value_{tf}"] = rng.normal(0.0, 40.0, n)
        npz[f"wt_cross_prev_value_{tf}"] = rng.normal(0.0, 40.0, n)
        npz[f"wt_cross_rising_{tf}"] = rng.integers(0, 2, n).astype(np.int8)
        npz[f"wt_velocity_{tf}"] = rng.normal(0.0, 3.0, n)
        npz[f"wt_cross_bars_ago_{tf}"] = rng.integers(0, 20, n)
        npz[f"wt_divergence_{tf}"] = rng.choice([-2, -1, 0, 1, 2], n).astype(np.int8)
        npz[f"wt_momentum_state_{tf}"] = rng.choice([-2, -1, 0, 1, 2], n).astype(np.int8)
        npz[f"wt_bullish_{tf}"] = rng.integers(0, 2, n).astype(np.int8)
        npz[f"dc_position_{tf}"] = rng.uniform(0.0, 1.0, n)
        npz[f"dc_width_{tf}"] = rng.uniform(0.0, 8.0, n)
    cfg = v12.QuickConfig()
    cfg.MODE = "crypto"
    cfg.USE_1M_3M_SIGNALS_ENABLED = False
    cfg.MTS_GATE_ENABLED = True
    cfg.MTS_WEIGHT_1m = 3.0
    cfg.MTS_WEIGHT_3m = 5.0
    cfg.MTS_WEIGHT_15m = 8.0
    cfg.MTS_WEIGHT_1h = 12.0
    cfg.MTS_WEIGHT_4h = 4.0
    cfg.MTS_WEIGHT_D = 2.0
    expected_state = analyze_mts_state_array(npz, cfg=cfg, is_long=True, n=n)
    expected = (expected_state["bottom_score"] >= 10.0) & (expected_state["entry_quality"] >= 5.0)
    seen = []
    original = lane_vec_mopup3.get
    def observed(switch, source, size, is_long, candidate_cfg, **kwargs):
        result = original(switch, source, size, is_long, candidate_cfg, **kwargs)
        if switch == "MTS_GATE_ENABLED":
            seen.append(result)
        return result
    monkeypatch.setattr(lane_vec_mopup3, "get", observed)
    v12.compute_entry_signals(npz, n, True, cfg, "SYNTH")
    assert len(seen) == 1
    assert np.array_equal(seen[0], expected)
    cfg.MTS_GATE_ENABLED = False
    seen.clear()
    v12.compute_entry_signals(npz, n, True, cfg, "SYNTH")
    assert len(seen) == 1
    assert seen[0] is None
