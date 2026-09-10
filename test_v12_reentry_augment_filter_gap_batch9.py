import csv
import json
from pathlib import Path

import numpy as np

from vec_paths.v12_reentry_augment_filter_gap_batch9 import FIELD_CONTRACTS, LIFECYCLE_FILTER_APIS, crypto_entry_score_decision, htf_open_route_mask, oi_confirmation_mask

ROOT = Path(__file__).resolve().parent


def _default(raw, typ):
    if typ == "bool": return raw.lower() == "true"
    if typ == "int": return int(raw)
    if typ == "float": return float(raw)
    return raw


def test_24_field_contract_matches_authority_consumers_and_modes():
    rows = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_REENTRY_AUGMENT_FILTER_GAP_BATCH9_CONTRACT.csv").open())}
    auth = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv").open())}
    assert len(rows) == len(FIELD_CONTRACTS) == 24
    for name, contract in FIELD_CONTRACTS.items():
        assert rows[name]["authoritative_type"] == auth[name]["authoritative_type"] == contract.value_type
        assert json.loads(rows[name]["authoritative_default_json"]) == _default(auth[name]["authoritative_default"], contract.value_type) == contract.default
        assert tuple(json.loads(rows[name]["authoritative_grid_json"])) == tuple(json.loads(auth[name]["authoritative_grid"])) == contract.grid
        assert set(LIFECYCLE_FILTER_APIS[name]) == set(contract.lifecycle_filter_consumers)
        assert rows[name]["supported_modes"].split("|") == list(contract.supported_modes)


def _state(bottom, quality):
    n = len(bottom)
    return {"candidate_mask": np.ones(n, bool), "is_exit": np.zeros(n, bool),
            "mts_bottom_score": np.asarray(bottom, float), "mts_entry_quality": np.asarray(quality, float)}


def _off(**extra):
    cfg = {"MTS_GATE_ENABLED": False, "K_ZONE_ENTRY_ENABLED": False, "MI_ENTRY_ENABLED": False,
           "MOMENTUM_FADE_ENABLED": False, "SENTIMENT_TOP_N_GATE_ENABLED": False}
    cfg.update(extra); return cfg


def test_mts_gate_and_strict_score_thresholds_use_authoritative_analyzer_state():
    state = _state([14, 15, 26, 41], [7, 8, 26, 41])
    result = crypto_entry_score_decision({}, state, True, "crypto", _off(MTS_GATE_ENABLED=True))
    assert result.available and result.mask.tolist() == [False, True, True, True]
    assert result.score_delta.tolist() == [0, 0, 6, 13]
    short = crypto_entry_score_decision({}, _state([9, 10, 26], [4, 5, 41]), False, "crypto", _off(MTS_GATE_ENABLED=True))
    assert short.mask.tolist() == [False, True, True]
    assert short.score_delta.tolist() == [0, 0, 9]


def test_mi_entry_adds_each_exact_structure_exhaustion_and_divergence_event():
    arrays = {
        "wt_trough_structure_1h": np.array(["HL", ""]), "wt_trough_structure_4h": np.array(["HL", ""]),
        "wt_peak_structure_1h": np.array(["", "LH"]), "wt_peak_structure_4h": np.array(["", "LH"]),
        "wt_momentum_state_1h": np.array(["EXHAUST_DOWN", "EXHAUST_UP"]), "wt_momentum_state_4h": np.array(["EXHAUST_DOWN", "EXHAUST_UP"]),
        "wt_divergence_1h": np.array(["BULL", "BEAR"]),
    }
    cfg = _off(MI_ENTRY_ENABLED=True)
    assert crypto_entry_score_decision(arrays, _state([0, 0], [0, 0]), True, "crypto", cfg).score_delta.tolist() == [44, 0]
    assert crypto_entry_score_decision(arrays, _state([0, 0], [0, 0]), False, "crypto", cfg).score_delta.tolist() == [0, 44]


def test_k_zone_uses_completed_native_3m_turn_and_candle_confirmation():
    arrays = {"k_3m": np.array([30, 30, 70, 70.]), "k_3m_prev": np.array([20, 40, 80, 60.]),
              "ha_3m": np.array(["green", "green", "red", "red"]), "ha_3m_prev": np.array(["red", "green", "green", "red"]),
              "ha_15m": np.array(["red", "green", "green", "red"])}
    cfg = _off(K_ZONE_ENTRY_ENABLED=True)
    assert crypto_entry_score_decision(arrays, _state([0]*4, [0]*4), True, "crypto", cfg).score_delta.tolist() == [25, 0, 0, 0]
    assert crypto_entry_score_decision(arrays, _state([0]*4, [0]*4), False, "crypto", cfg).score_delta.tolist() == [0, 0, 25, 0]


