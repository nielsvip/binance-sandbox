from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import publish_vector_overlay_uniqueness_receipt as publisher


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    root = tmp_path / "repo"
    reports = root / "data/reports"
    overlay_dir = reports / "full_trb_blank_matrix_vec_approx_20260801"
    overlay_dir.mkdir(parents=True)
    canonical = reports / "VECTOR_OVERLAY_UNIQUENESS_AUDIT.json"
    matrix = reports / "SWITCH_MATRIX_TRB.csv.gz"
    db = root / "data/param_results_stocks.db"
    overlay = overlay_dir / "vector_overlay_index.jsonl"
    engine = root / "backtest_v8_engine.py"
    candidate = reports / "candidate_global_uniqueness.json"
    canonical.write_text('{"old":true}\n')
    matrix.write_bytes(b"canonical matrix bytes")
    db.write_bytes(b"sqlite evidence bytes")
    overlay.write_bytes(b'{"overlay":1}\n')
    engine.write_text("# preserved backtest_v8 engine\n")
    candidate.write_text(
        json.dumps(
            {
                "contract": (
                    "STRICT_PER_KEY_UNIQUE_METRIC_AND_ACTION_NO_DUPLICATES"
                ),
                "current_engine_campaign": "stocks_repaired_20260730_c5",
                "matrix_numeric_fill_requires_pass": True,
                "input_rows": 826,
                "passed_numeric_overlays": 10,
                "data_unavailable_rows": 816,
                "quarantined_numeric_overlays": 0,
                "metric_collision_groups": 0,
                "fingerprint_collision_groups": 0,
                "engine_cells_modified": False,
                "mu_materialization_lineage": {"schema": "fixture"},
            },
            sort_keys=True,
        )
        + "\n"
    )
    return {
        "root": root,
        "canonical": canonical,
        "matrix": matrix,
        "db": db,
        "overlay": overlay,
        "engine": engine,
        "candidate": candidate,
        "old_sha": _sha(canonical),
        "candidate_sha": _sha(candidate),
        "matrix_sha": _sha(matrix),
        "db_sha": _sha(db),
        "overlay_sha": _sha(overlay),
        "engine_sha": _sha(engine),
    }


def _publish_args(fixture: dict[str, Path | str]) -> dict:
    return {
        "root": fixture["root"],
        "candidate": fixture["candidate"],
        "expected_candidate_sha256": fixture["candidate_sha"],
        "expected_old_canonical_sha256": fixture["old_sha"],
        "expected_matrix_sha256": fixture["matrix_sha"],
        "expected_db_sha256": fixture["db_sha"],
        "expected_overlay_sha256": fixture["overlay_sha"],
        "expected_engine_sha256": fixture["engine_sha"],
    }


def _passing_gates(monkeypatch) -> list[str]:
    calls: list[str] = []

    def uniqueness(receipt, campaign):
        calls.append(f"uniqueness:{campaign}")
        return True, []

    def materialization(root, receipt):
        calls.append(f"materialization:{root}")
        return []

    monkeypatch.setattr(
        publisher.matrix_resume_gate, "uniqueness_passes", uniqueness
    )
    monkeypatch.setattr(
        publisher.matrix_resume_gate,
        "validate_mu_materialization_lineage",
        materialization,
    )
    return calls


def test_receipt_only_cas_archives_old_and_preserves_all_bound_sources(
    monkeypatch, tmp_path
) -> None:
    fixture = _fixture(tmp_path)
    calls = _passing_gates(monkeypatch)
    before = {
        name: fixture[name].read_bytes()
        for name in ("matrix", "db", "overlay", "engine")
    }

    result = publisher.publish(**_publish_args(fixture))

    assert fixture["canonical"].read_bytes() == fixture["candidate"].read_bytes()
    archive = Path(result["archive"])
    assert archive.read_bytes() == b'{"old":true}\n'
    assert result["old_canonical_sha256"] == fixture["old_sha"]
    assert result["new_canonical_sha256"] == fixture["candidate_sha"]
    assert result["matrix_db_overlay_written"] is False
    assert calls[0].startswith("uniqueness:")
    assert calls[1].startswith("materialization:")
    for name, payload in before.items():
        assert fixture[name].read_bytes() == payload


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"expected_old_canonical_sha256": "0" * 64}, "OLD_CANONICAL"),
        ({"expected_matrix_sha256": "0" * 64}, "BOUND_HASH_MISMATCH"),
        ({"expected_db_sha256": "0" * 64}, "BOUND_HASH_MISMATCH"),
        ({"expected_overlay_sha256": "0" * 64}, "BOUND_HASH_MISMATCH"),
        ({"expected_engine_sha256": "0" * 64}, "BOUND_HASH_MISMATCH"),
    ],
)
def test_cas_or_bound_hash_mismatch_never_replaces_or_archives(
    monkeypatch, tmp_path, override, reason
) -> None:
    fixture = _fixture(tmp_path)
    _passing_gates(monkeypatch)
    original = fixture["canonical"].read_bytes()
    args = _publish_args(fixture)
    args.update(override)

    with pytest.raises(publisher.PublishBlocked, match=reason):
        publisher.publish(**args)

    assert fixture["canonical"].read_bytes() == original
    assert not (fixture["root"] / publisher.ARCHIVE_RELATIVE).exists()


def test_materialization_gate_blocks_without_surface_replacement(
    monkeypatch, tmp_path
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(
        publisher.matrix_resume_gate,
        "uniqueness_passes",
        lambda receipt, campaign: (True, []),
    )
    monkeypatch.setattr(
        publisher.matrix_resume_gate,
        "validate_mu_materialization_lineage",
        lambda root, receipt: ["MU_PRIOR_RESIDUAL_UNACCOUNTED"],
    )
    original = fixture["canonical"].read_bytes()

    with pytest.raises(publisher.PublishBlocked, match="MU_PRIOR_RESIDUAL"):
        publisher.publish(**_publish_args(fixture))

    assert fixture["canonical"].read_bytes() == original
    assert not (fixture["root"] / publisher.ARCHIVE_RELATIVE).exists()


def test_hash_drift_during_gate_validation_fails_before_archive(
    monkeypatch, tmp_path
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(
        publisher.matrix_resume_gate,
        "uniqueness_passes",
        lambda receipt, campaign: (True, []),
    )

    def drift(root, receipt):
        fixture["overlay"].write_bytes(b"concurrent overlay drift")
        return []

    monkeypatch.setattr(
        publisher.matrix_resume_gate,
        "validate_mu_materialization_lineage",
        drift,
    )
    original = fixture["canonical"].read_bytes()

    with pytest.raises(publisher.PublishBlocked, match="BOUND_HASH_MISMATCH"):
        publisher.publish(**_publish_args(fixture))

    assert fixture["canonical"].read_bytes() == original
    assert not (fixture["root"] / publisher.ARCHIVE_RELATIVE).exists()


def test_candidate_must_explicitly_preserve_engine_cells(
    monkeypatch, tmp_path
) -> None:
    fixture = _fixture(tmp_path)
    candidate = fixture["candidate"]
    payload = json.loads(candidate.read_text())
    payload["engine_cells_modified"] = True
    candidate.write_text(json.dumps(payload) + "\n")
    args = _publish_args(fixture)
    args["expected_candidate_sha256"] = _sha(candidate)

    with pytest.raises(publisher.PublishBlocked, match="ENGINE_CELLS"):
        publisher.publish(**args)
