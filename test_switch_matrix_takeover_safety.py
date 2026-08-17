from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess

from openpyxl import Workbook
import pytest

from tools import matrix_live_progress as progress
from tools import matrix_resume_gate as resume_gate
from tools.export_switch_matrix_xls import (
    apply_preserved_v8_overlay,
    apply_vector_approx_overlay,
)


ROOT = Path(__file__).resolve().parent


def _manifest(fingerprint: str = "c5:current") -> dict[str, object]:
    return {
        "campaign": "stocks_repaired_20260730_c5",
        "matrix_contract_version": "tradier-matrix-exec-c5-20260730",
        "no_live_promotion": True,
        "workers": [
            {
                "tag": "wm_exit",
                "symbol": "MU",
                "side": "LONG",
                "expected_contract_fingerprint": fingerprint,
                "priority_roots": ["EXIT_ROOT_ENABLED"],
            }
        ],
    }


def _uniqueness_receipt(*, quarantined: int = 0) -> dict[str, object]:
    return {
        "contract": "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES",
        "current_engine_campaign": "stocks_repaired_20260730_c5",
        "matrix_numeric_fill_requires_pass": True,
        "input_rows": 5,
        "passed_numeric_overlays": 4 - quarantined,
        "data_unavailable_rows": 1,
        "quarantined_numeric_overlays": quarantined,
        "metric_collision_groups": 0,
        "fingerprint_collision_groups": 0,
    }


def _passing_vector_row(metric: float = 9.5) -> dict[str, object]:
    return {
        "vector_evidence_class": "VEC_APPROX",
        "delta_gain_mo_vs_bh_diagnostic": metric,
        "status": "MOVED",
        "result_uniqueness_status": "PASS",
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "promotion_allowed": False,
        "live_config_write_allowed": False,
        "db_engine_write_allowed": False,
    }


def test_current_engine_value_has_absolute_precedence_over_vector() -> None:
    cell = Workbook().active["A1"]
    cell.value = 3.75

    assert apply_vector_approx_overlay(cell, _passing_vector_row(99.0)) is False
    assert cell.value == 3.75


