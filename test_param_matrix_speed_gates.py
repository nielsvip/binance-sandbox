import json
import sqlite3

import pytest

from config_tradier import TradierConfig
from tools import exact_wiring_gate
from tools import param_matrix_daemon as daemon


def test_trb_does_not_exact_test_other_account_namespaces():
    assert daemon.wrong_account_namespace("TRA_MIN_HOLD_MINUTES", "trb")
    assert daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trb")
    assert not daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trb")
    assert not daemon.wrong_account_namespace("WT_3M_FORCE_OPEN_ENABLED", "trb")


def test_trc_does_not_exact_test_trb_namespace():
    assert daemon.wrong_account_namespace("TRB_MAX_LONG_VALUE", "trc")
    assert not daemon.wrong_account_namespace("TRC_CONNORS_RSI_ENABLED", "trc")


def test_only_params_keeps_requested_canonical_daemon_cells():
    cells = [
        ("STOP_PACK", "x", {}),
        ("WT_DC_ENTRY_THRESHOLD", "35", {"WT_DC_ENTRY_THRESHOLD": 35}),
        ("WT_3M_FORCE_OPEN_ENABLED", "true", {"WT_3M_FORCE_OPEN_ENABLED": True}),
    ]
    assert daemon.only_params(
        cells, " wt_dc_entry_threshold,WT_3M_FORCE_OPEN_ENABLED "
    ) == cells[1:]
    assert daemon.only_params(cells, "") == cells


def test_bool_cells_keep_canonical_json_type_across_two_passes(monkeypatch, tmp_path):
    name = "WT_3M_FORCE_OPEN_ENABLED"
    monkeypatch.setattr(
        daemon.psc,
        "load_params",
        lambda _path, _limit: [(name, [False, True])],
    )
    monkeypatch.setattr(daemon, "useless_knobs", lambda: set())
    monkeypatch.setattr(daemon.wiring_gate.TradierConfig, name, False)

    cells = [
        cell
        for cell in daemon.all_cells(tmp_path / "missing.json", all_tiers=True)
        if cell[0] == name
    ]
    assert [value_json for _, value_json, _ in cells] == ["true", "false"]
    assert [json.loads(value_json) for _, value_json, _ in cells] == [True, False]
    assert [next(iter(override.values())) for _, _, override in cells] == [True, False]

    # The default false pass is represented by the accepted baseline and is
    # intentionally not rerun as a duplicate exact cell.  The one non-default
    # boolean exact pass must compare directly with that baseline.
    rows = [
        {
            "value": True,
            "value_json": "true",
            "fingerprint": "fp-true",
            "inert": False,
            "validation_status": "PASS",
        }
    ]
    verdict = exact_wiring_gate.classify_param(
        name,
        rows,
        "fp-baseline",
        tmp_path / "missing.npz",
        symbol="MU",
        side="LONG",
    )
    assert verdict["verdict"] == "WIRED_DIFFERENT"
    assert not verdict["skip_remaining_exact"]

    rows[0]["fingerprint"] = "fp-baseline"
    reconnect = exact_wiring_gate.classify_param(
        name,
        rows,
        "fp-baseline",
        tmp_path / "missing.npz",
        symbol="MU",
        side="LONG",
    )
    assert reconnect["verdict"] == "RED_RECONNECT"
    assert reconnect["range_binding"]["status"] == "BINDING_BY_TYPE"
    assert reconnect["skip_remaining_exact"]


def _dependency_row(
    *,
    group="ENTRY",
    role="MAIN_SWITCH",
    layer="FAMILY_MASTER",
    binding="DIRECT_LIVE_CONFIG_READ",
    live=True,
    backends=("EXACT_V8",),
):
    return {
        "group": group,
        "role": role,
        "precedence_layer": layer,
        "binding_evidence": binding,
        "consumed_by": {"live": live},
        "screen_backends": list(backends),
        "deployment_scope": "PER_SYMBOL" if live else "NONDEPLOYABLE_NO_LIVE_READ",
    }


