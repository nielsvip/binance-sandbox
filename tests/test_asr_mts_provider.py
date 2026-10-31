import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.asr_mts_provider import analyze_mts_state_array
from vec_decisions.asr_ordered_composer import bind_asr_npz_arrays


SOURCE = Path(__file__).resolve().parents[1] / "ez_positions_quick.py"


def _native_function():
    tree = ast.parse(SOURCE.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "analyze_multi_tf_state")
    namespace = {"config": SimpleNamespace(TF_SCALP="3m")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["analyze_multi_tf_state"]


def _fixture(n=128, seed=17):
    rng = np.random.default_rng(seed)
    out = {"timestamps": np.arange(n, dtype=np.int64) * 900 + 1_700_000_000}
    for i, tf in enumerate(("1m", "3m", "15m", "1h", "4h", "D")):
        out[f"k_{tf}"] = rng.uniform(0, 100, n).astype(np.float32)
        out[f"d_{tf}"] = rng.uniform(0, 100, n).astype(np.float32)
        out[f"k_{tf}_prev"] = rng.uniform(0, 100, n).astype(np.float32)
        out[f"wt1_{tf}"] = rng.normal(0, 35, n).astype(np.float32)
        out[f"wt2_{tf}"] = rng.normal(0, 35, n).astype(np.float32)
        out[f"wt_cross_{tf}"] = rng.choice([-1, 0, 1], n).astype(np.int8)
        out[f"wt_cross_value_{tf}"] = rng.normal(0, 45, n).astype(np.float32)
        out[f"wt_cross_prev_value_{tf}"] = rng.normal(0, 45, n).astype(np.float32)
        out[f"wt_cross_rising_{tf}"] = rng.integers(0, 2, n).astype(np.int8)
        out[f"wt_velocity_{tf}"] = rng.normal(0, 4, n).astype(np.float32)
        out[f"wt_cross_bars_ago_{tf}"] = rng.integers(0, 30, n).astype(np.int16)
        out[f"wt_divergence_{tf}"] = rng.choice([-2, -1, 0, 1, 2], n).astype(np.int8)
        out[f"wt_momentum_state_{tf}"] = rng.choice([-2, -1, 0, 1, 2], n).astype(np.int8)
        out[f"wt_bullish_{tf}"] = rng.integers(0, 2, n).astype(np.int8)
        out[f"dc_position_{tf}"] = rng.uniform(0, 1, n).astype(np.float32)
        out[f"dc_width_{tf}"] = rng.uniform(0, 8, n).astype(np.float32)
    return out


@pytest.mark.parametrize("is_long", [True, False])
def test_mts_numpy_matches_actual_scalar_source_both_sides(is_long):
    raw = _fixture()
    cfg = {"USE_1M_3M_SIGNALS_ENABLED": True, "TF_SCALP": "3m"}
    native = _native_function()
    vector = analyze_mts_state_array(raw, cfg=cfg, is_long=is_long)
    decoded = bind_asr_npz_arrays(raw, cfg)
    for i in (0, 1, 7, 31, 63, 127):
        row = {key: (value[i].item() if hasattr(value[i], "item") else value[i]) if isinstance(value, np.ndarray) and value.ndim else value for key, value in decoded.items()}
        scalar = native(row, row, is_long, float(row.get("close", 1.0)))
        for key in ("bottom_score", "entry_quality", "direction", "gain_potential", "size_multiplier", "range_score", "convergence_count", "convergence_score", "btb_multiplier", "price_through_extreme", "dc_htf_pos", "dc_ltf_pos", "dc_moment", "dc_expansion"):
            assert vector[key][i] == pytest.approx(scalar[key], abs=1e-10), (key, i, vector[key][i], scalar[key])
        assert vector["convergence_tfs"][i] == scalar["convergence_tfs"]
        for tf in ("1m", "3m", "15m", "1h", "4h", "D"):
            for key in ("bottom", "entry", "direction", "range", "k", "d", "dc_pos", "dc_width", "wt_score", "wt_bullish", "wt_velocity", "wt_bars_ago"):
                assert vector["tf_breakdown"][tf][key][i] == pytest.approx(scalar["tf_breakdown"][tf][key], abs=1e-10)


def test_mts_ltf_off_hydrates_missing_micro_columns_from_15m():
    raw = _fixture()
    for key in tuple(raw):
        if any(key.endswith("_" + tf) or ("_" + tf + "_") in key for tf in ("1m", "3m")):
            raw.pop(key)
    cfg = {"USE_1M_3M_SIGNALS_ENABLED": False, "TF_SCALP": "3m"}
    raw["k_15m"] = np.arange(len(raw["timestamps"]), dtype=np.float32) % 100
    result = analyze_mts_state_array(raw, cfg=cfg, is_long=True)
    assert np.array_equal(result["tf_breakdown"]["3m"]["k"], raw["k_15m"])
    assert np.array_equal(result["tf_breakdown"]["1m"]["k"], raw["k_15m"])


def test_mts_matches_source_nan_and_missing_field_defaults():
    raw = _fixture(n=8)
    raw["dc_position_15m"] = np.array([np.nan, 2.0, -1.0, 0.5, 0.5, 0.5, 0.5, 0.5])
    raw["dc_width_15m"] = np.array([np.nan, 0.0, 1.0, 2.0, 5.0, 6.0, 4.0, 3.0])
    raw.pop("0dc_htf_pos", None)
    cfg = {"USE_1M_3M_SIGNALS_ENABLED": True, "TF_SCALP": "3m"}
    native = _native_function()
    vector = analyze_mts_state_array(raw, cfg=cfg, is_long=True)
    decoded = bind_asr_npz_arrays(raw, cfg)
    for i in range(8):
        row = {key: (value[i].item() if hasattr(value[i], "item") else value[i]) if isinstance(value, np.ndarray) and value.ndim and value.shape[0] == 8 else value for key, value in decoded.items()}
        scalar = native(row, row, True, 1.0)
        for key in ("bottom_score", "entry_quality", "direction", "gain_potential", "size_multiplier", "range_score"):
            assert vector[key][i] == pytest.approx(scalar[key], abs=1e-10)