def test_preserved_historical_v8_blocks_a_later_vector_overlay() -> None:
    cell = Workbook().active["A1"]
    preserved = {
        "delta_gain_mo_vs_bh": 4.5,
        "gain_per_mo": 7.0,
        "campaign": "stocks_repaired_20260725_c2",
        "ts": "2026-07-25T00:00:00Z",
        "source_file": "param_matrix_daemon/result.json",
        "preservation_display_status": "PASS",
        "preservation_quarantine_reasons": [],
        "result_collision_count": 0,
    }

    assert apply_preserved_v8_overlay(cell, preserved) is True
    assert cell.value == 4.5
    assert apply_vector_approx_overlay(cell, _passing_vector_row(99.0)) is False
    assert cell.value == 4.5


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_pause_file_prevents_param_watchdog_from_relaunching(tmp_path: Path) -> None:
    """Exercise the real watchdog ordering with all external commands trapped."""
    source = (ROOT / "tools" / "param_matrix_watchdog.sh").read_text(
        encoding="utf-8"
    )
    sandbox = tmp_path / "sandbox"
    log_dir = tmp_path / "logs"
    fake_bin = tmp_path / "bin"
    data_dir = sandbox / "data"
    data_dir.mkdir(parents=True)
    log_dir.mkdir()
    fake_bin.mkdir()
    (data_dir / "MATRIX_WORKERS_PAUSED").write_text("paused\n", encoding="utf-8")
    (data_dir / "matrix_worker_manifest.json").write_text(
        json.dumps(
            {
                "campaign": "stocks_repaired_20260730_c5",
                "npz_dir": "data/npz",
                "end_date": "2026-07-21",
                "min_avail": 5000,
                "workers": [
                    {
                        "tag": "must-not-launch",
                        "symbol": "MU",
                        "side": "LONG",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    pgrep_marker = tmp_path / "pgrep-called"
    launch_marker = tmp_path / "setsid-called"
    _write_executable(
        fake_bin / "pgrep",
        f"#!/bin/sh\ntouch {shlex.quote(str(pgrep_marker))}\nexit 1\n",
    )
    _write_executable(
        fake_bin / "setsid",
        f"#!/bin/sh\ntouch {shlex.quote(str(launch_marker))}\nexit 0\n",
    )
    _write_executable(
        fake_bin / "free",
        "#!/bin/sh\nprintf 'Mem: 100000 1 1 1 1 99999 99999\\n'\n",
    )
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "jq",
        """#!/bin/sh
case "$2" in
  *campaign*) echo stocks_repaired_20260730_c5 ;;
  *npz_dir*) echo data/npz ;;
  *end_date*) echo 2026-07-21 ;;
  *min_avail*) echo 5000 ;;
  *workers*) printf 'must-not-launch\\tMU\\tLONG\\tfalse\\t\\n' ;;
esac
""",
    )

    source = source.replace(
        "SBX=/home/niels/binance-sandbox", f"SBX={shlex.quote(str(sandbox))}", 1
    ).replace(
        "PY=/home/niels/.conda/envs/binance_env/bin/python",
        f"PY={shlex.quote(os.environ.get('PYTHON', 'python3'))}",
        1,
    ).replace(
        "LOGDIR=/home/niels/logs", f"LOGDIR={shlex.quote(str(log_dir))}", 1
    )
    watchdog = tmp_path / "param_matrix_watchdog.sh"
    _write_executable(watchdog, source)

    result = subprocess.run(
        ["bash", str(watchdog)],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not pgrep_marker.exists(), "pause must be checked before process discovery"
    assert not launch_marker.exists(), "a paused watchdog must never reach setsid"
    log = (log_dir / "param_matrix_watchdog.log").read_text(encoding="utf-8")
    assert "RETIRED_IDLE" in log
    assert "scalar matrix watchdog replaced" in log


@pytest.mark.parametrize(
    "relative_watchdog",
    ("watchdog_lab_matrix.sh", "tools/watchdog_grouped_combo.sh"),
)
def test_pause_file_prevents_every_other_exact_watchdog_from_process_discovery(
    tmp_path: Path,
    relative_watchdog: str,
) -> None:
    """The legacy and grouped entry points cannot bypass the global pause."""
    sandbox = tmp_path / "sandbox"
    log_dir = tmp_path / "logs"
    fake_bin = tmp_path / "bin"
    (sandbox / "data").mkdir(parents=True)
    log_dir.mkdir()
    fake_bin.mkdir()
    (sandbox / "data" / "MATRIX_WORKERS_PAUSED").write_text(
        "paused\n", encoding="utf-8"
    )
    pgrep_marker = tmp_path / "pgrep-called"
    launch_marker = tmp_path / "setsid-called"
    _write_executable(
        fake_bin / "pgrep",
        f"#!/bin/sh\ntouch {shlex.quote(str(pgrep_marker))}\nexit 1\n",
    )
    _write_executable(
        fake_bin / "setsid",
        f"#!/bin/sh\ntouch {shlex.quote(str(launch_marker))}\nexit 0\n",
    )

    result = subprocess.run(
        ["bash", str(ROOT / relative_watchdog)],
        env={
            **os.environ,
            "MATRIX_SBX": str(sandbox),
            "MATRIX_PY": os.environ.get("PYTHON", "python3"),
            "MATRIX_LOGDIR": str(log_dir),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not pgrep_marker.exists(), "pause must precede process discovery"
    assert not launch_marker.exists(), "pause must precede setsid"


def test_progress_snapshot_schema_exposes_takeover_fields_for_all_six_slots(
    tmp_path: Path,
) -> None:
    path = tmp_path / "matrix_live_progress.json"
    progress.update_worker(
        path,
        4,
        worker_id="worker-4",
        parameter="WT_DC_EXIT_THRESHOLD",
        value="45",
        symbol="MU",
        side="LONG",
        combination="ENTRY+EXIT+REENTER",
        stage="VECTOR",
        status="TESTING",
        cells_remaining=411,
        total_cells=826,
        provisional_filled=415,
        exact_verified=17,
        now_epoch=100.0,
    )
    snapshot = json.loads(path.read_text(encoding="utf-8"))

    assert snapshot["schema"] == "switch-matrix-live-progress-v1"
    assert {
        "updated_at",
        "updated_at_epoch",
        "total_cells",
        "provisional_filled",
        "exact_verified",
        "cells_remaining",
        "workers",
    } <= snapshot.keys()
    assert len(snapshot["workers"]) == 6
    assert [worker["slot"] for worker in snapshot["workers"]] == list(range(1, 7))
    required_worker_fields = {
        "slot",
        "worker_id",
        "parameter",
        "value",
        "symbol",
        "side",
        "combination",
        "stage",
        "status",
        "last_update",
        "last_update_epoch",
        "cells_remaining",
    }
    assert all(required_worker_fields <= worker.keys() for worker in snapshot["workers"])
    active = snapshot["workers"][3]
    assert (
        active["parameter"],
        active["symbol"],
        active["side"],
        active["combination"],
        active["cells_remaining"],
    ) == (
        "WT_DC_EXIT_THRESHOLD",
        "MU",
        "LONG",
        "ENTRY+EXIT+REENTER",
        411,
    )
    assert snapshot["provisional_filled"] + snapshot["cells_remaining"] == 826


def test_resume_gate_requires_explicit_zero_quarantine_uniqueness_pass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    reports = root / "data" / "reports"
    reports.mkdir(parents=True)
    manifest_path = root / "data" / "matrix_worker_manifest.json"
    uniqueness_path = reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    uniqueness_path.write_text(
        json.dumps(_uniqueness_receipt(quarantined=1)), encoding="utf-8"
    )

    blocked = resume_gate.check_launch(root, manifest_path, uniqueness_path)
    assert blocked["launch_allowed"] is False
    assert blocked["uniqueness_status"] == "FAIL"
    assert "UNIQUENESS_QUARANTINED_1" in blocked["reasons"]

    collision = _uniqueness_receipt()
    collision["metric_collision_groups"] = 1
    uniqueness_path.write_text(json.dumps(collision), encoding="utf-8")
    collision_blocked = resume_gate.check_launch(
        root, manifest_path, uniqueness_path
    )
    assert collision_blocked["launch_allowed"] is False
    assert "UNIQUENESS_METRIC_COLLISIONS_1" in collision_blocked["reasons"]

    uniqueness_path.write_text(json.dumps(_uniqueness_receipt()), encoding="utf-8")
    allowed = resume_gate.check_launch(root, manifest_path, uniqueness_path)
    assert allowed["launch_allowed"] is True
    assert allowed["uniqueness_status"] == "PASS"


def test_resume_gate_fails_closed_when_uniqueness_receipt_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    (root / "data").mkdir(parents=True)
    manifest_path = root / "data" / "matrix_worker_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    verdict = resume_gate.check_launch(
        root,
        manifest_path,
        root / "data" / "reports" / "missing-uniqueness-receipt.json",
    )

    assert verdict["launch_allowed"] is False
    assert verdict["uniqueness_status"] == "FAIL"
    assert any(
        reason.startswith("UNIQUENESS_RECEIPT_UNAVAILABLE:")
        for reason in verdict["reasons"]
    )


def test_exact_engine_only_scope_is_not_blocked_by_vector_overlay_repair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    (root / "data").mkdir(parents=True)
    manifest_path = root / "data" / "matrix_worker_manifest.json"
    manifest = _manifest()
    manifest["launch_scope"] = (
        "EXACT_ENGINE_PARITY_AND_FINALIST_CONFIRMATION"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verdict = resume_gate.check_launch(
        root,
        manifest_path,
        root / "data" / "reports" / "missing-vector-receipt.json",
    )

    assert verdict["launch_allowed"] is True
    assert verdict["uniqueness_status"] == "NOT_APPLICABLE_EXACT_ENGINE_ONLY"
    assert verdict["launch_scope"] == manifest["launch_scope"]

    (root / "data" / "MATRIX_WORKERS_PAUSED").write_text(
        "operator pause\n", encoding="utf-8"
    )
    paused = resume_gate.check_launch(
        root,
        manifest_path,
        root / "data" / "reports" / "missing-vector-receipt.json",
    )
    assert paused["launch_allowed"] is False
    assert any(
        reason.startswith("MATRIX_WORKERS_PAUSED:")
        for reason in paused["reasons"]
    )


def test_resume_gate_pause_wins_even_with_a_valid_uniqueness_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    reports = root / "data" / "reports"
    reports.mkdir(parents=True)
    manifest_path = root / "data" / "matrix_worker_manifest.json"
    uniqueness_path = reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    uniqueness_path.write_text(json.dumps(_uniqueness_receipt()), encoding="utf-8")
    (root / "data" / "MATRIX_WORKERS_PAUSED").write_text(
        "operator pause\n", encoding="utf-8"
    )

    verdict = resume_gate.check_launch(root, manifest_path, uniqueness_path)
    assert verdict["launch_allowed"] is False
    assert any(reason.startswith("MATRIX_WORKERS_PAUSED:") for reason in verdict["reasons"])


def test_grouped_launcher_keeps_its_key_manifest_but_inherits_canonical_c5_gate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sandbox"
    reports = root / "data" / "reports"
    reports.mkdir(parents=True)
    manifest_path = root / "data" / "grouped_combo_worker_manifest.json"
    uniqueness_path = reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json"
    grouped = {
        "campaign": "stocks_repaired_20260730_c5_1yr",
        "matrix_contract_version": "tradier-matrix-exec-c5-20260730",
        "no_live_promotion": True,
        "max_workers": 2,
        "keys": ["MU_LONG", "TTD_SHORT"],
    }
    manifest_path.write_text(json.dumps(grouped), encoding="utf-8")
    uniqueness_path.write_text(json.dumps(_uniqueness_receipt()), encoding="utf-8")

    verdict = resume_gate.check_launch(root, manifest_path, uniqueness_path)

    assert verdict["launch_allowed"] is True
    assert verdict["campaign"] == "stocks_repaired_20260730_c5_1yr"


def test_every_exact_launcher_calls_shared_gate_before_any_launch_site() -> None:
    param = (ROOT / "tools" / "param_matrix_watchdog.sh").read_text()
    legacy = (ROOT / "watchdog_lab_matrix.sh").read_text()
    grouped = (ROOT / "tools" / "watchdog_grouped_combo.sh").read_text()

    assert param.index("matrix_resume_gate.py") < param.index("launch_one()")
    assert legacy.index("matrix_resume_gate.py") < legacy.index(
        'launch_repaired "$symbol"'
    )
    assert grouped.index("matrix_resume_gate.py") < grouped.index("pgrep -f")
    for source in (param, legacy, grouped):
        assert "MATRIX_WORKERS_PAUSED" in source

    production_manifest = json.loads(
        (ROOT / "data" / "matrix_worker_manifest.json").read_text()
    )
    assert production_manifest["launch_scope"] == (
        "EXACT_ENGINE_PARITY_AND_FINALIST_CONFIRMATION"
    )
    assert production_manifest["discovery_system"] == (
        "VECTOR_LIFECYCLE_PATH_PRODUCTIVITY_BEAM"
    )
    assert '--worker-manifest "$MANIFEST"' in param


def test_exact_cells_per_minute_counts_only_new_manifest_matched_engine_pass_rows(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    db_path = tmp_path / "param_results_stocks.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE param_cells ("
        "symbol TEXT, side TEXT, campaign TEXT, param TEXT, value_json TEXT, "
        "tier TEXT, validation_status TEXT, contract_fingerprint TEXT, ts TEXT, "
        "source_file TEXT)"
    )
    current_campaign = "stocks_repaired_20260730_c5"
    rows = [
        # The only two rows that may count.
        ("MU", "LONG", current_campaign, "A", "1", "ENGINE", "PASS", "c5:current", "950", "a/result.json"),
        ("MU", "LONG", current_campaign, "B", "2", "ENGINE", "PASS", "c5:current", "2026-01-01T00:15:00Z", "b/result.json"),
        # Provisional/vector, failed, stale, old, source-less and other campaign rows.
        ("MU", "LONG", current_campaign, "VEC", "1", "VEC_APPROX", "PASS", "c5:current", "960", "vec.json"),
        ("MU", "LONG", current_campaign, "FAIL", "1", "ENGINE", "FAIL", "c5:current", "970", "fail.json"),
        ("MU", "LONG", current_campaign, "STALE", "1", "ENGINE", "PASS", "c5:old", "980", "stale.json"),
        ("MU", "LONG", current_campaign, "OLD", "1", "ENGINE", "PASS", "c5:current", "100", "old.json"),
        ("MU", "LONG", current_campaign, "NOSOURCE", "1", "ENGINE", "PASS", "c5:current", "990", ""),
        ("MU", "LONG", "other_campaign", "OTHER", "1", "ENGINE", "PASS", "c5:current", "995", "other.json"),
        ("MU", "LONG", current_campaign, "CONTROL", "1", "ENGINE", "PASS_CONTROL_NO_CLOSE", "c5:current", "999", "control.json"),
    ]
    # Use a fixed ISO row inside the same ten-minute window as epoch 1000.
    rows[1] = (*rows[1][:8], "1970-01-01T00:15:50Z", rows[1][9])
    connection.executemany(
        "INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    connection.commit()
    connection.close()

    telemetry = resume_gate.accepted_exact_throughput(
        db_path, manifest_path, window_seconds=600, now_epoch=1000.0
    )

    assert telemetry["status"] == "PASS"
    assert telemetry["accepted_exact_new_cells"] == 2
    assert telemetry["accepted_exact_cells_per_minute"] == 0.2
    assert telemetry["source"] == "param_results_stocks.db:param_cells"
    assert telemetry["source_is_authoritative_exact_rows_only"] is True
    assert telemetry["excluded"] == {
        "stale_fingerprint": 1,
        "missing_source": 1,
        "timestamp_outside_window": 1,
        "timestamp_invalid": 0,
    }
