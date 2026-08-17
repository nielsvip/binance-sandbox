import numpy as np

from tools.classic_formation_exact_preflight import classic_formation_exact_preflight


def _recipe():
    return {
        "ENTRY": {
            "family": "ENTRY_CLASSIC_FORMATION_WEDGE",
            "params": {"timeframe": "15m", "min_score": 0.65, "position_size_mult": 1.0},
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_WEDGE",
            "params": {"timeframe": "15m", "min_score": 0.65, "exit_min_gain_pct": 0.0},
        },
    }


def _overrides():
    return {
        "FORMATION_WEDGE_ENTRY_ENABLED": True,
        "FORMATION_WEDGE_EXIT_ENABLED": True,
        "FORMATION_TFS": "15m",
        "FORMATION_MIN_SCORE": 0.65,
    }


def test_preflight_proves_shared_selector_entry_and_exit_actions(tmp_path):
    path = tmp_path / "NXE.npz"
    np.savez(
        path,
        timestamp=np.array([1, 2, 3]),
        formation_wedge_bear_score_15m=np.array([0.0, 0.9, 0.0]),
        formation_wedge_bull_score_15m=np.array([0.8, 0.0, 0.0]),
        formation_primary_15m=np.array(["none", "rising_wedge", "falling_wedge"]),
    )
    result = classic_formation_exact_preflight(path, "NXE_SHORT", _recipe(), _overrides())
    assert result["pass"] is True
    assert result["routes"]["ENTRY"]["evaluations"] == 3
    assert result["routes"]["ENTRY"]["selector_actions"] == 1
    assert result["routes"]["EXIT"]["selector_actions"] == 1


def test_preflight_blocks_missing_causal_input_before_v8(tmp_path):
    path = tmp_path / "NXE.npz"
    np.savez(path, formation_wedge_bear_score_15m=np.array([0.9]))
    result = classic_formation_exact_preflight(path, "NXE_SHORT", _recipe(), _overrides())
    assert result["pass"] is False
    assert "FORMATION_PREFLIGHT_EXIT_INPUT_MISSING" in result["blockers"]


def test_preflight_blocks_zero_selector_actions_before_v8(tmp_path):
    path = tmp_path / "NXE.npz"
    np.savez(
        path,
        formation_wedge_bear_score_15m=np.array([0.2]),
        formation_wedge_bull_score_15m=np.array([0.2]),
    )
    result = classic_formation_exact_preflight(path, "NXE_SHORT", _recipe(), _overrides())
    assert result["pass"] is False
    assert "FORMATION_PREFLIGHT_ENTRY_ZERO_SELECTOR_ACTIONS" in result["blockers"]
    assert "FORMATION_PREFLIGHT_EXIT_ZERO_SELECTOR_ACTIONS" in result["blockers"]


def test_preflight_does_not_credit_a_different_enabled_family(tmp_path):
    path = tmp_path / "NXE_other_family.npz"
    np.savez(
        path,
        timestamps=np.array([1, 2, 3]),
        formation_wedge_bear_score_15m=np.array([0.0, 0.0, 0.0]),
        formation_wedge_bull_score_15m=np.array([0.0, 0.0, 0.0]),
        formation_cup_handle_bear_score_15m=np.array([0.9, 0.0, 0.0]),
    )
    overrides = _overrides()
    overrides["FORMATION_CUP_HANDLE_ENTRY_ENABLED"] = True
    result = classic_formation_exact_preflight(path, "NXE_SHORT", _recipe(), overrides)
    assert result["routes"]["ENTRY"]["selector_actions"] == 0
    assert result["routes"]["ENTRY"]["other_family_selector_actions"] == 1
    assert "FORMATION_PREFLIGHT_ENTRY_ZERO_SELECTOR_ACTIONS" in result["blockers"]


def test_v8_candidate_admission_explicitly_covers_formation_routes():
    source = open("backtest_v8_engine.py").read()
    assert "def _classic_formation_flat_route_enabled_t" in source
    assert "_formation_long_t" in source
    assert "_formation_short_t" in source