def test_entry_inert_safe_baseline_schedules_only_source_backed_entry_probes():
    cells = [
        ("ENTRY_MASTER_ENABLED", "true", {"ENTRY_MASTER_ENABLED": True}),
        ("ENTRY_SOURCE_SCORE", "2", {"ENTRY_SOURCE_SCORE": 2}),
        ("ENTRY_FILTER_MIN", "3", {"ENTRY_FILTER_MIN": 3}),
        ("ENTRY_REENTRY_ENABLED", "true", {"ENTRY_REENTRY_ENABLED": True}),
        ("EXIT_MASTER_ENABLED", "true", {"EXIT_MASTER_ENABLED": True}),
        ("STOP_PACK", "trail", {"TRAIL_ENABLED": True}),
        ("TF_EXCLUDE", "1h", {"TF_EXCLUDE_1H": True}),
        ("UNBACKED_ENTRY_ENABLED", "true", {"UNBACKED_ENTRY_ENABLED": True}),
    ]
    rows = {
        "ENTRY_MASTER_ENABLED": _dependency_row(),
        "ENTRY_SOURCE_SCORE": _dependency_row(
            role="SUB_SETTING", layer="ENTRY_SOURCE"
        ),
        "ENTRY_FILTER_MIN": _dependency_row(
            role="FILTER", layer="ENTRY_FILTER"
        ),
        "ENTRY_REENTRY_ENABLED": _dependency_row(),
        "EXIT_MASTER_ENABLED": _dependency_row(group="EXIT"),
        "UNBACKED_ENTRY_ENABLED": _dependency_row(
            binding="NO_LIVE_DECISION_READ", live=False, backends=()
        ),
    }

    selected = daemon.gate_cells_for_safe_baseline(
        cells,
        {"real_closes": 2, "time_in_mkt_pct": 0.5},
        rows,
    )

    assert [cell[0] for cell in selected] == [
        "ENTRY_MASTER_ENABLED",
        "ENTRY_SOURCE_SCORE",
    ]


def test_safe_baseline_entry_inert_threshold_uses_exposure_not_close_count():
    assert not daemon.baseline_is_entry_inert(
        {"real_closes": 0, "time_in_mkt_pct": 50.0}
    )
    assert daemon.baseline_is_entry_inert(
        {"real_closes": 100, "time_in_mkt_pct": 0.999}
    )


def test_active_safe_baseline_keeps_cells_but_helpers_are_not_manifest_coverage():
    cells = [
        ("ENTRY_MASTER_ENABLED", "true", {"ENTRY_MASTER_ENABLED": True}),
        ("EXIT_MASTER_ENABLED", "true", {"EXIT_MASTER_ENABLED": True}),
        ("STOP_PACK", "trail", {"TRAIL_ENABLED": True}),
        ("TF_EXCLUDE", "1h", {"TF_EXCLUDE_1H": True}),
    ]
    assert daemon.gate_cells_for_safe_baseline(
        cells,
        {"real_closes": 3, "time_in_mkt_pct": 1.0},
        {},
    ) == cells
    assert [cell[0] for cell in cells if daemon.is_manifest_cell(cell)] == [
        "ENTRY_MASTER_ENABLED",
        "EXIT_MASTER_ENABLED",
    ]


def test_safe_matrix_floor_really_disables_competing_exit_families():
    floor = daemon.matrix_ladder_floor_overrides()
    assert len(floor) >= 100
    assert floor["DYNAMIC_SCORE_COUNTER_EXIT_ENABLED"] is False
    assert floor["WT_CROSSUNDER_FINAL_ENABLED"] is False
    assert floor["MTF_DC_REJECT_EXIT_ENABLED"] is False
    assert floor["LONG_STRUCT_EXIT_TF"] == "None"
    assert floor["SHORT_STRUCT_EXIT_TF"] == "None"
    assert floor["WT_DC_EXIT_THRESHOLD"] == 9999.0
    assert floor["LR_BAND_ENTRY_ENABLED"] is False
    assert floor["LR_BAND_REGIME_ENABLED"] is False
    assert floor["LR_BAND_LADDER_ENABLED"] is False
    assert floor["LR_BAND_LADDER_ORDINARY_PARITY_ENABLED"] is False
    assert floor["LR_BAND_E02_EXIT_ENABLED"] is False
    assert floor["BREAKOUT_SIZE_LADDER_ENABLED"] is False
    assert floor["WT_3M_FORCE_OPEN_ENABLED"] is False
    assert floor["WT_DC_ENTRY_THRESHOLD"] == 9999.0
    assert floor["TRA_WT_DC_ENTRY_THRESHOLD"] == 9999.0


