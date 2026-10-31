#!/usr/bin/env python3
"""Convert source-verified full-cell campaign results into bounded v18 training events.

This adapter only emits a filter training observation when a proven cell result has
an independently proven same-chain naked measurement on the identical frozen input.
Unpaired observations remain available as diagnostics and are never turned into zeroes.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path

CAT_SIDES = {"CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(paths):
    records = []
    sources = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        before = sha256_file(path)
        line_count = 0
        with path.open() as stream:
            for line_number, line in enumerate(stream, 1):
                line_count = line_number
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number}: object required")
                records.append((value, str(path), line_number, sha256_bytes(canonical(value))))
        if sha256_file(path) != before:
            raise ValueError(f"source changed during read: {path}")
        sources.append({"path": str(path), "sha256": before, "records": line_count})
    return records, sources


def load_audits(paths):
    audits = {}
    sources = []
    records, manifests = read_jsonl(paths)
    for record, source, line, identity in records:
        if record.get("schema") != "v22_verified_worker_proof/1":
            raise ValueError(f"{source}:{line}: unsupported proof audit schema")
        key = record.get("attempt_sha256")
        if not key or key in audits:
            raise ValueError(f"{source}:{line}: missing or duplicate attempt proof audit")
        audits[key] = record
    sources.extend(manifests)
    return audits, sources


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _context_key(record, audit):
    cell = record.get("cell") or {}
    context = audit.get("context") or {}
    return (
        context.get("sym_side"),
        context.get("cat_side"),
        cell.get("template_sha256"),
        cell.get("tab"),
        cell.get("row"),
        cell.get("switch"),
        canonical(cell.get("cand")).decode(),
        audit.get("engine_sha256"),
        audit.get("npz_sha256"),
        audit.get("input_cutoff_sha256"),
        canonical(audit.get("window")).decode(),
        audit.get("accepted_chain_sha256"),
    )


def _verify_record(record, source, line, source_digest, audits, context, require_naked_equivalence=False):
    cell = record.get("cell") or {}
    proof = record.get("actual_proof") or {}
    attempt_sha = sha256_bytes(canonical(record))
    audit = audits.get(attempt_sha)
    if audit is None:
        return {"record": record, "source": source, "line": line, "attempt_sha256": attempt_sha, "class": "proof_not_independently_verified"}, None
    if audit.get("attempt_source_sha256") != source_digest or audit.get("attempt_line") != line:
        raise ValueError(f"{source}:{line}: proof audit points to a different source row")
    expected_ref = {key: proof.get(key) for key in ("path", "sha256", "raw_sha256", "encoding", "store_directory")}
    if audit.get("actual_proof_ref") != expected_ref:
        raise ValueError(f"{source}:{line}: proof reference differs from verified audit")
    if audit.get("proof_schema") != "nextgen_actual_worker_measurement/1":
        raise ValueError(f"{source}:{line}: worker proof schema is not supported")
    if audit.get("engine_sha256") != context.get("engine_sha256") or audit.get("npz_sha256") != context.get("npz_sha256"):
        raise ValueError(f"{source}:{line}: worker proof does not match frozen runtime/input context")
    if audit.get("input_cutoff_sha256") != context.get("input_cutoff_sha256"):
        raise ValueError(f"{source}:{line}: worker proof cutoff differs from campaign context")
    if audit.get("accepted_chain_sha256") != (context.get("accepted_source") or {}).get("sha256"):
        raise ValueError(f"{source}:{line}: accepted chain differs from campaign context")
    if audit.get("window") != context.get("window"):
        raise ValueError(f"{source}:{line}: evaluation window differs from campaign context")
    for key in ("effective_config_sha256", "execution_ledger_sha256", "result_sha256"):
        if audit.get(key) != (record.get("engine_proof") or {}).get(key):
            raise ValueError(f"{source}:{line}: engine proof hash mismatch for {key}")
    if record.get("requested_effective_mismatches"):
        raise ValueError(f"{source}:{line}: requested settings did not match the effective engine config")
    if require_naked_equivalence and audit.get("naked_config_verified") is not True:
        return {"record": record, "source": source, "line": line, "attempt_sha256": attempt_sha, "class": "actual_result_but_naked_effective_config_not_proven"}, None
    if record.get("proven_actual_result") is not True or record.get("engine_execution_attempted") is not True:
        return {"record": record, "source": source, "line": line, "attempt_sha256": attempt_sha, "class": "not_a_proven_actual_measurement"}, None
    value = record.get("raw_gain_pct")
    if not _finite(value):
        return {"record": record, "source": source, "line": line, "attempt_sha256": attempt_sha, "class": "missing_or_nonfinite_gain_not_zero"}, None
    valid = record.get("raw_valid")
    if type(valid) is not bool:
        return {"record": record, "source": source, "line": line, "attempt_sha256": attempt_sha, "class": "validity_unknown_not_training_eligible"}, None
    measured = audit.get("result") or {}
    audit_gain = measured.get("gain_pct")
    if not _finite(audit_gain) or not math.isclose(float(audit_gain), float(value), rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{source}:{line}: raw attempt gain differs from unpacked worker result")
    if type(measured.get("valid")) is not bool or measured.get("valid") is not valid:
        raise ValueError(f"{source}:{line}: attempt validity differs from unpacked worker result")
    verified = {
        "record": record,
        "source": source,
        "line": line,
        "source_sha256": source_digest,
        "attempt_sha256": attempt_sha,
        "audit": audit,
        "class": "verified_valid_measurement" if valid else "verified_invalid_measurement",
    }
    return verified, verified


def _is_naked(item):
    cell = item["record"].get("cell") or {}
    return cell.get("kind") == "naked" or cell.get("filter") in (None, "")


def _metrics(audit):
    result = audit.get("result") or {}
    aliases = ("tim_pct", "trades", "max_dd_pct", "pool_sharpe", "wr_pct")
    return [result.get(name) for name in aliases]


def _event_pair(cell_item, naked_item, context):
    cell = cell_item["record"]["cell"]
    cell_audit, naked_audit = cell_item["audit"], naked_item["audit"]
    filter_name, option = cell.get("filter"), cell.get("opt")
    if not isinstance(filter_name, str) or not filter_name or option is None:
        raise ValueError("paired cell is missing filter/option")
    cell_gain = float(cell_item["record"]["raw_gain_pct"])
    naked_gain = float(naked_item["record"]["raw_gain_pct"])
    lift = cell_gain - naked_gain
    cell_metrics, naked_metrics = _metrics(cell_audit), _metrics(naked_audit)
    metric_lifts = []
    for current, base in zip(cell_metrics, naked_metrics):
        metric_lifts.append(float(current) - float(base) if _finite(current) and _finite(base) else None)
    observed = max(cell_audit.get("observed_at", ""), naked_audit.get("observed_at", ""))
    row_identity = {
        "context_key": list(_context_key(cell_item["record"], cell_audit)),
        "filter": filter_name,
        "option": option,
        "cell_attempt_sha256": cell_item["attempt_sha256"],
        "naked_attempt_sha256": naked_item["attempt_sha256"],
    }
    event_id = sha256_bytes(canonical(row_identity))
    sym_side = context["sym_side"]
    board_id = f"{sym_side}|v22fullcell|{cell_audit['npz_sha256']}"
    header = f"{filter_name}={option}"
    board = {
        "e": "board",
        "t": observed,
        "s": sym_side,
        "b": board_id,
        "bh": None,
        "ti": None,
        "rd": "v22_fullcell",
        "src": "v22_fullcell_campaign",
        "npz_sha256": cell_audit["npz_sha256"],
        "input_cutoff_sha256": cell_audit["input_cutoff_sha256"],
        "engine_sha256": cell_audit["engine_sha256"],
        "rk": sha256_bytes(("board|" + board_id).encode()),
    }
    row = {
        "e": "row",
        "t": observed,
        "s": sym_side,
        "b": board_id,
        "sh": cell["tab"],
        "k": f"{cell['switch']}={cell['cand']}",
        "cb": None,
        "n": None,
        "y": [[header, lift, 1, 0.0, *metric_lifts, 1.0, cell_gain]],
        "ya": [],
        "nu": 1,
        "ni": 0,
        "sy": [],
        "p": False,
        "ps": "v22_same_chain_pair",
        "g": "UNK",
        "ti": None,
        "bh": None,
        "rk": event_id,
        "eval_provenance": {
            "schema": "v22_same_chain_pair/1",
            "cell_attempt_sha256": cell_item["attempt_sha256"],
            "naked_attempt_sha256": naked_item["attempt_sha256"],
            "cell_result_sha256": cell_audit["result_sha256"],
            "naked_result_sha256": naked_audit["result_sha256"],
            "npz_sha256": cell_audit["npz_sha256"],
            "input_cutoff_sha256": cell_audit["input_cutoff_sha256"],
            "accepted_chain_sha256": cell_audit["accepted_chain_sha256"],
            "filter_lift_rule": "cell_gain_pct - same_chain_naked_gain_pct",
            "market_oos_claim": False,
        },
    }
    return board, row


def convert(attempt_paths, naked_paths, proof_paths, context_path, out_dir, cutoff, market_end_proof=None):
    out_dir = Path(out_dir).expanduser().resolve()
    if out_dir.exists():
        raise ValueError(f"output directory must be fresh: {out_dir}")
    context_path = Path(context_path).expanduser().resolve()
    context = json.loads(context_path.read_text())
    if context.get("schema") != "fullcell_actual_coverage_progress/1":
        raise ValueError("campaign context must be a fullcell_actual_coverage_progress/1 receipt")
    ctx = context.get("source_context") or {}
    if ctx.get("cat_side") not in CAT_SIDES or not ctx.get("sym_side"):
        raise ValueError("campaign context lacks explicit category/symbol identity")
    for key in ("engine_sha256", "npz_sha256", "input_cutoff_sha256", "baseline_ledger_sha256"):
        if not ctx.get(key):
            raise ValueError(f"campaign context missing {key}")
    market_proof = None
    if market_end_proof:
        proof_path = Path(market_end_proof).expanduser().resolve()
        market_proof = json.loads(proof_path.read_text())
        if market_proof.get("schema") != "v22_npz_market_end/1" or market_proof.get("npz_sha256") != ctx["npz_sha256"]:
            raise ValueError("market-end proof schema or NPZ hash mismatch")
        if not _finite(market_proof.get("last_timestamp_epoch")) or not market_proof.get("last_timestamp_utc"):
            raise ValueError("market-end proof lacks an actual final NPZ timestamp")
        market_proof = dict(market_proof, path=str(proof_path), sha256=sha256_file(proof_path))
    audits, proof_sources = load_audits(proof_paths)
    cell_records, cell_sources = read_jsonl(attempt_paths)
    naked_records, naked_sources = read_jsonl(naked_paths)
    verified_cells, verified_naked = [], []
    excluded = []
    naked_sources_set = {item["path"] for item in naked_sources}
    for record, source, line, attempt_sha in cell_records + naked_records:
        digest = next(item["sha256"] for item in cell_sources + naked_sources if item["path"] == source)
        result, verified = _verify_record(record, source, line, digest, audits, ctx, require_naked_equivalence=source in naked_sources_set)
        if verified is None:
            excluded.append(result)
            continue
        if source in naked_sources_set or _is_naked(verified):
            verified["class"] = "verified_same_chain_naked_measurement"
            verified_naked.append(verified)
        else:
            verified_cells.append(verified)
    naked_by_key = defaultdict(list)
    for item in verified_naked:
        naked_by_key[_context_key(item["record"], item["audit"])].append(item)
    pairs, unpaired, duplicates = [], [], []
    seen_pair_ids = set()
    for item in verified_cells:
        key = _context_key(item["record"], item["audit"])
        candidates = naked_by_key.get(key, [])
        if not candidates:
            unpaired.append({"attempt_sha256": item["attempt_sha256"], "class": "no_same_chain_naked_result_no_label"})
            continue
        naked = sorted(candidates, key=lambda value: value["attempt_sha256"])[0]
        pair_id = sha256_bytes(canonical([item["attempt_sha256"], naked["attempt_sha256"]]))
        if pair_id in seen_pair_ids:
            duplicates.append(pair_id)
            continue
        seen_pair_ids.add(pair_id)
        pairs.append((item, naked, pair_id))
    out_dir.mkdir(parents=True)
    (out_dir / "diagnostics.jsonl").write_text("".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in excluded + unpaired))
    spill_path = out_dir / "spill_v22_campaign.tsv.gz"
    with gzip.open(spill_path, "wt", compresslevel=3) as stream:
        for cell_item, naked_item, _pair_id in pairs:
            board, row = _event_pair(cell_item, naked_item, ctx)
            stream.write(board["t"] + "\t" + json.dumps(board, separators=(",", ":"), allow_nan=False) + "\n")
            stream.write(row["t"] + "\t" + json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    events_sha = sha256_file(spill_path)
    diagnostics_sha = sha256_file(out_dir / "diagnostics.jsonl")
    training_market_end = (ctx.get("window") or {}).get("confirm_end")
    manifest = {
        "schema": "v22_campaign_training_conversion/1",
        "status": "ready_for_review" if pairs else "no_paired_naked_results_no_training_labels",
        "created_from": "source-bound full-cell campaign attempts; proof audits are independently verified worker captures",
        "campaign_context": {"path": str(context_path), "sha256": sha256_file(context_path)},
        "campaign_scope": {key: ctx.get(key) for key in ("sym_side", "cat_side", "engine_sha256", "npz_sha256", "input_cutoff_sha256", "window", "baseline_ledger_sha256", "phase_status", "authority_threshold")},
        "sources": {"cell_attempts": cell_sources, "naked_attempts": naked_sources, "proof_audits": proof_sources},
        "counts": {"cell_attempt_records": len(cell_records), "naked_attempt_records": len(naked_records), "verified_cell_measurements": len(verified_cells), "verified_naked_measurements": len(verified_naked), "same_chain_pairs": len(pairs), "unpaired_cells_without_label": len(unpaired), "proof_or_quality_exclusions": len(excluded), "duplicate_pairs_not_counted": len(duplicates)},
        "label_rule": "filter marginal = paired cell gain_pct minus same row/candidate/accepted-chain naked gain_pct; never impute missing naked as zero",
        "validity_rule": "only explicit boolean-valid, finite-gain source-verified attempts enter training; invalid and unknown remain diagnostics",
        "proven_result_threshold": 101,
        "authoritative_cell_classification": "not_performed; training observations do not themselves promote/demote cells",
        "market_window": {"declared_confirm_end_epoch": training_market_end, "npz_payload_end_independently_verified": bool(market_proof), "npz_market_end_max_epoch": market_proof.get("last_timestamp_epoch") if market_proof else None, "npz_market_end_max_utc": market_proof.get("last_timestamp_utc") if market_proof else None, "market_end_proof": market_proof, "oos_claim": False, "note": "preserve this frozen input identity for later holdout separation; do not score the refreshed model on these same market bytes as independent evidence"},
        "training_cut": cutoff,
        "training_input": {"v18_spill": str(spill_path), "spill_dir": str(out_dir), "sha256": events_sha, "format": "v18_brain native event stream (board and row events); filename is compatible with spill_*.tsv.gz readers"},
        "diagnostics": {"path": str(out_dir / "diagnostics.jsonl"), "sha256": diagnostics_sha},
        "proposed_refresh_command": None,
        "promotion": False,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", action="append", required=True, help="fullcell cell_attempts.jsonl; repeat to include more source receipts")
    parser.add_argument("--naked-attempts", action="append", default=[], help="same-context naked measurement receipts; repeatable")
    parser.add_argument("--proof-audit", action="append", required=True, help="independently unpacked worker-proof audit JSONL; repeatable")
    parser.add_argument("--context", required=True, help="matching fullcell_actual_coverage_progress/1 receipt")
    parser.add_argument("--out-dir", required=True, help="fresh isolated conversion directory")
    parser.add_argument("--cut", required=True, help="explicit v18 trainer cutoff with timezone")
    parser.add_argument("--market-end-proof", help="independently hashed NPZ timestamp census for this exact campaign input")
    args = parser.parse_args()
    result = convert(args.attempts, args.naked_attempts, args.proof_audit, args.context, args.out_dir, args.cut, args.market_end_proof)
    print(json.dumps({"status": result["status"], "counts": result["counts"], "out_dir": args.out_dir}, sort_keys=True))


if __name__ == "__main__":
    main()
