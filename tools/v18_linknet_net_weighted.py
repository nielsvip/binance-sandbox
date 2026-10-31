#!/usr/bin/env python3
"""Isolated q70 NET trainer with fit-only square-root inverse-frequency BCE weights."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from tools import v18_linknet as linknet
from tools import v18_linknet_net as net_recipe

SCHEMA = net_recipe.SCHEMA
TARGET = net_recipe.TARGET
PINNED_NET_RECIPE_SHA256 = "51d86d02617e73168424513552b5add339e076ac540c2021488761f4111f0184"
PINNED_LINKNET_SHA256 = "4e8977a43d95a05aeb2db27d40456afd5a23516e326010267e662c373d1fa21e"
WEIGHTING = "fit-only per-kind square-root inverse-frequency BCE weights; fit-weight mean normalized to 1"


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(4 << 20), b""):
            value.update(block)
    return value.hexdigest()


def indices_sha(indices):
    return hashlib.sha256(np.asarray(indices, dtype=np.int64).tobytes()).hexdigest()


def verify_source_pins():
    recipe_sha = sha(net_recipe.__file__)
    linknet_sha = sha(linknet.__file__)
    if recipe_sha != PINNED_NET_RECIPE_SHA256 or linknet_sha != PINNED_LINKNET_SHA256:
        raise ValueError("pinned original NET recipe or LinkNet source changed")
    return {"net_recipe_sha256": recipe_sha, "linknet_sha256": linknet_sha}


def fit_only_class_weights(kinds, labels, base_mask, fit_rows):
    kinds = np.asarray(kinds, np.int8)
    labels = np.asarray(labels, np.float64)
    base_mask = np.asarray(base_mask, np.float64)
    fit_rows = np.asarray(fit_rows, np.int64)
    if kinds.ndim != 1 or labels.shape != kinds.shape or base_mask.shape != kinds.shape:
        raise ValueError("kind, label and base mask arrays must be aligned vectors")
    if fit_rows.ndim != 1 or len(np.unique(fit_rows)) != len(fit_rows) or np.any(fit_rows < 0) or np.any(fit_rows >= len(kinds)):
        raise ValueError("fit rows must be unique valid indices")
    if not np.isfinite(labels).all() or not np.isin(labels, (0.0, 1.0)).all() or not np.isfinite(base_mask).all() or np.any(base_mask < 0):
        raise ValueError("labels must be binary and base masks finite/nonnegative")
    if not np.isin(kinds[fit_rows], (0, 1)).all():
        raise ValueError("fit rows must contain only R naked and F_NET examples; forward rows are disabled")
    result = {}
    for kind, name in ((0, "R_naked"), (1, "F_NET")):
        rows = fit_rows[kinds[fit_rows] == kind]
        if not len(rows):
            raise ValueError(f"no fit rows for {name}")
        y = labels[rows]
        base = base_mask[rows]
        positive, negative = int(y.sum()), int((y == 0).sum())
        if not positive or not negative or float(base.sum()) <= 0:
            raise ValueError(f"both classes with positive base-mask mass are required for {name}")
        positive_fraction = positive / len(rows)
        raw_positive = 1.0 / np.sqrt(positive_fraction)
        raw_negative = 1.0 / np.sqrt(1.0 - positive_fraction)
        class_factor = np.where(y > 0.5, raw_positive, raw_negative)
        normalizer = float(np.dot(base, class_factor) / base.sum())
        positive_weight, negative_weight = raw_positive / normalizer, raw_negative / normalizer
        mean_weight = float(np.dot(base, np.where(y > 0.5, positive_weight, negative_weight)) / base.sum())
        if not np.isclose(mean_weight, 1.0, rtol=0.0, atol=1e-12):
            raise ValueError(f"normalized fit weight mean is not 1 for {name}")
        result[kind] = {"name": name, "positive": float(positive_weight), "negative": float(negative_weight)}
        result[name] = {
            "kind_code": kind,
            "n_fit": int(len(rows)),
            "positive_fit": positive,
            "negative_fit": negative,
            "positive_fraction_fit": float(positive_fraction),
            "base_mask_sum_fit": float(base.sum()),
            "raw_inverse_sqrt": {"positive": float(raw_positive), "negative": float(raw_negative)},
            "normalized_class_weight": {"positive": float(positive_weight), "negative": float(negative_weight)},
            "base_mask_weighted_mean": mean_weight,
        }
    return result


def apply_class_weights_to_batch(mask, kinds, labels, class_weights, primary_rows=None):
    weighted = np.asarray(mask, np.float32).copy()
    kinds = np.asarray(kinds, np.int8)
    labels = np.asarray(labels, np.float32)
    if weighted.ndim != 2 or weighted.shape[1] < 2 or kinds.ndim != 1 or labels.shape != kinds.shape:
        raise ValueError("BCE masks and aligned kind/label vectors are required")
    n = len(kinds) if primary_rows is None else int(primary_rows)
    if n != len(kinds) or n > len(weighted):
        raise ValueError("primary batch length does not match gathered R/F rows")
    for kind in (0, 1):
        selected = np.flatnonzero(kinds == kind)
        if not len(selected):
            continue
        weights = class_weights.get(kind)
        if not isinstance(weights, dict) or "positive" not in weights or "negative" not in weights:
            raise ValueError(f"missing class weights for kind {kind}")
        weighted[selected, 0] *= np.where(labels[selected] > 0.5, float(weights["positive"]), float(weights["negative"]))
    return weighted


class SqrtWeightedNetTrainer(net_recipe.NetTrainer):
    def __init__(self, category, dataset, cohort):
        super().__init__(category, dataset, cohort)
        self.fit_class_weights = None
        self.fit_class_weight_metadata = None

    def set_fit_class_weights(self, fit_rows):
        self.fit_class_weights = fit_only_class_weights(self.D["kind"], self.D["y"], self.M[:, 0], fit_rows)
        self.fit_class_weight_metadata = {key: value for key, value in self.fit_class_weights.items() if isinstance(key, str)}

    def _gather(self, rows, fwd_rows=None, drop=True, rng=None):
        if self.fit_class_weights is None:
            raise ValueError("fit-only class weights must be computed from q70 fit rows before training")
        idx, x, Y, M = super()._gather(rows, fwd_rows=fwd_rows, drop=drop, rng=rng)
        kinds = self.D["kind"][rows]
        labels = self.D["y"][rows]
        M = apply_class_weights_to_batch(M, kinds, labels, self.fit_class_weights, primary_rows=len(rows))
        return idx, x, Y, M


def train(args):
    if net_recipe.network.HF.mode() != "context_free":
        raise ValueError("original context_free feature mode required")
    pins = verify_source_pins()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    trainer = SqrtWeightedNetTrainer(args.category, args.dataset, args.cohort)
    splits, _ = trainer.splits()
    fit, validation = np.flatnonzero(splits["fit"]), np.flatnonzero(splits["val"])
    if not len(fit) or not len(validation):
        raise ValueError("nonempty chronological q70 fit/validation required")
    trainer.set_fit_class_weights(fit)
    dataset_path = Path(args.dataset) / f"ds_{args.category}.npz"
    keys_path = Path(args.dataset) / f"ds_{args.category}_keys.json.gz"
    sources_path = Path(args.dataset) / f"ds_{args.category}_sources.jsonl.gz"
    dataset_pins = {"dataset_npz_sha256": sha(dataset_path), "dataset_keys_sha256": sha(keys_path), "dataset_sources_sha256": sha(sources_path), "cohort_sha256": sha(args.cohort)}
    fit_weight_path = output / "fit_class_weights.json"
    fit_weight_path.write_text(json.dumps({"schema": "v18_net_fit_class_weights/1", "weighting": WEIGHTING, "fit_only": True, "category": args.category, "source_pins": pins | dataset_pins, "fit_indices_sha256": indices_sha(fit), "fit_indices_count": int(len(fit)), "weights": trainer.fit_class_weight_metadata}, indent=2) + "\n")
    started = time.monotonic()
    model, epoch, history = trainer.fit(fit, val_rows=validation, tag="NET-q70-sqrt-inv-frequency")
    heads = trainer.predict(model, validation)
    probabilities = linknet.sigmoid(heads[:, 0])
    counts, amounts = trainer.count_baseline(fit, validation)
    calibration, metrics = {}, {}
    labels, kinds = trainer.D["y"][validation], trainer.D["kind"][validation]
    for kind, code in (("R_naked", 0), ("F_NET", 1)):
        mask = kinds == code
        calibration[kind], metrics[kind] = {}, {}
        for name, prediction in (("NetV1", probabilities), ("fit_only_counts", counts)):
            coefficients = linknet.fit_platt(prediction[mask], labels[mask])
            calibrated = linknet.apply_platt(prediction[mask], coefficients)
            calibration[kind][name] = coefficients
            metrics[kind][name] = {"n": int(mask.sum()), "positive": int(labels[mask].sum()), "auc": linknet.auc(calibrated, labels[mask]), "Brier": float(np.mean((calibrated - labels[mask]) ** 2)) if mask.any() else None}
    metadata = {"schema": SCHEMA, "target": TARGET, "category": args.category, "feature_mode": "context_free", "vocab": trainer.fz.names, "selected_epoch": epoch, "calibration": calibration, "source_dataset_sha256": dataset_pins["dataset_npz_sha256"], "source_dataset_keys_sha256": dataset_pins["dataset_keys_sha256"], "source_dataset_sources_sha256": dataset_pins["dataset_sources_sha256"], "cohort_sha256": dataset_pins["cohort_sha256"], "fit_indices_sha256": indices_sha(fit), "validation_indices_sha256": indices_sha(validation), "scope": "q70-prefix weighted early stopping plus SAME-validation calibration; no q80/test/final refit, not independent quality evidence", "weighting_variant": WEIGHTING, "fit_class_weights": trainer.fit_class_weight_metadata, "fit_class_weights_sha256": sha(fit_weight_path), "loss_recipe": "only BCE mask M[:,0] is scaled by fit-derived per-kind class weight; Y, amount mask M[:,1], and disabled M[:,2:] remain unchanged", "forward_training": "disabled as in original NET q70 recipe", "source_pins": pins}
    model_path = output / f"selected_NET_q70_sqrt_weighted_{args.category}.npz"
    model.save(model_path, metadata)
    raw_path = output / f"raw_NET_validation_sqrt_weighted_{args.category}.npz"
    np.savez_compressed(raw_path, fit_indices=fit, validation_indices=validation, source_i=trainer.D["source_i"][validation], heads=heads, raw_NetV1_probability=probabilities, fit_only_counts_probability=counts, fit_only_counts_positive_arithmetic_amount=amounts, kind=kinds, labels=labels, actual_NET_delta=trainer.D["d"][validation], predicted_positive_geometric_amount=np.expm1(np.clip(heads[:, 1], 0, 6)), fit_class_weights_json=np.frombuffer(json.dumps(trainer.fit_class_weight_metadata, sort_keys=True).encode(), dtype=np.uint8))
    report = metadata | {"finished_at": linknet.utc_now(), "secs": time.monotonic() - started, "history": history, "validation_metrics": metrics, "source_code_sha256": sha(__file__), "network_code_sha256": sha(linknet.__file__), "net_recipe_code_sha256": sha(net_recipe.__file__), "artifacts": {path.name: sha(path) for path in (fit_weight_path, model_path, raw_path)}}
    (output / "net_weighted_phase_finished.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"category": args.category, "selected_epoch": epoch, "fit_class_weights": trainer.fit_class_weight_metadata, "out": str(output)}, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", choices=net_recipe.network.CAT_SIDES, required=True)
    parser.add_argument("--dataset", required=True, help="existing immutable NET dataset directory")
    parser.add_argument("--cohort", required=True, help="existing immutable category cohort JSON")
    parser.add_argument("--out", required=True, help="new unique output directory")
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
