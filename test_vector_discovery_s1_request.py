from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import vector_discovery_s1_request as hook
from tools import current_matrix_reporting


REQUEST_ID = "vd-20260802t030000z-deadbeef"


def test_absolute_path_cli_can_import_tools_package() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path("tools/vector_discovery_s1_request.py").resolve()), "--help"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "create-request" in completed.stdout


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mode() -> dict[str, object]:
    return {
        "schema": "matrix-execution-mode-v1",
        "global_pause": False,
        "discovery_mode": "VECTOR_LIFECYCLE",
        "discovery_workers": 6,
        "legacy_scalar_watchdogs": "RETIRED_IDLE",
        "exact_v8_mode": "FINALIST_COMPLETE_RECIPES_ONLY",
        "exact_v8_max_recipes_per_key": 2,
        "live_write_allowed": False,
    }


def _requests(epoch: float = 990.0) -> tuple[dict[str, object], dict[str, object]]:
    compute = {
        "schema": hook.REQUEST_SCHEMA,
        "action": hook.ACTION,
        "request_id": REQUEST_ID,
        "requested_at_epoch": epoch,
        "cohort_id": "primary",
    }
    return compute, {**compute, "schema": hook.PULL_SCHEMA, "action": hook.PULL_ACTION}


def _dispatch_root(root: Path, *, pull_pass: bool) -> None:
    compute, pull = _requests()
    _write(root / hook.REQUEST_REL, compute)
    _write(root / hook.PULL_REQUEST_REL, pull)
    _write(
        root / hook.common.SYNC_STATUS_REL,
        {
            "schema": hook.common.SYNC_SCHEMA,
            "status": "PASS",
            "phase": "COMPLETE",
            "completed_at_epoch": 995.0,
        },
    )
    source_mode = Path("data/MATRIX_EXECUTION_MODE.json").read_bytes()
    mode_path = root / hook.common.EXECUTION_MODE_REL
    mode_path.parent.mkdir(parents=True, exist_ok=True)
    mode_path.write_bytes(source_mode)
    if pull_pass:
        _write(
            root / hook.PULL_STATUS_REL,
            {
                "schema": hook.PULL_STATUS_SCHEMA,
                "status": "PASS",
                "request_id": REQUEST_ID,
                "transport_preflight_pass": True,
                "watcher_pid": os.getpid(),
                "observed_at_epoch": 999.0,
            },
        )


def test_requests_are_atomic_fixed_selectors_not_command_envelopes(
    tmp_path: Path,
) -> None:
    hook.create_requests(tmp_path, REQUEST_ID, now=990.0)
    compute, pull = _requests()
    assert json.loads((tmp_path / hook.REQUEST_REL).read_text()) == compute
    assert json.loads((tmp_path / hook.PULL_REQUEST_REL).read_text()) == pull
    assert not list((tmp_path / "data/sync").glob("*.tmp"))

    with pytest.raises(hook.Blocked, match="FIELDS_NOT_EXACT"):
        hook._validate_request(
            {**compute, "command": "backtest_v8_engine.py"},
            schema=hook.REQUEST_SCHEMA,
            action=hook.ACTION,
            now=1000.0,
        )


def test_fixed_next_cohort_is_exactly_six_disjoint_ranked_keys(
    tmp_path: Path,
) -> None:
    hook.create_requests(
        tmp_path,
        REQUEST_ID,
        cohort_id="ranked-page-2-six",
        now=990.0,
    )
    request = json.loads((tmp_path / hook.REQUEST_REL).read_text())
    assert request["cohort_id"] == "ranked-page-2-six"
    keys, symbols = hook._cohort(tmp_path, request["cohort_id"])
    assert keys == list(hook.COHORTS["ranked-page-2-six"])
    assert len(keys) == len(symbols) == 6
    assert not set(keys) & set(hook.PILOTS)
    assert hook.remote_argv(REQUEST_ID, request["cohort_id"])[-1].endswith(
        f"--request-id {REQUEST_ID} --cohort-id ranked-page-2-six"
    )


