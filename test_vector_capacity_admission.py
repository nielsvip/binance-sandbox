import json
from pathlib import Path

import pytest

from tools import vector_capacity_admission as vca


def test_memory_gate_keeps_required_headroom():
    memory = {"total_bytes": 32 * vca.GIB, "available_bytes": 5 * vca.GIB}
    assert vca.admission_safe(memory, 2)[0] is False
    memory["available_bytes"] = 20 * vca.GIB
    assert vca.admission_safe(memory, 2) == (True, "SAFE")


def test_disk_gate_reserves_floor_before_new_writer():
    disk = {"available_bytes": 7.5 * vca.GIB}
    assert vca.disk_admission_safe(disk, "precompute") == (
        False, "DISK_PROJECTED_LT_6GIB"
    )
    assert vca.disk_admission_safe(disk, "refinement") == (True, "SAFE")
    disk["available_bytes"] = 6.1 * vca.GIB
    assert vca.disk_admission_safe(disk, "hotlist")[0] is False


def test_rejects_v8_engine_runner(tmp_path, monkeypatch):
    root = tmp_path
    report = root / "data/reports"
    report.mkdir(parents=True)
    (report / "long.json").write_text('["ABC"]')
    (report / "empty.json").write_text("[]")
    manifest = {"contract": vca.CONTRACT, "queue": [{
        "label": "bad", "kind": "hotlist", "runner": "backtest_v8_engine.py",
        "workers": 1, "projected_memory_gib": 1, "pidfile": "data/reports/x.pid",
        "stdout": "data/reports/x.out", "stderr": "data/reports/x.err",
        "npz_dir": "data/matrix_npz/x", "out_dir": "data/reports/vec_research/x",
        "long_list": "data/reports/long.json", "short_list": "data/reports/empty.json",
    }]}
    with pytest.raises(ValueError, match="runner not allowed"):
        vca.validate_manifest(root, manifest)


def test_command_embeds_only_fixed_cost_contract(tmp_path):
    item = {"kind": "hotlist", "runner": vca.ALLOWED_RUNNERS["hotlist"],
            "npz_dir": "data/matrix_npz/x", "out_dir": "data/reports/vec_research/x",
            "long_list": "data/reports/l.json", "short_list": "data/reports/e.json",
            "logical_keys": ["ABC_LONG"],
            "workers": 6, "budget_minutes": 25, "start": "2024-04-01", "end": "2026-08-01"}
    cmd = vca.command(tmp_path, "python", item)
    assert cmd[cmd.index("--commission-bps") + 1] == "0"
    assert cmd[cmd.index("--slippage-bps") + 1] == "5"
    assert cmd[cmd.index("--cohort-keys") + 1] == "ABC_LONG"
    assert "backtest_v8_engine.py" not in cmd


def test_refinement_command_is_wider_but_still_bounded(tmp_path):
    item = {
        "kind": "refinement", "runner": vca.ALLOWED_RUNNERS["refinement"],
        "npz_dir": "data/matrix_npz/r", "out_dir": "data/reports/vec_research/r",
        "long_list": "data/reports/l.json", "short_list": "data/reports/e.json",
        "logical_keys": ["ABC_LONG"],
        "workers": 6, "budget_minutes": 25, "start": "2024-04-01", "end": "2026-08-01",
        "random_curves": 64, "entry_shortlist": 32, "entry_beam_width": 3,
        "exit_beam_width": 8, "hotlist_per_key": 8,
    }
    cmd = vca.command(tmp_path, "python", item)
    assert cmd[cmd.index("--random-curves") + 1] == "64"
    assert cmd[cmd.index("--entry-shortlist") + 1] == "32"
    assert cmd[cmd.index("--exit-beam-width") + 1] == "8"
    assert cmd[cmd.index("--budget-minutes") + 1] == "25"


def test_manifest_rejects_noncanonical_hotlist_worker_count(tmp_path):
    report = tmp_path / "data/reports"
    report.mkdir(parents=True)
    (report / "long.json").write_text('["ABC"]')
    (report / "empty.json").write_text("[]")
    manifest = {"contract": vca.CONTRACT, "queue": [{
        "label": "bad-five", "kind": "hotlist",
        "runner": vca.ALLOWED_RUNNERS["hotlist"], "workers": 5,
        "projected_memory_gib": 1, "pidfile": "data/reports/x.pid",
        "stdout": "data/reports/x.out", "stderr": "data/reports/x.err",
        "npz_dir": "data/matrix_npz/x",
        "out_dir": "data/reports/vec_research/x",
        "long_list": "data/reports/long.json",
        "short_list": "data/reports/empty.json",
    }]}
    with pytest.raises(ValueError, match="exactly six workers"):
        vca.validate_manifest(tmp_path, manifest)


def test_active_long_does_not_block_ready_short(tmp_path, monkeypatch):
    long = {"label": "long", "pidfile": "data/reports/long.pid"}
    short = {"label": "short", "pidfile": "data/reports/short.pid"}
    monkeypatch.setattr(vca, "complete", lambda root, item: False)
    monkeypatch.setattr(
        vca,
        "live_item_pid",
        lambda root, item: 123 if item["label"] == "long" else None,
    )
    monkeypatch.setattr(vca, "ready", lambda root, item: (True, "READY"))
    active, candidate, waiting = vca.queue_state(tmp_path, [long, short])
    assert active == [("long", 123)]
    assert candidate is short
    assert waiting == []


