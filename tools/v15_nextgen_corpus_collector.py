"""Inventory preserved evidence and classify measured labels without training or rewriting sources."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

POLICY = "explicit_valid_finite_preserved_evidence_v1"
SETTLED = ("noop_yellows", "yellow_dups", "zero_skipped_filters", "sampled_out_filters", "type_skipped", "tab_level_excluded", "excluded_unwired", "missing_yellows", "inert_filters", "prior_skipped_filters", "coarse_deferred_filters")
TAG = re.compile(r"^ts([0-9.]+)_n([0-9]+)$")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for payload in iter(lambda: source.read(1048576), b""):
            result.update(payload)
    return result.hexdigest()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def label_reason(value, valid):
    if valid is not True and not (type(valid) is int and valid == 1):
        return "invalid_flag" if valid is False or (type(valid) is int and valid == 0) else "unknown_validity"
    return "eligible_research_measurement" if finite(value) else "missing_or_nonfinite_delta"


def market_end(tag):
    match = TAG.fullmatch(str(tag or ""))
    if not match:
        return None
    try:
        result = float(match[1])
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def progress_identity(symbol_side, document):
    identity = document.get("npz_id") or {}
    expected = str(symbol_side).rsplit("_", 1)
    if len(expected) != 2 or expected[1] not in ("LONG", "SHORT") or document.get("symside") != symbol_side:
        return "source_identity_mismatch"
    if not isinstance(identity, dict) or not identity.get("path"):
        return "missing_original_npz_identity"
    if Path(identity["path"]).stem != expected[0]:
        return "npz_symbol_mismatch_no_suffix_guessing"
    if not re.fullmatch(r"[0-9a-fA-F]{32}", str(identity.get("md5") or "")):
        return "missing_original_npz_hash"
    return "exact_original_npz_symbol_ledger"


def spill_labels(event):
    if event.get("e") != "row":
        return
    naked = event.get("n")
    if isinstance(naked, list) and len(naked) >= 2:
        yield "naked_delta_vs_cumulative", None, naked[0], naked[1]
    for field, target in (("y", "cell_marginal_vs_naked"), ("ya", "cell_absolute_without_naked"), ("nz", "noop_binding_diagnostic")):
        for item in event.get(field) or []:
            if isinstance(item, list) and len(item) >= 3:
                yield target, item[0], item[1], item[2]


def progress_labels(record):
    reason = str(record.get("reason") or "")
    if record.get("is_running") or reason.startswith("RUNNING_IDENTITY"):
        return
    raw_invalid = record.get("delta_invalid")
    validity = not raw_invalid if type(raw_invalid) in (bool, int) and raw_invalid in (0, 1) else None
    naked = record.get("naked_delta")
    if naked is None and record.get("joint_delta") is None:
        naked = record.get("delta")
    if naked is not None:
        yield "naked_delta_vs_cumulative", None, naked, validity
    settled = set()
    for field in SETTLED:
        values = record.get(field)
        if isinstance(values, (list, tuple, dict)):
            settled.update(values)
    reasons = record.get("yellow_reasons") or {}
    for header, value in (record.get("yellows") or {}).items():
        if header not in settled:
            yield "cell_absolute_from_progress", header, value, None if validity is None else validity and header not in reasons


def classify_observation(target, header, value, valid, provenance, counters, sink=None):
    reason = label_reason(value, valid)
    counters[target + ":" + reason] += 1
    if reason == "eligible_research_measurement":
        counters[target + (":positive" if value > 0 else ":measured_zero" if value == 0 else ":negative")] += 1
    if sink is not None:
        record = dict(provenance, target=target, header=header, raw_valid=valid, raw_value=value if finite(value) else None, raw_value_token=repr(value), label_reason=reason, quality_training_verified=False)
        sink.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def scan_spill(path, max_events=5000, sink=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"requires an exact regular spill source: {path}")
    before = digest(path)
    counts, symbols, boards = Counter(), set(), set()
    timestamp_min, timestamp_max = None, None
    board_context = {}
    sampled = False
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as source:
        for line_number, line in enumerate(source, 1):
            if max_events and line_number > max_events:
                sampled = True
                break
            try:
                event = json.loads(line.partition("\t")[2])
            except (ValueError, TypeError):
                counts["parse_error"] += 1
                continue
            counts["event:" + str(event.get("e"))] += 1
            symbols.add(str(event.get("s")))
            if event.get("b"):
                boards.add(str(event["b"]))
            if event.get("t"):
                timestamp = str(event["t"])
                timestamp_min = min(timestamp_min, timestamp) if timestamp_min else timestamp
                timestamp_max = max(timestamp_max, timestamp) if timestamp_max else timestamp
            if event.get("e") == "board":
                board_context[event.get("b")] = {key: event.get(key) for key in ("src", "ts_source", "npz", "wd", "rd")}
            context = board_context.get(event.get("b")) or {}
            lineage = "explicit_progress_market_end" if event.get("ts_source") == "explicit_progress_row_npz" and market_end(event.get("explicit_row_npz")) is not None else "market_window_unknown"
            counts["lineage:" + lineage] += 1
            provenance = {"source_file": str(path), "source_sha256": before, "source_line": line_number, "event_id": event.get("rk"), "symbol_side": event.get("s"), "board_id": event.get("b"), "sheet": event.get("sh"), "key": event.get("k"), "cumulative_before": event.get("cb"), "event_t_raw": event.get("t"), "board_context": context, "lineage_tier": lineage, "timestamp_interpretation": "raw source timestamp; never assumed NPZ/window end"}
            for target, header, value, valid in spill_labels(event):
                classify_observation(target, header, value, valid, provenance, counts, sink)
    if digest(path) != before:
        raise ValueError(f"spill changed during collection: {path}")
    return {"path": str(path), "sha256": before, "bytes": path.stat().st_size, "sampled_not_complete_counts": sampled, "max_events": max_events, "counters": dict(counts), "unique_symbols_in_scan": len(symbols), "unique_boards_in_scan": len(boards), "raw_timestamp_min": timestamp_min, "raw_timestamp_max": timestamp_max}


def scan_progress_index(index_path, sink=None):
    index_path = Path(index_path)
    payload = index_path.read_bytes()
    index_sha = hashlib.sha256(payload).hexdigest()
    records, seen, report = json.loads(payload), set(), []
    for entry in records:
        path = Path(entry["file"])
        if str(path) in seen:
            continue
        seen.add(str(path))
        if not path.is_file():
            report.append({"path": str(path), "status": "missing_preserved_source"})
            continue
        payload = path.read_bytes()
        before = hashlib.sha256(payload).hexdigest()
        document = json.loads(payload)
        identity = progress_identity(entry.get("s"), document)
        counts = Counter()
        for key, row in (document.get("done") or {}).items():
            if not isinstance(row, dict):
                counts["malformed_row"] += 1
                continue
            end = market_end(row.get("npz"))
            lineage = "explicit_progress_market_end" if end is not None else "market_window_unknown"
            counts["lineage:" + lineage] += 1
            if row.get("is_running") or str(row.get("reason") or "").startswith("RUNNING_IDENTITY"):
                counts["identity_not_gain_negative"] += 1
            provenance = {"source_file": str(path), "source_sha256": before, "progress_key": key, "symbol_side": entry.get("s"), "identity_tier": identity, "lineage_tier": lineage, "explicit_row_npz": row.get("npz"), "market_end_from_explicit_tag": end, "window_days": document.get("window_days"), "top_npz_id_not_row_byte_hash": document.get("npz_id"), "raw_reason": row.get("reason"), "engine_version_per_row": row.get("engine_md5"), "source_result_engine_md5_not_per_row": document.get("result_engine_md5")}
            for target, header, value, valid in progress_labels(row):
                classify_observation(target, header, value, valid, provenance, counts, sink)
        if digest(path) != before:
            raise ValueError(f"progress source changed during collection: {path}")
        report.append({"path": str(path), "source_sha256": before, "bytes": len(payload), "symbol_side": entry.get("s"), "identity_tier": identity, "counters": dict(counts)})
    if digest(index_path) != index_sha:
        raise ValueError("progress index changed during collection")
    return {"index_path": str(index_path), "index_sha256": index_sha, "input_entries": len(records), "sources": report}


def collect(out_dir, spill_paths=(), progress_index=None, max_events=5000, emit=False):
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    report = {"schema": "preserved_corpus_inventory/1", "policy": POLICY, "status": "running", "quality_training_verified": False, "scope": "research evidence inventory, not a training corpus; unknown lineage retained, never zero negatives; no current-book filtering", "spills": []}
    output = root / "inventory.json"
    sink = gzip.open(root / "observations_research_only.jsonl.gz", "wt") if emit else None
    try:
        for path in spill_paths:
            report["spills"].append(scan_spill(path, max_events, sink))
            output.write_text(json.dumps(report, indent=2))
        if progress_index:
            report["original_progress"] = scan_progress_index(progress_index, sink)
        report["status"] = "inventory_complete_not_training_approved"
    except BaseException as error:
        report.update(status="failed_ineligible", error=str(error))
        raise
    finally:
        if sink:
            sink.close()
        report["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        output.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--spill-list", help="JSON list of exact preserved spill paths, no directory expansion")
    parser.add_argument("--progress-index")
    parser.add_argument("--max-events", type=int, default=5000, help="per spill; 0 explicitly requests a complete streaming census")
    parser.add_argument("--emit-observations", action="store_true", help="diagnostic sidecar only; never named spill_* or accepted as training input")
    args = parser.parse_args()
    if args.max_events < 0 or not (args.spill_list or args.progress_index):
        parser.error("requires inputs and nonnegative event limit")
    paths = json.loads(Path(args.spill_list).read_text()) if args.spill_list else []
    report = collect(args.out_dir, paths, args.progress_index, args.max_events, args.emit_observations)
    print(json.dumps({"status": report["status"], "spills": len(report["spills"]), "progress_sources": len((report.get("original_progress") or {}).get("sources") or []), "quality_training_verified": False}))


if __name__ == "__main__":
    main()