def test_terminal_campaign_summary_is_small_request_bound_truth(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "pulled" / REQUEST_ID
    cohort = ["MU_LONG", "USAR_LONG", "USAR_SHORT"]
    _write(destination / "S1_VECTOR_DISCOVERY_STATUS.json", {
        "request_id": REQUEST_ID,
        "status": "COMPLETE_BLOCKED",
        "cohort_keys": cohort,
    })
    receipt = destination / "path_productivity_hotlist/campaign_receipt.json"
    _write(receipt, {
        "run_class": "VECTOR_DISCOVERY",
        "tier": "VECTOR_LIFECYCLE",
        "cohort_key_count": 3,
        "ready_key_count": 3,
        "candidate_recipe_count": 450,
        "accepted_vector_recipe_count": 7,
        "authoritative_engine_pass_cell_count": 0,
        "authoritative_matrix_write_count": 0,
    })

    summary = hook._publish_terminal_campaign_summary(
        tmp_path, destination, REQUEST_ID, now_epoch=1000.0
    )
    stored = json.loads(
        hook._request_terminal_summary_path(
            tmp_path, REQUEST_ID
        ).read_text()
    )
    assert stored == summary
    assert stored["request_id"] == REQUEST_ID
    assert stored["candidate_recipe_count"] == 450
    assert stored["accepted_vector_recipe_count"] == 7
    assert stored["authoritative_engine_pass_cell_count"] == 0
    assert stored["source_receipt_sha256"] == hashlib.sha256(
        receipt.read_bytes()
    ).hexdigest()


def test_dispatch_will_not_compute_before_pull_watcher_pass(tmp_path: Path) -> None:
    _dispatch_root(tmp_path, pull_pass=False)
    launched: list[list[str]] = []

    def popen(argv, **kwargs):
        launched.append(argv)
        return object()

    def forbidden(*args, **kwargs):
        raise AssertionError("S1 compute must not launch")

    assert hook.dispatch(
        tmp_path, now=1000.0, popen=popen, ssh_runner=forbidden
    ) == 0
    assert launched == [[
        os.sys.executable,
        str(tmp_path / "tools/vector_discovery_s1_request.py"),
        "pull-watch",
        "--request-id",
        REQUEST_ID,
    ]]
    status = json.loads((tmp_path / hook.STATUS_REL).read_text())
    assert status["status"] == "WAITING_PULL_WATCHER_PASS"
    assert status["exact_v8_invoked"] is False


def test_dispatch_uses_fixed_remote_command_only_after_sync_and_pull_pass(
    tmp_path: Path,
) -> None:
    _dispatch_root(tmp_path, pull_pass=True)
    observed: list[list[str]] = []

    def ssh(argv, **kwargs):
        observed.append(argv)
        result = {
            "status": "COMPLETE_BLOCKED",
            "request_id": REQUEST_ID,
            "campaign_dir": f"{hook.REMOTE_CAMPAIGN_REL}/{REQUEST_ID}",
        }
        return subprocess.CompletedProcess(argv, 2, json.dumps(result) + "\n", "")

    assert hook.dispatch(tmp_path, now=1000.0, ssh_runner=ssh) == 2
    assert observed == [hook.remote_argv(REQUEST_ID)]
    assert "backtest_v8_engine.py" not in observed[0][-1]
    assert "run_path_productivity_hotlist.py" not in observed[0][-1]


def test_dispatch_waits_for_fresh_overall_sync_even_if_pull_is_pass(
    tmp_path: Path,
) -> None:
    _dispatch_root(tmp_path, pull_pass=True)
    sync_path = tmp_path / hook.common.SYNC_STATUS_REL
    _write(sync_path, {"schema": hook.common.SYNC_SCHEMA, "status": "RUNNING", "phase": "RESULT_PULL"})

    assert hook.dispatch(
        tmp_path,
        now=1000.0,
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no watcher")),
        ssh_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no S1")),
    ) == 0
    assert json.loads((tmp_path / hook.STATUS_REL).read_text())["status"] == "WAITING_SYNC"


def test_terminal_dispatch_status_is_not_overwritten_when_pull_watcher_completed(
    tmp_path: Path,
) -> None:
    _dispatch_root(tmp_path, pull_pass=False)
    _write(tmp_path / hook.STATUS_REL, {
        "schema": hook.STATUS_SCHEMA,
        "status": "COMPLETE_BLOCKED",
        "request_id": REQUEST_ID,
    })
    _write(tmp_path / hook.PULL_STATUS_REL, {
        "schema": hook.PULL_STATUS_SCHEMA,
        "status": "COMPLETE",
        "request_id": REQUEST_ID,
    })

    assert hook.dispatch(
        tmp_path,
        now=1000.0,
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no watcher")),
        ssh_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no S1")),
    ) == 0
    assert json.loads((tmp_path / hook.STATUS_REL).read_text())["status"] == "COMPLETE_BLOCKED"


def test_dispatch_accepts_durable_last_pass_while_next_sync_is_running(
    tmp_path: Path,
) -> None:
    _dispatch_root(tmp_path, pull_pass=True)
    current = tmp_path / hook.common.SYNC_STATUS_REL
    last_pass = json.loads(current.read_text())
    _write(tmp_path / hook.SYNC_LAST_PASS_REL, last_pass)
    _write(current, {
        "schema": hook.common.SYNC_SCHEMA,
        "status": "RUNNING",
        "phase": "SOURCE_PUSH",
    })
    observed: list[list[str]] = []

    def ssh(argv, **kwargs):
        observed.append(argv)
        result = {
            "status": "COMPLETE_BLOCKED",
            "request_id": REQUEST_ID,
            "campaign_dir": f"{hook.REMOTE_CAMPAIGN_REL}/{REQUEST_ID}",
        }
        return subprocess.CompletedProcess(argv, 2, json.dumps(result) + "\n", "")

    assert hook.dispatch(tmp_path, now=1000.0, ssh_runner=ssh) == 2
    assert observed == [hook.remote_argv(REQUEST_ID)]


def test_dispatch_rejects_last_pass_that_predates_request(tmp_path: Path) -> None:
    _dispatch_root(tmp_path, pull_pass=True)
    current = tmp_path / hook.common.SYNC_STATUS_REL
    _write(tmp_path / hook.SYNC_LAST_PASS_REL, {
        "schema": hook.common.SYNC_SCHEMA,
        "status": "PASS",
        "phase": "COMPLETE",
        "completed_at_epoch": 989.0,
    })
    _write(current, {
        "schema": hook.common.SYNC_SCHEMA,
        "status": "RUNNING",
        "phase": "SOURCE_PUSH",
    })

    assert hook.dispatch(
        tmp_path,
        now=1000.0,
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no watcher")),
        ssh_runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no S1")),
    ) == 0
    assert json.loads((tmp_path / hook.STATUS_REL).read_text())["status"] == "WAITING_SYNC"


def _pipeline_root(root: Path) -> dict[str, str]:
    _write(root / hook.common.EXECUTION_MODE_REL, _mode())
    _write(root / "symbols_trb_long.json", [f"L{i}" for i in range(10)])
    _write(root / "symbols_trb_short.json", [f"S{i}" for i in range(10)])
    _keys, symbols = hook._cohort(root)
    for symbol in symbols:
        _write(
            root / hook.RAW_TRADIER_REL / f"{symbol}_15m.json",
            [{"timestamp": "2024-01-01T00:00:00Z", "close": 1}],
        )
    marker = root / "pipeline-marker"
    marker.write_bytes(b"fixed")
    return {"pipeline-marker": hashlib.sha256(b"fixed").hexdigest()}


def test_s1_pipeline_is_fixed_six_worker_vector_only_staged_sequence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s1"
    expected = _pipeline_root(root)
    commands: list[list[str]] = []

    def executor(argv, **kwargs):
        commands.append(argv)
        if argv[1] == "backtest_v8_precompute.py":
            out = root / argv[argv.index("--out-dir") + 1]
            for symbol in argv[argv.index("--symbols") + 1].split(","):
                (out / f"{symbol}.npz").write_bytes(b"causal")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "tools/backtest_data_contract.py":
            return subprocess.CompletedProcess(argv, 0, '{"valid":true}\n', "")
        if argv[1] == "tools/run_path_productivity_hotlist.py":
            out = root / argv[argv.index("--out-dir") + 1]
            out.mkdir(parents=True)
            (out / "campaign_receipt.json").write_text(
                json.dumps(
                    {
                        "tier": "VECTOR_LIFECYCLE",
                        "run_class": "VECTOR_DISCOVERY",
                        "hard_limits": {"workers": 6},
                        "per_key_budget_minutes": 25.0,
                        "matrix_written": False,
                        "database_written": False,
                        "live_written": False,
                        "authoritative_engine_pass_cell_count": 0,
                        "authoritative_matrix_write_count": 0,
                    }
                )
            )
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    result = hook.run_s1_pipeline(
        root, REQUEST_ID, expected=expected, executor=executor, require_s1=False
    )

    assert result["status"] == "COMPLETE_READY"
    precompute = commands[0]
    assert precompute[1:3] == ["backtest_v8_precompute.py", "--symbols"]
    assert precompute[precompute.index("--mode") + 1] == "tradier"
    assert precompute[precompute.index("--workers") + 1] == "6"
    assert "--out-dir" in precompute
    contracts = [cmd for cmd in commands if cmd[1] == "tools/backtest_data_contract.py"]
    assert len(contracts) == len(set(precompute[3].split(",")))
    assert all(cmd[cmd.index("--profile") + 1] == "ladder" for cmd in contracts)
    assert all(cmd[cmd.index("--start") + 1] == hook.START for cmd in contracts)
    hotlist = commands[-1]
    assert hotlist[1] == "tools/run_path_productivity_hotlist.py"
    fixed = {
        "--workers": "6",
        "--budget-minutes": "25",
        "--start": "2024-01-01",
        "--end": "2026-01-01",
        "--commission-bps": "0",
        "--slippage-bps": "5",
        "--progress-snapshot": "chart_static/matrix_live_progress.json",
    }
    assert all(hotlist[hotlist.index(flag) + 1] == value for flag, value in fixed.items())
    assert not any("backtest_v8_engine.py" in " ".join(cmd) for cmd in commands)
    assert not (root / "data/param_results_stocks.db").exists()
    assert not (root / "data/reports/SWITCH_MATRIX_TRB.xlsx").exists()
    snapshot = json.loads((root / hook.PROGRESS_REL).read_text())
    assert len(snapshot["workers"]) == 6
    assert all(row["run_class"] == "VECTOR_DISCOVERY" for row in snapshot["workers"])
    assert all(row["accepted_cells_per_minute"] == 0 for row in snapshot["workers"])
    assert all(row["candidate_recipe_count"] == 0 for row in snapshot["workers"])
    assert all(row["accepted_recipe_count"] == 0 for row in snapshot["workers"])


def test_s1_pipeline_archives_stale_pause_and_rejects_non_vector_mode(tmp_path: Path) -> None:
    root = tmp_path / "s1"
    expected = _pipeline_root(root)
    pause = root / hook.common.MATRIX_PAUSE_REL
    pause.write_text("stale\n")
    commands: list[list[str]] = []
    def executor(argv, **kwargs):
        commands.append(argv)
        if argv[1] == "backtest_v8_precompute.py":
            out = root / argv[argv.index("--out-dir") + 1]
            for symbol in argv[argv.index("--symbols") + 1].split(","):
                (out / f"{symbol}.npz").write_bytes(b"causal")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "tools/backtest_data_contract.py":
            return subprocess.CompletedProcess(argv, 0, '{"valid":true}\n', "")
        if argv[1] == "tools/run_path_productivity_hotlist.py":
            out = root / argv[argv.index("--out-dir") + 1]
            out.mkdir(parents=True)
            (out / "campaign_receipt.json").write_text(json.dumps({
                "tier": "VECTOR_LIFECYCLE", "run_class": "VECTOR_DISCOVERY",
                "hard_limits": {"workers": 6}, "per_key_budget_minutes": 25.0,
                "matrix_written": False, "database_written": False,
                "live_written": False, "authoritative_engine_pass_cell_count": 0,
                "authoritative_matrix_write_count": 0,
            }))
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    assert hook.run_s1_pipeline(
        root, REQUEST_ID, expected=expected, executor=executor, require_s1=False
    )["status"] == "COMPLETE_READY"
    assert not pause.exists()
    assert list((root / "data/reports/archived_controls").glob(
        "MATRIX_WORKERS_PAUSED.cleared_vector_dispatch_*"
    ))

    root = tmp_path / "invalid-s1"
    expected = _pipeline_root(root)
    mode = _mode(); mode["exact_v8_mode"] = "BROAD_EXACT"
    _write(root / hook.common.EXECUTION_MODE_REL, mode)
    with pytest.raises(hook.Blocked, match="MATRIX_EXECUTION_MODE_INVALID"):
        hook.run_s1_pipeline(root, REQUEST_ID, expected=expected, require_s1=False)


def test_protected_v8_real_fingerprint_ignores_surface_bytes_but_not_exact_rows(
    monkeypatch, tmp_path: Path
) -> None:
    exact = [{
        "campaign": "c5", "key": "MU_LONG", "symbol": "MU", "side": "LONG",
        "param": "P", "value_json": "1", "validation_status": "PASS",
        "contract_fingerprint": "contract", "trades_fingerprint": "trades",
        "receipt_identity_sha256": "receipt", "source_file": "result.json",
        "ts": "2026-08-01T00:00:00Z",
    }]
    monkeypatch.setattr(current_matrix_reporting, "merged_rows", lambda _root: exact)
    first = hook._canonical_hashes(tmp_path)
    surface = tmp_path / "data/reports/SWITCH_MATRIX_TRB.xlsx"
    surface.parent.mkdir(parents=True)
    surface.write_bytes(b"regenerated workbook bytes")
    assert hook._canonical_hashes(tmp_path) == first

    exact[0]["trades_fingerprint"] = "mutated"
    assert hook._canonical_hashes(tmp_path) != first


def test_mac_heartbeat_invokes_only_fixed_vector_dispatcher() -> None:
    source = Path("mac_live_heartbeat.py").read_text()
    body = source[source.index("def _launch_requested_vector_discovery") : source.index("def main()")]
    assert '"dispatch"' in body
    assert 'def _launch_stale_vector_pull_watcher' in body
    assert '"pull-watch"' in body
    assert "shell=True" not in body
    assert "backtest_v8_engine" not in body
    assert "run_path_productivity_hotlist" not in body


def test_pull_watcher_excludes_active_npz_and_survives_transfer_timeouts() -> None:
    source = Path("tools/vector_discovery_s1_request.py").read_text()
    body = source[source.index("def pull_watch") : source.index("def main()")]
    assert '"--exclude=/causal_tradier_npz_v1/***"' in body
    assert "except subprocess.TimeoutExpired" in body
    assert '"status": "DEGRADED"' in body
    assert '"phase": "FINAL_EVIDENCE_PULL"' in body
    assert '"--partial-dir=.vector-pull-partial"' in body
