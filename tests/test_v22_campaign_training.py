import gzip
import json

from tools import v18_brain
from tools.v22_campaign_training import canonical, convert, sha256_bytes, sha256_file


class EmptyBuckets:
    def of(self, sheet, switch, candidate):
        return "UNKNOWN"


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values))


def test_converter_pairs_only_same_frozen_chain_and_v18_consumes_event(tmp_path):
    context = {
        "schema": "fullcell_actual_coverage_progress/1",
        "source_context": {
            "cat_side": "CRYPTO_SHORT",
            "sym_side": "1INCHUSDT_SHORT",
            "engine_sha256": "engine",
            "npz_sha256": "npz",
            "input_cutoff_sha256": "cutoff",
            "baseline_ledger_sha256": "baseline-ledger",
            "window": {"days": 7, "confirm_end": 100},
            "accepted_source": {"sha256": "accepted-chain"},
            "phase_status": "diagnostic",
            "authority_threshold": 101,
        },
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    proof_ref = {"path": "/proof/record.json", "sha256": "proof-sha", "raw_sha256": "raw-sha", "encoding": "cas-v1", "store_directory": "/proof"}
    base = {
        "status": "measured_ledger_changed",
        "promotion_allowed": False,
        "requested_effective_mismatches": {},
        "actual_proof": proof_ref,
        "engine_proof": {"effective_config_sha256": "effective", "execution_ledger_sha256": "ledger", "result_sha256": "result"},
        "raw_gain_pct": 4.0,
        "raw_valid": True,
        "proven_actual_result": True,
        "engine_execution_attempted": True,
    }
    shared_cell = {"cat_side": "CRYPTO_SHORT", "template_sha256": "template", "tab": "ENTRY_REVERSAL_BOUNCE", "row": 29, "switch": "ENTRY_LH_TRIGGER_DIVERG_ENABLED", "cand": False}
    naked = dict(base, cell=dict(shared_cell, filter="NOOP_SENTINEL", opt="OFF"))
    cell = dict(base, cell=dict(shared_cell, filter="GR_FILTER_VEC_FILTER_TF", opt="OFF"), raw_gain_pct=6.5, engine_proof=dict(base["engine_proof"], execution_ledger_sha256="ledger-cell", result_sha256="result-cell"))
    cell_path, naked_path = tmp_path / "cell.jsonl", tmp_path / "naked.jsonl"
    write_jsonl(cell_path, [cell])
    write_jsonl(naked_path, [naked])
    cell_digest, naked_digest = sha256_file(cell_path), sha256_file(naked_path)
    audit_values = []
    for record, digest, line_no, ref, gain, ledger, result_sha in (
        (cell, cell_digest, 1, proof_ref, 6.5, "ledger-cell", "result-cell"),
        (naked, naked_digest, 1, proof_ref, 4.0, "ledger", "result"),
    ):
        audit_values.append({
            "schema": "v22_verified_worker_proof/1",
            "attempt_sha256": sha256_bytes(canonical(record)),
            "attempt_source_sha256": digest,
            "attempt_line": line_no,
            "actual_proof_ref": ref,
            "proof_schema": "nextgen_actual_worker_measurement/1",
            "context": {"sym_side": "1INCHUSDT_SHORT", "cat_side": "CRYPTO_SHORT"},
            "engine_sha256": "engine",
            "npz_sha256": "npz",
            "input_cutoff_sha256": "cutoff",
            "window": {"days": 7, "confirm_end": 100},
            "accepted_chain_sha256": "accepted-chain",
            "naked_config_verified": True,
            "effective_config_sha256": "effective",
            "execution_ledger_sha256": ledger,
            "result_sha256": result_sha,
            "observed_at": "2026-10-31T05:00:00Z",
            "result": {"gain_pct": gain, "valid": True, "tim_pct": 40, "trades": 20, "max_dd_pct": 10, "pool_sharpe": 0.2, "wr_pct": 60},
        })
    audit_path = tmp_path / "proof_audit.jsonl"
    write_jsonl(audit_path, audit_values)
    output = tmp_path / "out"
    result = convert([cell_path], [naked_path], [audit_path], context_path, output, "2026-11-01T00:00:00Z")
    assert result["counts"]["same_chain_pairs"] == 1
    with gzip.open(output / "spill_v22_campaign.tsv.gz", "rt") as stream:
        events = [json.loads(line.split("\t", 1)[1]) for line in stream]
    assert [event["e"] for event in events] == ["board", "row"]
    assert events[1]["n"] is None
    assert events[1]["y"][0][1] == 2.5
    brain = v18_brain.CatBrain("CRYPTO_SHORT", EmptyBuckets(), "2026-11-01T00:00:00Z")
    for event in events:
        brain.handle(event, event["t"])
    assert brain.c["evidence_cells"] == 1
    assert brain.c["evidence_naked"] == 0


def test_converter_does_not_turn_unpaired_cell_into_zero_delta(tmp_path):
    context = {
        "schema": "fullcell_actual_coverage_progress/1",
        "source_context": {
            "cat_side": "CRYPTO_SHORT", "sym_side": "1INCHUSDT_SHORT", "engine_sha256": "engine", "npz_sha256": "npz",
            "input_cutoff_sha256": "cutoff", "baseline_ledger_sha256": "base", "window": {"days": 7, "confirm_end": 100}, "accepted_source": {"sha256": "chain"},
        },
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    record = {
        "cell": {"cat_side": "CRYPTO_SHORT", "template_sha256": "template", "tab": "ENTRY_REVERSAL_BOUNCE", "row": 29, "switch": "S", "cand": False, "filter": "F", "opt": "OFF"},
        "status": "measured_no_ledger_change", "actual_proof": {"path": "/proof", "sha256": "proof", "raw_sha256": "raw", "encoding": "cas-v1", "store_directory": "/proof"},
        "engine_proof": {"effective_config_sha256": "effective", "execution_ledger_sha256": "ledger", "result_sha256": "result"},
        "raw_gain_pct": 3.0, "raw_valid": True, "proven_actual_result": True, "engine_execution_attempted": True, "requested_effective_mismatches": {},
    }
    attempts = tmp_path / "attempts.jsonl"
    write_jsonl(attempts, [record])
    audit = {
        "schema": "v22_verified_worker_proof/1", "attempt_sha256": sha256_bytes(canonical(record)), "attempt_source_sha256": sha256_file(attempts), "attempt_line": 1,
        "actual_proof_ref": record["actual_proof"], "proof_schema": "nextgen_actual_worker_measurement/1", "context": {"sym_side": "1INCHUSDT_SHORT", "cat_side": "CRYPTO_SHORT"},
        "engine_sha256": "engine", "npz_sha256": "npz", "input_cutoff_sha256": "cutoff", "window": {"days": 7, "confirm_end": 100}, "accepted_chain_sha256": "chain",
        "accepted_chain_sha256": "chain", "naked_config_verified": True,
        "effective_config_sha256": "effective", "execution_ledger_sha256": "ledger", "result_sha256": "result", "observed_at": "2026-10-31T05:00:00Z", "result": {"gain_pct": 3.0, "valid": True},
    }
    proof_path = tmp_path / "proof.jsonl"
    write_jsonl(proof_path, [audit])
    output = tmp_path / "out"
    result = convert([attempts], [], [proof_path], context_path, output, "2026-11-01T00:00:00Z")
    assert result["counts"]["verified_cell_measurements"] == 1
    assert result["counts"]["same_chain_pairs"] == 0
    assert result["status"] == "no_paired_naked_results_no_training_labels"
    with gzip.open(output / "spill_v22_campaign.tsv.gz", "rt") as stream:
        assert stream.read() == ""
    diagnostic = json.loads((output / "diagnostics.jsonl").read_text())
    assert diagnostic["class"] == "no_same_chain_naked_result_no_label"
