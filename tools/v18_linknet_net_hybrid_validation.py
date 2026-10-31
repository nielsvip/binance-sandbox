#!/usr/bin/env python3
"""Fit-only count/NetV1 hybrid diagnostic on an existing NET q70 validation packet."""
import argparse
import json
from pathlib import Path

import numpy as np

from tools import v18_linknet as network
from tools import v18_linknet_net_validation as analysis

def evaluate(dataset_dir, raw_path, phase_path):
    phase = json.loads(Path(phase_path).read_text())
    category = phase["category"]
    if phase["schema"] != "v18_linknet_candidate_net/1":
        raise ValueError("NET target phase required")
    raw = dict(np.load(raw_path))
    data, keys = network.load_ds(category, dataset_dir)
    dataset_path = Path(dataset_dir) / f"ds_{category}.npz"
    if analysis.sha(dataset_path) != phase["source_dataset_sha256"] or analysis.sha(raw_path) != phase["artifacts"][Path(raw_path).name]:
        raise ValueError("NET source or saved validation identity mismatch")
    fit, validation = raw["fit_indices"], raw["validation_indices"]
    if np.intersect1d(fit, validation).size or len(np.unique(fit)) != len(fit) or len(np.unique(validation)) != len(validation):
        raise ValueError("overlapping or duplicate fit/validation indices")
    if not np.array_equal(raw["labels"], data["y"][validation]) or not np.array_equal(raw["source_i"], data["source_i"][validation]) or not np.array_equal(raw["actual_NET_delta"], data["d"][validation]):
        raise ValueError("validation labels/source identities changed")
    if not np.isfinite(raw["heads"]).all() or not np.isfinite(raw["raw_NetV1_probability"]).all():
        raise ValueError("nonfinite saved neural output")
    trainer = network.Trainer.__new__(network.Trainer)
    trainer.D, trainer.K = data, keys
    counts, _ = trainer.count_baseline(fit, validation)
    if not np.array_equal(counts, raw["fit_only_counts_probability"]):
        raise ValueError("fit-only count reconstruction is not exact")
    measured_n, measured_positive, prior = trainer._last_counts
    hybrid = network.hybrid_p(raw["raw_NetV1_probability"], measured_n, measured_positive, prior)
    if not np.isfinite(hybrid).all() or np.any((hybrid < 0) | (hybrid > 1)):
        raise ValueError("invalid reconstructed hybrid probability")
    kind, labels, delta = raw["kind"], raw["labels"], raw["actual_NET_delta"]
    calibrated = np.empty(len(labels), np.float64)
    metrics = {}
    for name, code in (("R_naked", 0), ("F_NET", 1)):
        mask = kind == code
        coefficients = network.fit_platt(hybrid[mask], labels[mask])
        calibrated[mask] = network.apply_platt(hybrid[mask], coefficients)
        score = calibrated[mask]
        clipping = lambda value: np.clip(value, 1e-15, 1 - 1e-15)
        loss = lambda value: float(-np.mean(labels[mask] * np.log(clipping(value)) + (1 - labels[mask]) * np.log1p(-clipping(value))))
        before, after = loss(hybrid[mask]), loss(score)
        if not np.isfinite(score).all() or after > before + 1e-10:
            raise ValueError("calibration degraded identity loss")
        metrics[name] = analysis._metrics(score, labels[mask]) | {"calibration": coefficients, "identity_logloss": before, "calibrated_logloss": after, "fixed_budget_curves": [analysis._retention(score, labels[mask], np.maximum(delta[mask], 0), fraction) for fraction in analysis.BUDGETS]}
    _, row_score, known_positive, rescue, positive_mass = analysis._row_summary(kind, labels, delta, calibrated, raw["source_i"])
    rows = {"n": len(row_score), "observed_positive_rows": int(known_positive.sum()), "observed_rescue_rows": int(rescue.sum()), "fixed_budget_curves": [{"known_positive_rows": analysis._retention(row_score, known_positive, positive_mass, fraction), "known_rescue_rows": analysis._retention(row_score, rescue, positive_mass, fraction)} for fraction in analysis.BUDGETS]}
    report = {"schema": "v18_linknet_NET_hybrid_validation/1", "category": category, "target": phase["target"], "scope": "Existing selected q70 NET NetV1 weights, fit-only counts reconstructed EXACTLY from saved fit_indices; existing hybrid_p formula/m=8; repaired Platt fitted on SAME validation labels. Validation was used for early stopping/calibration/diagnostics: not an independent test or S2 quality/time result. No retraining, q80/final refit, serving update or threshold adoption.", "row_scope": "max of observed candidate probabilities is a ranking proxy on recorded candidates, not calibrated P(any), full-template maximum, or complete-row negative evidence; missing trials remain unknown", "source_dataset_sha256": analysis.sha(dataset_path), "raw_validation_sha256": analysis.sha(raw_path), "phase_sha256": analysis.sha(phase_path), "analysis_source_sha256": analysis.sha(__file__), "network_source_sha256": analysis.sha(network.__file__), "budget_analysis_source_sha256": analysis.sha(analysis.__file__), "exact_fit_only_counts_reconstruction": True, "head_metrics": metrics, "observed_row_proxy": rows}
    packet = {"validation_indices": validation, "fit_indices": fit, "source_i": raw["source_i"], "kind": kind, "labels": labels, "actual_NET_delta": delta, "raw_hybrid_v1": hybrid, "calibrated_hybrid_v1": calibrated, "fit_only_measured_n": measured_n, "fit_only_positive_count": measured_positive, "fit_only_prior": prior}
    return report, packet

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    report, packet = evaluate(args.dataset, args.raw, args.phase)
    report["actual_observation_utc"] = network.utc_now()
    packet_path = output / f"hybrid_NET_validation_{report['category']}.npz"
    np.savez_compressed(packet_path, **packet)
    report["packet_sha256"] = analysis.sha(packet_path)
    (output / "hybrid_NET_validation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
