import gzip
import hashlib
import json

from tools import export_switch_matrix_xls as export


def test_rebind_updates_only_matching_canonical_export(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    matrix = reports / "SWITCH_MATRIX_TRB.csv.gz"
    with gzip.open(matrix, "wt") as handle:
        handle.write("# CURRENT_CAMPAIGN=stocks_repaired_20260730_c5\n")
        handle.write("# CURRENT_ENGINE_CUTOFF=2026-07-30T03:30:10Z\n")
        handle.write("# CURRENT_CONTRACT_VERSION=tradier-matrix-exec-c5-20260730\n")
        handle.write("# CURRENT_MATRIX_SCOPE=CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY\n")
        handle.write("# CANONICAL_MATRIX=data/reports/SWITCH_MATRIX_TRB.csv.gz\n")
        handle.write("main_switch,MU_LONG,MSTR_SHORT\n")
    sidecar = reports / "SWITCH_MATRIX_TRB_DIGEST.md.provenance.json"
    sidecar.write_text(json.dumps({
        "schema": "switch-matrix-trb-current-digest-v1",
        "campaign": "stocks_repaired_20260730_c5",
        "engine_cutoff": "2026-07-30T03:30:10Z",
        "contract_version": "tradier-matrix-exec-c5-20260730",
        "matrix_scope": "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY",
        "canonical_matrix": "data/reports/SWITCH_MATRIX_TRB.csv.gz",
        "canonical_matrix_sha256": "stale",
        "tim_policy": {"MU_LONG": {}, "MSTR_SHORT": {}},
    }))
    monkeypatch.setattr(export, "BASE", tmp_path)
    export.rebind_digest_provenance_after_canonical_export(
        matrix, {"MU_LONG", "MSTR_SHORT"}
    )
    rebound = json.loads(sidecar.read_text())
    assert rebound["canonical_matrix_sha256"] == hashlib.sha256(matrix.read_bytes()).hexdigest()
    assert "matrix_export_rebound_at_ns" in rebound
