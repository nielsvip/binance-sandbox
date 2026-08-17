import json

import numpy as np

from tools import vectorized_exit_reentry_approx as approx
from tools import run_exit_reentry_vec_approx_fleet as fleet


def test_mapping_covers_at_least_seventy_percent_and_all_registered_values():
    cells, excluded, stats = approx.build_cells()
    assert stats["coverage_pct"] >= 70.0
    assert stats["mapped_value_cells"] == len(cells)
    assert stats["target_value_cells"] >= len(cells)
    assert cells
    assert excluded


def test_normalized_rank_maps_numeric_values_into_compatible_proxy_units():
    row = {
        "param": "PRICE_CROSS_BACK_MAX_AGE_MIN",
        "family": "PRICE_CROSS_BACK",
        "group": "ENTRY",
        "precedence_layer": "REENTRY",
    }
    low = approx.proxy_mapping(row, 262800000.0, 0.0)
    high = approx.proxy_mapping(row, 788400000.0, 1.0)
    assert low[0] == high[0] == "MINUTES"
    assert low[1] == 15.0
    assert high[1] == 1440.0
    assert low[1] != 262800000.0


def test_boolean_master_maps_directly_and_never_claims_exact():
    row = {
        "param": "STDEV_REJECT_EXIT_ENABLED",
        "family": "STDEV_REJECT_EXIT",
        "group": "EXIT",
        "precedence_layer": "FAMILY_MASTER",
    }
    kind, value, confidence, mismatch, _formula = approx.proxy_mapping(
        row, True, 0.0
    )
    assert (kind, value) == ("BOOLEAN_ENABLE", True)
    assert confidence == "MEDIUM"
    assert mismatch == "INLINE_EXACT_DUPLICATE"


def test_shared_core_numeric_threshold_keeps_compatible_units_and_rank():
    row = {
        "param": "STRUCTURAL_RANGE_SHIFT_K_HIGH",
        "family": "STRUCTURAL_RANGE_SHIFT",
        "group": "EXIT",
        "precedence_layer": "EXIT_FILTER",
    }
    kind, value, confidence, mismatch, _formula = approx.proxy_mapping(
        row, 85.0, 1.0
    )
    assert kind == "SHARED_CORE_THRESHOLD"
    assert value == 85.0
    assert confidence == "MEDIUM"
    assert mismatch == "INLINE_EXACT_DUPLICATE"


