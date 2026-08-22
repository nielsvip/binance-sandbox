import json
import csv
import gzip
from pathlib import Path

import numpy as np

from classic_formations import formation_vector_mask
from tools.run_classic_formation_universe import (
    FORMATION_SWITCHES,
    FORMATION_TIMEFRAMES,
    VARIANTS,
    aggregate_variant,
    bh_return_pct,
    configured_variant,
    require_complete_universe,
    wanted_formation_direction,
)
from v12_wide_engine import SweepConfig


ROOT = Path(__file__).resolve().parent


def test_current_matrix_directional_universe_contract():
    longs = json.loads((ROOT / "symbols_trb_long.json").read_text())
    shorts = json.loads((ROOT / "symbols_trb_short.json").read_text())
    keys = [*(f"{symbol}_LONG" for symbol in longs), *(f"{symbol}_SHORT" for symbol in shorts)]
    assert len(keys) == len(set(keys))
    assert len(set(longs) | set(shorts)) > 0


def test_universe_refuses_missing_npz_instead_of_running_114_of_115():
    keys = ["A_LONG", "CRWD_SHORT"]
    require_complete_universe(keys, [], 2)
    try:
        require_complete_universe(keys, ["CRWD_SHORT"], 2)
    except ValueError as exc:
        assert str(exc) == "MISSING_NPZ_KEYS:CRWD_SHORT"
    else:
        raise AssertionError("missing directional key was not rejected")


def test_variants_are_baseline_plus_every_single_arm():
    assert FORMATION_TIMEFRAMES == ("15m", "1h", "4h", "D")
    assert len(VARIANTS) == 71
    assert len(FORMATION_SWITCHES) == 14
    for label in VARIANTS:
        config = configured_variant(SweepConfig(), label)
        enabled = [name for name in FORMATION_SWITCHES if getattr(config, name)]
        assert len(enabled) == (0 if label == "baseline" else 1)
        if label != "baseline":
            timeframe = label.rsplit("_", 1)[-1]
            assert config.FORMATION_TFS == (
                "15m,1h,4h,D" if timeframe == "ALL" else timeframe
            )


def test_side_correct_buy_and_hold_return():
    assert bh_return_pct([100.0, 50.0], "LONG", 0.0) == -50.0
    assert bh_return_pct([100.0, 50.0], "SHORT", 0.0) == 50.0
    assert bh_return_pct([100.0, 50.0], "SHORT", 0.25) == 49.75


def test_long_short_entry_exit_direction_contract():
    assert wanted_formation_direction("LONG", "ENTRY") == "bull"
    assert wanted_formation_direction("SHORT", "ENTRY") == "bear"
    assert wanted_formation_direction("LONG", "EXIT") == "bear"
    assert wanted_formation_direction("SHORT", "EXIT") == "bull"


def test_vector_selector_applies_direction_contract_on_each_action():
    arrays = {
        "formation_head_shoulders_bull_score_15m": np.array([0.0, 0.70, 0.90]),
        "formation_head_shoulders_bear_score_15m": np.array([0.0, 0.80, 0.75]),
    }
    for side, action, expected in (
        ("LONG", "entry", 0.90),
        ("SHORT", "entry", 0.75),
        ("LONG", "exit", 0.75),
        ("SHORT", "exit", 0.90),
    ):
        config = configured_variant(SweepConfig(), f"head_shoulders_{action}_15m")
        mask, scores, _ = formation_vector_mask(
            arrays,
            is_long=side == "LONG",
            action=action,
            config=config,
            n=3,
        )
        assert mask[-1]
        assert np.isclose(scores[-1], expected)


def test_aggregate_retains_cross_key_robustness_and_sample_contract():
    rows = []
    for index, side in enumerate(("LONG", "SHORT")):
        baseline = [0.1] * 35
        candidate = [0.2] * 35 if index == 0 else [0.05] * 35
        rows.append({
            "key": f"T{index}_{side}",
            "symbol": f"T{index}",
            "side": side,
            "history_years": 2.0,
            "bh_return_pct": 1.0,
            "variants": {
                "baseline": {
                    "returns": baseline,
                    "trades": len(baseline),
                    "total_gain_pct": sum(baseline),
                    "max_dd_pct": 1.0,
                    "formation_action_count": 0,
                    "action_fingerprint": "base" + side,
                },
                "head_shoulders_entry_15m": {
                    "returns": candidate,
                    "trades": len(candidate),
                    "total_gain_pct": sum(candidate),
                    "max_dd_pct": 0.5,
                    "formation_action_count": 2,
                    "action_fingerprint": "arm" + side,
                },
            },
        })
    result = aggregate_variant("head_shoulders_entry_15m", rows, years=2.0)
    assert result["positive_delta_vs_baseline_keys"] == 1
    assert result["action_changed_keys"] == 2
    assert result["formation_fired_keys"] == 2
    assert result["qualified_side_cells_gt_1yr_30trades"] == 2
    assert result["sample_contract_status"].startswith("DIAGNOSTIC")
    assert result["formation_timeframe"] == "15m"
