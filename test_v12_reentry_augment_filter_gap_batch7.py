import csv
import json
from pathlib import Path

import numpy as np

from vec_paths.v12_reentry_augment_filter_gap_batch7 import (
    FIELD_CONTRACTS,
    LIFECYCLE_FILTER_APIS,
    htf_direction_filter_mask,
    ratio_pnl_filter_mask,
    wt_entry_filter_mask,
)


ROOT = Path(__file__).resolve().parent


def _default(raw, typ):
    if typ == "bool": return raw.lower() == "true"
    if typ == "int": return int(raw)
    if typ == "float": return float(raw)
    return raw


def test_22_field_contract_matches_authority_and_real_consumers():
    rows = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_REENTRY_AUGMENT_FILTER_GAP_BATCH7_CONTRACT.csv").open())}
    auth = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv").open())}
    assert len(rows) == len(FIELD_CONTRACTS) == 22
    expected = {"HTF_DIRECTION": {"ENTRY", "REENTRY", "AUGMENT"}, "RATIO_PNL": {"ENTRY", "AUGMENT"}, "WT_ENTRY": {"ENTRY"}}
    for name, contract in FIELD_CONTRACTS.items():
        assert rows[name]["authoritative_type"] == auth[name]["authoritative_type"] == contract.value_type
        assert json.loads(rows[name]["authoritative_default_json"]) == _default(auth[name]["authoritative_default"], contract.value_type) == contract.default
        assert tuple(json.loads(rows[name]["authoritative_grid_json"])) == tuple(json.loads(auth[name]["authoritative_grid"])) == contract.grid
        assert set(contract.lifecycle_filter_consumers) == set(LIFECYCLE_FILTER_APIS[name]) == expected[contract.family]


def _htf_fixture():
    n = 4
    arrays = {
        "wt1_D": np.array([2, -2, 2, 2.]), "wt2_D": np.array([1, -1, 1, 1.]),
        "wt1_4h": np.array([2, -2, 0, 2.]), "wt2_4h": np.array([1, -1, 1, 1.]),
        "wt1_1h": np.array([0, -2, 2, 2.]), "wt2_1h": np.array([1, -1, 1, 1.]),
        "close": np.array([110, 90, 110, 90.]), "sma_200_D": np.full(n, 100.),
    }
    state = {k: np.zeros(n, bool) for k in ("is_augment_target", "is_rz_entry", "is_v3_bypass", "is_ratio_recovery_bypass")}
    state.update(candidate_mask=np.ones(n, bool), is_open_target=np.ones(n, bool))
    return arrays, state


def test_htf_direction_exact_votes_d_mandatory_sma_and_short():
    arrays, state = _htf_fixture()
    cfg = {"HTF_DIRECTION_GATE_ENABLED": True, "HTF_GATE_MIN_CONFIRMATIONS": 2, "HTF_GATE_SIGNALS_SMA200D": True}
    assert htf_direction_filter_mask(arrays, state, True, cfg).mask.tolist() == [True, False, True, True]
    assert htf_direction_filter_mask(arrays, state, False, cfg).mask.tolist() == [False, True, False, False]
    assert htf_direction_filter_mask(arrays, state, True, {**cfg, "HTF_GATE_D_MANDATORY": True}).mask.tolist() == [True, False, True, True]
    assert htf_direction_filter_mask(arrays, state, True, {**cfg, "HTF_GATE_SIGNALS_SMA200D": False}).mask.tolist() == [True, False, True, True]
    # At three confirmations the real SMA vote is decisive for the first bar.
    assert htf_direction_filter_mask(arrays, state, True, {**cfg, "HTF_GATE_MIN_CONFIRMATIONS": 3}).mask[0]
    assert not htf_direction_filter_mask(arrays, state, True, {**cfg, "HTF_GATE_MIN_CONFIRMATIONS": 3, "HTF_GATE_SIGNALS_SMA200D": False}).mask[0]


def test_htf_explicit_lifecycle_routes_and_bypasses_are_not_reason_proxies():
    arrays, state = _htf_fixture(); arrays["wt1_D"][:] = -2; arrays["wt2_D"][:] = -1
    cfg = {"HTF_DIRECTION_GATE_ENABLED": True, "HTF_GATE_MIN_CONFIRMATIONS": 3, "HTF_GATE_APPLY_TO_AUGMENT": True}
    state["is_open_target"][:] = False; state["is_augment_target"][:] = True
    assert not htf_direction_filter_mask(arrays, state, True, cfg).mask.any()
    state["is_rz_entry"][0] = True; state["is_v3_bypass"][1] = True; state["is_ratio_recovery_bypass"][2] = True
    assert htf_direction_filter_mask(arrays, state, True, cfg).mask.tolist() == [True, True, True, False]
    assert not htf_direction_filter_mask(arrays, state, True, {**cfg, "HTF_GATE_BYPASS_RZ": False}).mask[0]


def test_htf_disabled_or_nonapplicable_does_not_require_arrays_but_enabled_applicable_does():
    _, state = _htf_fixture()
    assert htf_direction_filter_mask({}, state, True, {"HTF_DIRECTION_GATE_ENABLED": False}).available
    state["is_open_target"][:] = False
    assert htf_direction_filter_mask({}, state, True, {"HTF_DIRECTION_GATE_ENABLED": True}).available
    state["is_open_target"][0] = True
    result = htf_direction_filter_mask({}, state, True, {"HTF_DIRECTION_GATE_ENABLED": True})
    assert not result.available and not result.mask.any() and "wt1_D" in result.reason


