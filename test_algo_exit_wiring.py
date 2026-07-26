import json

from tools.audit_algo_exit_wiring import audit_repo, audit_sources
from tools.path_fleet_campaign import PATHS
from tools.path_fleet_algo_exit_audit_worker import cohort_rows


def test_current_algo_inventory_row_is_proven_disconnected_disabled_and_inert():
    result = audit_repo()
    assert result["classification"] == (
        "DISCONNECTED_DISABLED_INERT_REGISTRY_ROW"
    )
    assert not result["inventory_key_declared"]
    assert not result["inventory_key_read_lines"]
    assert result["replacement_key_default"] is False
    assert result["replacement_key_read_lines"]
    assert not result["replacement_key_conditional_lines"]
    assert not result["active_calculate_signal_score_is_exit_true_lines"]
    assert not result["active_algo_exit_reason_lines"]
    assert not result["screen_authorized"]


def test_audit_fails_closed_if_a_real_router_is_reintroduced():
    config_source = (
        "class C:\n"
        "    EXIT_ALGO_SCORE_ENABLED: bool = True\n"
    )
    manage_source = (
        "def route(config, self):\n"
        "    if getattr(config, 'EXIT_ALGO_SCORE_ENABLED', False):\n"
        "        score = self.calculate_signal_score(is_exit=True)\n"
        "        return 'ALGO_EXIT_STRONG_REDUCE'\n"
    )
    result = audit_sources(config_source, manage_source)
    assert result["classification"] == "REQUIRES_FRESH_MANUAL_AUDIT"
    assert result["replacement_key_conditional_lines"]
    assert result["active_calculate_signal_score_is_exit_true_lines"]
    assert result["active_algo_exit_reason_lines"]


def test_registry_reconciles_stale_inventory_without_authorizing_a_screen():
    path = next(row for row in PATHS if row.path_id == "EXIT_ALGO_EXIT_ENABLED")
    assert path.adapter_status == "READY_AUDIT_ONLY_DISCONNECTED"
    assert path.runner == "tools/audit_algo_exit_wiring.py"
    assert "EXIT_ALGO_SCORE_ENABLED" in path.config_keys
    assert "ALGO_EXIT_ENABLED" in path.config_keys
    assert path.settings["range_status"] == [
        "NO_SCREEN_UNTIL_COMPONENTS_HAVE_SEPARATE_PATH_IDS"
    ]


def test_audit_worker_labels_controls_red_without_strategy_metrics(tmp_path):
    controls = []
    for symbol, side in (("AAA", "LONG"), ("BBB", "SHORT")):
        artifact = tmp_path / f"{symbol}_{side}"
        artifact.mkdir()
        (artifact / "result.json").write_text(
            json.dumps({"manifest": {"side": side}})
        )
        controls.append(
            {
                "symbol": symbol,
                "status": "CONTROL_ROW",
                "artifact": str(artifact),
            }
        )
    rows = cohort_rows([{"symbols": controls}])
    assert len(rows) == 2
    assert {row["side"] for row in rows} == {"LONG", "SHORT"}
    assert all(row["status"] == "RED_DISCONNECTED_NO_SCREEN" for row in rows)
    assert all(not row["vector_screen_performed"] for row in rows)
    assert all(row["actual_exit_events"] == 0 for row in rows)
    assert all("strategy_return_pct" not in row for row in rows)
