"""Convert frozen original progress through the official assembler; never train or deploy."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import v15_nextgen_corpus_collector as corpus
NON_EVAL_REASONS = ("SKIPPED_", "ZERO_FORMULA_ROW", "INVENTED_ALT", "TYPE_MISMATCH", "ABLATION_NEVER_ON", "NO_CANDIDATE", "MAXAUG_NEVER_ZERO", "DATA_ERROR")
AMBIGUOUS_GAIN_REASONS = ("TIM-guard", "TIM ", "DD ", "trades ", "ZERO_TRADES", "npz covers", "override ", "invalid", "no result", "timeout", "error")


def strict_record(raw):
    record = dict(raw)
    identity = bool(raw.get("is_running")) or str(raw.get("reason") or "").startswith("RUNNING_IDENTITY")
    raw_invalid = raw.get("delta_invalid")
    reason = str(raw.get("reason") or "")
    explicit_valid = type(raw_invalid) in (bool, int) and raw_invalid == 0 and not reason.startswith(NON_EVAL_REASONS + AMBIGUOUS_GAIN_REASONS) and not raw.get("err")
    naked = raw.get("naked_delta")
    promotion_block = reason.startswith(("DEAD_VEC_PATH", "LIVE_DEAD_KEY"))
    naked_proof = raw.get("naked_valid") is True or raw.get("naked_ok") is True or raw.get("promoted_how") == "naked"
    naked_ok = explicit_valid and corpus.finite(naked) and corpus.finite(raw.get("cumulative_before")) and (not promotion_block or naked_proof)
    baseline_ok = explicit_valid and corpus.finite(raw.get("cumulative_before")) and (identity or naked_ok)
    record["delta_invalid"] = not naked_ok
    reasons = dict(raw.get("yellow_reasons") or {})
    for header, value in (raw.get("yellows") or {}).items():
        if not baseline_ok or not corpus.finite(value):
            reasons[header] = reasons.get(header) or "EXPANDED_POPULATION_UNVERIFIED_BASE_OR_DELTA"
    record["yellow_reasons"] = reasons
    return record, naked_ok, baseline_ok


def convert(manifest_path, archive_path, out_dir, templates):
    from tools import v18_brain as brain
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(Path(manifest_path).read_text())
    archive_sha = corpus.digest(archive_path)
    if archive_sha != manifest["archive_sha256"]:
        raise ValueError("frozen source archive hash mismatch")
    members = {entry["archive_member"]: entry for entry in manifest["sources"]}
    if len(members) != len(manifest["sources"]):
        raise ValueError("duplicate frozen source member")
    cutoffs = manifest["cutoffs_epoch"]
    counts = {category: Counter() for category in cutoffs}
    official_counts, seen_rows, seen_members, source_reports = Counter(), set(), set(), []
    spill = root / "spill_expanded_explicit_progress.tsv.gz"
    cases = root / "cases_preserved_not_training.jsonl.gz"
    report = {"schema": "expanded_official_progress_population/1", "status": "running", "source_manifest_sha256": corpus.digest(manifest_path), "source_archive_sha256": archive_sha, "cutoffs_epoch": cutoffs, "sources": source_reports, "quality_training_verified": False, "scope": "original81 plus exact historical NPZ-ledger sources; no current-book filtering/suffix mapping; frozen source bytes retained; official marginal conversion; no training", "target_policy": "R requires explicit finite naked_delta, never row-winner fallback; ambiguous failure/constraint reasons and promotion-block-hidden naked validity excluded; F requires finite measured cell and eligible naked or identity baseline; raw ambiguous measurements retained as diagnostics", "temporal_semantics": "t is explicit per-row NPZ last-bar, never claimed as cell computation time; engine/window-start/context completeness remains unverified"}
    report["wiring_era_policy"] = "old real zero is evidence only for its original engine/configuration; unknown wiring era cannot establish newly wired switch deadness; source result engine hashes are not per-row lineage; no suppression or training authorized here"
    output_report = root / "population.json"
    try:
        with gzip.open(spill, "wt", compresslevel=3) as output, gzip.open(cases, "wt", compresslevel=3) as diagnostics, tarfile.open(archive_path, "r|gz") as archive:
            for member in archive:
                if member.name not in members or member.name in seen_members or not member.isfile():
                    raise ValueError(f"unexpected/duplicate source archive member: {member.name}")
                seen_members.add(member.name)
                entry = members[member.name]
                payload = archive.extractfile(member).read()
                if hashlib.sha256(payload).hexdigest() != entry["source_sha256"]:
                    raise ValueError(f"frozen source hash mismatch: {member.name}")
                document = json.loads(payload)
                symbol = entry["symbol_side"]
                if document.get("symside") != symbol:
                    raise ValueError("frozen progress symbol mismatch")
                category = brain.cat_side_of(symbol)
                identity_tier = corpus.progress_identity(symbol, document)
                if entry["selection"] == "historical_exact_ledger" and identity_tier != "exact_original_npz_symbol_ledger":
                    raise ValueError("historical source identity no longer matches its exact original NPZ ledger")
                counter, groups = counts[category], {}
                initial_sha = hashlib.sha256(json.dumps(document.get("initial_overrides"), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
                for key, raw in (document.get("done") or {}).items():
                    counter["source_rows"] += 1
                    if not isinstance(raw, dict):
                        counter["malformed_rows"] += 1
                        continue
                    end = corpus.market_end(raw.get("npz"))
                    provenance = {"source_file": entry["source_file"], "source_sha256": entry["source_sha256"], "archive_member": member.name, "progress_key": key, "symbol_side": symbol, "identity_tier": identity_tier, "explicit_row_npz": raw.get("npz"), "market_end": end, "raw_reason": raw.get("reason"), "engine_hash_per_row": raw.get("engine_md5") or raw.get("engine_hash")}
                    if end is None or end >= cutoffs[category]:
                        reason = "unknown_explicit_row_npz" if end is None else "market_end_not_before_holdout"
                        counter[reason] += 1
                        diagnostics.write(json.dumps(dict(provenance, reason=reason), separators=(",", ":")) + "\n")
                        continue
                    context = {"npz": raw["npz"], "cumulative_before": raw.get("cumulative_before"), "window_days": document.get("window_days"), "initial_overrides_sha256": initial_sha}
                    identity = hashlib.sha256(json.dumps([entry["source_sha256"], key, context], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
                    if identity in seen_rows:
                        counter["duplicate_source_key_context"] += 1
                        continue
                    seen_rows.add(identity)
                    record, naked_ok, baseline_ok = strict_record(raw)
                    groups.setdefault(end, []).append((key, record, dict(provenance, source_record_identity=identity), naked_ok, baseline_ok))
                    counter["rows_before_cutoff"] += 1
                template_rows = {f"{tab}|{switch}={candidate}" for tab, block in templates.get(category, {}).get("tabs", {}).items() for row, switch, candidate, default in block["rows"]}
                emitted_before = counter["emitted_rows"]
                for end, rows in sorted(groups.items()):
                    current = [None]
                    common = {"src": "progress", "ts_source": "explicit_progress_row_npz", "npz": end, "cs": category, "wd": document.get("window_days"), "source_file": entry["source_file"], "source_sha256": entry["source_sha256"], "archive_member": member.name, "selection": entry["selection"], "source_result_engine_md5_not_per_row": document.get("result_engine_md5"), "source_result_pilot_md5_not_per_row": document.get("result_pilot_md5"), "label_computation_time": "not inferred from NPZ timestamp", "set0": document.get("initial_overrides") or None}
                    def emit(event):
                        event.update({key: value for key, value in common.items() if key != "set0" or event.get("e") == "board"})
                        if event.get("e") == "board":
                            output.write(event["t"] + "\t" + json.dumps(event, separators=(",", ":"), default=str) + "\n")
                            return
                        provenance, naked_ok, baseline_ok = current[0]
                        event.update(provenance)
                        if event.get("e") != "row":
                            counter["identity_or_nonrow_diagnostic"] += 1
                            diagnostics.write(json.dumps({"reason": "identity_or_nonrow_not_gain_label", "event": event}, separators=(",", ":"), default=str) + "\n")
                            return
                        naked = event.get("n")
                        if naked is not None and naked[-1]:
                            naked[1] = int(naked_ok and corpus.finite(naked[0]))
                        eligible_r = bool(naked and naked[-1] and naked[1] and corpus.finite(naked[0]))
                        for cell in event.get("y") or []:
                            cell[2] = int(bool(cell[2]) and baseline_ok and corpus.finite(cell[1]))
                        eligible_f = sum(bool(cell[2]) and corpus.finite(cell[1]) for cell in event.get("y") or [])
                        scope = "in_frozen_template" if f"{event.get('sh')}|{event.get('k')}" in template_rows else "outside_frozen_template_preserved"
                        event["template_scope"] = scope
                        if scope != "in_frozen_template":
                            counter["outside_frozen_template_rows"] += 1
                            diagnostics.write(json.dumps({"reason": scope, "event": event}, separators=(",", ":"), default=str) + "\n")
                        if not eligible_r and not eligible_f:
                            counter["rows_without_eligible_targets"] += 1
                            diagnostics.write(json.dumps({"reason": "no_explicit_valid_finite_target", "event": event}, separators=(",", ":"), default=str) + "\n")
                            return
                        counter["eligible_naked"] += int(eligible_r)
                        counter["eligible_marginal_cells"] += eligible_f
                        counter["emitted_rows"] += 1
                        counter["measured_zero_naked"] += int(eligible_r and naked[0] == 0)
                        counter["measured_zero_marginal"] += sum(bool(cell[2]) and cell[1] == 0 for cell in event.get("y") or [])
                        output.write(event["t"] + "\t" + json.dumps(event, separators=(",", ":"), default=str) + "\n")
                    emitter = brain.BoardEmitter("progress", symbol, brain.iso_of(end), emit, official_counts, common, entry["source_sha256"] + "|" + str(end))
                    emitter.start(brain._f(document.get("bh")), document.get("tier"), document.get("defaults_round"))
                    for key, record, provenance, naked_ok, baseline_ok in rows:
                        current[0] = provenance, naked_ok, baseline_ok
                        brain._progress_row(emitter, key, record, official_counts, category)
                    emitter.finish()
                source_reports.append(dict(entry, identity_tier=identity_tier, category=category, source_rows=sum(len(rows) for rows in groups.values()), emitted_rows=counter["emitted_rows"] - emitted_before, source_result_engine_md5_not_per_row=document.get("result_engine_md5"), source_result_pilot_md5_not_per_row=document.get("result_pilot_md5")))
        if seen_members != set(members):
            raise ValueError("source archive lacks declared members")
        if corpus.digest(archive_path) != archive_sha:
            raise ValueError("frozen source archive changed during conversion")
        report["counters_by_category"] = {category: dict(counter) for category, counter in counts.items()}
        report["official_converter_counters"] = dict(official_counts)
        report["artifacts_sha256"] = {path.name: corpus.digest(path) for path in (spill, cases)}
        if any(not counter["emitted_rows"] for counter in counts.values()):
            raise ValueError("expanded population lacks eligible examples for a category")
        report["status"] = "expanded_population_converted_not_training_approved"
    except BaseException as error:
        report.update(status="failed_ineligible", error=str(error))
        raise
    finally:
        report["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        output_report.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--template-root", required=True)
    args = parser.parse_args()
    os.environ["V18_ROOT"] = args.template_root
    from tools import v18_learn_common as common
    templates = {category: common.template_index(category, cache=False) for category in common.CAT_SIDES}
    report = convert(args.source_manifest, args.source_archive, args.out_dir, templates)
    print(json.dumps({"status": report["status"], "sources": len(report["sources"]), "counters_by_category": report["counters_by_category"]}))


if __name__ == "__main__":
    main()
