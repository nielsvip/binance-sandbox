from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from test_matrix_resume_gate_mu_lineage import _build_package
from tools import build_candidate_global_uniqueness_receipt as builder
from tools import matrix_resume_gate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _pass_row(
    *,
    key: str,
    param: str,
    value: object,
    metric: float,
    fingerprint: str,
) -> dict[str, object]:
    return {
        "key": key,
        "param": param,
        "value_json": value,
        "status": "MOVED",
        "result_uniqueness_status": "PASS",
        "delta_gain_mo_vs_bh_approx": metric,
        "behavior_fingerprint": fingerprint,
        "trades": 2,
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    root = tmp_path / "repo"
    root.mkdir()
    global_shape = _build_package(root)
    campaign = root / str(
        global_shape["mu_materialization_lineage"]["campaign_receipt_path"]
    )
    overlay = root / "data/reports/non_mu_existing.jsonl"
    existing_raw_pass = _pass_row(
        key="NVDA_LONG",
        param="alpha",
        value=1,
        metric=2.5,
        fingerprint="nvda-alpha-1",
    )
    existing_raw_pass.pop("result_uniqueness_status")
    _write_jsonl(
        overlay,
        [
            existing_raw_pass,
            {
                "key": "VT_SHORT",
                "param": "unsupported_adapter",
                "value_json": False,
                "status": "DATA_UNAVAILABLE",
                "result_uniqueness_status": ("NOT_APPLICABLE_DATA_UNAVAILABLE"),
            },
        ],
    )
    protected = {
        root / builder.CANONICAL_RELATIVE: b'{"canonical":"preserve"}\n',
        root / "data/reports/SWITCH_MATRIX_TRB.xlsx": b"workbook sentinel",
        root / "data/param_results_stocks.db": b"database sentinel",
        root / "live_config.json": b'{"live":"preserve"}\n',
        root / "backtest_v8_engine.py": b"# exact engine sentinel\n",
    }
    for path, payload in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    output = root / "data/reports/candidates/global.json"
    return root, campaign, overlay, global_shape


def test_builds_candidate_only_and_binds_every_staged_source(tmp_path: Path) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    output = root / "data/reports/candidates/global.json"
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    result = builder.build_candidate(
        root=root,
        mu_campaign_receipt=campaign,
        non_mu_overlays=[overlay],
        output=output,
    )

    receipt = json.loads(output.read_text())
    assert result["status"] == "PASS"
    assert result["candidate_sha256"] == _sha(output)
    assert receipt["input_rows"] == 828
    assert receipt["passed_numeric_overlays"] == 2
    assert receipt["data_unavailable_rows"] == 826
    assert receipt["quarantined_numeric_overlays"] == 0
    assert receipt["metric_collision_groups"] == 0
    assert receipt["fingerprint_collision_groups"] == 0
    assert receipt["strict_audit"]["allowed_plateau_groups"] == 0
    assert receipt["engine_cells_modified"] is False
    assert receipt["canonical_receipt_written"] is False
    assert receipt["matrix_workbook_db_live_config_written"] is False
    roles = {binding["role"] for binding in receipt["source_bindings"]}
    assert roles == {
        "MU_CAMPAIGN_RECEIPT",
        "MU_RAW_LEDGER",
        "MU_AUDITED_LEDGER",
        "MU_PORTABLE_LEDGER",
        "MU_UNIQUENESS_AUDIT",
        "NON_MU_OVERLAY_1",
    }
    for binding in receipt["source_bindings"]:
        bound = root / binding["path"]
        assert binding["sha256"] == _sha(bound)
    assert receipt["source_ledger_count"] == 2
    assert receipt["source_ledger_sha256"][str(overlay.relative_to(root))] == _sha(
        overlay
    )
    assert matrix_resume_gate.uniqueness_passes(
        receipt, matrix_resume_gate.FULL_C5_CAMPAIGN
    ) == (True, [])
    assert matrix_resume_gate.validate_mu_materialization_lineage(root, receipt) == []

    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path != output
    }
    assert after == before
    assert (root / builder.CANONICAL_RELATIVE).read_bytes() == (
        b'{"canonical":"preserve"}\n'
    )


