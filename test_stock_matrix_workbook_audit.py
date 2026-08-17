import gzip
import hashlib
import json
import time

import pytest
from openpyxl import Workbook

from tools.audit_stock_matrix_workbooks import (
    PARAM_REQUIRED_SHEETS,
    SWITCH_PATH_SHEETS,
    assert_audit,
    audit_param_workbook,
    audit_switch_workbook,
    active_keys,
    configured_keys,
    universe_snapshot,
)
from tools import param_results_store as prs


def _universe(tmp_path):
    (tmp_path / "symbols_trb_long.json").write_text('["MU"]')
    (tmp_path / "symbols_trb_short.json").write_text('["HAO"]')
    # Make the workbook unambiguously newer on filesystems with coarse mtimes.
    time.sleep(0.01)


def _switch_workbook(path, keys=("MU_LONG", "HAO_SHORT")):
    workbook = Workbook()
    workbook.active.title = "Engine Coverage"
    workbook.create_sheet("Universe Audit")
    for name in SWITCH_PATH_SHEETS:
        sheet = workbook.create_sheet(name)
        sheet.append([
            "main_switch", "sub_setting", "value", "description", "status",
            "n_tested", "n_inert", "mean_delta", "best_key", "best_delta",
            "worst_key", "worst_delta", *keys,
        ])
        sheet.append([
            "WT_DC_ENTRY_ENABLED", "", "true", "Human path description",
            "NOT_ENGINE_TESTED",
        ])
    workbook.create_sheet("Coverage")
    baseline = workbook.create_sheet("Baselines")
    baseline.append(["key"])
    for key in keys:
        baseline.append([key])
    inventory = workbook.create_sheet("Inventory")
    inventory.append([
        "setting", "tier", "exclusion_reason", "consumed_by", "description"
    ])
    inventory.append(["A", "DEAD", "reason", "{}", "Human setting description"])
    workbook.save(path)


def _param_workbook(path, keys=("MU_LONG", "HAO_SHORT")):
    workbook = Workbook()
    workbook.active.title = "Workbook Guide"
    for name in PARAM_REQUIRED_SHEETS[1:]:
        workbook.create_sheet(name)
    per_sym = workbook["PerSym Results"]
    per_sym.append(["key"])
    for key in keys:
        per_sym.append([key])
    for name in ("Entry Paths", "Exit Paths"):
        sheet = workbook[name]
        sheet.cell(1, 1, "key")
        sheet.cell(1, 6, "WT_DC")
        sheet.cell(2, 6, "Human description | SETTINGS: threshold=35,45")
        sheet.cell(3, 6, "tested settings")
        for row, key in enumerate(keys, 4):
            sheet.cell(row, 1, key)
    fleet = workbook["Path Fleet Results"]
    fleet.append(["path_id", "kind", "priority", "status", "description"])
    fleet.append(["ENTRY_WT_DC", "ENTRY", 1, "PENDING", "Human fleet description"])
    workbook.save(path)


def test_blank_cells_are_allowed_but_configured_axes_cannot_disappear(tmp_path):
    _universe(tmp_path)
    switch = tmp_path / "SWITCH_MATRIX_TRB.xlsx"
    param = tmp_path / "PARAM_BASELINE_STOCKS.xlsx"
    _switch_workbook(switch)
    _param_workbook(param)

    switch_report = audit_switch_workbook(switch, tmp_path)
    param_report = audit_param_workbook(param, tmp_path)

    assert_audit(switch_report)
    assert_audit(param_report)
    assert switch_report.sheet_key_counts["Entry"] == 2
    assert param_report.sheet_key_counts["Entry Paths"] == 2
    assert switch_report.formula_errors == []
    assert param_report.blank_path_descriptions == []


def test_missing_current_key_fails_every_relevant_path_sheet(tmp_path):
    _universe(tmp_path)
    switch = tmp_path / "SWITCH_MATRIX_TRB.xlsx"
    param = tmp_path / "PARAM_BASELINE_STOCKS.xlsx"
    _switch_workbook(switch, keys=("MU_LONG",))
    _param_workbook(param, keys=("MU_LONG",))

    switch_report = audit_switch_workbook(switch, tmp_path)
    param_report = audit_param_workbook(param, tmp_path)

    assert not switch_report.ok
    assert not param_report.ok
    assert switch_report.missing_keys_by_sheet["Exit"] == ["HAO_SHORT"]
    assert param_report.missing_keys_by_sheet["Exit Paths"] == ["HAO_SHORT"]
    with pytest.raises(RuntimeError, match="HAO_SHORT"):
        assert_audit(switch_report)


def test_param_path_authority_missing_fails_closed(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "param_sweep_manifest_tradier.json").write_text(
        json.dumps({"params": {"WT_DC_ENTRY_ENABLED": {"sweepable": True}}})
    )
    with pytest.raises(FileNotFoundError, match="knob_registry.py build"):
        prs._path_definitions(tmp_path)


