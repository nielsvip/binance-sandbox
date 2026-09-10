import csv
import json
from pathlib import Path

import numpy as np

from vec_paths.v12_reentry_augment_filter_gap_batch8 import FIELD_CONTRACTS, LIFECYCLE_FILTER_APIS, entry_score_filter_decision

ROOT = Path(__file__).resolve().parent


def _default(raw, typ):
    if typ == "bool": return raw.lower() == "true"
    if typ == "int": return int(raw)
    if typ == "float": return float(raw)
    return raw


def test_38_field_contract_matches_authority_and_entry_consumer():
    rows = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_REENTRY_AUGMENT_FILTER_GAP_BATCH8_CONTRACT.csv").open())}
    auth = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv").open())}
    assert len(rows) == len(FIELD_CONTRACTS) == 38
    for name, contract in FIELD_CONTRACTS.items():
        assert rows[name]["authoritative_type"] == auth[name]["authoritative_type"] == contract.value_type
        assert json.loads(rows[name]["authoritative_default_json"]) == _default(auth[name]["authoritative_default"], contract.value_type) == contract.default
        assert tuple(json.loads(rows[name]["authoritative_grid_json"])) == tuple(json.loads(auth[name]["authoritative_grid"])) == contract.grid
        assert contract.lifecycle_filter_consumers == ("ENTRY",)
        assert LIFECYCLE_FILTER_APIS[name] == {"ENTRY": "entry_score_filter_decision"}


def _state(n): return {"candidate_mask": np.ones(n, bool), "is_exit": np.zeros(n, bool)}


def _off(**extra):
    cfg = {"WT_COMPOSITE_SCORING_ENABLED": False, "DC_MOMENT_ENABLED": False,
           "WT_COMPOSITE_DELTA_SCORE_ENABLED": False, "R_S3_DIV_STACK_ENABLED": False,
           "R_S4_HA_STREAK_ENABLED": False, "R_S5_SENT_VEL_ENABLED": False,
           "R_S7_HHLL_STACK_ENABLED": False}
    cfg.update(extra); return cfg


def _comp_arrays():
    return {
        "wt_bull_alignment": np.array([2, 3, 4, 5.]), "wt_bear_alignment": np.array([5, 4, 3, 2.]),
        "wt_composite_long": np.array([-21, 10, 30, 50.]), "wt_composite_short": np.array([50, 30, 10, -21.]),
        "wt_oversold_tf_count": np.array([0, 2, 0, 0.]), "wt_overbought_tf_count": np.array([2, 0, 0, 0.]),
        "wt_hl_count": np.array([0, 0, 2, 0.]), "wt_lh_count": np.array([0, 2, 0, 0.]),
        "wt_bull_cross_count": np.array([0, 0, 0, 2.]), "wt_bear_cross_count": np.array([2, 0, 0, 0.]),
        "wt_any_bull_div": np.array([0, 0, 1, 0]), "wt_any_bear_div": np.array([0, 1, 0, 0]),
    }


def test_composite_hard_gate_and_score_tiers_use_side_exact_arrays():
    arrays = _comp_arrays(); cfg = _off(WT_COMPOSITE_SCORING_ENABLED=True, WT_COMPOSITE_HTF_GATE=True)
    long = entry_score_filter_decision(arrays, _state(4), True, "crypto", cfg)
    assert long.available and long.mask.tolist() == [False, True, True, True]
    assert long.score_delta.tolist() == [0, 7, 20, 11]
    short = entry_score_filter_decision(arrays, _state(4), False, "crypto", cfg)
    assert short.mask.tolist() == [True, True, True, False]
    assert short.score_delta.tolist() == [16, 20, 2, 0]


def test_rs1_rs2_are_nested_under_composite_master_and_use_strict_boundaries():
    arrays = _comp_arrays(); arrays.update(wt_composite_delta=np.array([51, 50, -50, -51.]), wt_percentile_15m=np.array([9, 10, 90, 91.]))
    cfg = _off(WT_COMPOSITE_SCORING_ENABLED=True, R_S1_WT_COMPOSITE_DELTA_USE_ENABLED=True, R_S2_WT_ADAPTIVE_OS_ENABLED=True)
    long = entry_score_filter_decision(arrays, _state(4), True, "crypto", cfg)
    # Base composite [0,7,20,11], then +6/+7 only on strict >/< witnesses.
    assert long.score_delta.tolist() == [13, 7, 20, 11]
    short = entry_score_filter_decision(arrays, _state(4), False, "crypto", cfg)
    assert short.score_delta.tolist() == [16, 20, 2, 13]
    disabled = entry_score_filter_decision({"wt_composite_delta": arrays["wt_composite_delta"]}, _state(4), True, "crypto", _off(R_S1_WT_COMPOSITE_DELTA_USE_ENABLED=True))
    assert disabled.available and not disabled.score_delta.any()


