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


def test_mts_v12_consumer_matches_actual_indicator_store_snapshot(tmp_path):
    import ast
    from pathlib import Path
    from types import SimpleNamespace

    from backtest_v8_harness import IndicatorStore
    from vec_decisions.asr_input_adapter import paired_indicator_view
    from vec_decisions.live_indicator_labels import decode_live_indicator_labels

    n = 7
    timestamps = np.arange(n, dtype=np.int64) * 900 + 1_790_000_000
    arrays = {"timestamps": timestamps, "close": np.linspace(100.0, 103.0, n)}
    for offset, tf in enumerate(("1m", "3m", "15m", "1h", "4h", "D")):
        arrays[f"k_{tf}"] = np.linspace(10.0 + offset, 80.0 - offset, n)
        arrays[f"d_{tf}"] = np.linspace(15.0, 75.0, n)
        arrays[f"k_{tf}_prev"] = np.linspace(12.0, 78.0, n)
        arrays[f"wt1_{tf}"] = np.linspace(-55.0, 25.0, n)
        arrays[f"wt2_{tf}"] = np.linspace(-45.0, 20.0, n)
        arrays[f"wt_cross_{tf}"] = np.asarray([-1, 0, 1, 1, 0, -1, 0], dtype=np.int8)
        arrays[f"wt_cross_value_{tf}"] = np.linspace(-60.0, 60.0, n)
        arrays[f"wt_cross_prev_value_{tf}"] = np.linspace(60.0, -60.0, n)
        arrays[f"wt_cross_rising_{tf}"] = np.asarray([0, 1, 1, 0, 1, 0, 1], dtype=np.int8)
        arrays[f"wt_velocity_{tf}"] = np.linspace(-5.0, 5.0, n)
        arrays[f"wt_cross_bars_ago_{tf}"] = np.asarray([0, 1, 2, 4, 8, 12, 999], dtype=np.int16)
        arrays[f"wt_divergence_{tf}"] = np.asarray([-1, 0, 1, 2, -2, 0, 1], dtype=np.int8)
        arrays[f"wt_momentum_state_{tf}"] = np.asarray([-2, -1, 0, 1, 2, 0, -2], dtype=np.int8)
        arrays[f"wt_bullish_{tf}"] = np.asarray([1, 1, 0, 1, 0, 0, 1], dtype=np.int8)
        arrays[f"dc_position_{tf}"] = np.linspace(0.05, 0.95, n)
        arrays[f"dc_width_{tf}"] = np.linspace(1.0, 7.0, n)
    path = tmp_path / "mts_indicator_store_fixture.npz"
    np.savez(path, **arrays)
    store = IndicatorStore(str(path))
    cfg = v12.QuickConfig()
    cfg.USE_1M_3M_SIGNALS_ENABLED = False
    cfg.TF_SCALP = "3m"
    cfg.MTS_WEIGHT_1m = 3.0
    cfg.MTS_WEIGHT_3m = 5.0
    cfg.MTS_WEIGHT_15m = 8.0
    cfg.MTS_WEIGHT_1h = 12.0
    cfg.MTS_WEIGHT_4h = 4.0
    cfg.MTS_WEIGHT_D = 2.0
    native_source = Path(__file__).resolve().parents[1] / "ez_positions_quick.py"
    tree = ast.parse(native_source.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "analyze_multi_tf_state")
    namespace = {"config": SimpleNamespace(TF_SCALP="3m")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(native_source), "exec"), namespace)
    native_analyzer = namespace["analyze_multi_tf_state"]
    raw = {key: arrays[key] for key in arrays if key != "timestamps"}
    raw["timestamps"] = timestamps
    vector = analyze_mts_state_array(raw, cfg=cfg, is_long=True, n=n)
    for index in range(n):
        snapshot = decode_live_indicator_labels(store.build_indicator_dict(index))
        ind, metrics, missing = paired_indicator_view(snapshot, snapshot, False)
        assert not missing
        native = native_analyzer(ind, metrics, True, float(snapshot["current_price"]))
        assert vector["bottom_score"][index] == native["bottom_score"]
        assert vector["entry_quality"][index] == native["entry_quality"]
