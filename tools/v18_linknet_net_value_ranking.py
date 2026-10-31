#!/usr/bin/env python3
"""q70 diagnostic ranking curves from saved NET probabilities and amount predictions."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import v18_linknet as network

SCHEMA = "v18_linknet_net_value_ranking/1"
DATASET_SCHEMA = "v18_linknet_candidate_net/1"
TARGET = "valid measured absolute candidate delta versus same event cumulative baseline > EPS"
BUDGETS = (0.01, 0.03, 0.05, 0.10, 0.20)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()


def vector(data, key, dtype=None):
    value = np.asarray(data[key], dtype=dtype)
    if value.ndim != 1:
        raise ValueError(f"{key} must be one-dimensional")
    return value


def fractional_selection(scores, fraction):
    scores = np.asarray(scores, np.float64)
    if scores.ndim != 1 or not np.isfinite(scores).all() or not 0 < fraction <= 1:
        raise ValueError("finite scores and a budget in (0, 1] are required")
    selected = np.zeros(len(scores), np.float64)
    remaining = fraction * len(scores)
    order = np.argsort(-scores, kind="mergesort")
    start = 0
    while start < len(order) and remaining > 1e-12:
        end = start + 1
        while end < len(order) and scores[order[end]] == scores[order[start]]:
            end += 1
        group = order[start:end]
        take = min(1.0, remaining / len(group))
        selected[group] = take
        remaining -= take * len(group)
        start = end
    return selected


def retention(scores, labels, positive_mass, fraction):
    selected = fractional_selection(scores, fraction)
    positive = np.asarray(labels) > 0.5
    mass = np.asarray(positive_mass, np.float64)
    if mass.shape != selected.shape or positive.shape != selected.shape:
        raise ValueError("score, label and mass shapes differ")
    total_count = int(positive.sum())
    total_mass = float(mass[positive].sum())
    retained_count = float(selected[positive].sum())
    retained_mass = float((selected * mass * positive).sum())
    return {
        "budget_fraction": fraction,
        "selected_equivalent_n": float(selected.sum()),
        "observed_positive_count_total": total_count,
        "observed_positive_count_retained": retained_count,
        "observed_positive_count_retention": retained_count / total_count if total_count else None,
        "measured_positive_NET_label_mass_total": total_mass,
        "measured_positive_NET_label_mass_retained": retained_mass,
        "measured_positive_NET_label_mass_retention": retained_mass / total_mass if total_mass > 0 else None,
    }


def load_needed_sources(path, needed):
    records = {}
    with gzip.open(path, "rt") as source:
        for index, line in enumerate(source):
            if index in needed:
                records[index] = json.loads(line)
    if needed - set(records):
        raise ValueError("source_i is absent from source sidecar")
    return records


def make_value_scores(validation, phase):
    kind = vector(validation, "kind", np.int8)
    raw_net = vector(validation, "raw_NetV1_probability", np.float64)
    raw_counts = vector(validation, "fit_only_counts_probability", np.float64)
    arithmetic = vector(validation, "fit_only_counts_positive_arithmetic_amount", np.float64)
    geometric = vector(validation, "predicted_positive_geometric_amount", np.float64)
    size = len(kind)
    if any(len(a) != size for a in (raw_net, raw_counts, arithmetic, geometric)):
        raise ValueError("raw validation probability and amount arrays differ in length")
    if not np.isin(kind, (0, 1)).all() or not all(np.isfinite(a).all() for a in (raw_net, raw_counts, arithmetic, geometric)):
        raise ValueError("raw validation arrays must be finite R/F rows")
    if np.any((raw_net < 0) | (raw_net > 1)) or np.any((raw_counts < 0) | (raw_counts > 1)) or np.any(arithmetic < 0) or np.any(geometric < 0):
        raise ValueError("probabilities must be in [0,1] and positive-amount estimates nonnegative")
    net_p, counts_p = np.empty(size, np.float64), np.empty(size, np.float64)
    for code, name in ((0, "R_naked"), (1, "F_NET")):
        mask = kind == code
        if not mask.any():
            continue
        c = phase["calibration"].get(name, {})
        if "NetV1" not in c or "fit_only_counts" not in c:
            raise ValueError(f"saved calibration is missing {name}")
        net_p[mask] = network.apply_platt(raw_net[mask], c["NetV1"])
        counts_p[mask] = network.apply_platt(raw_counts[mask], c["fit_only_counts"])
    return {
        "NetV1_probability_only": net_p,
        "fit_only_counts_probability_only": counts_p,
        "NetV1_x_fit_arithmetic_amount": net_p * arithmetic,
        "fit_only_counts_x_fit_arithmetic_amount": counts_p * arithmetic,
        "NetV1_x_predicted_geometric_amount": net_p * geometric,
    }


def source_row_curves(scores, kinds, labels, deltas, source_i):
    rows = {}
    for j, source in enumerate(source_i):
        row = rows.setdefault(int(source), {"scores": [], "r_seen": False, "r_positive": False, "f_positive": False, "f_mass": 0.0})
        row["scores"].append(float(scores[j]))
        if kinds[j] == 0:
            row["r_seen"] = True
            row["r_positive"] |= bool(labels[j] > 0.5)
        elif kinds[j] == 1 and labels[j] > 0.5:
            row["f_positive"] = True
            row["f_mass"] += max(0.0, float(deltas[j]))
    ids = np.asarray(sorted(rows), np.int64)
    row_scores = np.asarray([max(rows[int(i)]["scores"]) for i in ids], np.float64)
    positive = np.asarray([rows[int(i)]["r_positive"] or rows[int(i)]["f_positive"] for i in ids], np.float64)
    rescue = np.asarray([rows[int(i)]["r_seen"] and not rows[int(i)]["r_positive"] and rows[int(i)]["f_positive"] for i in ids], np.float64)
    mass = np.asarray([rows[int(i)]["f_mass"] for i in ids], np.float64)
    return row_scores, positive, rescue, mass


def analyze(validation, dataset, phase, source_records):
    indices = vector(validation, "validation_indices", np.int64)
    source_i = vector(validation, "source_i", np.int64)
    kinds = vector(validation, "kind", np.int8)
    labels = vector(validation, "labels", np.float64)
    deltas = vector(validation, "actual_NET_delta", np.float64)
    if len(np.unique(indices)) != len(indices) or np.any(indices < 0) or np.any(indices >= len(dataset["kind"])):
        raise ValueError("validation_indices must be unique valid dataset rows")
    if any(len(v) != len(indices) for v in (source_i, kinds, labels, deltas)):
        raise ValueError("validation vectors have inconsistent lengths")
    for key, observed, dtype in (("source_i", source_i, np.int64), ("kind", kinds, np.int8), ("y", labels, np.float64)):
        if not np.array_equal(observed, np.asarray(dataset[key], dtype=dtype)[indices]):
            raise ValueError(f"raw validation {key} does not match exact dataset rows")
    if not np.allclose(deltas, np.asarray(dataset["d"], np.float64)[indices], rtol=0.0, atol=1e-6):
        raise ValueError("raw validation NET deltas do not match exact dataset rows")
    if not np.array_equal(labels, (deltas > network.VB.EPS).astype(np.float64)):
        raise ValueError("labels do not match measured absolute NET delta positivity")
    if np.any(source_i < 0) or any(int(i) not in source_records for i in np.unique(source_i)):
        raise ValueError("source_i does not resolve to a source-sidecar row")
    identities = [source_records[int(i)].get("source_record_identity") for i in np.unique(source_i)]
    if any(not isinstance(i, str) or len(i) != 64 for i in identities) or len(set(identities)) != len(identities):
        raise ValueError("source sidecar identities are invalid or duplicated")
    score_sets = make_value_scores(validation, phase)
    fmask = kinds == 1
    f_labels = labels[fmask]
    f_mass = np.where(f_labels > 0.5, np.maximum(0.0, deltas[fmask]), 0.0)
    results = {}
    for name, scores in score_sets.items():
        per_cell_scores = scores[fmask]
        per_cell = {f"top_{int(frac * 100)}pct": retention(per_cell_scores, f_labels, f_mass, frac) for frac in BUDGETS}
        row_scores, positive_rows, rescue_rows, row_mass = source_row_curves(scores, kinds, labels, deltas, source_i)
        per_row = {}
        for frac in BUDGETS:
            selected = fractional_selection(row_scores, frac)
            npos, nrescue = float(positive_rows.sum()), float(rescue_rows.sum())
            mass_total, mass_selected = float(row_mass.sum()), float((selected * row_mass).sum())
            positive_retained, rescue_retained = float((selected * positive_rows).sum()), float((selected * rescue_rows).sum())
            per_row[f"top_{int(frac * 100)}pct"] = {
                "budget_fraction": frac,
                "selected_equivalent_source_rows": float(selected.sum()),
                "observed_positive_source_rows": int(npos),
                "observed_positive_source_rows_retained": positive_retained,
                "observed_positive_source_row_retention": positive_retained / npos if npos else None,
                "known_rescue_source_rows": int(nrescue),
                "known_rescue_source_rows_retained": rescue_retained,
                "known_rescue_source_row_retention": rescue_retained / nrescue if nrescue else None,
                "measured_positive_NET_label_mass": mass_total,
                "measured_positive_NET_label_mass_retained": mass_selected,
                "measured_positive_NET_label_mass_retention": mass_selected / mass_total if mass_total else None,
            }
        results[name] = {"per_cell_F_NET": per_cell, "observed_source_row_max_proxy": per_row}
    return {
        "schema": SCHEMA,
        "dataset_schema": DATASET_SCHEMA,
        "target": TARGET,
        "category": phase["category"],
        "scope": "q70 validation slice; early-stopping and calibration reuse; not an untouched test or S2 holdout",
        "amount_semantics": "arithmetic fit-only amount and predicted geometric amount are value-ranking proxies; retained labels are measured absolute NET deltas, not unbiased expected gains or portfolio returns",
        "row_grouping": "exact source_i only; row score is max over observed R/F predictions; incomplete candidate rows remain unknown",
        "known_rescue_definition": "validation source_i with observed nonpositive naked R label and at least one measured positive F_NET label",
        "complete_negative_row_labels": 0,
        "validation_examples": int(len(indices)),
        "validation_F_NET_examples": int(fmask.sum()),
        "validation_F_NET_positive": int((f_labels > 0.5).sum()),
        "validation_positive_NET_label_mass": float(f_mass.sum()),
        "validation_source_rows": int(len(np.unique(source_i))),
        "source_record_identity_sha256": hashlib.sha256("\n".join(sorted(identities)).encode()).hexdigest(),
        "rankings": results,
    }


def analyze_files(dataset_path, sources_path, keys_path, validation_path, phase_path):
    with np.load(dataset_path, allow_pickle=False) as file:
        dataset = {key: file[key] for key in file.files}
    with np.load(validation_path, allow_pickle=False) as file:
        validation = {key: file[key] for key in file.files}
    with gzip.open(keys_path, "rt") as source:
        keys = json.load(source)
    phase = json.loads(Path(phase_path).read_text())
    if keys.get("schema") != DATASET_SCHEMA or keys.get("target") != TARGET or phase.get("schema") != DATASET_SCHEMA or phase.get("target") != TARGET:
        raise ValueError("dataset keys or phase schema/target mismatch")
    if phase.get("category") != keys.get("cat_side") or phase.get("source_dataset_sha256") != sha(dataset_path):
        raise ValueError("dataset category or hash does not match completed phase")
    needed = set(map(int, np.unique(validation["source_i"])))
    sources = load_needed_sources(sources_path, needed)
    result = analyze(validation, dataset, phase, sources)
    result.update({
        "captured_utc": network.utc_now(),
        "selected_epoch": phase.get("selected_epoch"),
        "training_phase_scope": phase.get("scope"),
        "analysis_code_sha256": sha(Path(__file__)),
        "input_sha256": {
            "dataset_npz": sha(dataset_path),
            "dataset_sources_gz": sha(sources_path),
            "dataset_keys_gz": sha(keys_path),
            "raw_validation_npz": sha(validation_path),
            "phase_finished_json": sha(phase_path),
        },
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sources", required=True)
    parser.add_argument("--keys", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = analyze_files(args.dataset, args.sources, args.keys, args.validation, args.phase)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"category": result["category"], "out": str(output), "validation_F_NET_examples": result["validation_F_NET_examples"], "value_proxy_count": len(result["rankings"])}))


if __name__ == "__main__":
    main()
