import json
import sqlite3

import pytest

from tools import param_matrix_daemon as daemon


def _row(
    *,
    group="EXIT",
    dependencies=(),
    order=10,
    family="FAMILY",
):
    return {
        "group": group,
        "activation_dependencies": list(dependencies),
        "precedence_order": order,
        "family": family,
    }


def test_dependency_pack_includes_parent_root_and_descendants_not_siblings():
    rows = {
        "SHARED_PARENT": _row(group="OTHER", order=5),
        "ROOT_EXIT": _row(
            dependencies=("SHARED_PARENT",), family="ROOT"
        ),
        "ROOT_THRESHOLD": _row(
            dependencies=("ROOT_EXIT",), order=50, family="ROOT"
        ),
        "ROOT_TF": _row(
            dependencies=("ROOT_THRESHOLD",), order=60, family="ROOT"
        ),
        "UNRELATED_SIBLING": _row(
            dependencies=("SHARED_PARENT",), family="SIBLING"
        ),
    }

    assert daemon.dependency_pack_params(["ROOT_EXIT"], rows) == {
        "SHARED_PARENT",
        "ROOT_EXIT",
        "ROOT_THRESHOLD",
        "ROOT_TF",
    }


def test_priority_scheduler_removes_helpers_and_orders_dependency_pack():
    rows = {
        "SHARED_PARENT": _row(group="OTHER", order=5),
        "ROOT_EXIT": _row(
            dependencies=("SHARED_PARENT",), family="ROOT"
        ),
        "ROOT_THRESHOLD": _row(
            dependencies=("ROOT_EXIT",), order=50, family="ROOT"
        ),
        "UNRELATED_EXIT": _row(family="OTHER"),
    }
    cells = [
        ("STOP_PACK", "ride", {"X": True}),
        ("UNRELATED_EXIT", "true", {"UNRELATED_EXIT": True}),
        ("ROOT_THRESHOLD", "2", {"ROOT_THRESHOLD": 2}),
        ("ROOT_EXIT", "true", {"ROOT_EXIT": True}),
        ("SHARED_PARENT", "true", {"SHARED_PARENT": True}),
        ("ROOT_EXIT", "false", {"ROOT_EXIT": False}),
        ("TF_EXCLUDE", "5m", {"TF_EXCLUDE_5M": True}),
    ]

    selected = daemon.prioritize_dependency_pack_cells(
        cells, ["ROOT_EXIT"], rows
    )

    assert [cell[0] for cell in selected] == [
        "SHARED_PARENT",
        "ROOT_EXIT",
        "ROOT_EXIT",
        "ROOT_THRESHOLD",
    ]
    assert all(daemon.is_manifest_cell(cell) for cell in selected)


def test_priority_scheduler_smokes_every_path_before_grid_tails():
    rows = {
        "ROOT_EXIT": _row(family="ROOT"),
        "A_THRESHOLD": _row(
            dependencies=("ROOT_EXIT",), order=50, family="ROOT"
        ),
        "B_THRESHOLD": _row(
            dependencies=("ROOT_EXIT",), order=60, family="ROOT"
        ),
    }
    cells = [
        ("ROOT_EXIT", "true", {"ROOT_EXIT": True}),
        ("ROOT_EXIT", "false", {"ROOT_EXIT": False}),
        ("A_THRESHOLD", "1", {"A_THRESHOLD": 1}),
        ("A_THRESHOLD", "9", {"A_THRESHOLD": 9}),
        ("A_THRESHOLD", "3", {"A_THRESHOLD": 3}),
        ("A_THRESHOLD", "5", {"A_THRESHOLD": 5}),
        ("A_THRESHOLD", "7", {"A_THRESHOLD": 7}),
        ("B_THRESHOLD", "10", {"B_THRESHOLD": 10}),
        ("B_THRESHOLD", "90", {"B_THRESHOLD": 90}),
        ("B_THRESHOLD", "30", {"B_THRESHOLD": 30}),
        ("B_THRESHOLD", "50", {"B_THRESHOLD": 50}),
        ("B_THRESHOLD", "70", {"B_THRESHOLD": 70}),
    ]

    selected = daemon.prioritize_dependency_pack_cells(
        cells, ["ROOT_EXIT"], rows
    )

    assert [(cell[0], cell[1]) for cell in selected] == [
        ("ROOT_EXIT", "true"),
        ("ROOT_EXIT", "false"),
        ("A_THRESHOLD", "1"),
        ("A_THRESHOLD", "9"),
        ("B_THRESHOLD", "10"),
        ("B_THRESHOLD", "90"),
        ("A_THRESHOLD", "3"),
        ("B_THRESHOLD", "30"),
        ("A_THRESHOLD", "5"),
        ("B_THRESHOLD", "50"),
        ("A_THRESHOLD", "7"),
        ("B_THRESHOLD", "70"),
    ]


