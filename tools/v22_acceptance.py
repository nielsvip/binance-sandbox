"""Read-only aggregate acceptance check for a pinned v21/v22 NN comparison."""

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path


EXPECTED_SNAPSHOT = "snap_20261030T210717Z_current8_r2"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_FIELDS = {
    "snapshot_id",
    "snapshot_manifest_sha256",
    "runtime_sha256",
    "engine_sha256",
    "evaluation_policy_sha256",
}


def _sha256(value, label):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256")


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric data")
    return float(value)


def _utc(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must be UTC")
    return parsed


def _identity(value, label):
    if not isinstance(value, dict) or set(value) != IDENTITY_FIELDS:
        raise ValueError(f"{label} must declare the exact common runtime/input identity fields")
    if value["snapshot_id"] != EXPECTED_SNAPSHOT:
        raise ValueError(f"{label} is not pinned to {EXPECTED_SNAPSHOT}")
    for key in IDENTITY_FIELDS - {"snapshot_id"}:
        _sha256(value[key], f"{label}.{key}")
    return value


def _read_snapshot(payload):
    snapshot = payload.get("snapshot_manifest")
    if not isinstance(snapshot, dict) or set(snapshot) != {"path", "sha256"}:
        raise ValueError("snapshot_manifest must give the pinned manifest path and SHA256")
    path = Path(snapshot["path"])
    _sha256(snapshot["sha256"], "snapshot_manifest.sha256")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
        raise ValueError("snapshot manifest content does not match its declared SHA256")
    manifest = json.loads(raw)
    pinned_path = path.parent.name == EXPECTED_SNAPSHOT
    if manifest.get("id") != EXPECTED_SNAPSHOT and manifest.get("snapshot_id") != EXPECTED_SNAPSHOT and not pinned_path:
        raise ValueError("snapshot manifest is not the pinned current8_r2 snapshot")
    sides = manifest.get("sides")
    if not isinstance(sides, list) or len(sides) != 8 or len(set(sides)) != 8 or any(not isinstance(side, str) for side in sides):
        raise ValueError("pinned snapshot manifest must declare exactly eight unique sides")
    return set(sides), snapshot["sha256"]


def comparison_identity_sha256(common_identity, sides, score_kind):
    value = {"common_identity": common_identity, "sides": sorted(sides), "score_kind": score_kind}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluate(payload):
    """Validate evidence and return aggregate thresholds plus per-side diagnostics."""
    if not isinstance(payload, dict) or payload.get("schema") != "v22_nn_acceptance_input/1":
        raise ValueError("unknown acceptance input schema")
    comparison_id = payload.get("comparison_id")
    if not isinstance(comparison_id, str) or not comparison_id.strip():
        raise ValueError("comparison_id is required")
    if payload.get("score_kind") != "oos_gain_pct":
        raise ValueError("acceptance must use actual OOS gain_pct scores, not fit results")
    sides, manifest_sha = _read_snapshot(payload)
    common_identity = _identity(payload.get("common_identity"), "common_identity")
    if common_identity["snapshot_manifest_sha256"] != manifest_sha:
        raise ValueError("common identity does not bind the pinned snapshot manifest")
    gate = payload.get("s2_gate_decision")
    if not isinstance(gate, dict):
        raise ValueError("existing S2 gate decision evidence is required")
    if (gate.get("baseline_version"), gate.get("candidate_version")) not in {("v20", "v21"), ("v21", "v22")}:
        raise ValueError("S2 gate must compare the immediately prior NN version: v20→v21 or v21→v22")
    gate_pass = gate.get("status") == "PASS"
    if not isinstance(gate.get("decision_id"), str) or not gate["decision_id"].strip():
        raise ValueError("S2 gate decision_id is required")
    _sha256(gate.get("evidence_sha256"), "s2_gate_decision.evidence_sha256")
    identity_sha = comparison_identity_sha256(common_identity, sides, payload["score_kind"])
    if gate.get("input_identity_sha256") != identity_sha:
        raise ValueError("S2 gate input identity does not match this comparison")

    results = payload.get("sides")
    if not isinstance(results, dict) or set(results) != sides:
        missing = sorted(sides - set(results or {})) if isinstance(results, dict) else sorted(sides)
        extra = sorted(set(results or {}) - sides) if isinstance(results, dict) else []
        raise ValueError(f"comparison coverage differs from pinned snapshot; missing={missing}, extra={extra}")
    model_maps = {arm: {} for arm in ("baseline", "candidate")}
    rows = {}
    for side in sorted(sides):
        record = results[side]
        if not isinstance(record, dict) or set(record) != {"baseline", "candidate"}:
            raise ValueError(f"{side} must contain baseline and candidate measurements")
        parsed = {}
        for arm in ("baseline", "candidate"):
            sample = record[arm]
            required = {"identity", "model_sha256", "training_window_end_utc", "oos_start_utc", "oos_gain_pct", "wall_seconds"}
            if not isinstance(sample, dict) or set(sample) != required:
                raise ValueError(f"{side}.{arm} has an invalid OOS measurement shape")
            if _identity(sample["identity"], f"{side}.{arm}.identity") != common_identity:
                raise ValueError(f"{side}.{arm} identity differs from common frozen runtime/input")
            _sha256(sample["model_sha256"], f"{side}.{arm}.model_sha256")
            train_end = _utc(sample["training_window_end_utc"], f"{side}.{arm}.training_window_end_utc")
            oos_start = _utc(sample["oos_start_utc"], f"{side}.{arm}.oos_start_utc")
            if train_end >= oos_start:
                raise ValueError(f"{side}.{arm} training window overlaps or reaches its OOS window")
            parsed[arm] = {
                "gain_pct": _finite(sample["oos_gain_pct"], f"{side}.{arm}.oos_gain_pct"),
                "wall_seconds": _finite(sample["wall_seconds"], f"{side}.{arm}.wall_seconds"),
                "training_window_end_utc": train_end,
                "oos_start_utc": oos_start,
            }
            if parsed[arm]["wall_seconds"] <= 0:
                raise ValueError(f"{side}.{arm}.wall_seconds must be positive")
            model_maps[arm][side] = sample["model_sha256"]
        if parsed["baseline"]["oos_start_utc"] != parsed["candidate"]["oos_start_utc"]:
            raise ValueError(f"{side} baseline and candidate OOS windows differ")
        baseline, candidate = parsed["baseline"], parsed["candidate"]
        delta = candidate["gain_pct"] - baseline["gain_pct"]
        side_pct = None if baseline["gain_pct"] == 0 else 100.0 * delta / abs(baseline["gain_pct"])
        rows[side] = {
            "baseline_oos_gain_pct": baseline["gain_pct"],
            "candidate_oos_gain_pct": candidate["gain_pct"],
            "oos_gain_delta_pct_points": delta,
            "oos_relative_change_pct": side_pct,
            "candidate_is_profitable": candidate["gain_pct"] > 0,
            "baseline_wall_seconds": baseline["wall_seconds"],
            "candidate_wall_seconds": candidate["wall_seconds"],
            "wall_reduction_pct": 100.0 * (baseline["wall_seconds"] - candidate["wall_seconds"]) / baseline["wall_seconds"],
            "side_regression": delta < 0,
        }
    for arm in ("baseline", "candidate"):
        if gate.get(f"{arm}_model_sha256_by_side") != model_maps[arm]:
            raise ValueError(f"S2 decision's consumed {arm} model identities do not match comparison results")

    baseline_sum = sum(row["baseline_oos_gain_pct"] for row in rows.values())
    candidate_sum = sum(row["candidate_oos_gain_pct"] for row in rows.values())
    if baseline_sum == 0:
        raise ValueError("aggregate baseline is zero; relative OOS quality improvement is undefined")
    quality_change = 100.0 * (candidate_sum - baseline_sum) / abs(baseline_sum)
    baseline_wall = sum(row["baseline_wall_seconds"] for row in rows.values())
    candidate_wall = sum(row["candidate_wall_seconds"] for row in rows.values())
    time_reduction = 100.0 * (baseline_wall - candidate_wall) / baseline_wall
    if not math.isfinite(quality_change) or not math.isfinite(time_reduction):
        raise ValueError("aggregate derived metric is non-finite")
    if baseline_sum < 0 and candidate_sum < 0:
        quality_label = "aggregate_loss_reduction_pct" if candidate_sum > baseline_sum else "aggregate_loss_worsening_pct"
    elif baseline_sum < 0 <= candidate_sum:
        quality_label = "aggregate_loss_to_positive_gain_pct"
    elif candidate_sum < 0:
        quality_label = "aggregate_positive_gain_to_loss_pct"
    else:
        quality_label = "aggregate_positive_gain_change_pct"
    quality_pass = quality_change >= 20.0
    speed_pass = time_reduction > 90.0
    reasons = []
    if not gate_pass:
        reasons.append(f"existing S2 gate decision state is {gate.get('status')!r}, not PASS")
    if not quality_pass:
        reasons.append("aggregate OOS quality change is below 20%")
    if not speed_pass:
        reasons.append("aggregate whole-board wall reduction is not greater than 90%")
    return {
        "schema": "v22_nn_acceptance_result/1",
        "comparison_id": comparison_id,
        "s2_decision_id": gate["decision_id"],
        "baseline_version": gate["baseline_version"],
        "candidate_version": gate["candidate_version"],
        "snapshot_id": EXPECTED_SNAPSHOT,
        "coverage": {"expected": len(sides), "measured": len(rows), "complete": True},
        "score_kind": payload["score_kind"],
        "aggregate_baseline_oos_gain_pct": baseline_sum,
        "aggregate_candidate_oos_gain_pct": candidate_sum,
        "aggregate_oos_quality_change_pct": quality_change,
        "quality_change_label": quality_label,
        "aggregate_candidate_is_profitable": candidate_sum > 0,
        "quality_pass": quality_pass,
        "baseline_whole_board_wall_seconds": baseline_wall,
        "candidate_whole_board_wall_seconds": candidate_wall,
        "whole_board_time_reduction_pct": time_reduction,
        "speed_pass": speed_pass,
        "s2_gate_pass": gate_pass,
        "accepted": gate_pass and quality_pass and speed_pass,
        "reasons": reasons,
        "sides": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison_json", help="read-only v22_nn_acceptance_input/1 JSON file")
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(Path(args.comparison_json).read_text()))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"accepted": False, "evidence_valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
