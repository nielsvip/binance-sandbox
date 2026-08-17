import csv
import hashlib
import io
import json
from pathlib import Path

from tools import build_vector_capacity_compact_conclusions as compact


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def _campaign(tmp_path: Path, campaign: str, key: str, result: dict) -> Path:
    root = tmp_path / "data/reports/vec_research" / campaign
    _write(
        root / "campaign_manifest.json",
        {
            "schema": "TRB_PATH_PRODUCTIVITY_HOTLIST_V2",
            "cohort_keys": [key],
            "matrix_written": False,
            "database_written": False,
            "live_written": False,
        },
    )
    path = root / "keys" / key / "key_result.json"
    _write(path, result)
    return path


def test_authoritative_key_result_is_distilled_and_hash_bound(tmp_path):
    key = "ABC_LONG"
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "ENTRY": {"family": "ENTRY_WT_DC", "params": {"threshold": 1.2}},
        "EXIT": {"family": "EXIT_MTF_ATR_TRAIL", "params": {"mult": 1.5}},
        "REENTER": {"family": "MANDATORY_ZERO_BUFFER_RECLAIM"},
    }
    source = {
        "key": key,
        "status": "HOTLIST_WINNER",
        "npz": "/large/never/export/ABC.npz",
        "champion": {
            "evidence_class": "VECTOR_LIFECYCLE",
            "strategy_return_pct": 14.5,
            "bh_return_pct": 4.0,
            "alpha_vs_bh_pp": 10.5,
            "ledger_backed_vector_close_fills": 17,
            "tim_pct": 72.25,
            "entry_family": "ENTRY_WT_DC",
            "entry_params": {"threshold": 1.2},
            "exit_family": "EXIT_MTF_ATR_TRAIL",
            "exit_params": {"mult": 1.5},
            "validation": ["2024-01-01", "2026-01-01"],
            "exact_queue_status": "NOT_DISPATCHED_ADAPTER_UNVERIFIED",
            "complete_recipe": recipe,
            "chart_event_ledger": [{"raw": "must-not-export"}],
        },
    }
    key_result = _campaign(tmp_path, "capacity_frontier_test", key, source)

    payload = compact.build(tmp_path)
    row = payload["current_rows"][0]
    assert row["numeric_status"] == "AUTHORITATIVE"
    assert row["metrics"] == {
        "strategy_return_pct": 14.5,
        "side_aware_bh_return_pct": 4.0,
        "alpha_vs_bh_pp": 10.5,
        "real_close_trades": 17,
        "trades": 17,
        "time_in_market_pct": 72.25,
    }
    assert row["selected_recipe"]["entry_family"] == "ENTRY_WT_DC"
    assert row["selected_recipe"]["exit_family"] == "EXIT_MTF_ATR_TRAIL"
    assert row["source"]["key_result_sha256"] == hashlib.sha256(
        key_result.read_bytes()
    ).hexdigest()
    assert row["summary_semantic_sha256"] == compact.stable_hash(
        {k: v for k, v in row.items() if k != "summary_semantic_sha256"}
    )
    encoded = json.dumps(row, sort_keys=True)
    assert "must-not-export" not in encoded
    assert ".npz" not in encoded

    compact.publish(tmp_path, payload)
    per_key = (
        tmp_path
        / "data/reports/vector_capacity_compact_conclusions"
        / "capacity_frontier_test/ABC_LONG.json"
    )
    assert json.loads(per_key.read_text())["metrics"]["real_close_trades"] == 17
    csv_path = (
        tmp_path / "data/reports/VECTOR_CAPACITY_COMPACT_CONCLUSIONS_CURRENT.csv"
    )
    csv_row = next(csv.DictReader(io.StringIO(csv_path.read_text())))
    assert csv_row["strategy_return_pct"] == "14.5"
    assert csv_row["key_result_sha256"] == row["source"]["key_result_sha256"]


def test_failed_or_inconsistent_key_never_gets_invented_metrics(tmp_path):
    failed = _campaign(
        tmp_path,
        "capacity_frontier_failed",
        "BAD_SHORT",
        {"key": "BAD_SHORT", "status": "TIME_BUDGET_EXCEEDED", "champion": None},
    )
    mismatch = _campaign(
        tmp_path,
        "capacity_frontier_mismatch",
        "ODD_LONG",
        {
            "key": "ODD_LONG",
            "status": "UNRESOLVED_USE_BH",
            "champion": {
                "strategy_return_pct": 8.0,
                "bh_return_pct": 3.0,
                "alpha_vs_bh_pp": 999.0,
                "ledger_backed_vector_close_fills": 12,
                "tim_pct": 55.0,
                "entry_family": "ENTRY_GOLDEN_RULE",
                "exit_family": "EXIT_E02_DONCHIAN",
            },
        },
    )
    payload = compact.build(tmp_path, [failed, mismatch])
    by_key = {row["key"]: row for row in payload["current_rows"]}
    assert by_key["BAD_SHORT"]["numeric_status"] == "INCOMPLETE"
    assert all(value is None for value in by_key["BAD_SHORT"]["metrics"].values())
    assert "NO_NUMERIC_CHAMPION" in by_key["BAD_SHORT"]["blockers"]
    assert by_key["ODD_LONG"]["numeric_status"] == "INCOMPLETE"
    assert "DECLARED_ALPHA_MISMATCH" in by_key["ODD_LONG"]["blockers"]
    # Arithmetic alpha is transparent, but the conflicting declared value
    # prevents this receipt from being called authoritative.
    assert by_key["ODD_LONG"]["metrics"]["alpha_vs_bh_pp"] == 5.0


def test_compact_pull_builds_and_transfers_only_distilled_conclusions():
    script = Path("tools/pull_vector_capacity_compact_results.sh").read_text()
    assert "build_vector_capacity_compact_conclusions.py" in script
    assert "VECTOR_CAPACITY_COMPACT_CONCLUSIONS_CURRENT.json" in script
    assert "VECTOR_CAPACITY_COMPACT_CONCLUSIONS_CURRENT.csv" in script
    assert "data/reports/vector_capacity_compact_conclusions" in script
    assert "beam campaign_result" in script
    assert "-name campaign_result.json" not in script