def _ratio_state():
    n = 4
    return {
        "candidate_mask": np.ones(n, bool), "current_long_value": np.array([45, 250, 45, 250.]),
        "current_short_value": np.array([100, 100, 100, 100.]), "order_notional": np.full(n, 10.),
        "long_avg_gain": np.array([5, -5, 5, -5.]), "short_avg_gain": np.zeros(n),
        "long_count": np.ones(n), "short_count": np.ones(n),
        "is_hedge": np.zeros(n, bool), "is_reentry": np.zeros(n, bool),
        "is_rz": np.zeros(n, bool), "is_ratio_recovery": np.zeros(n, bool),
    }


def test_ratio_pnl_tightens_only_losing_side_and_obeys_open_augment_exemptions():
    state = _ratio_state(); cfg = {"LS_RATIO_ENFORCE": True, "RATIO_PNL_DYNAMIC_GATES_ENABLED": True}
    # Longs winning tightens min to .5: a new SHORT at .409 is blocked; disabled uses .4 and passes.
    assert not ratio_pnl_filter_mask(state, False, cfg).mask[0]
    assert ratio_pnl_filter_mask(state, False, {**cfg, "RATIO_PNL_DYNAMIC_GATES_ENABLED": False}).mask[0]
    # Shorts winning tightens max to 2.0: a new LONG at 2.6 is blocked; disabled uses 2.5 (already above), also blocked.
    assert not ratio_pnl_filter_mask(state, True, cfg).mask[1]
    state["is_reentry"][0] = True; state["is_hedge"][1] = True; state["is_rz"][2] = True; state["is_ratio_recovery"][3] = True
    assert ratio_pnl_filter_mask(state, False, cfg).mask.all()


def _wt_state(n=5):
    return {"candidate_mask": np.ones(n, bool), "is_exit": np.zeros(n, bool), "is_hedge": np.zeros(n, bool)}


def _wt_off(**extra):
    base = {"WT_MTF_VEL_GATE_ENABLED": False, "WT_CHOP_GATE_ENABLED": False, "WT_COMPOSITE_DELTA_GATE_ENABLED": False,
            "WT_EXHAUST_ENTRY_GATE_ENABLED": False, "WT_PERCENTILE_ENTRY_GATE_ENABLED": False,
            "WT_DIV_ENTRY_GATE_ENABLED": False, "R_G10_HTF_DIV_GATE_ENABLED": False}
    base.update(extra); return base


def test_wt_velocity_and_composite_exact_long_short_thresholds():
    arrays = {"wt_velocity_up_count": np.arange(5), "wt_velocity_down_count": np.arange(4, -1, -1),
              "wt_composite_delta": np.array([-101, -100, 0, 100, 101.])}
    cfg = _wt_off(WT_MTF_VEL_GATE_ENABLED=True, WT_MTF_VEL_MIN=2, WT_COMPOSITE_DELTA_GATE_ENABLED=True)
    assert wt_entry_filter_mask(arrays, _wt_state(), True, "crypto", cfg).mask.tolist() == [False, False, True, True, True]
    assert wt_entry_filter_mask(arrays, _wt_state(), False, "crypto", cfg).mask.tolist() == [True, True, True, False, False]
    arrays["wt_velocity_up_count"][2] = -1
    assert not wt_entry_filter_mask(arrays, _wt_state(), True, "crypto", cfg).available


def test_wt_chop_and_exhaust_use_exact_venue_native_tf():
    n = 3
    arrays = {"wt_cross_count_bull_5m": np.array([8, 8, 0]), "wt_cross_count_bull_15m": np.array([8, 0, 8]),
              "wt_cross_count_bull_1h": np.array([0, 8, 8]), "wt_momentum_state_5m": np.array(["EXHAUST_UP", "RUN", "RUN"]),
              "wt_momentum_state_15m": np.array(["EXHAUST_UP", "RUN", "RUN"])}
    cfg = _wt_off(WT_CHOP_GATE_ENABLED=True, WT_CHOP_MAX=8, WT_EXHAUST_ENTRY_GATE_ENABLED=True)
    assert wt_entry_filter_mask(arrays, _wt_state(n), True, "tradier", cfg).mask.tolist() == [False, False, False]
    crypto = wt_entry_filter_mask(arrays, _wt_state(n), True, "crypto", cfg)
    assert not crypto.available and "wt_cross_count_bull_3m" in crypto.reason


def test_wt_percentile_divergence_and_configured_htf_divergence():
    arrays = {"wt_percentile_D": np.array([91, 90, 9, 10.]), "wt_any_bear_div": np.array([0, 1, 0, 0]),
              "wt_any_bull_div": np.array([0, 0, 1, 0]), "wt_divergence_4h": np.array(["", "", "BEAR", "BULL"]),
              "wt_divergence_D": np.array(["", "BEAR", "", "BULL"])}
    cfg = _wt_off(WT_PERCENTILE_ENTRY_GATE_ENABLED=True, WT_DIV_ENTRY_GATE_ENABLED=True,
                  R_G10_HTF_DIV_GATE_ENABLED=True, R_G10_HTF_DIV_TFS="4h,D")
    assert wt_entry_filter_mask(arrays, _wt_state(4), True, "crypto", cfg).mask.tolist() == [False, False, False, True]
    assert wt_entry_filter_mask(arrays, _wt_state(4), False, "crypto", cfg).mask.tolist() == [True, True, False, False]
    del arrays["wt_divergence_D"]
    result = wt_entry_filter_mask(arrays, _wt_state(4), True, "crypto", cfg)
    assert not result.available and "wt_divergence_D" in result.reason


def test_wt_entry_gate_skips_exit_and_hedge_without_loading_irrelevant_arrays():
    state = _wt_state(2); state["is_exit"][0] = True; state["is_hedge"][1] = True
    result = wt_entry_filter_mask({}, state, True, "crypto", {})
    assert result.available and result.mask.all()
