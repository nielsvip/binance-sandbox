from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import mac_live_heartbeat as heartbeat
from tools import matrix_live_progress
from tools import vector_approx_scalar_s1_request as hook


REQUEST_ID = "vas-20260802t090000z-deadbeef"


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
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


def _pipeline_root(root: Path) -> dict[str, str]:
    _write(root / hook.request_common.EXECUTION_MODE_REL, _mode())
    (root / "config.py").write_text("CONFIG = 1\n")
    (root / "config_tradier.py").write_text("CONFIG = 1\n")
    for symbol in hook.SYMBOLS:
        _write(
            root / hook.RAW_TRADIER_REL / f"{symbol}_15m.json",
            [{"timestamp": 1}],
        )
    required = {
        str(hook.request_common.EXECUTION_MODE_REL): hook._sha(
            root / hook.request_common.EXECUTION_MODE_REL
        )
    }
    return required


def test_absolute_cli_and_selector_only_request(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(Path("tools/vector_approx_scalar_s1_request.py").resolve()), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "create-request" in completed.stdout

    hook.create_requests(tmp_path, REQUEST_ID, now=1000.0)
    request = json.loads((tmp_path / hook.REQUEST_REL).read_text())
    pull = json.loads((tmp_path / hook.PULL_REQUEST_REL).read_text())
    assert set(request) == hook.FIELDS
    assert request["action"] == hook.ACTION
    assert pull["action"] == hook.PULL_ACTION
    progress = json.loads(hook._request_progress_path(tmp_path, REQUEST_ID).read_text())
    assert progress["producer_request_id"] == REQUEST_ID
    assert len(progress["workers"]) == 6
    assert [row["worker_id"] for row in progress["workers"]] == [
        f"vec-approx-slot-{slot}" for slot in range(1, 7)
    ]


def test_request_bound_scalar_terminal_summary_uses_strict_ledger_counts(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "pulled" / REQUEST_ID
    _write(destination / "S1_VECTOR_APPROX_SCALAR_STATUS.json", {
        "request_id": REQUEST_ID,
        "status": "COMPLETE_BLOCKED",
    })
    fleet = destination / "fleet" / REQUEST_ID
    summary_path = fleet / "TTD_SHORT_summary.json"
    _write(summary_path, {
        "key": "TTD_SHORT",
        "ranked_exact_candidates": 2,
        "moved": 3,
        "inert": 2,
        "zero_trade": 0,
    })
    receipt = fleet / "campaign_receipt.json"
    _write(receipt, {
        "campaign_id": REQUEST_ID,
        "status": "BOUNDED_INCOMPLETE",
        "terminal_key_count": 1,
        "terminal_ledgers": [{
            "key": "TTD_SHORT",
            "strict_passed_cells": 5,
            "raw_rows": 5,
            "summary_sha256": hook._sha(summary_path),
        }],
    })

    stored = hook._publish_terminal_scalar_summary(
        tmp_path, destination, REQUEST_ID, now_epoch=1000.0
    )
    assert stored["request_id"] == REQUEST_ID
    assert stored["terminal_key_count"] == 1
    assert stored["keys_remaining"] == 5
    assert stored["strict_passed_cell_count"] == 5
    assert stored["ranked_exact_candidate_count"] == 2
    assert stored["authoritative_engine_pass_cell_count"] == 0
    assert json.loads(
        hook._request_terminal_summary_path(
            tmp_path, REQUEST_ID
        ).read_text()
    ) == stored


def test_fixed_cohort_is_the_six_audited_uncovered_keys() -> None:
    assert hook.KEYS == (
        "TTD_SHORT",
        "ACN_SHORT",
        "MRVL_LONG",
        "PWR_SHORT",
        "AMAT_SHORT",
        "VICR_SHORT",
    )
    assert hook.SYMBOLS == ("TTD", "ACN", "MRVL", "PWR", "AMAT", "VICR")
    assert hook.AUDITED_BLANK_CELL_COUNT == 1352


def test_mac_heartbeat_scalar_dispatch_is_request_gated_and_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = tmp_path / "VECTOR_APPROX_SCALAR_S1_REQUEST.json"
    log = tmp_path / "vector_approx_scalar_s1_dispatch.log"
    launches: list[tuple[list[str], dict[str, object]]] = []

    def popen(argv, **kwargs):
        launches.append((argv, kwargs))
        return object()

    monkeypatch.setattr(heartbeat, "BASE_PATH", str(tmp_path))
    monkeypatch.setattr(heartbeat, "VECTOR_APPROX_SCALAR_REQUEST", str(request))
    monkeypatch.setattr(heartbeat, "VECTOR_APPROX_SCALAR_LOG", str(log))
    monkeypatch.setattr(heartbeat.subprocess, "Popen", popen)

    heartbeat._launch_requested_vector_approx_scalar()
    assert launches == []

    request.write_text("{}", encoding="utf-8")
    heartbeat._launch_requested_vector_approx_scalar()
    assert len(launches) == 1
    argv, kwargs = launches[0]
    assert argv == [
        sys.executable,
        str(tmp_path / "tools/vector_approx_scalar_s1_request.py"),
        "dispatch",
    ]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


def test_mac_heartbeat_keeps_scalar_and_lifecycle_dispatch_independent() -> None:
    source = Path("mac_live_heartbeat.py").read_text()
    scalar = source[
        source.index("def _launch_requested_vector_approx_scalar") :
        source.index("def _launch_stale_vector_pull_watcher")
    ]
    main = source[source.index("def main()") :]
    assert "VECTOR_APPROX_SCALAR_REQUEST" in scalar
    assert "vector_approx_scalar_s1_request.py" in scalar
    assert '"dispatch"' in scalar
    assert "shell=True" not in scalar
    assert "_launch_requested_vector_discovery()" in main
    assert "_launch_requested_vector_approx_scalar()" in main
    assert "elif" not in main


def test_remote_argv_is_fixed_and_request_id_restricted() -> None:
    command = hook.remote_argv(REQUEST_ID)
    assert command[-1].endswith(
        f"tools/vector_approx_scalar_s1_request.py s1-run --request-id {REQUEST_ID}"
    )
    assert "run_vector_approx_pilot_fleet.py" not in command[-1]
    with pytest.raises(hook.Blocked, match="REQUEST_ID_INVALID"):
        hook.remote_argv("vas-bad;touch-x")


def test_pinned_pipeline_sources_match_reviewed_mac_bytes() -> None:
    for relative, expected in hook.PREREQUISITES.items():
        path = Path(relative)
        assert path.is_file(), relative
        assert hook._sha(path) == expected, relative


def test_s1_pipeline_precomputes_six_validates_three_profiles_and_launches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "s1"
    expected = _pipeline_root(root)
    protected_calls: list[int] = []

    def protected(_root):
        protected_calls.append(1)
        return {"protected_v8_real_engine_pass_rows": "2:stable"}

    monkeypatch.setattr(hook, "_protected_hashes", protected)
    free_bytes = 3 * 1024 * 1024 * 1024
    monkeypatch.setattr(
        hook.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=free_bytes),
    )
    commands: list[list[str]] = []
    command_lock = threading.Lock()

    def executor(argv, **_kwargs):
        with command_lock:
            commands.append(list(argv))
        if argv[1] == "backtest_v8_precompute.py":
            out = root / argv[argv.index("--out-dir") + 1]
            out.mkdir(parents=True)
            for symbol in argv[argv.index("--symbols") + 1].split(","):
                (out / f"{symbol}.npz").write_bytes(f"causal-{symbol}".encode())
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "tools/backtest_data_contract.py":
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"valid": True}) + "\n", ""
            )
        if argv[1] == "tools/run_vector_approx_pilot_fleet.py":
            out_root = root / argv[argv.index("--out-root") + 1]
            campaign_id = argv[argv.index("--campaign-id") + 1]
            out = out_root / campaign_id
            out.mkdir(parents=True)
            (out / "campaign_receipt.json").write_text(
                json.dumps(
                    {
                        "status": "COMPLETE_STRICT",
                        "keys": list(hook.KEYS),
                        "worker_slots": 6,
                        "terminal_key_count": 6,
                        "tier": "VEC_APPROX",
                        "run_class": "VECTOR_DISCOVERY",
                        "matrix_written": False,
                        "database_written": False,
                        "workbook_written": False,
                        "exact_store_written": False,
                        "live_config_written": False,
                        "canonical_artifacts_written": False,
                        "exact_completion_credit": False,
                    }
                )
            )
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    result = hook.run_s1_pipeline(
        root,
        REQUEST_ID,
        expected=expected,
        executor=executor,
        require_s1=False,
    )

    assert result["status"] == "COMPLETE_READY"
    assert result["fleet_launch_count"] == 1
    assert result["profiles"] == ["core", "ladder", "floor"]
    assert result["audited_blank_cell_count"] == 1352
    assert result["free_bytes_before_campaign"] == free_bytes
    assert result["free_bytes_after_precompute"] == free_bytes
    assert len(protected_calls) == 2
    precompute = [row for row in commands if row[1] == "backtest_v8_precompute.py"]
    audits = [row for row in commands if row[1] == "tools/backtest_data_contract.py"]
    fleets = [row for row in commands if row[1] == "tools/run_vector_approx_pilot_fleet.py"]
    assert len(precompute) == 1
    assert precompute[0][precompute[0].index("--workers") + 1] == "6"
    assert precompute[0][precompute[0].index("--symbols") + 1] == ",".join(hook.SYMBOLS)
    assert len(audits) == 18
    assert {
        row[row.index("--profile") + 1] for row in audits
    } == {"core", "ladder", "floor"}
    mrvl_audits = [
        row for row in audits
        if row[row.index("--symbol") + 1] == "MRVL"
    ]
    assert len(mrvl_audits) == 3
    assert {
        row[row.index("--profile") + 1] for row in mrvl_audits
    } == {"core", "ladder", "floor"}
    campaign = root / hook.REMOTE_CAMPAIGN_REL / REQUEST_ID
    launch_receipt = json.loads(
        (campaign / "S1_SINGLE_LAUNCH_RECEIPT.json").read_text()
    )
    npz_receipt = json.loads(
        (campaign / "causal_npz_build_receipt.json").read_text()
    )
    assert launch_receipt["free_bytes_before_campaign"] == free_bytes
    assert npz_receipt["free_bytes_after_precompute"] == free_bytes
    assert npz_receipt["post_precompute_capacity_status"] == "PASS"
    assert len(fleets) == 1
    assert "--allow-validated-causal-npz" in fleets[0]
    assert fleets[0][fleets[0].index("--keys") + 1] == ",".join(hook.KEYS)
    assert fleets[0][fleets[0].index("--workers") + 1] == "6"

    # A repeated S1 invocation returns the existing terminal and cannot launch.
    repeated = hook.run_s1_pipeline(
        root,
        REQUEST_ID,
        expected=expected,
        executor=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("campaign relaunched")
        ),
        require_s1=False,
    )
    assert repeated["status"] == "COMPLETE_READY"


