#!/usr/bin/env python3
"""Isolated historical candidate-NET dataset and q70 NetV1 experiment; no serving integration."""
import argparse
import array
import collections
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time

import numpy as np

from tools import v18_linknet as network

SCHEMA = "v18_linknet_candidate_net/1"
TARGET = "valid measured absolute candidate delta versus same event cumulative baseline > EPS"
SOURCE_SHA256 = "b0590af8b985a630ec5902046d7a9935734cc6ac49684e3163fd4463411bfac5"
ROUNDING_TOLERANCE = 2e-5

def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()

def finite(value):
    try:
        return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False

def same_baseline(proof, baseline):
    return finite(proof.get("cum_before")) and round(float(proof["cum_before"]), 5) == round(float(baseline), 5)

def net_event(event, counters):
    """Keep measured NET labels with original source identity; absence never becomes a negative."""
    brain = network.VB
    naked = event.get("n")
    proof = (event.get("eval_provenance") or {}).get("naked") or {}
    valid_identity = all(isinstance(event.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", event[key]) for key in ("source_sha256", "source_record_identity"))
    if event.get("e") != "row" or event.get("src") != "progress" or event.get("ts_source") != "explicit_progress_row_npz" or not valid_identity or not all(event.get(key) for key in ("progress_key", "archive_member", "source_file")):
        counters["unknown_source"] += 1
        return None
    end = brain.npz_ts_of(event.get("explicit_row_npz"))
    if not finite(event.get("cb")) or end is None or end != brain.npz_ts_of(event.get("npz")) or not finite(event.get("market_end")) or float(event["market_end"]) != end:
        counters["unknown_same_context"] += 1
        return None
    naked_ok = isinstance(naked, list) and len(naked) >= 10 and naked[1] == 1 and naked[-1] == 1 and finite(naked[0]) and proof.get("valid") is True and proof.get("cached") is False and not proof.get("err") and not proof.get("invalid_reason") and proof.get("evaluation_success") is not False and same_baseline(proof, event["cb"]) and not event.get("is_running") and not str(event.get("raw_reason") or event.get("reason") or "").startswith(("RUNNING_IDENTITY", "DEAD_VEC_PATH", "NOT_WIRED_VEC"))
    if not naked_ok:
        counters["unknown_naked"] += 1
    excluded = set((event.get("yellow_reasons") or {}).keys())
    for key in brain.PROGRESS_SETTLED:
        if key != "noop_yellows":
            excluded.update(event.get(key) or [])
    measured, conflicts = {}, set()
    originals = list(event.get("y") or [])
    noops = brain.source_noop_gain_cells(event, counters) if naked_ok else []
    for position, cell in enumerate(originals + noops):
        if not isinstance(cell, (list, tuple)) or len(cell) < 11:
            counters["unknown_cell"] += 1
            continue
        header, marginal, valid, absolute = str(cell[0]).strip(), cell[1], cell[2], cell[10]
        cell_proof = (event.get("eval_provenance") or {}).get(header)
        proof_ok = position >= len(originals) or (isinstance(cell_proof, dict) and cell_proof.get("valid") is True and cell_proof.get("cached") is False and not cell_proof.get("err") and not cell_proof.get("invalid_reason") and cell_proof.get("evaluation_success") is not False and same_baseline(cell_proof, event["cb"]))
        consistent = finite(absolute) and (not naked_ok or (finite(marginal) and abs(float(absolute) - float(naked[0]) - float(marginal)) <= ROUNDING_TOLERANCE))
        good = valid == 1 and finite(absolute) and brain.YELLOW_RE.fullmatch(header) and header not in excluded and proof_ok and consistent
        if not good:
            conflicts.add(header)
            counters["unknown_cell"] += 1
            continue
        if header in measured:
            if measured[header] != float(absolute):
                conflicts.add(header)
                counters["conflicting_duplicate"] += 1
            else:
                counters["duplicate_cell"] += 1
            continue
        measured[header] = float(absolute)
    for header in conflicts:
        measured.pop(header, None)
    out = copy.copy(event)
    out["y"] = [[header, delta, 1, 0.0, None, None, None, None, None, 1.0, delta] for header, delta in measured.items()]
    out.pop("nz", None)
    if not measured and not naked_ok:
        return None
    out["n"] = list(naked) if naked_ok else None
    if naked_ok:
        out["n"][3:8] = [None] * 5
        out["n"][8] = 1.0
    counters["source_rows"] += 1
    counters["R_positive"] += int(naked_ok and float(naked[0]) > brain.EPS)
    counters["R_nonpositive"] += int(naked_ok and float(naked[0]) <= brain.EPS)
    counters["F_positive_NET"] += sum(delta > brain.EPS for delta in measured.values())
    counters["F_nonpositive_NET"] += sum(delta <= brain.EPS for delta in measured.values())
    counters["rescued_rows"] += int(naked_ok and float(naked[0]) <= brain.EPS and any(delta > brain.EPS for delta in measured.values()))
    counters["F_with_unknown_naked"] += len(measured) if not naked_ok else 0
    return out

class NetDataset(network.CatDS):
    def __init__(self, category):
        super().__init__(category)
        self.source_indices = array.array("i")
        self.sources = []
        self.identities = set()

    def source_row(self, event, timestamp):
        identity = event.get("source_record_identity")
        if identity in self.identities:
            self.c["duplicate_source_record"] += 1
            return
        transformed = net_event(event, self.c)
        if transformed is None:
            return
        self.identities.add(identity)
        before = len(self.ex.c["kind"])
        self.row(transformed, timestamp)
        emitted = len(self.ex.c["kind"]) - before
        if not emitted:
            return
        source_i = len(self.sources)
        keys = ("source_record_identity", "source_sha256", "archive_member", "progress_key", "s", "sh", "k", "b", "t", "cb", "npz", "explicit_row_npz", "market_end", "engine_hash_per_row")
        proof_sha256 = hashlib.sha256(json.dumps(event.get("eval_provenance"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.sources.append({key: event.get(key) for key in keys} | {"eval_provenance_sha256": proof_sha256, "row_completeness": "unknown"})
        self.source_indices.extend([source_i] * emitted)

    def save(self, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        columns = self.ex.np()
        columns["source_i"] = np.asarray(self.source_indices, np.int32)
        path = output / f"ds_{self.cs}.npz"
        np.savez_compressed(path, **columns)
        keys = {"schema": SCHEMA, "target": TARGET, "cat_side": self.cs, "feature_mode": self.feature_mode, "sets": self.sets.keys, "filts": self.filts.keys, "set_combos": self.scombo.keys, "filt_combos": self.fcombo.keys, "ctxs": [list(context) for context in self.ctxs.keys], "boards": self.boards.keys, "xctxs": [list(context) for context in self.xctx.keys], "counters": dict(self.c), "built_at": network.utc_now(), "current_engine_authority": False}
        with gzip.open(output / f"ds_{self.cs}_keys.json.gz", "wt") as target:
            json.dump(keys, target)
        with gzip.open(output / f"ds_{self.cs}_sources.jsonl.gz", "wt") as target:
            for source in self.sources:
                target.write(json.dumps(source, separators=(",", ":")) + "\n")
        return path

class NetTrainer(network.Trainer):
    def __init__(self, category, dataset, cohort):
        self.source_cohort = json.loads(Path(cohort).read_text())
        super().__init__(category, ds_dir=dataset, k=16, H=64, batch=4096, lr=0.002, max_epochs=8, patience=2, seed=7)
        if self.K.get("schema") != SCHEMA or self.K.get("target") != TARGET:
            raise ValueError("NET target dataset required; marginal datasets cannot be substituted")
        self.M[:, 2:] = 0.0

    def splits(self, q_val=0.70, q_test=0.80):
        data = self.D
        self.cut_val = network.VB.epoch_of(self.source_cohort["chronological_cut_validation"])
        self.cut_test = network.VB.epoch_of(self.source_cohort["chronological_cut_test"])
        if self.cut_val is None or self.cut_test is None or self.cut_val >= self.cut_test:
            raise ValueError("original board-group chronology required")
        baseline, timestamp = data["b_t0"], data["ts"]
        eligible = network.gain_mask(data)
        return {"fit": (baseline < self.cut_val) & (timestamp < self.cut_val) & eligible, "val": (baseline >= self.cut_val) & (baseline < self.cut_test) & (timestamp < self.cut_test) & eligible, "test": (baseline >= self.cut_test) & eligible}, {}

def build(args):
    if sha(args.spill) != SOURCE_SHA256:
        raise ValueError("exact original b059 historical source required")
    if network.HF.mode() != "context_free":
        raise ValueError("original context_free feature mode required")
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    spill = Path(args.spill).resolve()
    neighbors = list(spill.parent.glob("spill_*.tsv")) + list(spill.parent.glob("spill_*.tsv.gz"))
    if len(neighbors) != 1 or neighbors[0].resolve() != spill:
        raise ValueError("source directory must contain only the pinned original spill")
    temporary = Path(args.tmp)
    temporary.mkdir(parents=True, exist_ok=False)
    sorted_path = temporary / "net_events_sorted.tsv"
    network.VB.sorted_events([str(spill.parent)], temporary, sorted_path)
    report = {"schema": SCHEMA, "target": TARGET, "source": str(spill), "source_sha256": SOURCE_SHA256, "source_code_sha256": sha(__file__), "network_code_sha256": sha(network.__file__), "built_at": network.utc_now(), "categories": {}, "current_engine_authority": False, "precision": "normalized spill absolute/marginal deltas and cb rounded5; NPZ tags do not prove exact-byte NPZ identity", "row_utility_negatives": "unknown; no full-row negatives fabricated", "sort": "original stable timestamp sorted_events; one category resident at a time"}
    for category in network.CAT_SIDES:
        dataset = NetDataset(category)
        with sorted_path.open() as source:
            for line in source:
                timestamp, _, payload = line.partition("\t")
                event = json.loads(payload)
                if network.VB.cat_side_of(event.get("s")) != category:
                    continue
                if event.get("e") == "board":
                    dataset.board_event(event)
                elif event.get("e") == "row":
                    dataset.source_row(event, timestamp)
        path = dataset.save(output)
        cohort_path = Path(args.cohorts) / f"cohort_{category}.json"
        cohort = json.loads(cohort_path.read_text())
        cut_val = network.VB.epoch_of(cohort["chronological_cut_validation"])
        cut_test = network.VB.epoch_of(cohort["chronological_cut_test"])
        if cut_val is None or cut_test is None or cut_val >= cut_test:
            raise ValueError("original board-group chronology required")
        kind = np.frombuffer(dataset.ex.c["kind"], np.int8)
        labels = np.frombuffer(dataset.ex.c["y"], np.int8)
        baseline = np.frombuffer(dataset.ex.c["b_t0"], np.float64)
        timestamp = np.frombuffer(dataset.ex.c["ts"], np.float64)
        splits = {"fit": (baseline < cut_val) & (timestamp < cut_val), "val": (baseline >= cut_val) & (baseline < cut_test) & (timestamp < cut_test), "test": baseline >= cut_test}
        support = {}
        for name, mask in splits.items():
            support[name] = {name: {"n": int(np.sum(mask & (kind == code))), "positive": int(np.sum(labels[mask & (kind == code)]))} for name, code in (("R_naked", 0), ("F_NET", 1))}
        report["categories"][category] = {"counters": dict(dataset.c), "examples": len(dataset.source_indices), "source_rows": len(dataset.sources), "cohort_sha256": sha(cohort_path), "cut_val": network.VB.iso_of(cut_val), "cut_test": network.VB.iso_of(cut_test), "support": support, "artifacts": {name.name: sha(name) for name in (path, output / f"ds_{category}_keys.json.gz", output / f"ds_{category}_sources.jsonl.gz")}}
        del dataset, kind, labels, baseline, timestamp, splits
        print(json.dumps({category: report["categories"][category]}), flush=True)
    report["sorted_source_sha256"] = sha(sorted_path)
    (output / "net_dataset_report.json").write_text(json.dumps(report, indent=2))

def train(args):
    if network.HF.mode() != "context_free":
        raise ValueError("original context_free feature mode required")
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    trainer = NetTrainer(args.category, args.dataset, args.cohort)
    splits, _ = trainer.splits()
    fit, validation = np.flatnonzero(splits["fit"]), np.flatnonzero(splits["val"])
    if not len(fit) or not len(validation):
        raise ValueError("nonempty chronological fit/validation required")
    started = time.monotonic()
    model, epoch, history = trainer.fit(fit, val_rows=validation, tag="NET-q70-only")
    heads = trainer.predict(model, validation)
    probabilities = network.sigmoid(heads[:, 0])
    counts, amounts = trainer.count_baseline(fit, validation)
    calibration, metrics = {}, {}
    labels, kinds = trainer.D["y"][validation], trainer.D["kind"][validation]
    for kind, code in (("R_naked", 0), ("F_NET", 1)):
        mask = kinds == code
        calibration[kind], metrics[kind] = {}, {}
        for name, prediction in (("NetV1", probabilities), ("fit_only_counts", counts)):
            coefficients = network.fit_platt(prediction[mask], labels[mask])
            calibrated = network.apply_platt(prediction[mask], coefficients)
            calibration[kind][name] = coefficients
            metrics[kind][name] = {"n": int(mask.sum()), "positive": int(labels[mask].sum()), "auc": network.auc(calibrated, labels[mask]), "Brier": float(np.mean((calibrated - labels[mask]) ** 2)) if mask.any() else None}
    metadata = {"schema": SCHEMA, "target": TARGET, "category": args.category, "feature_mode": "context_free", "vocab": trainer.fz.names, "selected_epoch": epoch, "calibration": calibration, "source_dataset_sha256": sha(Path(args.dataset) / f"ds_{args.category}.npz"), "cohort_sha256": sha(args.cohort), "scope": "q70-prefix early stopping plus SAME-validation calibration; no q80/test/final refit, not independent quality evidence"}
    model.save(output / f"selected_NET_q70_{args.category}.npz", metadata)
    np.savez_compressed(output / f"raw_NET_validation_{args.category}.npz", fit_indices=fit, validation_indices=validation, source_i=trainer.D["source_i"][validation], heads=heads, raw_NetV1_probability=probabilities, fit_only_counts_probability=counts, fit_only_counts_positive_arithmetic_amount=amounts, kind=kinds, labels=labels, actual_NET_delta=trainer.D["d"][validation], predicted_positive_geometric_amount=np.expm1(np.clip(heads[:, 1], 0, 6)))
    report = metadata | {"finished_at": network.utc_now(), "secs": time.monotonic() - started, "history": history, "validation_metrics": metrics, "source_code_sha256": sha(__file__), "network_code_sha256": sha(network.__file__), "artifacts": {path.name: sha(path) for path in output.glob("*.npz")}}
    (output / "net_phase_finished.json").write_text(json.dumps(report, indent=2))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    dataset = commands.add_parser("dataset")
    dataset.add_argument("--spill", required=True)
    dataset.add_argument("--cohorts", required=True)
    dataset.add_argument("--tmp", required=True)
    dataset.add_argument("--out", required=True)
    training = commands.add_parser("train-q70")
    training.add_argument("--category", choices=network.CAT_SIDES, required=True)
    training.add_argument("--dataset", required=True)
    training.add_argument("--cohort", required=True)
    training.add_argument("--out", required=True)
    args = parser.parse_args()
    {"dataset": build, "train-q70": train}[args.command](args)

if __name__ == "__main__":
    main()
