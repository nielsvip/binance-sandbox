#!/usr/bin/env python3
"""Reconstruct pre-evaluation context only where logged full-set hashes prove it; write fresh candidate stores."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import v18_experience as experience
from tools import v15_v18_planner as planner


def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            result.update(chunk)
    return result.hexdigest()


def anchors(progress):
    result = {}
    for expected, values in (progress.get("sets_by_sha") or {}).items():
        if isinstance(values, dict) and planner.set_sha256(values) == expected:
            result[expected] = (dict(values), "sets_by_sha")
    for name in ("initial_overrides", "cumulative_overrides"):
        values = progress.get(name)
        if isinstance(values, dict) and values:
            result.setdefault(planner.set_sha256(values), (dict(values), name))
    return result


def promotion_record(progress, raw):
    key = f"{raw.get('sheet')}!{raw.get('row')}:{raw.get('switch')}={raw.get('cand')}"
    record = (progress.get("done") or {}).get(key)
    return record if isinstance(record, dict) else None


def verified_transition(current, raw, progress, known):
    expected = raw.get("set_sha256")
    if not expected:
        return None, "missing_logged_sha"
    if expected in known:
        return dict(known[expected][0]), "exact_" + known[expected][1]
    if current is None:
        return None, "no_verified_previous_set"
    candidate = dict(current)
    if raw.get("label") == "ROW_DONE" and raw.get("promoted"):
        record = promotion_record(progress, raw)
        if record is None or record.get("promoted") is not True:
            return None, "missing_matching_progress_promotion"
        if not record.get("is_running"):
            candidate[str(raw["switch"])] = experience.coerce(raw.get("cand"))
        for header in record.get("promoted_filters") or []:
            name, separator, value = str(header).partition("=")
            if not separator:
                return None, "malformed_promoted_filter"
            candidate[name.strip()] = experience.coerce(value.strip())
        dependency = (record.get("dep_forced") or {}).get("promoted") or {}
        if not isinstance(dependency, dict):
            return None, "malformed_dependency_overrides"
        candidate.update(dependency)
    if planner.set_sha256(candidate) != expected:
        return None, "post_transition_sha_mismatch"
    return candidate, "exact_replayed_promotion" if raw.get("promoted") else "exact_unchanged"


def reconstruct(log_path, progress_path, host, emit, diagnose):
    log_path, progress_path = Path(log_path), Path(progress_path)
    before = {str(path): file_sha(path) for path in (log_path, progress_path)}
    progress = json.loads(progress_path.read_text())
    known = anchors(progress)
    counts = Counter()
    current, board, cumulative = None, {}, None
    opener = gzip.open if log_path.name.endswith(".gz") else open
    with opener(log_path, "rt") as stream:
        for line_number, line in enumerate(stream, 1):
            counts["lines"] += 1
            try:
                raw = json.loads(line)
            except ValueError:
                counts["malformed_json"] += 1
                diagnose({"line": line_number, "reason": "malformed_json", "raw": line.rstrip()})
                continue
            if not isinstance(raw, dict):
                counts["non_object"] += 1
                continue
            symbol_side = raw.get("sym_side")
            if symbol_side != progress.get("symside"):
                counts["foreign_symbol_side"] += 1
                continue
            label = raw.get("label")
            if label in ("BOARD_CTX", "ROW_DONE", "ROW_SETTLED"):
                if label == "BOARD_CTX":
                    current, board = None, dict(raw)
                    cumulative = raw.get("baseline_gain")
                current, reason = verified_transition(current, raw, progress, known)
                counts[reason] += 1
                if label == "ROW_DONE":
                    cumulative = raw.get("cum_after")
                if current is None:
                    diagnose({"line": line_number, "reason": reason, "raw": raw})
                continue
            if raw.get("gain_pct") is None or raw.get("cum_before") is None:
                counts["non_candidate_line"] += 1
                continue
            if current is None:
                counts["eval_without_verified_chain"] += 1
                diagnose({"line": line_number, "reason": "eval_without_verified_chain", "raw": raw})
                continue
            try:
                consistent = cumulative is not None and abs(float(raw["cum_before"]) - float(cumulative)) <= 1e-7
            except (TypeError, ValueError):
                consistent = False
            if not consistent:
                counts["cumulative_identity_mismatch"] += 1
                diagnose({"line": line_number, "reason": "cumulative_identity_mismatch", "raw": raw})
                current = None
                continue
            emit(dict(raw), dict(current), dict(board), {"source_file": str(log_path), "source_sha256": before[str(log_path)], "line": line_number, "host": host, "progress_file": str(progress_path), "progress_sha256": before[str(progress_path)], "chain_sha_verified": True, "base_set_sha256": planner.set_sha256(current)})
            counts["verified_eval_rows"] += 1
    if any(file_sha(path) != digest for path, digest in before.items()):
        raise ValueError("raw log or progress changed during reconstruction")
    return {"sources": before, "counters": dict(counts), "scope": "exact logged hash contexts only; no round/count or nearest-time fallback"}


def write_eval(store, raw, values, board, proof):
    timestamp = experience._iso_epoch(raw.get("ts"))
    category = experience.cat_side_of(raw.get("sym_side"))
    if timestamp is None or category is None:
        raise ValueError("verified eval lacks category or event timestamp")
    month = experience.month_of(timestamp)
    text = planner.canonical_json(values)
    identity = planner.set_sha256(values)
    store.put_set(category, month, identity, "exact_logged_chain_sha", len(values), text, timestamp, proof["source_file"])
    item = {"eval_id": "ctxexact1:" + hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(), "provenance": "log", "sym_side": raw["sym_side"], "cat_side": category, "era": experience.era_of(category, timestamp), "ts": timestamp, "base_set_id": identity, "delta_rule": "none", "switch": raw.get("switch"), "cand": raw.get("cand"), "label": raw.get("label"), "window_days": raw.get("window_days", board.get("window_days")), "offset_days": raw.get("offset_days"), "npz_md5": raw.get("npz_md5"), "gain": raw.get("gain_pct"), "trades": raw.get("trades"), "valid": raw.get("valid"), "invalid_reason": raw.get("invalid_reason"), "cum_before": raw["cum_before"], "run": board.get("round"), "sheet": raw.get("sheet"), "row_num": raw.get("row"), "source": proof["source_file"], "prov": json.dumps(dict(proof, board_identity={key: board.get(key) for key in ("set_sha256", "npz_last_bar_ts", "window_days", "round")}, raw=raw), sort_keys=True)}
    connection = store.shard(category, month)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(evals)")}
    item = {key: value for key, value in item.items() if key in columns}
    names = list(item)
    connection.execute("INSERT OR IGNORE INTO evals (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")", [item[name] for name in names])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", action="append", required=True, help="exact raw-log path=matching-progress path; repeat for multiple frozen boards")
    parser.add_argument("--host", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--apply", action="store_true", help="default audit only; applying writes a fresh official-schema context store")
    args = parser.parse_args()
    root = Path(args.out_dir).expanduser().resolve()
    if root.exists():
        parser.error("output directory must be fresh")
    root.mkdir(parents=True)
    store = experience.Store(root / "store", apply=args.apply)
    reports = []
    try:
        with (root / "unknown_contexts.jsonl").open("w") as diagnostics:
            for pair in args.pair:
                log, separator, progress = pair.partition("=")
                if not separator:
                    raise ValueError("pair needs log=progress")
                emit = (lambda raw, values, board, proof: write_eval(store, raw, values, board, proof)) if args.apply else (lambda *args: None)
                reports.append(reconstruct(log, progress, args.host, emit, lambda record: diagnostics.write(json.dumps(record, sort_keys=True) + "\n")))
        store.flush()
        (root / "report.json").write_text(json.dumps({"schema": "exact_chain_context_v1", "applied": args.apply, "pairs": reports, "status": "exact_contexts_only_not_production_eligible"}, indent=2))
    finally:
        store.close()
    print(json.dumps({"out_dir": str(root), "pairs": len(reports), "applied": args.apply}))


if __name__ == "__main__":
    main()