def test_priority_scheduler_is_deterministic_and_does_not_mutate_inputs():
    rows = {
        "ROOT_EXIT": _row(family="ROOT"),
        "ROOT_THRESHOLD": _row(
            dependencies=("ROOT_EXIT",), order=50, family="ROOT"
        ),
    }
    cells = [
        ("ROOT_THRESHOLD", "1", {"ROOT_THRESHOLD": 1}),
        ("ROOT_THRESHOLD", "9", {"ROOT_THRESHOLD": 9}),
        ("ROOT_THRESHOLD", "5", {"ROOT_THRESHOLD": 5}),
        ("ROOT_EXIT", "true", {"ROOT_EXIT": True}),
        ("ROOT_EXIT", "false", {"ROOT_EXIT": False}),
    ]
    original_cells = [
        (name, value, dict(overrides)) for name, value, overrides in cells
    ]
    original_rows = json.loads(json.dumps(rows))

    first = daemon.prioritize_dependency_pack_cells(
        cells, ["ROOT_EXIT"], rows
    )
    second = daemon.prioritize_dependency_pack_cells(
        cells, ["ROOT_EXIT"], rows
    )

    assert first == second
    assert cells == original_cells
    assert rows == original_rows


def test_compatibility_queue_no_longer_leads_with_stop_pack_or_drops():
    cells = [
        ("STOP_PACK", "ride", {}),
        ("DROPPED_EXIT", "true", {}),
        ("USEFUL_EXIT", "true", {}),
        ("TF_EXCLUDE", "5m", {}),
    ]

    ordered = daemon.prioritize_compatibility_cells(
        cells, {"DROPPED_EXIT"}
    )

    assert [cell[0] for cell in ordered] == [
        "USEFUL_EXIT",
        "DROPPED_EXIT",
        "STOP_PACK",
        "TF_EXCLUDE",
    ]


def test_historical_useless_labels_do_not_skip_without_cohort_retirement_receipt(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "useless_knobs.json").write_text(
        json.dumps(
            {
                "campaign": "stocks_baseline_v2_s4h",
                "reconnect": ["STDEV_REJECT_EXIT_ZONE"],
                "degenerate": ["LR_BAND_HARVEST_HI"],
            }
        )
    )
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", "stocks_repaired_20260730_c5"
    )

    assert daemon.useless_knobs() == set()

    monkeypatch.setattr(daemon.psc, "CAMPAIGN", "stocks_baseline_v2_s4h")
    assert daemon.useless_knobs() == set()


def test_cohort_receipt_requires_both_directions_and_exact_contract(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    receipt = {
        "param": "TEST_EXIT",
        "campaign": "stocks_repaired_20260730_c5",
        "eligible_symbol_sides": 10,
        "tested_symbol_sides": ["AAA_LONG", "BBB_SHORT"],
        "cohort_fraction": 0.20,
        "exact_current_contract": True,
        "all_action_fingerprints_unique": True,
        "all_capital_receipts_normalized_2000": True,
        "all_lifecycle_roles_tested": True,
    }
    (data / "useless_knobs.json").write_text(
        json.dumps({"approved_retirements": [receipt]})
    )
    monkeypatch.setattr(daemon, "SBX", tmp_path)
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", "stocks_repaired_20260730_c5"
    )
    assert daemon.useless_knobs() == {"TEST_EXIT"}

    receipt["tested_symbol_sides"] = ["AAA_LONG", "BBB_LONG"]
    (data / "useless_knobs.json").write_text(
        json.dumps({"approved_retirements": [receipt]})
    )
    assert daemon.useless_knobs() == set()


