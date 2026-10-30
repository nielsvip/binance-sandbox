"""Read a frozen delta-log sample and replay a disconnected planning shadow."""
import argparse
from collections import Counter, defaultdict
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.v15_nextgen_shadow_plan import observation_key, shadow_plan


def replay(rows, *, cat_side, fixed_cpu_estimate_s, cpu_per_wall_estimate, budget_fraction=0.1):
    if fixed_cpu_estimate_s < 0 or cpu_per_wall_estimate <= 0 or not 0 < budget_fraction <= 1:
        raise ValueError("explicit positive cost assumptions required")
    clean = [r for r in rows if isinstance(r, dict)]
    midpoint = len(clean) // 2
    measured = [r for r in clean if r.get("cached") is False and isinstance(r.get("secs"), (float, int)) and r["secs"] > 0 and r.get("switch")]
    if not measured:
        raise ValueError("no measured elapsed timings in sample")
    # These are only elapsed-time proxies. Parallel wait and scheduling overhead
    # cannot be converted to CPU without an independently measured scale factor.
    p90 = sorted(r["secs"] for r in measured)[min(len(measured) - 1, int(len(measured) * 0.9))]
    history = defaultdict(list)
    candidates = {}
    observations = Counter()
    eras = sorted({str(r["round"]) for r in clean if r.get("round")})
    era = "sample:" + hashlib.sha256("|".join(eras).encode()).hexdigest()[:16]
    sym_sides = {r.get("sym_side") for r in clean if r.get("sym_side")}
    if len(sym_sides) != 1:
        raise ValueError("adapter requires one explicit sym_side per sample")
    sym_side = next(iter(sym_sides))
    for index, row in enumerate(clean):
        key = row.get("key")
        switch = row.get("switch") or (str(key).split("=", 1)[0] if key else None)
        cand = row.get("cand", str(key).split("=", 1)[1] if key and "=" in str(key) else "")
        if not switch:
            continue
        label = str(row.get("label") or "")
        pair = row.get("filter") or (label if "=" in label and label not in ("ROW_DONE", "SETTLE", "ROW_SETTLED") else None)
        pair = f"{cand}|{pair}" if pair else None
        kind = "pair" if pair else "switch"
        identity = "|".join((str(row.get("sheet") or ""), switch, str(cand), pair or "NAKED"))
        obs_key = observation_key(era, cat_side, kind, switch, pair, sym_side)
        evaluated = row.get("cached") is False and row.get("valid") is True and isinstance(row.get("delta"), (float, int)) and isinstance(row.get("secs"), (float, int))
        if index < midpoint:
            if evaluated:
                observations[obs_key] += 1
                history[identity].append(float(row["delta"]))
            continue
        if label in ("ROW_DONE", "ROW_SETTLED", "BOARD_CTX"):
            continue
        prior = history.get(identity, [])
        predicted = statistics.median(prior) if prior else None
        code = str(row.get("code") or row.get("reason_code") or "")
        candidates.setdefault(identity, {"id": identity, "cat_side": cat_side, "sym_side": sym_side, "switch": switch, "pair": pair, "eval_cpu_s": p90 * cpu_per_wall_estimate, "joint_cpu_s": p90 * cpu_per_wall_estimate, "fixed_cpu_s": fixed_cpu_estimate_s, "predicted_marginal_delta": predicted, "protected_evidence": any(value > 0 for value in prior) or code == "KEEP_EVIDENCE", "mandatory": str(row.get("phase")) in ("MUST", "ESSENTIAL"), "queue_age": 0, "source_code": code})
    if not candidates:
        raise ValueError("no candidates after chronological split")
    full_estimate = sum(c["eval_cpu_s"] + c["joint_cpu_s"] + c["fixed_cpu_s"] for c in candidates.values())
    plan = shadow_plan(list(candidates.values()), observations, era=era, cat_side=cat_side, sym_side=sym_side, budget_cpu_s=full_estimate * budget_fraction)
    return {"mode": "SHADOW_ONLY", "sample_records": len(clean), "fresh_elapsed_samples": len(measured), "measured_elapsed_s_p90": p90, "candidate_count": len(candidates), "historical_observation_keys": len(observations), "first_observation_candidates": sum(d["first_observation"] for d in plan["decisions"]), "selector_codes": dict(Counter(c["source_code"] or "EVALUATED_OR_UNTAGGED" for c in candidates.values())), "selection_reasons": dict(Counter(d["reason"] for d in plan["decisions"])), "full_plan_estimated_cpu_s": full_estimate, "budget_fraction_requested": budget_fraction, "cost_assumptions": {"cpu_seconds_per_elapsed_second_estimate": cpu_per_wall_estimate, "fixed_cpu_seconds_per_bundle_estimate": fixed_cpu_estimate_s, "joint": "one additional p90 elapsed proxy per indivisible bundle"}, "limitations": ["No measured CPU reduction; elapsed timing is not CPU", "Only candidates present in a bounded log tail are represented", "Era is sampled round metadata, not independently verified loaded-code identity", "Prediction uses only first half of the sample; it is not an efficacy claim", "No board recompute, output changes, publication or production integration"], "plan": plan}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sample", type=Path)
    parser.add_argument("--cat-side", required=True)
    parser.add_argument("--fixed-cpu-estimate-s", required=True, type=float)
    parser.add_argument("--cpu-per-wall-estimate", required=True, type=float)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.sample.read_text().splitlines() if line.strip()]
    result = replay(rows, cat_side=args.cat_side, fixed_cpu_estimate_s=args.fixed_cpu_estimate_s, cpu_per_wall_estimate=args.cpu_per_wall_estimate)
    result.update(at_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), source_sample=str(args.sample), source_sha256=hashlib.sha256(args.sample.read_bytes()).hexdigest())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