def test_s1_pipeline_refuses_below_2gib_before_campaign_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "s1"
    free_bytes = hook.MIN_FREE_BYTES - 1
    commands: list[list[str]] = []
    monkeypatch.setattr(
        hook.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=free_bytes),
    )

    with pytest.raises(hook.Blocked, match="S1_FREE_SPACE_BELOW_2GIB") as raised:
        hook.run_s1_pipeline(
            root,
            REQUEST_ID,
            expected={},
            executor=lambda argv, **_kwargs: commands.append(list(argv)),
            require_s1=False,
        )

    assert raised.value.details == {
        "free_bytes_before_campaign": free_bytes,
        "required_free_bytes_before_campaign": hook.MIN_FREE_BYTES,
    }
    assert commands == []
    assert not (root / hook.REMOTE_CAMPAIGN_REL / REQUEST_ID).exists()


def test_s1_pipeline_refuses_below_1_5gib_after_precompute_before_audit_or_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "s1"
    expected = _pipeline_root(root)
    before_free = hook.MIN_FREE_BYTES + 1
    after_free = hook.MIN_POST_PRECOMPUTE_FREE_BYTES - 1
    observed_free = iter((before_free, after_free))
    monkeypatch.setattr(
        hook.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=next(observed_free)),
    )
    monkeypatch.setattr(
        hook,
        "_protected_hashes",
        lambda _root: {"protected_v8_real_engine_pass_rows": "2:stable"},
    )
    commands: list[list[str]] = []

    def executor(argv, **_kwargs):
        commands.append(list(argv))
        if argv[1] != "backtest_v8_precompute.py":
            raise AssertionError(f"unexpected post-capacity command: {argv}")
        out = root / argv[argv.index("--out-dir") + 1]
        out.mkdir(parents=True)
        for symbol in hook.SYMBOLS:
            (out / f"{symbol}.npz").write_bytes(
                f"causal-{symbol}".encode()
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(
        hook.Blocked, match="S1_FREE_SPACE_BELOW_1_5GIB_AFTER_NPZ"
    ) as raised:
        hook.run_s1_pipeline(
            root,
            REQUEST_ID,
            expected=expected,
            executor=executor,
            require_s1=False,
        )

    assert len(commands) == 1
    assert commands[0][1] == "backtest_v8_precompute.py"
    assert raised.value.details["free_bytes_before_campaign"] == before_free
    assert raised.value.details["free_bytes_after_precompute"] == after_free
    campaign = root / hook.REMOTE_CAMPAIGN_REL / REQUEST_ID
    receipt = json.loads(
        (campaign / "causal_npz_build_receipt.json").read_text()
    )
    status = json.loads(
        (campaign / "S1_VECTOR_APPROX_SCALAR_STATUS.json").read_text()
    )
    assert receipt["free_bytes_after_precompute"] == after_free
    assert receipt["post_precompute_capacity_status"] == "BLOCKED"
    assert status["status"] == "COMPLETE_BLOCKED"
    assert status["free_bytes_before_campaign"] == before_free
    assert status["free_bytes_after_precompute"] == after_free


def test_pull_watcher_pulls_progress_and_tree_on_twenty_second_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "mac"
    hook.create_requests(root, REQUEST_ID, now=1000.0)
    _write(root / hook.request_common.EXECUTION_MODE_REL, _mode())
    mode_hash = hook._sha(root / hook.request_common.EXECUTION_MODE_REL)
    monkeypatch.setattr(
        hook,
        "PREREQUISITES",
        {str(hook.request_common.EXECUTION_MODE_REL): mode_hash},
    )
    monkeypatch.setattr(
        hook,
        "_protected_hashes",
        lambda _root: {"protected_v8_real_engine_pass_rows": "2:stable"},
    )
    active_count = 0
    progress_count = 0
    sleeps: list[float] = []

    def progress_payload(epoch: float) -> dict:
        payload = matrix_live_progress.empty_snapshot(now_epoch=epoch)
        payload["producer_request_id"] = REQUEST_ID
        payload["vector_updated_at_epoch"] = epoch
        payload["vector_updated_at"] = f"epoch-{epoch}"
        for slot, row in enumerate(payload["workers"], 1):
            row.update(
                {
                    "worker_id": f"vec-approx-slot-{slot}",
                    "run_class": "VECTOR_DISCOVERY",
                    "accepted_cells_per_minute": 0,
                }
            )
        return payload

    def runner(argv, **_kwargs):
        nonlocal active_count, progress_count
        if argv[0] == hook.SSH[0]:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        destination = Path(argv[-1].rstrip("/"))
        source = str(argv[-2])
        if source.endswith(str(hook.request_common.EXECUTION_MODE_REL)):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((root / hook.request_common.EXECUTION_MODE_REL).read_bytes())
        elif source.endswith(str(hook.PROGRESS_REL)):
            progress_count += 1
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(progress_payload(1000 + progress_count)))
        elif str(hook.REMOTE_CAMPAIGN_REL / REQUEST_ID) in source:
            if "-azc" in argv:
                pass
            else:
                active_count += 1
            destination.mkdir(parents=True, exist_ok=True)
            if active_count >= 2:
                (destination / "S1_VECTOR_APPROX_SCALAR_STATUS.json").write_text(
                    json.dumps(
                        {
                            "status": "COMPLETE_READY",
                            "request_id": REQUEST_ID,
                        }
                    )
                )
                receipt = destination / "fleet" / REQUEST_ID / "campaign_receipt.json"
                receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt.write_text(json.dumps({"status": "COMPLETE_STRICT"}))
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    result = hook.pull_watch(
        root,
        REQUEST_ID,
        runner=runner,
        sleep=lambda seconds: sleeps.append(seconds),
        now=lambda: 1000.0,
    )

    assert result == 0
    assert active_count == 2
    assert progress_count == 2
    assert sleeps == [20]
    pulled = root / hook.LOCAL_PULL_REL / REQUEST_ID
    tree = json.loads((pulled / "MAC_PULL_RECEIPT.json").read_text())
    assert tree["npz_excluded"] is True
    assert tree["file_count"] >= 2
    request_progress = json.loads(
        hook._request_progress_path(root, REQUEST_ID).read_text()
    )
    assert request_progress["producer_request_id"] == REQUEST_ID
    pull_status = json.loads(
        hook._request_pull_status_path(root, REQUEST_ID).read_text()
    )
    assert pull_status["status"] == "COMPLETE"
    assert pull_status["pull_interval_seconds"] == 20