def test_worker_policy_fails_closed_on_contract_or_promotion_mismatch(
    tmp_path,
):
    path = tmp_path / "workers.json"
    payload = {
        "campaign": "stocks_repaired_20260730_c5",
        "matrix_contract_version": "tradier-matrix-exec-c5-20260730",
        "no_live_promotion": True,
        "workers": [
            {
                "tag": "wm_exit",
                "symbol": "MU",
                "side": "LONG",
                "expected_contract_fingerprint": "fp-current",
                "priority_roots": ["ROOT_EXIT"],
            }
        ],
    }
    path.write_text(json.dumps(payload))

    policy = daemon.load_worker_priority_policy(
        path,
        "wm_exit",
        "MU",
        "LONG",
        campaign="stocks_repaired_20260730_c5",
        contract_version="tradier-matrix-exec-c5-20260730",
        contract_fingerprint="fp-current",
    )
    assert policy["priority_roots"] == ("ROOT_EXIT",)
    assert policy["selection_mode"] == "DEPENDENCY_PACKS_ONLY"

    payload["no_live_promotion"] = False
    path.write_text(json.dumps(payload))
    with pytest.raises(
        SystemExit, match="MATRIX_WORKER_MANIFEST_CONTRACT_MISMATCH"
    ):
        daemon.load_worker_priority_policy(
            path,
            "wm_exit",
            "MU",
            "LONG",
            campaign="stocks_repaired_20260730_c5",
            contract_version="tradier-matrix-exec-c5-20260730",
            contract_fingerprint="fp-current",
        )


def test_compatibility_queue_exercises_exit_lifecycle_before_entry_and_sizing():
    cells = [
        ("ENTRY_A", "1", {"ENTRY_A": 1}),
        ("ENTRY_A", "9", {"ENTRY_A": 9}),
        ("ENTRY_A", "5", {"ENTRY_A": 5}),
        ("SIZE_A", "1", {"SIZE_A": 1}),
        ("EXIT_A", "false", {"EXIT_A": False}),
        ("EXIT_A", "true", {"EXIT_A": True}),
        ("EXIT_B", "1", {"EXIT_B": 1}),
        ("REENTRY_A", "true", {"REENTRY_A": True}),
    ]
    rows = {
        "ENTRY_A": {
            "group": "ENTRY",
            "precedence_order": 20,
            "family": "ENTRY_A",
        },
        "SIZE_A": {
            "group": "SIZING",
            "precedence_order": 70,
            "family": "SIZE_A",
        },
        "EXIT_A": {
            "group": "EXIT",
            "precedence_order": 40,
            "family": "EXIT_A",
        },
        "EXIT_B": {
            "group": "EXIT",
            "precedence_order": 40,
            "family": "EXIT_B",
        },
        "REENTRY_A": {
            "group": "ENTRY",
            "precedence_order": 60,
            "family": "REENTRY_A",
        },
    }

    ordered = daemon.prioritize_compatibility_cells(cells, set(), rows)
    names = [row[0] for row in ordered]

    assert names[:3] == ["EXIT_A", "EXIT_A", "EXIT_B"]
    assert names.index("REENTRY_A") < names.index("ENTRY_A")
    assert names.index("ENTRY_A") < names.index("SIZE_A")