def test_preflight_derives_legacy_formation_fields_despite_compatibility_3m(tmp_path):
    # The exact regression archive has both 5m and compatibility 3m fields.
    # The preflight must use the same frozen-OHLC derivation as the opt-in V8
    # harness instead of treating absent persisted formation_* keys as zero.
    path = tmp_path / "NXE_legacy.npz"
    n = 60
    close = np.linspace(10.0, 20.0, n)
    np.savez(
        path,
        close_15m=close, open_15m=close - 0.1, high_15m=close + 0.2,
        low_15m=close - 0.2, volume_15m=np.full(n, 100.0),
        close_5m=close, close_3m=close,
    )
    result = classic_formation_exact_preflight(path, "NXE_SHORT", _recipe(), _overrides())
    assert result["formation_fields"] == "PERSISTED_OR_DERIVED_FROM_FROZEN_OHLC"
    assert result["routes"]["ENTRY"]["evaluations"] == n
    assert "FORMATION_PREFLIGHT_ENTRY_INPUT_MISSING" not in result["blockers"]


def test_preflight_collapses_broadcast_15m_using_parent_timestamp(tmp_path):
    path = tmp_path / "NXE_native_5m.npz"
    parents = np.r_[
        np.linspace(100, 112, 13),
        np.linspace(111, 109, 26),
        [113.0],
    ]
    repeats = 3
    close = np.repeat(parents, repeats)
    np.savez(
        path,
        timestamps=np.arange(len(close), dtype=np.int64) * 300,
        timestamp_15m=np.repeat(
            np.arange(len(parents), dtype=np.int64) * 900 + 900, repeats
        ),
        open_15m=close,
        high_15m=close + 0.3,
        low_15m=close - 0.3,
        close_15m=close,
        volume_15m=np.full(len(close), 100.0),
    )
    overrides = _overrides()
    overrides["FORMATION_FLAG_PENNANT_ENTRY_ENABLED"] = True
    recipe = _recipe()
    recipe["ENTRY"] = {
        "family": "ENTRY_CLASSIC_FORMATION_FLAG_PENNANT",
        "params": {"timeframe": "15m", "min_score": 0.65, "position_size_mult": 1.0},
    }
    result = classic_formation_exact_preflight(path, "NXE_LONG", recipe, overrides)
    assert result["routes"]["ENTRY"]["selector_actions"] == 1
    assert result["routes"]["ENTRY"]["first_actions"][0]["index"] % repeats == 0


def test_preflight_uses_bound_validation_window_not_all_npz_bars(tmp_path):
    path = tmp_path / "NXE_window.npz"
    np.savez(
        path,
        timestamps=np.array([1735689600, 1735776000, 1735862400]),
        formation_wedge_bear_score_15m=np.array([0.9, 0.0, 0.0]),
        formation_wedge_bull_score_15m=np.array([0.8, 0.0, 0.0]),
    )
    recipe = _recipe()
    recipe["VALIDATION"] = ["2025-01-02", "2025-01-03"]
    result = classic_formation_exact_preflight(path, "NXE_SHORT", recipe, _overrides())
    assert result["evaluation_bars"] == 2
    assert result["routes"]["ENTRY"]["evaluations"] == 2
    assert "FORMATION_PREFLIGHT_ENTRY_ZERO_SELECTOR_ACTIONS" in result["blockers"]


def test_preflight_ignores_unrelated_object_arrays(tmp_path):
    path = tmp_path / "NXE_object_compat.npz"
    np.savez(
        path,
        timestamps=np.array([1, 2, 3]),
        formation_wedge_bear_score_15m=np.array([0.0, 0.9, 0.0]),
        formation_wedge_bull_score_15m=np.array([0.8, 0.0, 0.0]),
        formation_primary_15m=np.array(["none", "rising_wedge", "falling_wedge"]),
        unrelated_object_payload=np.array([{"legacy": True}], dtype=object),
    )
    result = classic_formation_exact_preflight(
        path, "NXE_SHORT", _recipe(), _overrides()
    )
    assert result["pass"] is True
