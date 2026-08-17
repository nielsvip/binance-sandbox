from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tools import audit_vector_cell_contract as uniqueness
from tools import run_vector_approx_pilot_fleet as fleet


def _args(tmp_path: Path, campaign_id: str = "vas-test"):
    npz = tmp_path / "npz"
    npz.mkdir(exist_ok=True)
    for key in fleet.DEFAULT_KEYS:
        symbol = key.rsplit("_", 1)[0]
        (npz / f"{symbol}.npz").write_bytes(key.encode())
    return fleet.parse_args(
        [
            "--npz-dir",
            str(npz),
            "--out-root",
            str(tmp_path / "campaigns"),
            "--campaign-id",
            campaign_id,
            "--progress-snapshot",
            str(tmp_path / "progress.json"),
            "--round-seconds",
            "1",
            "--max-rounds",
            "1",
        ]
    )


def _write_key_artifacts(
    out_dir: Path,
    key: str,
    *,
    duplicate: bool = False,
    causal_hash: str | None = None,
) -> None:
    rows = [
        {
            "schema_version": 1,
            "tier": "VEC_APPROX",
            "key": key,
            "param": "PARAM_A",
            "value_json": "1",
            "status": "MOVED",
            "delta_gain_mo_vs_bh_approx": 1.25,
            "behavior_fingerprint": f"fp-{key}" if not duplicate else "same",
            "trades": 2,
            "exact_completion_credit": False,
            "engine_ranking_allowed": False,
            "db_engine_write_allowed": False,
            "promotion_allowed": False,
            "live_config_write_allowed": False,
            "validated_causal_npz_sha256": causal_hash,
        }
    ]
    if duplicate:
        rows.append(
            {
                **rows[0],
                "param": "PARAM_B",
                "value_json": "2",
            }
        )
    ledger = out_dir / f"{key}.jsonl"
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    latest, _ = uniqueness.latest_rows([ledger])
    _audited, report = uniqueness.audit_rows(latest)
    uniqueness_path = out_dir / f"{key}_uniqueness_report.json"
    uniqueness_path.write_text(json.dumps(report, sort_keys=True) + "\n")
    data_contract = {"calculation_allowed": True}
    if causal_hash is not None:
        data_contract.update(
            {
                "data_contract_status": "PASS_VALIDATED_CAUSAL_NPZ",
                "causal_npz_contract": {
                    "calculation_allowed": True,
                    "npz_sha256": causal_hash,
                    "profiles_required": ["floor", "core", "ladder"],
                    "profiles": {
                        profile: {"valid": True}
                        for profile in ("floor", "core", "ladder")
                    },
                },
            }
        )
    (out_dir / f"{key}_data_contract.json").write_text(
        json.dumps(data_contract) + "\n"
    )
    (out_dir / f"{key}_exact_queue_ranked.json").write_text(
        json.dumps({"key": key, "candidates": []}) + "\n"
    )
    summary = {
        "schema_version": 1,
        "key": key,
        "tier": "VEC_APPROX",
        "total_approx_eligible_cells": len(rows),
        "remaining_after_run": 0,
        "failures": 0,
        "campaign_must_stop": report["campaign_must_stop"],
        "uniqueness_contract": report,
        "uniqueness_report": str(uniqueness_path),
        "exact_completion_credit": False,
        "engine_ranking_allowed": False,
        "db_engine_write_allowed": False,
        "promotion_allowed": False,
        "live_config_write_allowed": False,
    }
    (out_dir / f"{key}_summary.json").write_text(
        json.dumps(summary, sort_keys=True) + "\n"
    )


def test_exactly_six_disjoint_keys_are_required() -> None:
    assert fleet.parse_keys(",".join(fleet.DEFAULT_KEYS)) == fleet.DEFAULT_KEYS
    with pytest.raises(ValueError, match="EXACTLY_SIX"):
        fleet.parse_keys("MU_LONG,NVDA_LONG")
    with pytest.raises(ValueError, match="DISJOINT"):
        fleet.parse_keys(
            "MU_LONG,MU_LONG,VT_LONG,TTD_SHORT,ACN_SHORT,LAC_SHORT"
        )