def test_dc_moment_and_composite_delta_score_are_exact_additive_semantics():
    arrays = {"0dc_moment": np.array([-41, -40, 40, 41.]), "wt_composite_delta": np.array([-51, -50, 50, 51.])}
    cfg = _off(DC_MOMENT_ENABLED=True, WT_COMPOSITE_DELTA_SCORE_ENABLED=True)
    assert entry_score_filter_decision(arrays, _state(4), True, "crypto", cfg).score_delta.tolist() == [-15, 0, 0, 13]
    assert entry_score_filter_decision(arrays, _state(4), False, "crypto", cfg).score_delta.tolist() == [13, 0, 0, -15]


def test_rs3_divergence_stack_uses_native_3m_or_5m_without_proxy():
    n = 3
    arrays = {f"wt_divergence_{tf}": np.array(values) for tf, values in {
        "5m": ["HIDDEN_BULL", "BEAR", ""], "15m": ["HIDDEN_BULL", "BEAR", ""],
        "1h": ["", "", ""], "4h": ["", "BEAR", ""], "D": ["", "", ""]}.items()}
    cfg = _off(R_S3_DIV_STACK_ENABLED=True)
    assert entry_score_filter_decision(arrays, _state(n), True, "tradier", cfg).score_delta.tolist() == [25, -20, 0]
    weighted = entry_score_filter_decision(arrays, _state(n), True, "tradier", {**cfg, "R_S3_HTF_WEIGHT_ENABLED": True})
    assert weighted.score_delta.tolist() == [9.375, -65, 0]
    arrays["wt_divergence_3m"] = arrays.pop("wt_divergence_5m")
    result = entry_score_filter_decision(arrays, _state(n), True, "tradier", cfg)
    assert not result.available and "wt_divergence_5m" in result.reason


def test_rs4_and_rs5_score_only_aligned_real_values():
    arrays = {"ha_streak_1h": np.array([0, 2, 6, 2.]), "ha_1h": np.array(["green", "green", "green", "red"]),
              "0sentiment_velocity": np.array([75, 74, -75, 100.])}
    cfg = _off(R_S4_HA_STREAK_ENABLED=True, R_S5_SENT_VEL_ENABLED=True)
    assert entry_score_filter_decision(arrays, _state(4), True, "crypto", cfg).score_delta.tolist() == [5, 10, 25, 5]
    assert entry_score_filter_decision(arrays, _state(4), False, "crypto", cfg).score_delta.tolist() == [0, 0, 5, 10]


def _hhll_tf(arrays, tf, bullish=True, n=3):
    arrays[f"high_{tf}"] = np.full(n, 11 if bullish else 9.); arrays[f"high_{tf}_prev"] = np.full(n, 10.)
    arrays[f"low_{tf}"] = np.full(n, 6 if bullish else 4.); arrays[f"low_{tf}_prev"] = np.full(n, 5.)
    arrays[f"wt_structure_{tf}"] = np.full(n, "HH" if bullish else "LL")
    arrays[f"k_{tf}"] = np.full(n, 60 if bullish else 40.); arrays[f"k_{tf}_prev"] = np.full(n, 50.)


def test_rs7_hhll_requires_min_indicators_and_tfs_with_tradier_native_mapping():
    arrays = {}; _hhll_tf(arrays, "5m"); _hhll_tf(arrays, "15m")
    cfg = _off(R_S7_HHLL_STACK_ENABLED=True, R_S7_HHLL_TFS="3m,15m")
    result = entry_score_filter_decision(arrays, _state(3), True, "tradier", cfg)
    assert result.available and result.score_delta.tolist() == [6, 6, 6]
    del arrays["high_5m"]; arrays["high_3m"] = np.full(3, 11.)
    result = entry_score_filter_decision(arrays, _state(3), True, "tradier", cfg)
    assert not result.available and "high_5m" in result.reason


def test_enabled_missing_data_fails_closed_but_disabled_and_exit_do_not_load_arrays():
    state = _state(2)
    assert entry_score_filter_decision({}, state, True, "crypto", _off()).available
    result = entry_score_filter_decision({}, state, True, "crypto", _off(DC_MOMENT_ENABLED=True))
    assert not result.available and not result.mask.any() and "0dc_moment" in result.reason
    state["is_exit"][:] = True
    result = entry_score_filter_decision({}, state, True, "crypto", {})
    assert result.available and result.mask.all() and not result.score_delta.any()


def test_rs6_zero_only_authoritative_grid_is_not_claimed_as_a_causal_field():
    auth = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv").open())}
    assert json.loads(auth["R_S6_WT_MSTATE_GATE_MODE"]["authoritative_grid"]) == [0]
    assert "R_S6_WT_MSTATE_GATE_MODE" not in FIELD_CONTRACTS