def test_pidfile_requires_exact_queue_command_identity():
    item = {
        "kind": "refinement",
        "runner": "tools/run_path_productivity_hotlist.py",
        "npz_dir": "data/matrix_npz/exact",
        "out_dir": "data/reports/vec_research/exact",
        "logical_keys": ["ABC_LONG", "XYZ_LONG"],
    }
    exact = (
        "python -u tools/run_path_productivity_hotlist.py "
        "--npz-dir data/matrix_npz/exact "
        "--out-dir data/reports/vec_research/exact "
        "--cohort-keys ABC_LONG,XYZ_LONG"
    )
    assert vca.pid_matches_item_command(exact, item) is True
    assert vca.pid_matches_item_command("python ez_rankings.py", item) is False
    assert vca.pid_matches_item_command(exact.replace("XYZ_LONG", "BAD_LONG"), item) is False


def test_six_worker_projection_fills_sub_70_host_without_exceeding_hard_ceiling():
    cores = 16
    projected = 6 * 100.0 / cores
    assert 58.0 + projected <= 100.0
    assert 76.5 + projected > 100.0


def test_execution_mode_allows_valid_matrix_pause_for_readonly_discovery(tmp_path):
    mode = {
        "schema": "matrix-execution-mode-v1",
        "global_pause": False,
        "discovery_mode": "VECTOR_LIFECYCLE",
        "discovery_workers": 6,
        "legacy_scalar_watchdogs": "RETIRED_IDLE",
        "exact_v8_mode": "FINALIST_COMPLETE_RECIPES_ONLY",
        "live_write_allowed": False,
    }
    path = tmp_path / "data/MATRIX_EXECUTION_MODE.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(mode))
    audit = tmp_path / "data/reports/SWITCH_MATRIX_UNIQUE_NONZERO_AUDIT_CURRENT.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(json.dumps({
        "status": "PASS",
        "advance_to_next_symbol_allowed": True,
    }))
    vca.validate_execution_mode(tmp_path)

    pause = tmp_path / "data/MATRIX_WORKERS_PAUSED"
    pause.write_text(json.dumps({
        "schema": "matrix-integrity-pause-v1",
        "status": "PAUSED",
        "reason": "PROHIBITED_ZERO_OR_DUPLICATE_SWITCH_MATRIX_CELL",
    }))
    vca.validate_execution_mode(tmp_path)

    pause.write_text("stale exact pause\n")
    with pytest.raises(json.JSONDecodeError):
        vca.validate_execution_mode(tmp_path)


def test_capacity_holds_while_fixed_vector_campaign_is_active(tmp_path):
    status = (
        tmp_path
        / "data/reports/vector_discovery_campaigns/request/S1_VECTOR_DISCOVERY_STATUS.json"
    )
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({
        "status": "PATH_PRODUCTIVITY_HOTLIST",
        "observed_at_epoch": 1000.0,
    }))
    assert vca.fixed_vector_active(tmp_path, now=1001.0) is True

    status.write_text(json.dumps({
        "status": "COMPLETE_READY",
        "observed_at_epoch": 1001.0,
    }))
    assert vca.fixed_vector_active(tmp_path, now=1002.0) is False


def test_deferred_item_waits_for_live_classic_formation_pid(tmp_path, monkeypatch):
    status = tmp_path / "data/sync/CLASSIC_FORMATION_S1_STATUS.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "RUNNING_HOLDOUT", "pid": 12345}))
    monkeypatch.setattr(vca.os, "kill", lambda pid, signal: None)
    item = {"kind": "refinement", "defer_until_classic_formation_complete": True,
            "logical_keys": ["ABC_LONG"], "npz_dir": "data/matrix_npz/r"}
    assert vca.ready(tmp_path, item) == (False, "WAIT_CLASSIC_FORMATION_PID_ACTIVE")


def test_deferred_item_releases_only_after_terminal_classic_status(tmp_path):
    status = tmp_path / "data/sync/CLASSIC_FORMATION_S1_STATUS.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "COMPLETE_READY", "pid": 0}))
    (tmp_path / "data/matrix_npz/r").mkdir(parents=True)
    (tmp_path / "data/matrix_npz/r/ABC.npz").write_bytes(b"frozen")
    item = {"kind": "refinement", "defer_until_classic_formation_complete": True,
            "logical_keys": ["ABC_LONG"], "npz_dir": "data/matrix_npz/r"}
    assert vca.ready(tmp_path, item) == (True, "READY")


def test_deferred_item_fails_closed_for_failed_classic_formation(tmp_path):
    status = tmp_path / "data/sync/CLASSIC_FORMATION_S1_STATUS.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "FAILED_DATA_CONTRACT", "pid": 0}))
    item = {"kind": "refinement", "defer_until_classic_formation_complete": True,
            "logical_keys": ["ABC_LONG"], "npz_dir": "data/matrix_npz/r"}
    assert vca.ready(tmp_path, item) == (
        False, "WAIT_CLASSIC_FORMATION_FAILED_DATA_CONTRACT"
    )
