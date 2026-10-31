from pathlib import Path

import numpy as np
import pytest

import v12_quick_engine as v12
from tools.opt.evaluate_v12 import _exact_30d_slice
from vec_paths.live_entry_engine import aggregate_entry_boost_array, compute_engine_score_arrays


def test_engine_boost_reaches_actual_wt_dc_entry_mask_on_same_bar():
    if not Path("backtest_v8/indicators/AAPL.npz").exists():
        pytest.skip("frozen local AAPL NPZ is unavailable")
    npz = v12.load_npz("tradier", ["AAPL"], "2024-01-01").get("AAPL")
    if npz is None:
        pytest.skip("frozen local AAPL NPZ is unavailable")
    data, _ = _exact_30d_slice(npz, False, 30)
    n = len(data["timestamps"])
    cfg = v12.QuickConfig()
    cfg.apply_tradier_defaults()
    values = {
        "MODE": "tradier",
        "WT_DC_ENABLED": True,
        "WT_DC_DETAILED_SCORER_ENABLED": True,
        "WT_DC_ENTRY_THRESHOLD": 45.0,
        "ENTRY_SCORE_THRESHOLD": 0.0,
        "LIVE_ENTRY_ENGINE_ENABLED": True,
        "LIVE_ENTRY_ENGINE_BOOST_SCORE": 8.0,
        "LIVE_ENTRY_ENGINE_MIN_SCORE": 0.5,
        "ENTRY_ISOLATE_FAMILY": "WT_DC",
        "WT_DC_HTF_GATE": "1h",
        "WT_DC_TF_HTF": "none",
        "WT_DC_TF_HTF2": "none",
        "HTF_ALIGN_REQUIRED_TRADIER": 0,
        "USE_1M_3M_SIGNALS_ENABLED": False,
    }
    for key, value in values.items():
        setattr(cfg, key, value)
    for engine in ("WT", "STOCH", "DC", "HTF", "STDEV_MACRO"):
        setattr(cfg, f"LIVE_ENTRY_ENGINE_{engine}_ENABLED", True)
    components = compute_engine_score_arrays(data, n, "LONG", cfg, mode="tradier")
    boost = aggregate_entry_boost_array(components, cfg, n)
    enabled_mask = v12.compute_entry_signals(data, n, True, cfg)
    cfg.LIVE_ENTRY_ENGINE_ENABLED = False
    disabled_mask = v12.compute_entry_signals(data, n, True, cfg)
    same_bar = 103
    assert boost[same_bar] == pytest.approx(5.6)
    assert enabled_mask[same_bar]
    assert not disabled_mask[same_bar]