def test_historical_pilot_exit_families_run_before_generic_and_ablation_exits():
    cells = [
        ("ABLATION_DISABLE_CHECK_NOLOSS", "false", {}),
        ("GENERIC_EXIT_ENABLED", "true", {}),
        ("SRS_EXIT_ENABLED", "true", {}),
        ("SRS_EXIT_THRESHOLD", "10", {}),
        ("ENTRY_A", "true", {}),
    ]
    rows = {
        "ABLATION_DISABLE_CHECK_NOLOSS": {
            "group": "EXIT",
            "family": "ABLATION_DISABLE_CHECK",
            "precedence_order": 10,
        },
        "GENERIC_EXIT_ENABLED": {
            "group": "EXIT",
            "family": "GENERIC_EXIT",
            "precedence_order": 10,
        },
        "SRS_EXIT_ENABLED": {
            "group": "EXIT",
            "family": "STRUCTURAL_RANGE_SHIFT",
            "precedence_order": 10,
        },
        "SRS_EXIT_THRESHOLD": {
            "group": "EXIT",
            "family": "STRUCTURAL_RANGE_SHIFT",
            "precedence_order": 50,
        },
        "ENTRY_A": {
            "group": "ENTRY",
            "family": "ENTRY_A",
            "precedence_order": 10,
        },
    }

    names = [
        row[0]
        for row in daemon.prioritize_compatibility_cells(cells, set(), rows)
    ]

    assert names[:2] == ["SRS_EXIT_ENABLED", "SRS_EXIT_THRESHOLD"]
    assert names.index("GENERIC_EXIT_ENABLED") < names.index("ENTRY_A")
    assert names.index("ENTRY_A") < names.index(
        "ABLATION_DISABLE_CHECK_NOLOSS"
    )


def test_one_year_dedupe_preserves_full_window_cells():
    assert daemon.evidence_campaigns_for_dedupe(
        "stocks_repaired_20260730_c5_1yr"
    ) == (
        "stocks_repaired_20260730_c5",
        "stocks_repaired_20260730_c5_1yr",
    )
    assert daemon.evidence_campaigns_for_dedupe(
        "stocks_repaired_20260730_c5"
    ) == ("stocks_repaired_20260730_c5",)


def test_grouped_recipe_unwraps_internal_marker_before_engine(monkeypatch):
    monkeypatch.setattr(
        daemon, "matrix_ladder_floor_overrides", lambda: {"EXIT_A": False}
    )
    effective = daemon.grouped_recipe_effective_overrides(
        {
            daemon.GROUPED_RECIPE_MARKER: {
                "recipe_id": "gcr-123",
                "overrides": {"ENTRY_A": True, "EXIT_A": True},
            }
        }
    )
    assert effective == {"ENTRY_A": True, "EXIT_A": True}
    assert daemon.GROUPED_RECIPE_MARKER not in effective