def test_causal_npz_opt_in_is_manifest_bound_and_passed_only_when_enabled(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    disabled = fleet.child_command(
        slot=3,
        key="VT_LONG",
        out_dir=tmp_path / "out",
        args=args,
    )
    assert "--allow-validated-causal-npz" not in disabled
    disabled_contract = fleet.build_contract(args, fleet.DEFAULT_KEYS)
    assert disabled_contract["allow_validated_causal_npz"] is False

    args.allow_validated_causal_npz = True
    enabled = fleet.child_command(
        slot=3,
        key="VT_LONG",
        out_dir=tmp_path / "out",
        args=args,
    )
    assert enabled[-1] == "--allow-validated-causal-npz"
    enabled_contract = fleet.build_contract(args, fleet.DEFAULT_KEYS)
    assert enabled_contract["allow_validated_causal_npz"] is True
    assert (
        fleet.resume_contract(enabled_contract)
        != fleet.resume_contract(disabled_contract)
    )


def test_causal_terminal_requires_all_profiles_and_matching_npz_hash(
    tmp_path: Path,
) -> None:
    key = "MU_LONG"
    _write_key_artifacts(tmp_path, key, causal_hash="causal-sha")

    terminal = fleet.validate_terminal_key(
        tmp_path,
        key,
        expected_npz_sha256="causal-sha",
        allow_validated_causal_npz=True,
    )
    assert terminal["status"] == "COMPLETE_STRICT"

    with pytest.raises(ValueError, match="CAUSAL_NPZ_CONTRACT_INVALID"):
        fleet.validate_terminal_key(
            tmp_path,
            key,
            expected_npz_sha256="different-sha",
            allow_validated_causal_npz=True,
        )
    with pytest.raises(ValueError, match="UNAUTHORIZED_CAUSAL_NPZ"):
        fleet.validate_terminal_key(
            tmp_path,
            key,
            expected_npz_sha256="causal-sha",
            allow_validated_causal_npz=False,
        )


def test_six_slot_campaign_writes_versioned_receipt_and_5077_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path)
    telemetry: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        fleet.matrix_live_progress,
        "update_worker",
        lambda _path, slot, **fields: telemetry.append((slot, fields)),
    )

    def fake_child(**kwargs):
        _write_key_artifacts(kwargs["out_dir"], kwargs["key"])
        return fleet.ChildResult(0)

    code, receipt = fleet.run_campaign(args, child_runner=fake_child)

    out = args.out_root / args.campaign_id
    assert code == 0
    assert receipt["status"] == "COMPLETE_STRICT"
    assert receipt["terminal_key_count"] == 6
    assert receipt["worker_slots"] == 6
    assert out.name == "vas-test"
    assert (out / "campaign_manifest.json").is_file()
    assert (out / "campaign_status.json").is_file()
    assert (out / "campaign_receipt.json").is_file()
    assert receipt["matrix_written"] is False
    assert receipt["database_written"] is False
    assert receipt["workbook_written"] is False
    assert receipt["exact_store_written"] is False
    assert receipt["live_config_written"] is False
    assert {slot for slot, _fields in telemetry} == set(range(1, 7))
    assert all(
        fields["run_class"] == "VECTOR_DISCOVERY"
        for _slot, fields in telemetry
    )
    terminal_updates = [
        fields for _slot, fields in telemetry
        if fields.get("status") == "COMPLETE_STRICT"
    ]
    assert len(terminal_updates) == 6
    assert all(fields["worker_cells_remaining"] == 0 for fields in terminal_updates)


def test_resume_reuses_only_hash_valid_terminal_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _args(tmp_path, "vas-first")
    monkeypatch.setattr(
        fleet.matrix_live_progress, "update_worker", lambda *_a, **_k: None
    )

    def complete_child(**kwargs):
        _write_key_artifacts(kwargs["out_dir"], kwargs["key"])
        return fleet.ChildResult(0)

    code, _receipt = fleet.run_campaign(first, child_runner=complete_child)
    assert code == 0

    second = _args(tmp_path, "vas-second")
    second.resume_from = first.out_root / first.campaign_id

    def must_not_run(**_kwargs):
        raise AssertionError("valid terminal ledger was recomputed")

    code, receipt = fleet.run_campaign(second, child_runner=must_not_run)

    assert code == 0
    assert receipt["resumed_key_count"] == 6
    assert receipt["new_terminal_keys"] == []
    assert all(row["state"] == "RESUMED_COMPLETE" for row in receipt["slots"])

    opt_in = _args(tmp_path, "vas-opt-in")
    opt_in.resume_from = first.out_root / first.campaign_id
    opt_in.allow_validated_causal_npz = True
    with pytest.raises(ValueError, match="RESUME_MANIFEST_CONTRACT_MISMATCH"):
        fleet.run_campaign(opt_in, child_runner=must_not_run)


def test_duplicate_collision_stops_campaign_and_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path, "vas-duplicate")
    telemetry: list[tuple[int, str]] = []
    monkeypatch.setattr(
        fleet.matrix_live_progress,
        "update_worker",
        lambda _path, slot, **fields: telemetry.append(
            (slot, str(fields.get("status")))
        ),
    )
    first_key = fleet.DEFAULT_KEYS[0]

    def duplicate_child(**kwargs):
        if kwargs["key"] == first_key:
            _write_key_artifacts(
                kwargs["out_dir"], kwargs["key"], duplicate=True
            )
            return fleet.ChildResult(2)
        kwargs["stop_event"].wait(1)
        return fleet.ChildResult(1, stopped_by_campaign=True)

    code, receipt = fleet.run_campaign(args, child_runner=duplicate_child)

    assert code == 2
    assert receipt["status"] == "STOP_DUPLICATE_RESULTS"
    assert receipt["duplicate_stop"]["triggered"] is True
    assert receipt["duplicate_stop"]["key"] == first_key
    assert (1, "STOP_DUPLICATE_RESULTS") in telemetry
    status = json.loads(
        (args.out_root / args.campaign_id / "campaign_status.json").read_text()
    )
    assert status["duplicate_stop"]["triggered"] is True
    assert any(
        row["state"] == "STOPPED_BY_DUPLICATE" for row in status["slots"][1:]
    )