def test_momentum_fade_body_volume_and_optional_k_zone_are_exact():
    arrays = {"high_3m": np.array([12, 12, 12.]), "low_3m": np.array([10, 10, 10.]), "atr_3m": np.ones(3),
              "relative_volume_3m": np.array([2, 1, 2.]), "relative_volume_15m": np.array([1, 2, 1.]), "k_3m": np.array([39, 50, 61.])}
    cfg = _off(MOMENTUM_FADE_ENABLED=True)
    assert crypto_entry_score_decision(arrays, _state([0]*3, [0]*3), True, "crypto", cfg).score_delta.tolist() == [35, 0, 0]
    assert crypto_entry_score_decision(arrays, _state([0]*3, [0]*3), False, "crypto", cfg).score_delta.tolist() == [0, 0, 35]
    no_zone = crypto_entry_score_decision(arrays, _state([0]*3, [0]*3), True, "crypto", {**cfg, "MOMENTUM_FADE_K_ZONE": False})
    assert no_zone.score_delta.tolist() == [35, 35, 35]


def test_sentiment_rank_gate_is_side_exact_and_hedges_bypass():
    arrays = {"0sentiment_rank": np.array([1, 20, 21, 350]), "0ranking_points": np.array([0, 5, 6, 100.])}
    state = _state([0]*4, [0]*4); state["is_hedge"] = np.array([0, 0, 0, 1], bool)
    cfg = _off(SENTIMENT_TOP_N_GATE_ENABLED=True)
    assert crypto_entry_score_decision(arrays, state, True, "crypto", cfg).mask.tolist() == [True, True, False, True]
    assert crypto_entry_score_decision(arrays, state, False, "crypto", cfg).mask.tolist() == [True, True, False, True]


def test_oi_four_quadrant_gate_and_strict_minimums_cover_all_action_consumers():
    arrays = {"oi_change_1h_pct": np.array([1, -1, 1, -1, .49]), "close": np.array([101, 101, 99, 99, 101.]), "close_1h_prev": np.full(5, 100.)}
    state = {"candidate_mask": np.ones(5, bool)}; cfg = {"OI_CONFIRM_ENABLED": True}
    assert oi_confirmation_mask(arrays, state, True, cfg).mask.tolist() == [True, False, False, True, True]
    assert oi_confirmation_mask(arrays, state, False, cfg).mask.tolist() == [False, True, True, False, True]
    for name in ("OI_CONFIRM_ENABLED", "OI_CONFIRM_MIN_CHANGE_PCT", "OI_CONFIRM_MIN_PRICE_PCT"):
        assert set(LIFECYCLE_FILTER_APIS[name]) == {"ENTRY", "REENTRY", "AUGMENT"}


def test_htf_apply_to_open_calls_exact_batch7_predicate_and_can_disable_route():
    n = 2
    arrays = {"wt1_D": np.full(n, -2.), "wt2_D": np.full(n, -1.), "wt1_4h": np.full(n, -2.), "wt2_4h": np.full(n, -1.),
              "wt1_1h": np.full(n, -2.), "wt2_1h": np.full(n, -1.), "close": np.full(n, 90.), "sma_200_D": np.full(n, 100.)}
    state = {"candidate_mask": np.ones(n, bool), "is_open_target": np.ones(n, bool), "is_augment_target": np.zeros(n, bool),
             "is_rz_entry": np.zeros(n, bool), "is_v3_bypass": np.zeros(n, bool), "is_ratio_recovery_bypass": np.zeros(n, bool)}
    base = {"HTF_DIRECTION_GATE_ENABLED": True, "HTF_GATE_MIN_CONFIRMATIONS": 2}
    assert not htf_open_route_mask(arrays, state, True, {**base, "HTF_GATE_APPLY_TO_OPEN": True}).mask.any()
    assert htf_open_route_mask(arrays, state, True, {**base, "HTF_GATE_APPLY_TO_OPEN": False}).mask.all()


def test_generic_crypto_score_fields_are_honest_mode_na_and_enabled_missing_fails_closed():
    result = crypto_entry_score_decision({}, _state([0], [0]), True, "tradier", _off())
    assert not result.available and "*_TRADIER" in result.reason
    result = crypto_entry_score_decision({}, _state([0], [0]), True, "crypto", _off(MI_ENTRY_ENABLED=True))
    assert not result.available and not result.mask.any() and "wt_trough_structure_1h" in result.reason


def test_unreachable_or_no_effect_rows_are_not_claimed():
    auth = {r["field"]: r for r in csv.DictReader((ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.csv").open())}
    assert "OI_HEDGE_GATE_ENABLED" not in FIELD_CONTRACTS  # outer live source excludes is_hedge before reading it
    assert json.loads(auth["HOUR_OF_DAY_BLOCKED_UTC"]["authoritative_grid"]) == []
    assert "HOUR_OF_DAY_GATE_ENABLED" not in FIELD_CONTRACTS