def test_pinned_live_source_is_unioned_with_historical_sandbox(
    tmp_path, monkeypatch
):
    _universe(tmp_path)
    pinned = tmp_path / "pinned_live"
    pinned.mkdir()
    (pinned / "symbols_trb_long.json").write_text('["MU", "OLED"]')
    (pinned / "symbols_trb_short.json").write_text('["TTD"]')
    monkeypatch.setenv(
        "TRADIER_MATRIX_SYMBOL_SOURCE_DIRS", str(pinned)
    )

    assert configured_keys(tmp_path) == [
        "MU_LONG",
        "OLED_LONG",
        "TTD_SHORT",
        "HAO_SHORT",
    ]
    assert active_keys(tmp_path) == [
        "MU_LONG",
        "OLED_LONG",
        "TTD_SHORT",
    ]
    snapshot = universe_snapshot(tmp_path)
    assert snapshot["keys"] == configured_keys(tmp_path)
    assert snapshot["active_keys"] == active_keys(tmp_path)
    assert snapshot["historical_keys"] == ["HAO_SHORT"]
    assert len(snapshot["active_key_sha256"]) == 64
    assert any(
        source["path"].endswith("pinned_live/symbols_trb_long.json")
        for source in snapshot["sources"]
    )


def test_explicit_pin_excludes_stale_sibling_checkout(tmp_path, monkeypatch):
    sandbox = tmp_path / "binance-sandbox"
    sibling = tmp_path / "binance"
    pinned = tmp_path / "pinned"
    for root in (sandbox, sibling, pinned):
        root.mkdir()
    (sandbox / "symbols_trb_long.json").write_text('["OLD"]')
    (sandbox / "symbols_trb_short.json").write_text("[]")
    (sibling / "symbols_trb_long.json").write_text('["SIBLING_STALE"]')
    (sibling / "symbols_trb_short.json").write_text("[]")
    (pinned / "symbols_trb_long.json").write_text('["LIVE"]')
    (pinned / "symbols_trb_short.json").write_text("[]")
    monkeypatch.setenv("TRADIER_MATRIX_SYMBOL_SOURCE_DIRS", str(pinned))

    assert configured_keys(sandbox) == ["LIVE_LONG", "OLD_LONG"]


def test_default_report_snapshot_is_historical_without_environment(tmp_path):
    _universe(tmp_path)
    pinned = (
        tmp_path
        / "data"
        / "reports"
        / "trb_matrix_universe_snapshot_20260729"
    )
    pinned.mkdir(parents=True)
    (pinned / "symbols_trb_long.json").write_text('["LIVE"]')
    (pinned / "symbols_trb_short.json").write_text('["NOW"]')

    assert active_keys(tmp_path) == ["MU_LONG", "HAO_SHORT"]
    assert configured_keys(tmp_path) == [
        "LIVE_LONG",
        "NOW_SHORT",
        "MU_LONG",
        "HAO_SHORT",
    ]


def test_hash_bound_canonical_provenance_freezes_synced_active_keys(tmp_path):
    _universe(tmp_path)
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    matrix = reports / "SWITCH_MATRIX_TRB.csv.gz"
    metadata = {
        "CURRENT_CAMPAIGN": "campaign_c5",
        "CURRENT_ENGINE_CUTOFF": "2026-07-30T03:30:10Z",
        "CURRENT_CONTRACT_VERSION": "contract_c5",
        "CURRENT_MATRIX_SCOPE": (
            "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
        ),
        "CANONICAL_MATRIX": "data/reports/SWITCH_MATRIX_TRB.csv.gz",
    }
    with gzip.open(matrix, "wt") as handle:
        for name, value in metadata.items():
            handle.write(f"# {name}={value}\n")
        handle.write("a,b\n")
    policy = {
        "REMOTE_LONG": {
            "rank": 1,
            "cohort": "TOP_10",
            "tim_min_pct": 50.0,
            "tim_max_pct": 80.0,
            "source": "symbols_trb_long.json:first_10",
        },
        "REMOTE_SHORT": {
            "rank": 1,
            "cohort": "TOP_10",
            "tim_min_pct": 50.0,
            "tim_max_pct": 80.0,
            "source": "symbols_trb_short.json:first_10",
        },
    }
    sidecar = {
        "schema": "switch-matrix-trb-current-digest-v1",
        "campaign": metadata["CURRENT_CAMPAIGN"],
        "engine_cutoff": metadata["CURRENT_ENGINE_CUTOFF"],
        "contract_version": metadata["CURRENT_CONTRACT_VERSION"],
        "matrix_scope": metadata["CURRENT_MATRIX_SCOPE"],
        "canonical_matrix": metadata["CANONICAL_MATRIX"],
        "canonical_matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
        "active_key_count": len(policy),
        "tim_policy": policy,
    }
    (reports / "SWITCH_MATRIX_TRB_DIGEST.md.provenance.json").write_text(
        json.dumps(sidecar)
    )

    assert active_keys(tmp_path) == ["REMOTE_LONG", "REMOTE_SHORT"]
    assert configured_keys(tmp_path) == [
        "MU_LONG", "HAO_SHORT", "REMOTE_LONG", "REMOTE_SHORT",
    ]

    sidecar["canonical_matrix_sha256"] = "0" * 64
    (reports / "SWITCH_MATRIX_TRB_DIGEST.md.provenance.json").write_text(
        json.dumps(sidecar)
    )
    with pytest.raises(
        RuntimeError, match="CANONICAL_ACTIVE_UNIVERSE_PROVENANCE_INVALID"
    ):
        active_keys(tmp_path)
