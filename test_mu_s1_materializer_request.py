from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools import mu_s1_materializer_request as hook


REQUEST_ID = "mu-20260802t020000z-deadbeef"


def _request(epoch: float = 990.0) -> dict[str, object]:
    return {
        "schema": hook.REQUEST_SCHEMA,
        "action": hook.ACTION,
        "request_id": REQUEST_ID,
        "requested_at_epoch": epoch,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _execution_mode() -> dict[str, object]:
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


def test_request_is_not_a_command_envelope() -> None:
    assert hook.validate_request(_request(), now=1000.0)["request_id"] == REQUEST_ID

    for field, value in (
        ("command", "rm -rf /"),
        ("args", ["--out-dir", "data/reports/canonical"]),
        ("host", "somewhere-else"),
        ("matrix_path", "/tmp/fake"),
    ):
        payload = {**_request(), field: value}
        with pytest.raises(hook.RequestBlocked, match="REQUEST_FIELDS_NOT_EXACT"):
            hook.validate_request(payload, now=1000.0)


def test_request_creator_writes_only_atomic_allowlisted_payload(tmp_path: Path) -> None:
    path = hook.create_request(tmp_path, REQUEST_ID, now=990.0)

    assert json.loads(path.read_text()) == _request()
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_sync_must_be_complete_fresh_and_newer_than_request() -> None:
    valid = {
        "schema": hook.SYNC_SCHEMA,
        "status": "PASS",
        "phase": "COMPLETE",
        "completed_at_epoch": 995.0,
    }
    hook.validate_fresh_sync(valid, request_epoch=990.0, now=1000.0)

    for changed, reason in (
        ({"status": "RUNNING"}, "OVERALL_SYNC_NOT_PASS"),
        ({"phase": "SOURCE_VERIFY"}, "OVERALL_SYNC_NOT_PASS"),
        ({"completed_at_epoch": 989.0}, "OVERALL_SYNC_PREDATES_REQUEST"),
        ({"completed_at_epoch": 600.0}, "OVERALL_SYNC_PREDATES_REQUEST"),
    ):
        with pytest.raises(hook.RequestBlocked, match=reason):
            hook.validate_fresh_sync(
                {**valid, **changed}, request_epoch=990.0, now=1000.0
            )


def test_dispatch_waits_without_launching_until_overall_sync_passes(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / hook.REQUEST_REL, _request())
    _write_json(
        tmp_path / hook.SYNC_STATUS_REL,
        {
            "schema": hook.SYNC_SCHEMA,
            "status": "RUNNING",
            "phase": "SOURCE_VERIFY",
        },
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("SSH must not launch")

    assert hook.dispatch(tmp_path, now=1000.0, ssh_runner=forbidden) == 0
    status = json.loads((tmp_path / hook.STATUS_REL).read_text())
    assert status["status"] == "WAITING_SYNC"
    assert status["matrix_pause_removed"] is False
    assert status["canonical_publish_attempted"] is False
    assert (tmp_path / hook.REQUEST_REL).is_file()
    assert not list((tmp_path / hook.STATUS_REL.parent).glob("*.tmp"))


def test_dispatch_uses_only_fixed_ssh_command_after_fresh_pass(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / hook.REQUEST_REL, _request())
    _write_json(
        tmp_path / hook.SYNC_STATUS_REL,
        {
            "schema": hook.SYNC_SCHEMA,
            "status": "PASS",
            "phase": "COMPLETE",
            "completed_at_epoch": 995.0,
        },
    )
    observed: list[list[str]] = []

    def fixed_ssh(argv, **kwargs):
        observed.append(argv)
        payload = {
            "status": "COMPLETE_BLOCKED",
            "request_id": REQUEST_ID,
            "staged_out_dir": f"{hook.STAGING_REL}/{REQUEST_ID}",
            "campaign_receipt": None,
            "materializer_returncode": 2,
        }
        return subprocess.CompletedProcess(argv, 2, json.dumps(payload) + "\n", "")

    assert hook.dispatch(tmp_path, now=1000.0, ssh_runner=fixed_ssh) == 2
    assert observed == [hook.remote_ssh_argv(REQUEST_ID)]
    remote_command = observed[0][-1]
    assert "run_mu_826_vector_amber.py" not in remote_command
    assert "mu_s1_materializer_request.py s1-run" in remote_command
    assert "--request-id " + REQUEST_ID in remote_command
    status = json.loads((tmp_path / hook.STATUS_REL).read_text())
    assert status["status"] == "COMPLETE_BLOCKED"
    assert status["canonical_publish_attempted"] is False


def test_s1_guard_hashes_inputs_and_writes_only_request_staging(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s1"
    _write_json(root / hook.EXECUTION_MODE_REL, _execution_mode())
    expected: dict[str, str] = {}
    originals: dict[str, bytes] = {}
    for relative, payload in (
        ("tools/run_mu_826_vector_amber.py", b"runner"),
        ("data/matrix_npz/stocks_repaired_20260725_c2/MU.npz", b"npz"),
        (
            "data/reports/full_trb_blank_matrix_vec_approx_20260801/MU_LONG.jsonl",
            b"raw",
        ),
        ("data/reports/SWITCH_MATRIX_TRB.csv.gz", b"matrix"),
        ("data/param_results_stocks.db", b"database"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        originals[relative] = payload
        expected[relative] = hashlib.sha256(payload).hexdigest()
    mode_path = root / hook.EXECUTION_MODE_REL
    expected[str(hook.EXECUTION_MODE_REL)] = hashlib.sha256(
        mode_path.read_bytes()
    ).hexdigest()
    observed: list[list[str]] = []

    def materializer(argv, **kwargs):
        observed.append(argv)
        out = root / argv[argv.index("--out-dir") + 1]
        (out / "campaign_receipt.json").write_text(
            json.dumps(
                {
                    "safe_to_merge": True,
                    "campaign_must_stop": False,
                    "quarantined_numeric_rows": 0,
                    "prior_residual_conservation_ok": True,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = hook.run_s1_once(
        root,
        REQUEST_ID,
        expected=expected,
        executor=materializer,
        require_s1_identity=False,
    )

    assert result["status"] == "COMPLETE_READY"
    assert observed == [
        [
            hook.S1_PYTHON,
            "tools/run_mu_826_vector_amber.py",
            "--npz-dir",
            "data/matrix_npz/stocks_repaired_20260725_c2",
            "--out-dir",
            f"{hook.STAGING_REL}/{REQUEST_ID}",
            "--start",
            "2025-07-21",
            "--matrix-path",
            "data/reports/SWITCH_MATRIX_TRB.csv.gz",
            "--db-path",
            "data/param_results_stocks.db",
        ]
    ]
    assert not (root / hook.MATRIX_PAUSE_REL).exists()
    for relative, payload in originals.items():
        assert (root / relative).read_bytes() == payload
    stage = root / hook.STAGING_REL / REQUEST_ID
    assert (stage / "S1_REQUEST_STATUS.json").is_file()
    assert not (root / "data/reports/VECTOR_OVERLAY_UNIQUENESS_AUDIT.json").exists()


def test_s1_guard_fails_before_stage_on_hash_drift(tmp_path: Path) -> None:
    root = tmp_path / "s1"
    _write_json(root / hook.EXECUTION_MODE_REL, _execution_mode())
    path = root / "input"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"wrong")
    with pytest.raises(hook.RequestBlocked, match="HASH_MISMATCH"):
        hook.run_s1_once(
            root,
            REQUEST_ID,
            expected={"input": hashlib.sha256(b"expected").hexdigest()},
            executor=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("must not execute")
            ),
            require_s1_identity=False,
        )

    assert not (root / hook.STAGING_REL).exists()


def test_s1_guard_rejects_symlinked_prerequisite_parent(tmp_path: Path) -> None:
    root = tmp_path / "s1"
    _write_json(root / hook.EXECUTION_MODE_REL, _execution_mode())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "input").write_bytes(b"expected")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(hook.RequestBlocked, match="NOT_REGULAR"):
        hook.run_s1_once(
            root,
            REQUEST_ID,
            expected={"linked/input": hashlib.sha256(b"expected").hexdigest()},
            require_s1_identity=False,
        )


def test_s1_guard_requires_unpaused_vector_lifecycle_mode(tmp_path: Path) -> None:
    root = tmp_path / "s1"
    _write_json(root / hook.EXECUTION_MODE_REL, _execution_mode())
    input_path = root / "input"
    input_path.write_bytes(b"expected")
    expected = {"input": hashlib.sha256(b"expected").hexdigest()}
    pause = root / hook.MATRIX_PAUSE_REL
    pause.write_text("stale pause\n")

    with pytest.raises(hook.RequestBlocked, match="GLOBAL_PAUSE_FILE_PRESENT"):
        hook.run_s1_once(
            root,
            REQUEST_ID,
            expected=expected,
            require_s1_identity=False,
        )

    pause.unlink()
    bad_mode = _execution_mode()
    bad_mode["legacy_scalar_watchdogs"] = "ACTIVE"
    _write_json(root / hook.EXECUTION_MODE_REL, bad_mode)
    with pytest.raises(hook.RequestBlocked, match="MATRIX_EXECUTION_MODE_INVALID"):
        hook.run_s1_once(
            root,
            REQUEST_ID,
            expected=expected,
            require_s1_identity=False,
        )


def test_mac_heartbeat_only_invokes_fixed_dispatcher() -> None:
    source = Path("mac_live_heartbeat.py").read_text(encoding="utf-8")
    function = source[
        source.index("def _launch_requested_mu_materializer") : source.index(
            "def _launch_requested_vector_discovery"
        )
    ]
    assert '"dispatch"' in function
    assert "MU_S1_MATERIALIZER_REQUEST.json" not in function
    assert "os.unlink" not in function
    assert "MATRIX_WORKERS_PAUSED" not in function
    assert "shell=True" not in function


def test_overall_sync_atomically_publishes_complete_pass_receipt() -> None:
    source = Path("tools/always_connected_sync.sh").read_text(encoding="utf-8")

    assert '[[ "$status" == PASS ]]' in source
    assert '"completed_at_epoch":%s' in source
    pass_index = source.index("write_sync_status PASS")
    assert pass_index < source.index("SYNC_COMPLETE=true", pass_index)
    assert 'mv "$temp" "$SYNC_STATUS"' in source
