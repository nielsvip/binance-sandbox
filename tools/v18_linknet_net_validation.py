#!/usr/bin/env python3
"""Analyze q70 LinkNet NET validation outputs without treating them as holdout evidence."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import v18_linknet as network

SCHEMA = "v18_linknet_net_validation_analysis/1"
DATASET_SCHEMA = "v18_linknet_candidate_net/1"
TARGET = "valid measured absolute candidate delta versus same event cumulative baseline > EPS"
BUDGETS = (0.01, 0.03, 0.05, 0.10, 0.20)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()


def _vector(data, key, dtype=None):
    value = np.asarray(data[key], dtype=dtype)
    if value.ndim != 1:
        raise ValueError(f"{key} must be one-dimensional")
    return value


def _fractional_selection(scores, fraction):
    """Return inclusion weights for an exact budget, sharing the boundary across ties."""
    scores = np.asarray(scores, np.float64)
    if scores.ndim != 1 or not np.isfinite(scores).all() or not 0 < fraction <= 1:
        raise ValueError("finite scores and a budget in (0, 1] are required")
    target = fraction * len(scores)
    include = np.zeros(len(scores), np.float64)
    if not len(scores):
        return include
    order = np.argsort(-scores, kind="mergesort")
    remaining = target
    start = 0
    while start < len(order) and remaining > 1e-12:
        end = start + 1
        while end < len(order) and scores[order[end]] == scores[order[start]]:
            end += 1
        group = order[start:end]
        take = min(1.0, remaining / len(group))
        include[group] = take
        remaining -= take * len(group)
        start = end
    return include


def _retention(scores, labels, positive_mass, fraction):
    selected = _fractional_selection(scores, fraction)
    labels = np.asarray(labels, np.float64)
    positive_mass = np.asarray(positive_mass, np.float64)
    if labels.shape != selected.shape or positive_mass.shape != selected.shape:
        raise ValueError("selection, labels and positive mass must have matching shapes")
    positive = labels > 0.5
    total_count = float(positive.sum())
    total_mass = float(positive_mass[positive].sum())
    retained_count = float(selected[positive].sum())
    retained_mass = float((selected * positive_mass * positive).sum())
    return {
        "budget_fraction": fraction,
        "selected_equivalent_n": float(selected.sum()),
        "observed_positive_count_total": int(positive.sum()),
        "observed_positive_count_retained": retained_count,
        "observed_positive_count_retention": retained_count / total_count if total_count else None,
        "measured_positive_NET_label_mass_total": total_mass,
        "measured_positive_NET_label_mass_retained": retained_mass,
        "measured_positive_NET_label_mass_retention": retained_mass / total_mass if total_mass > 0 else None,
    }


def _metrics(probability, labels):
    probability = np.asarray(probability, np.float64)
    labels = np.asarray(labels, np.float64)
    if not len(labels):
        return {"n": 0, "positive": 0, "prevalence": None, "auc": None, "brier": None}
    return {
        "n": int(len(labels)),
        "positive": int((labels > 0.5).sum()),
        "prevalence": float(np.mean(labels > 0.5)),
        "auc": network.auc(probability, labels),
        "brier": float(np.mean((probability - labels) ** 2)),
    }


def _row_summary(kind, labels, deltas, predictions, source_i):
    rows = {}
    for i, source in enumerate(source_i):
        item = rows.setdefault(int(source), {"scores": [], "r_seen": False, "r_positive": False, "f_positive": False, "f_positive_mass": 0.0})
        item["scores"].append(float(predictions[i]))
        if kind[i] == 0:
            item["r_seen"] = True
            item["r_positive"] |= bool(labels[i] > 0.5)
        elif kind[i] == 1:
            if labels[i] > 0.5:
                item["f_positive"] = True
                item["f_positive_mass"] += max(0.0, float(deltas[i]))
    source_ids = np.asarray(sorted(rows), np.int64)
    score = np.asarray([max(rows[int(source)]["scores"]) for source in source_ids], np.float64)
    known_positive = np.asarray([rows[int(source)]["r_positive"] or rows[int(source)]["f_positive"] for source in source_ids], bool)
    rescue = np.asarray([rows[int(source)]["r_seen"] and not rows[int(source)]["r_positive"] and rows[int(source)]["f_positive"] for source in source_ids], bool)
    mass = np.asarray([rows[int(source)]["f_positive_mass"] for source in source_ids], np.float64)
    return source_ids, score, known_positive.astype(np.float64), rescue.astype(np.float64), mass


def analyze_arrays(validation, dataset, calibration, source_records):
    """Analyze one exact validation slice; source_i is the only R/F row join key."""
    indices = _vector(validation, "validation_indices", np.int64)
    if len(np.unique(indices)) != len(indices):
        raise ValueError("validation indices must be unique")
    if np.any(indices < 0) or np.any(indices >= len(dataset["kind"])):
        raise ValueError("validation index is outside the exact dataset")
    source_i = _vector(validation, "source_i", np.int64)
    kind = _vector(validation, "kind", np.int8)
    labels = _vector(validation, "labels", np.float64)
    deltas = _vector(validation, "actual_NET_delta", np.float64)
    raw_net = _vector(validation, "raw_NetV1_probability", np.float64)
    raw_counts = _vector(validation, "fit_only_counts_probability", np.float64)
    if any(len(x) != len(indices) for x in (source_i, kind, labels, deltas, raw_net, raw_counts)):
        raise ValueError("validation arrays do not share one row order")
    if not np.isfinite(labels).all() or not np.isin(labels, (0.0, 1.0)).all() or not np.isfinite(deltas).all():
        raise ValueError("validation labels/deltas must be finite and labels binary")
    if not np.array_equal(labels, (deltas > network.VB.EPS).astype(np.float64)):
        raise ValueError("validation labels do not match measured NET-delta positivity")
    if not np.array_equal(source_i, np.asarray(dataset["source_i"], np.int64)[indices]):
        raise ValueError("raw validation source_i does not match exact dataset rows")
    if not np.array_equal(kind, np.asarray(dataset["kind"], np.int8)[indices]):
        raise ValueError("raw validation kind does not match exact dataset rows")
    if not np.array_equal(labels, np.asarray(dataset["y"], np.float64)[indices]):
        raise ValueError("raw validation labels do not match exact dataset rows")
    if not np.allclose(deltas, np.asarray(dataset["d"], np.float64)[indices], rtol=0.0, atol=1e-6):
        raise ValueError("raw validation NET deltas do not match exact dataset rows")
    if not np.isin(kind, (0, 1)).all():
        raise ValueError("only R naked and F_NET validation examples are in scope")
    if len(source_i) and (source_i.min() < 0 or any(int(i) not in source_records for i in np.unique(source_i))):
        raise ValueError("source_i does not resolve to a source-sidecar record")
    if np.any(~np.isfinite(raw_net)) or np.any(~np.isfinite(raw_counts)):
        raise ValueError("saved validation probabilities must be finite")
    if np.any((raw_net < 0) | (raw_net > 1)) or np.any((raw_counts < 0) | (raw_counts > 1)):
        raise ValueError("saved validation probabilities must lie in [0, 1]")
    net_p = np.empty(len(kind), np.float64)
    counts_p = np.empty(len(kind), np.float64)
    for code, name in ((0, "R_naked"), (1, "F_NET")):
        mask = kind == code
        if not mask.any():
            continue
        if name not in calibration or "NetV1" not in calibration[name] or "fit_only_counts" not in calibration[name]:
            raise ValueError(f"calibration is missing {name} coefficients")
        net_p[mask] = network.apply_platt(raw_net[mask], calibration[name]["NetV1"])
        counts_p[mask] = network.apply_platt(raw_counts[mask], calibration[name]["fit_only_counts"])
    fmask = kind == 1
    f_label = labels[fmask]
    f_mass = np.where(f_label > 0.5, np.maximum(0.0, deltas[fmask]), 0.0)
    metrics = {}
    for name, probability in (("NetV1", net_p), ("fit_only_counts", counts_p)):
        per_cell = probability[fmask]
        metrics[name] = {
            "per_cell_F_NET_discrimination": _metrics(per_cell, f_label),
            "per_cell_F_NET_budget_retention": {
                f"top_{int(fraction * 100)}pct": _retention(per_cell, f_label, f_mass, fraction) for fraction in BUDGETS
            },
        }
        _, row_score, row_positive, row_rescue, row_mass = _row_summary(kind, labels, deltas, probability, source_i)
        rbudget = {}
        for fraction in BUDGETS:
            selected = _fractional_selection(row_score, fraction)
            positive_count = float((selected * row_positive).sum())
            rescue_count = float((selected * row_rescue).sum())
            positive_total = float(row_positive.sum())
            rescue_total = float(row_rescue.sum())
            positive_mass_total = float(row_mass.sum())
            positive_mass_retained = float((selected * row_mass).sum())
            rbudget[f"top_{int(fraction * 100)}pct"] = {
                "budget_fraction": fraction,
                "selected_equivalent_n": float(selected.sum()),
                "represented_source_rows": int(len(row_score)),
                "observed_positive_rows_total": int(positive_total),
                "observed_positive_rows_retained": positive_count,
                "observed_positive_row_retention": positive_count / positive_total if positive_total else None,
                "observed_rescue_rows_total": int(rescue_total),
                "observed_rescue_rows_retained": rescue_count,
                "observed_rescue_row_retention": rescue_count / rescue_total if rescue_total else None,
                "measured_positive_F_NET_label_mass_total": positive_mass_total,
                "measured_positive_F_NET_label_mass_retained": positive_mass_retained,
                "measured_positive_F_NET_label_mass_retention": positive_mass_retained / positive_mass_total if positive_mass_total else None,
                "row_score": "max(calibrated R naked prediction, calibrated measured F_NET predictions), grouped only by exact source_i",
            }
        metrics[name]["observed_candidate_row_budget_retention"] = rbudget
    source_identity = [source_records[int(i)].get("source_record_identity") for i in np.unique(source_i)]
    if any(not isinstance(value, str) or len(value) != 64 for value in source_identity):
        raise ValueError("validation source_i lacks pinned source-record identity")
    if len(set(source_identity)) != len(source_identity):
        raise ValueError("multiple source_i values resolve to one source-record identity")
    return {
        "schema": SCHEMA,
        "dataset_schema": DATASET_SCHEMA,
        "target": TARGET,
        "scope": "q70 validation slice only; early-stopping/calibration reuse; not an untouched test, S2 holdout, serving result, runtime reduction or portfolio gain",
        "probability_calibration": "apply saved kind-specific Platt coefficients from the same validation phase; no refit in this analysis",
        "row_grouping": "exact source_i only; absent/incomplete candidates are unknown and never labeled negative",
        "row_score_semantics": "max of observed R naked and measured F_NET predictions is an observed-candidate ranking proxy, not calibrated P(any candidate wins)",
        "complete_negative_row_labels": 0,
        "row_negative_policy": "a validation source row without an observed positive label remains unknown; no complete-menu negatives are constructed",
        "mass_semantics": "sum of positive measured absolute NET deltas; diagnostic label mass, not portfolio gain",
        "validation_examples": int(len(indices)),
        "validation_source_rows": int(len(np.unique(source_i))),
        "validation_F_NET_examples": int(fmask.sum()),
        "validation_F_NET_positive": int((f_label > 0.5).sum()),
        "validation_F_NET_positive_mass": float(f_mass.sum()),
        "known_positive_source_rows": int(sum(1 for i in np.unique(source_i) if any(kind[j] == 0 and labels[j] > 0.5 for j in np.flatnonzero(source_i == i)) or any(kind[j] == 1 and labels[j] > 0.5 for j in np.flatnonzero(source_i == i)))),
        "known_rescue_source_rows": int(sum(1 for i in np.unique(source_i) if any(kind[j] == 0 for j in np.flatnonzero(source_i == i)) and not any(kind[j] == 0 and labels[j] > 0.5 for j in np.flatnonzero(source_i == i)) and any(kind[j] == 1 and labels[j] > 0.5 for j in np.flatnonzero(source_i == i)))),
        "models": metrics,
        "source_record_identity_sha256": hashlib.sha256("\n".join(sorted(source_identity)).encode()).hexdigest(),
    }


def _load_sources(path, needed):
    records = {}
    with gzip.open(path, "rt") as source:
        for index, line in enumerate(source):
            if index in needed:
                records[index] = json.loads(line)
    missing = needed - set(records)
    if missing:
        raise ValueError(f"source sidecar does not contain source_i values: {sorted(missing)[:5]}")
    return records


def analyze_files(dataset_path, sources_path, keys_path, validation_path, phase_path):
    with np.load(dataset_path, allow_pickle=False) as file:
        dataset = {key: file[key] for key in file.files}
    with np.load(validation_path, allow_pickle=False) as file:
        validation = {key: file[key] for key in file.files}
    phase = json.loads(Path(phase_path).read_text())
    keys = json.loads(gzip.open(keys_path, "rt").read())
    if keys.get("schema") != DATASET_SCHEMA or keys.get("target") != TARGET:
        raise ValueError("dataset keys do not describe the source-keyed NET dataset")
    if phase.get("schema") != DATASET_SCHEMA or phase.get("target") != TARGET:
        raise ValueError("phase receipt does not describe the source-keyed NET training")
    if phase.get("source_dataset_sha256") != sha(dataset_path):
        raise ValueError("phase source dataset hash does not match supplied dataset")
    if phase.get("category") != keys.get("cat_side"):
        raise ValueError("dataset and phase category do not match")
    needed = set(map(int, np.unique(validation["source_i"])))
    sources = _load_sources(sources_path, needed)
    result = analyze_arrays(validation, dataset, phase["calibration"], sources)
    result.update({
        "category": phase["category"],
        "captured_utc": network.utc_now(),
        "analysis_code_sha256": sha(Path(__file__)),
        "input_sha256": {
            "dataset_npz": sha(dataset_path),
            "dataset_keys_gz": sha(keys_path),
            "dataset_sources_gz": sha(sources_path),
            "raw_validation_npz": sha(validation_path),
            "phase_finished_json": sha(phase_path),
        },
        "training_phase_scope": phase.get("scope"),
        "selected_epoch": phase.get("selected_epoch"),
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="exact ds_<CAT>.npz")
    parser.add_argument("--sources", required=True, help="exact ds_<CAT>_sources.jsonl.gz")
    parser.add_argument("--keys", required=True, help="exact ds_<CAT>_keys.json.gz")
    parser.add_argument("--validation", required=True, help="raw_NET_validation_<CAT>.npz")
    parser.add_argument("--phase", required=True, help="net_phase_finished.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = analyze_files(args.dataset, args.sources, args.keys, args.validation, args.phase)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as target:
        target.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(output), "category": report["category"], "validation_F_NET_examples": report["validation_F_NET_examples"], "known_positive_source_rows": report["known_positive_source_rows"]}))


if __name__ == "__main__":
    main()