def test_exit_filter_cell_enables_only_its_registry_family():
    effective = daemon.matrix_cell_overrides(
        "DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD",
        {"DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD": 70.0},
    )
    assert effective["DYNAMIC_SCORE_COUNTER_EXIT_ENABLED"] is True
    assert effective["DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD"] == 70.0
    assert effective["WT_CROSSUNDER_FINAL_ENABLED"] is False
    assert effective["MTF_DC_REJECT_EXIT_ENABLED"] is False


def test_exit_cell_enables_explicit_cross_family_activation_parent():
    effective = daemon.matrix_cell_overrides(
        "MTF_DC_REJECT_EXIT_ENABLED",
        {"MTF_DC_REJECT_EXIT_ENABLED": True},
    )
    assert effective["MTF_EXIT_USE_COMPOUND"] is True
    assert effective["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert effective["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert effective["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert effective["MTF_BB_REJECT_EXIT_ENABLED"] is False
    assert effective["MTF_WT_CROSS_EXIT_ENABLED"] is False


def test_all_mtf_exit_roots_restore_live_companions_from_none_floor():
    dc = daemon.matrix_cell_overrides(
        "MTF_DC_REJECT_EXIT_ENABLED",
        {"MTF_DC_REJECT_EXIT_ENABLED": True},
    )
    assert dc["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert dc["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5

    bb = daemon.matrix_cell_overrides(
        "MTF_BB_REJECT_EXIT_ENABLED",
        {"MTF_BB_REJECT_EXIT_ENABLED": True},
    )
    assert bb["MTF_EXIT_USE_COMPOUND"] is True
    assert bb["MTF_BB_REJECT_EXIT_TF"] == "1h"
    assert bb["MTF_BB_REJECT_EXIT_LOOKBACK"] == 5

    wt = daemon.matrix_cell_overrides(
        "MTF_WT_CROSS_EXIT_ENABLED",
        {"MTF_WT_CROSS_EXIT_ENABLED": True},
    )
    assert wt["MTF_EXIT_USE_COMPOUND"] is True
    assert wt["MTF_WT_CROSS_EXIT_TF"] == "15m"
    assert wt["MTF_GR_EXIT_GATE_ENABLED"] is True
    assert wt["MTF_GR_EXIT_MIN_TFS"] == 3


def test_wt_dc_exit_master_restores_numeric_off_sentinels():
    effective = daemon.matrix_cell_overrides(
        "WT_DC_EXIT_ENABLED",
        {"WT_DC_EXIT_ENABLED": True},
    )
    assert effective["WT_DC_EXIT_ENABLED"] is True
    assert effective["WT_DC_EXIT_THRESHOLD"] == 30
    assert effective["WT_DC_EXIT_STALE_MAX_S"] == 600


def test_wt_dc_exit_threshold_value_wins_over_live_companion_default():
    effective = daemon.matrix_cell_overrides(
        "WT_DC_EXIT_THRESHOLD",
        {"WT_DC_EXIT_THRESHOLD": 45},
    )
    assert effective["WT_DC_EXIT_ENABLED"] is True
    assert effective["WT_DC_EXIT_THRESHOLD"] == 45
    assert effective["WT_DC_EXIT_STALE_MAX_S"] == 600


def test_wt_force_recipe_has_room_above_exact_seed_and_value_wins():
    enabled = daemon.matrix_cell_overrides(
        "WT_3M_FORCE_OPEN_ENABLED",
        {"WT_3M_FORCE_OPEN_ENABLED": True},
    )
    assert enabled["WT_3M_FORCE_OPEN_BUILD_TO_TARGET"] is True
    assert enabled["WT_3M_FORCE_OPEN_TARGET_USD"] == 16000.0
    assert enabled["WT_3M_FORCE_OPEN_USE_SMA200"] is True
    assert enabled["WT_3M_FORCE_OPEN_ENABLED"] is True

    multiplier = daemon.matrix_cell_overrides(
        "WT_3M_FORCE_OPEN_TF_LADDER_MULT",
        {"WT_3M_FORCE_OPEN_TF_LADDER_MULT": 1.5},
    )
    assert multiplier["WT_3M_FORCE_OPEN_ENABLED"] is True
    assert multiplier["WT_3M_FORCE_OPEN_TARGET_USD"] == 16000.0
    assert multiplier["WT_3M_FORCE_OPEN_TF_LADDER_MULT"] == 1.5


def test_delta_exit_recipe_restores_actual_reader_inputs():
    effective = daemon.matrix_cell_overrides(
        "DELTA_EXIT_ENABLED",
        {"DELTA_EXIT_ENABLED": True},
    )
    assert effective["DELTA_ENGINE_ENABLED"] is True
    assert effective["DELTA_EXIT_ENABLED"] is True
    assert effective["DELTA_EXIT_TF"] == "15m"
    assert effective["DELTA_EXIT_TYPE"] == "speed_decay"
    assert effective["NOLOSS_MIN_PROFIT_PCT_TRADIER"] == 0.01


def test_mtf_compound_master_true_executes_representative_child():
    effective = daemon.matrix_cell_overrides(
        "MTF_EXIT_USE_COMPOUND",
        {"MTF_EXIT_USE_COMPOUND": True},
    )
    assert effective["MTF_EXIT_USE_COMPOUND"] is True
    assert effective["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert effective["MTF_DC_REJECT_EXIT_TF"] == "1h"
    assert effective["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert daemon.matrix_cell_is_baseline_alias(
        "MTF_EXIT_USE_COMPOUND",
        {"MTF_EXIT_USE_COMPOUND": False},
    )


def test_stdev_reject_restores_tf_and_wtdc_hold_gateway():
    effective = daemon.matrix_cell_overrides(
        "STDEV_REJECT_EXIT_ENABLED",
        {"STDEV_REJECT_EXIT_ENABLED": True},
    )
    assert effective["STDEV_REJECT_EXIT_ENABLED"] is True
    assert effective["STDEV_REJECT_EXIT_TF"] == "D"
    assert effective["WT_DC_EXIT_ENABLED"] is True
    assert effective["WT_DC_EXIT_THRESHOLD"] == 9999.0
    assert effective["WT_DC_EXIT_STALE_MAX_S"] == 600


def test_stdev_reject_test_value_wins_over_default_companion():
    effective = daemon.matrix_cell_overrides(
        "STDEV_REJECT_EXIT_TF",
        {"STDEV_REJECT_EXIT_TF": "4h"},
    )
    assert effective["STDEV_REJECT_EXIT_ENABLED"] is True
    assert effective["STDEV_REJECT_EXIT_TF"] == "4h"
    assert effective["WT_DC_EXIT_THRESHOLD"] == 9999.0


def test_stdev_bb_rz_restores_tf_and_wtdc_hold_gateway():
    effective = daemon.matrix_cell_overrides(
        "STDEV_BB_RZ_EXIT_ENABLED",
        {"STDEV_BB_RZ_EXIT_ENABLED": True},
    )
    assert effective["STDEV_BB_RZ_EXIT_ENABLED"] is True
    assert effective["STDEV_BB_RZ_EXIT_TF"] == "D"
    assert effective["WT_DC_EXIT_ENABLED"] is True
    assert effective["WT_DC_EXIT_THRESHOLD"] == 9999.0


def test_repaired_receipt_gate_rejects_pre_fix_dead_recipes():
    assert not daemon.mtf_companion_receipt_valid(
        "WT_3M_FORCE_OPEN_ENABLED",
        {
            "WT_3M_FORCE_OPEN_ENABLED": True,
            "WT_3M_FORCE_OPEN_TARGET_USD": 2000.0,
        },
    )
    assert not daemon.mtf_companion_receipt_valid(
        "DELTA_EXIT_ENABLED",
        {
            "DELTA_EXIT_ENABLED": True,
            "DELTA_EXIT_TF": "None",
            "DELTA_EXIT_TYPE": "None",
            "NOLOSS_MIN_PROFIT_PCT_TRADIER": 9999.0,
        },
    )
    assert not daemon.mtf_companion_receipt_valid(
        "MTF_EXIT_USE_COMPOUND",
        {
            "MTF_EXIT_USE_COMPOUND": True,
            "MTF_DC_REJECT_EXIT_ENABLED": False,
            "MTF_DC_REJECT_EXIT_TF": "None",
        },
    )
    assert not daemon.mtf_companion_receipt_valid(
        "WT_DC_EXIT_ENABLED",
        {
            "WT_DC_EXIT_ENABLED": True,
            "WT_DC_EXIT_THRESHOLD": 9999.0,
            "WT_DC_EXIT_STALE_MAX_S": 0.0,
        },
    )
    assert not daemon.mtf_companion_receipt_valid(
        "STDEV_REJECT_EXIT_ENABLED",
        {
            "STDEV_REJECT_EXIT_ENABLED": True,
            "STDEV_REJECT_EXIT_TF": "None",
            "WT_DC_EXIT_ENABLED": False,
        },
    )
    assert not daemon.mtf_companion_receipt_valid(
        "MTF_WT_CROSS_EXIT_ENABLED",
        {
            "MTF_EXIT_USE_COMPOUND": True,
            "MTF_WT_CROSS_EXIT_ENABLED": True,
            "MTF_WT_CROSS_EXIT_TF": "15m",
            "MTF_GR_EXIT_GATE_ENABLED": False,
        },
    )


def test_repaired_receipt_gate_accepts_complete_recipes():
    for param in (
        "WT_3M_FORCE_OPEN_ENABLED",
        "WT_3M_FORCE_OPEN_TF_LADDER",
        "WT_3M_FORCE_OPEN_TF_LADDER_MULT",
        "DELTA_EXIT_ENABLED",
        "MTF_EXIT_USE_COMPOUND",
        "WT_DC_EXIT_ENABLED",
        "STDEV_REJECT_EXIT_ENABLED",
        "STDEV_BB_RZ_EXIT_ENABLED",
        "MTF_WT_CROSS_EXIT_ENABLED",
    ):
        overrides = daemon.matrix_cell_overrides(param, {param: True})
        assert daemon.mtf_companion_receipt_valid(param, overrides)


def test_mtf_companion_fix_does_not_mutate_hashed_dependency_contract():
    dependencies = daemon._matrix_activation_dependencies()
    assert dependencies["MTF_DC_REJECT_EXIT_ENABLED"] == (
        "MTF_EXIT_USE_COMPOUND",
    )
    assert dependencies["MTF_BB_REJECT_EXIT_ENABLED"] == ()
    assert dependencies["MTF_WT_CROSS_EXIT_ENABLED"] == ()
    assert "MTF_DC_REJECT_EXIT_TF" not in dependencies
    assert "MTF_DC_REJECT_EXIT_LOOKBACK" not in dependencies
    assert "MTF_BB_REJECT_EXIT_TF" not in dependencies
    assert "MTF_BB_REJECT_EXIT_LOOKBACK" not in dependencies
    assert "MTF_WT_CROSS_EXIT_TF" not in dependencies


def test_mtf_family_subsetting_gets_companions_then_its_test_value_wins():
    effective = daemon.matrix_cell_overrides(
        "MTF_DC_REJECT_EXIT_TF",
        {"MTF_DC_REJECT_EXIT_TF": "4h"},
    )
    assert effective["MTF_EXIT_USE_COMPOUND"] is True
    assert effective["MTF_DC_REJECT_EXIT_ENABLED"] is True
    assert effective["MTF_DC_REJECT_EXIT_LOOKBACK"] == 5
    assert effective["MTF_DC_REJECT_EXIT_TF"] == "4h"


def test_mtf_companion_receipt_rejects_pre_repair_none_tf_rows():
    bad = {
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_DC_REJECT_EXIT_TF": "None",
        "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
    }
    good = {
        "MTF_EXIT_USE_COMPOUND": True,
        "MTF_DC_REJECT_EXIT_TF": "1h",
        "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
    }
    assert not daemon.mtf_companion_receipt_valid(
        "MTF_DC_REJECT_EXIT_ENABLED", bad
    )
    assert not daemon.mtf_companion_receipt_valid(
        "MTF_DC_REJECT_EXIT_ENABLED", "not-json"
    )
    assert daemon.mtf_companion_receipt_valid(
        "MTF_DC_REJECT_EXIT_ENABLED", good
    )
    assert daemon.mtf_companion_receipt_valid(
        "MTF_DC_REJECT_EXIT_ENABLED", json.dumps(good)
    )


def test_exact_gate_ignores_current_fingerprint_row_with_none_tf(
    monkeypatch, tmp_path
):
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE param_cells ("
        "mode TEXT,campaign TEXT,symbol TEXT,side TEXT,param TEXT,tier TEXT,"
        "contract_fingerprint TEXT,validation_status TEXT,value_json TEXT,"
        "value_num REAL,trades_fingerprint TEXT,inert INTEGER,"
        "overrides_json TEXT,capital_accounting_version TEXT)"
    )
    common = (
        daemon.psc.MODE,
        daemon.psc.CAMPAIGN,
        "TTD",
        "SHORT",
        "MTF_DC_REJECT_EXIT_ENABLED",
        "ENGINE",
        "fp",
        "PASS",
        "true",
        None,
    )
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        common
        + (
            "bad-trades",
            0,
            json.dumps(
                {
                    "MTF_EXIT_USE_COMPOUND": True,
                    "MTF_DC_REJECT_EXIT_TF": "None",
                    "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
                }
            ),
            daemon.psc.CAPITAL_ACCOUNTING_VERSION,
        ),
    )
    con.execute(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        common
        + (
            "good-trades",
            0,
            json.dumps(
                {
                    "MTF_EXIT_USE_COMPOUND": True,
                    "MTF_DC_REJECT_EXIT_TF": "1h",
                    "MTF_DC_REJECT_EXIT_LOOKBACK": 5,
                }
            ),
            daemon.psc.CAPITAL_ACCOUNTING_VERSION,
        ),
    )
    captured = {}

    def classify(_pname, rows, *_args, **_kwargs):
        captured["rows"] = rows
        return {"verdict": "TEST"}

    monkeypatch.setattr(
        daemon.psc, "matrix_contract_fingerprint", lambda *_: "fp"
    )
    monkeypatch.setattr(daemon.wiring_gate, "classify_param", classify)
    monkeypatch.setattr(daemon.psc, "MATRIX_NPZ_DIR", tmp_path)
    ctx = {"con": con, "base_fp": {"TTD_SHORT": "baseline"}}

    daemon.exact_gate_verdict(
        ctx, "TTD", "SHORT", "MTF_DC_REJECT_EXIT_ENABLED"
    )

    assert [row["fingerprint"] for row in captured["rows"]] == [
        "good-trades"
    ]


def test_non_exit_cell_keeps_complete_exit_floor():
    effective = daemon.matrix_cell_overrides(
        "TRADIER_MIN_HOLD_MINUTES",
        {"TRADIER_MIN_HOLD_MINUTES": 30.0},
    )
    assert effective["TRADIER_MIN_HOLD_MINUTES"] == 30.0
    assert effective["DYNAMIC_SCORE_COUNTER_EXIT_ENABLED"] is False
    assert effective["WT_CROSSUNDER_FINAL_ENABLED"] is False


def test_sweep_entry_unblock_does_not_silently_zero_exit_min_hold(monkeypatch):
    monkeypatch.setenv("V8_SWEEP_MODE", "1")
    cfg = TradierConfig()
    assert cfg.TRADIER_MIN_HOLD_MINUTES == 4320.0


def test_missing_or_incomplete_interdependency_metadata_fails_closed(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "params": {
                    "ENTRY_MASTER_ENABLED": {
                        "sweepable": True,
                        "test_values": [False, True],
                    }
                }
            }
        )
    )
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit, match="INTERDEPENDENCY_METADATA_REQUIRED"):
        daemon.load_interdependency_rows(missing, manifest)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"paths": []}))
    with pytest.raises(SystemExit, match="missing 1 manifest path"):
        daemon.load_interdependency_rows(incomplete, manifest)