def test_grouped_combo_queue_uses_only_current_exact_capital_receipts(
    monkeypatch,
):
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE param_cells (campaign TEXT,param TEXT,value_json TEXT,"
        "delta_gain_mo_vs_bh REAL,overrides_json TEXT,contract_fingerprint TEXT,"
        "trades_fingerprint TEXT,ts TEXT,mode TEXT,symbol TEXT,side TEXT,"
        "tier TEXT,validation_status TEXT,capital_accounting_version TEXT,"
        "real_closes INTEGER,source_file TEXT,trades INTEGER)"
    )
    con.execute(
        "CREATE TABLE key_baseline (campaign TEXT,mode TEXT,symbol TEXT,"
        "side TEXT,years REAL)"
    )
    con.execute(
        "INSERT INTO key_baseline VALUES (?,?,?,?,?)",
        ("stocks_repaired_20260730_c5", "tradier", "VT", "LONG", 2.1),
    )
    con.execute(
        "INSERT INTO key_baseline VALUES (?,?,?,?,?)",
        ("stocks_repaired_20260725_c2", "tradier", "VT", "LONG", 2.4),
    )
    common = (
        "stocks_repaired_20260730_c5",
        "fp",
        "TRADES",
        "2026-07-31T00:00:00Z",
        "tradier",
        "VT",
        "LONG",
        "ENGINE",
        "PASS",
        "cap-v1",
    )
    for param, value, delta in (
        ("ENTRY_A", True, 2.0),
        ("EXIT_A", True, 1.0),
    ):
        con.execute(
            "INSERT INTO param_cells "
            "(campaign,param,value_json,delta_gain_mo_vs_bh,overrides_json,"
                "contract_fingerprint,trades_fingerprint,ts,mode,symbol,side,tier,"
                "validation_status,capital_accounting_version,real_closes,"
                "source_file,trades) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                common[0],
                param,
                json.dumps(value),
                delta,
                json.dumps({param: value}),
                *common[1:],
                2,
                f"param_matrix_daemon/{common[0]}/tag",
                2,
            ),
        )
    # A stale-fingerprint arm must not enter any recipe.
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            common[0],
            "REDUCE_STALE",
            "true",
            99.0,
            '{"REDUCE_STALE":true}',
            "old-fp",
            *common[2:],
            2,
            f"param_matrix_daemon/{common[0]}/tag",
            2,
        ),
    )
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "stocks_repaired_20260725_c2",
            "REDUCE_HIST",
            "true",
            -3.0,
            '{"REDUCE_HIST":true}',
            "tradier-matrix-exec-c4-20260729:old",
            "HIST_TRADES",
            "2026-07-29T00:00:00Z",
            "tradier",
            "VT",
            "LONG",
            "ENGINE",
            "PASS",
            "cap-v1",
            3,
            "param_matrix_daemon/stocks_repaired_20260725_c2/hist",
            3,
        ),
    )
    monkeypatch.setattr(daemon.psc, "MODE", "tradier")
    monkeypatch.setattr(
        daemon.psc, "CAMPAIGN", "stocks_repaired_20260730_c5"
    )
    monkeypatch.setattr(daemon.psc, "CAPITAL_ACCOUNTING_VERSION", "cap-v1")
    monkeypatch.setattr(
        daemon.psc, "matrix_contract_fingerprints", lambda *_: {"fp"}
    )
    monkeypatch.setattr(
        daemon, "matrix_ladder_floor_overrides", lambda: {}
    )
    monkeypatch.setattr(
        daemon, "_historical_exact_receipt_valid", lambda *args: True
    )
    monkeypatch.setattr(
        daemon,
        "matrix_cell_overrides",
        lambda param, overrides: dict(overrides),
    )
    rows = {
        "ENTRY_A": {"param": "ENTRY_A", "group": "ENTRY"},
        "EXIT_A": {"param": "EXIT_A", "group": "EXIT"},
        "REDUCE_STALE": {"param": "REDUCE_STALE", "group": "EXIT"},
        "REDUCE_HIST": {"param": "REDUCE_HIST", "group": "EXIT"},
    }

    cells = daemon.grouped_combo_cells(con, "VT", "LONG", rows)

    assert cells
    assert all(cell[0] == daemon.GROUPED_COMBO_PARAM for cell in cells)
    assert all("REDUCE_STALE" not in cell[1] for cell in cells)
    assert any(
        any(
            receipt.startswith("historical-not-current:")
            for receipt in cell[2][daemon.GROUPED_RECIPE_MARKER][
                "member_receipts"
            ]
        )
        for cell in cells
    )
    assert any(
        {member["param"] for member in json.loads(cell[1])["members"]}
        == {"ENTRY_A", "EXIT_A"}
        for cell in cells
    )


def test_checked_in_c4_manifest_matches_current_exact_fingerprints():
    path = daemon.SBX / "data" / "matrix_worker_manifest.json"
    payload = json.loads(path.read_text())
    missing_npz = [
        row["symbol"]
        for row in payload["workers"]
        if not (
            daemon.psc.MATRIX_NPZ_DIR / f"{row['symbol'].upper()}.npz"
        ).exists()
    ]
    if missing_npz:
        pytest.skip(
            "checked-in manifest is pinned to S1 c4 NPZ artifacts that are not "
            f"mirrored on this host: {','.join(sorted(missing_npz))}"
        )
    assert payload["no_live_promotion"] is True
    assert (
        payload["matrix_contract_version"]
        == daemon.psc.MATRIX_CONTRACT_VERSION
        == "tradier-matrix-exec-c5-20260730"
    )
    assert {
        f"{row['symbol']}_{row['side']}" for row in payload["workers"]
    } == {
        "MU_LONG",
        "NVDA_LONG",
        "VT_LONG",
        "TTD_SHORT",
        "ACN_SHORT",
        "LAC_SHORT",
    }
    for row in payload["workers"]:
        policy = daemon.load_worker_priority_policy(
            path,
            row["tag"],
            row["symbol"],
            row["side"],
            campaign=daemon.psc.CAMPAIGN,
            contract_version=daemon.psc.MATRIX_CONTRACT_VERSION,
            contract_fingerprint=daemon.psc.matrix_contract_fingerprint(
                row["symbol"], row["side"]
            ),
        )
        assert policy["priority_roots"]
        assert "STOP_PACK" not in policy["priority_roots"]