def test_duplicate_metric_and_fingerprint_block_without_output(tmp_path: Path) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    _write_jsonl(
        overlay,
        [
            _pass_row(
                key="NVDA_LONG",
                param="alpha",
                value=1,
                metric=2.5,
                fingerprint="broadcast",
            ),
            _pass_row(
                key="NVDA_LONG",
                param="beta",
                value=2,
                metric=2.5,
                fingerprint="broadcast",
            ),
        ],
    )
    output = root / "data/reports/candidates/collision.json"

    with pytest.raises(builder.CandidateBlocked, match="STRICT_GLOBAL_UNIQUENESS"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=output,
        )

    assert not output.exists()


def test_bounded_pair_plateau_is_still_forbidden(tmp_path: Path) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    rows = [
        _pass_row(
            key="NVDA_LONG",
            param="axis",
            value=index,
            metric=10.0 if index < 2 else 10.0 + index,
            fingerprint="pair" if index < 2 else f"unique-{index}",
        )
        for index in range(5)
    ]
    _write_jsonl(overlay, rows)
    output = root / "data/reports/candidates/plateau.json"

    with pytest.raises(builder.CandidateBlocked, match="allowed_plateau_groups.*1"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=output,
        )

    assert not output.exists()


def test_non_mu_source_cannot_smuggle_stale_mu_row(tmp_path: Path) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    _write_jsonl(
        overlay,
        [
            _pass_row(
                key="MU_LONG",
                param="stale",
                value=1,
                metric=7.0,
                fingerprint="stale-mu",
            )
        ],
    )
    output = root / "data/reports/candidates/stale-mu.json"

    with pytest.raises(builder.CandidateBlocked, match="NON_MU_SOURCE_CONTAINS_MU"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=output,
        )

    assert not output.exists()


def test_tampered_staged_artifact_blocks_before_output(tmp_path: Path) -> None:
    root, campaign_path, overlay, _global_shape = _fixture(tmp_path)
    campaign = json.loads(campaign_path.read_text())
    portable = root / campaign["portable_key_path"]
    portable.write_text(portable.read_text() + "\n", encoding="utf-8")
    output = root / "data/reports/candidates/tampered.json"

    with pytest.raises(
        builder.CandidateBlocked, match="MU_PORTABLE_LEDGER_HASH_MISMATCH"
    ):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign_path,
            non_mu_overlays=[overlay],
            output=output,
        )

    assert not output.exists()


def test_source_drift_during_gate_blocks_before_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    output = root / "data/reports/candidates/drift.json"
    real_validator = matrix_resume_gate.validate_mu_materialization_lineage

    def validate_then_drift(
        checked_root: Path, receipt: dict[str, object]
    ) -> list[str]:
        reasons = real_validator(checked_root, receipt)
        overlay.write_bytes(b"concurrent overlay drift\n")
        return reasons

    monkeypatch.setattr(
        builder.matrix_resume_gate,
        "validate_mu_materialization_lineage",
        validate_then_drift,
    )

    with pytest.raises(builder.CandidateBlocked, match="SOURCE_HASH_DRIFT"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=output,
        )

    assert not output.exists()


def test_refuses_canonical_or_existing_output(tmp_path: Path) -> None:
    root, campaign, overlay, _global_shape = _fixture(tmp_path)
    canonical = root / builder.CANONICAL_RELATIVE

    with pytest.raises(builder.CandidateBlocked, match="CANONICAL_TARGET"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=canonical,
        )

    existing = root / "data/reports/existing-candidate.json"
    existing.write_text("preserve me", encoding="utf-8")
    with pytest.raises(builder.CandidateBlocked, match="OUTPUT_ALREADY_EXISTS"):
        builder.build_candidate(
            root=root,
            mu_campaign_receipt=campaign,
            non_mu_overlays=[overlay],
            output=existing,
        )
    assert existing.read_text() == "preserve me"