def _data(n=80):
    ts = np.arange(n, dtype=float) * 300.0
    close = 100.0 + np.sin(np.arange(n) / 4.0) * 5.0
    return {
        "availability_ts": ts,
        "close": close,
        "wt1_15m": np.sin(np.arange(n) / 5.0),
        "wt2_15m": np.zeros(n),
        "wt1_1h": np.sin(np.arange(n) / 6.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.sin(np.arange(n) / 8.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.sin(np.arange(n) / 10.0),
        "wt2_D": np.zeros(n),
        "stoch_k_1h": 50 + np.sin(np.arange(n) / 4.0) * 45,
    }


def test_causal_simulator_uses_canonical_timestamps_when_ts_metadata_is_scalar():
    data = _data()
    data["timestamps"] = data.pop("availability_ts")
    data["ts"] = np.array("metadata-only", dtype=object)
    cells, _excluded, _stats = approx.build_cells()
    row = approx.simulate_cell(cells[0], data, "LONG")
    assert row["tier"] == "VEC_APPROX"


def test_causal_exit_and_reentry_screens_emit_only_diagnostic_receipts():
    cells, _excluded, _stats = approx.build_cells()
    exit_cell = next(
        cell
        for cell in cells
        if cell.action_group == "EXIT"
        and cell.proxy_kind == "CONFIDENCE_THRESHOLD"
    )
    reentry_cell = next(
        cell
        for cell in cells
        if cell.action_group == "REENTER_RECLAIM"
        and cell.proxy_kind in {"MINUTES", "CONFIDENCE_THRESHOLD"}
    )
    for cell in (exit_cell, reentry_cell):
        row = approx.simulate_cell(cell, _data(), "LONG")
        assert row["tier"] == "VEC_APPROX"
        assert row["exact_completion_credit"] is False
        assert row["promotion_allowed"] is False
        assert row["db_engine_write_allowed"] is False
        assert row["required_next_stage"] == "EXACT_V8"
        assert len(row["action_fingerprint"]) == 64
        assert row["benchmark_deployed_usd"] == 2000.0


def test_disabled_exit_master_produces_hold_not_a_frozen_exit_seed():
    cells, _excluded, _stats = approx.build_cells()
    disabled = next(
        cell
        for cell in cells
        if cell.action_group == "EXIT"
        and cell.proxy_kind == "BOOLEAN_ENABLE"
        and cell.proxy_value is False
    )
    row = approx.simulate_cell(disabled, _data(), "LONG")
    assert row["event_count"] == 0


def test_proxy_size_is_normalized_back_to_two_thousand_deployment():
    cells, _excluded, _stats = approx.build_cells()
    size_cell = next(
        cell
        for cell in cells
        if cell.proxy_kind == "SIZE_MULTIPLIER"
        and float(cell.proxy_value) != 1.0
    )
    row = approx.simulate_cell(size_cell, _data(), "LONG")
    assert row["average_deployed_usd_diagnostic"] == (
        2000.0 * float(size_cell.proxy_value)
    )
    assert row["capital_normalization_factor_diagnostic"] == round(
        1.0 / float(size_cell.proxy_value), 10
    )


def test_ranked_queue_preserves_proxy_provenance_and_no_promotion():
    rows = [
        {
            "cell_id": "a",
            "param": "A",
            "value_json": 1,
            "source_rank": 0.0,
            "proxy_value": 0.55,
            "confidence": "LOW",
            "mismatch_class": "GENERIC",
            "delta_gain_mo_vs_bh_diagnostic": 1.0,
            "event_count": 2,
        },
        {
            "cell_id": "b",
            "param": "B",
            "value_json": 2,
            "source_rank": 1.0,
            "proxy_value": 0.95,
            "confidence": "MEDIUM",
            "mismatch_class": "INLINE",
            "delta_gain_mo_vs_bh_diagnostic": 2.0,
            "event_count": 1,
        },
    ]
    queue = approx.ranked_exact_queue(rows)
    assert [row["cell_id"] for row in queue] == ["b", "a"]
    assert all(row["required_next_stage"] == "EXACT_V8" for row in queue)
    assert all(row["promotion_allowed"] is False for row in queue)


def test_map_artifact_has_no_engine_or_completion_leak(tmp_path):
    payload = approx.write_map(tmp_path / "map.json")
    serialized = json.dumps(payload)
    assert payload["tier"] == "VEC_APPROX"
    assert payload["db_engine_write_allowed"] is False
    assert payload["exact_completion_credit"] is False
    assert '"tier": "ENGINE"' not in serialized


def test_fleet_only_schedules_existing_frozen_npz(tmp_path, monkeypatch):
    root = tmp_path
    (root / "npz").mkdir()
    (root / "npz" / "VT.npz").write_bytes(b"fixture")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "npz_dir": "npz",
                "keys": ["VT_LONG", "MISSING_SHORT"],
            }
        )
    )
    monkeypatch.setattr(fleet, "ROOT", root)
    jobs = fleet.tasks(manifest, root / "out")
    assert [key for key, _command in jobs] == ["VT_LONG"]
    assert "--key" in jobs[0][1]
    assert "VEC_APPROX" not in " ".join(jobs[0][1])  # tier is internal/fixed


def test_frozen_npz_loader_accepts_canonical_object_metadata(tmp_path):
    path = tmp_path / "VT.npz"
    np.savez(
        path,
        close=np.array([1.0, 2.0]),
        provenance=np.array([{"source": "trusted-frozen-matrix"}], dtype=object),
    )
    loaded = approx.load_frozen_npz(path)
    assert loaded["close"].tolist() == [1.0, 2.0]
    assert loaded["provenance"][0]["source"] == "trusted-frozen-matrix"
