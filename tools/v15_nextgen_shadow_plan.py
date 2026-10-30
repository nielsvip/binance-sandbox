"""Pure shadow planner. Predictions and cost estimates never become board deltas."""
import hashlib
import math


def _number(value, minimum=0.0):
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError("non-finite or negative planning input")
    return value


def observation_key(era, cat_side, kind, switch, pair=None, sym_side=None):
    if cat_side not in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        raise ValueError("exact cat_side required")
    if not era or kind not in ("switch", "pair") or not switch or (kind == "pair" and not pair):
        raise ValueError("era, observation kind and canonical switch/pair required")
    return (str(era), cat_side, str(sym_side or "CAT_SIDE_SHARED"), kind, str(switch), str(pair) if kind == "pair" else "")


def shadow_plan(candidates, observations, *, era, cat_side, budget_cpu_s, sym_side=None, cycle=0, first_share=0.2, floor_every=1000):
    """Return decisions only; observations must count real evals within this engine/wiring era.

    Each candidate represents one indivisible evaluation bundle, including naked,
    applicable yellows and joint cost. Carry age must come from the prior queue.
    mandatory bundles may exceed budget; the report exposes that debt explicitly.
    """
    budget = _number(budget_cpu_s)
    share = _number(first_share)
    if share > 1 or not isinstance(floor_every, int) or not 1 <= floor_every <= 1000:
        raise ValueError("invalid exploration policy")
    items = []
    ids = set()
    for raw in candidates:
        item = dict(raw)
        identity = str(item["id"])
        if identity in ids:
            raise ValueError("duplicate candidate id")
        ids.add(identity)
        if item.get("cat_side") != cat_side or (sym_side is not None and item.get("sym_side") != sym_side):
            raise ValueError("candidate evidence scope mismatch")
        kind = "pair" if item.get("pair") is not None else "switch"
        key = observation_key(era, cat_side, kind, item["switch"], item.get("pair"), sym_side)
        count = int(observations.get(key, 0))
        if count < 0:
            raise ValueError("negative observation count")
        # No pos_sym field is consulted. Newly wired eras receive fresh observations.
        item.update(id=identity, kind=kind, first=count == 0, queue_age=int(_number(item.get("queue_age", 0))))
        item["estimated_cpu_s"] = sum(_number(item.get(k, 0)) for k in ("eval_cpu_s", "joint_cpu_s", "fixed_cpu_s"))
        if item["estimated_cpu_s"] <= 0:
            raise ValueError("positive conservative CPU cost required")
        prediction = item.get("predicted_marginal_delta")
        item["predicted_marginal_delta"] = None if prediction is None else float(prediction)
        if prediction is not None and not math.isfinite(float(prediction)):
            raise ValueError("non-finite prediction")
        digest = int(hashlib.sha256((str(era) + "|" + identity).encode()).hexdigest(), 16)
        item["rotation"] = (digest - int(cycle)) % floor_every
        item["score"] = max(0.0, float(prediction or 0)) / item["estimated_cpu_s"]
        items.append(item)
    chosen = {}
    spent = 0.0
    first_spent = {"switch": 0.0, "pair": 0.0}

    def select(item, reason):
        nonlocal spent
        chosen[item["id"]] = reason
        spent += item["estimated_cpu_s"]
        if item["first"]:
            first_spent[item["kind"]] += item["estimated_cpu_s"]

    for item in sorted(items, key=lambda x: x["id"]):
        if item.get("mandatory") or item.get("essential") or item.get("protected_evidence"):
            select(item, "KEEP_PROTECTED_EVIDENCE" if item.get("protected_evidence") else "KEEP_MANDATORY")
    # Separate quotas protect switch and pair observations, without claiming every
    # new candidate fits today. Aged carries precede rotating deterministic ties.
    kinds = ("switch", "pair") if int(cycle) % 2 == 0 else ("pair", "switch")
    for kind in kinds:
        quota = budget * share / 2
        pending = sorted((x for x in items if x["first"] and x["kind"] == kind and x["id"] not in chosen), key=lambda x: (-x["queue_age"], x["rotation"], x["id"]))
        if pending:
            quota = max(quota, pending[0]["estimated_cpu_s"])
        for item in pending:
            cost = item["estimated_cpu_s"]
            if first_spent[kind] + cost <= quota and spent + cost <= budget:
                select(item, "KEEP_FIRST_OBSERVATION")
    pending = sorted((x for x in items if x["id"] not in chosen), key=lambda x: (x["rotation"] != 0, -x["score"], -x["queue_age"], x["id"]))
    for item in pending:
        if spent + item["estimated_cpu_s"] <= budget:
            select(item, "KEEP_EXPLORATION_FLOOR" if item["rotation"] == 0 else "KEEP_PREDICTED_DELTA_PER_CPU")
    decisions = []
    for item in items:
        selected = item["id"] in chosen
        decisions.append({"id": item["id"], "selected": selected, "reason": chosen.get(item["id"], "CARRY_EXPLORATION_FLOOR_BUDGET" if item["rotation"] == 0 else "CARRY_FIRST_OBSERVATION_BUDGET" if item["first"] else "CARRY_CPU_BUDGET"), "observation_kind": item["kind"], "first_observation": item["first"], "queue_age": 0 if selected else item["queue_age"] + 1, "estimated_cpu_s": item["estimated_cpu_s"], "predicted_marginal_delta": item["predicted_marginal_delta"], "score": item["score"], "delta": None})
    return {"mode": "SHADOW_ONLY", "era": str(era), "cat_side": cat_side, "sym_side": sym_side, "evidence_scope": "sym_side" if sym_side is not None else "cat_side_shared", "floor_eligibility_period": floor_every, "guaranteed_inclusion_probability": None, "floor_caveat": "Eligibility rotates; CPU budget may carry an eligible candidate. No inclusion propensity is claimed.", "budget_cpu_s": budget, "estimated_cpu_s": spent, "mandatory_budget_overflow_cpu_s": max(0.0, spent - budget), "first_observation_estimated_cpu_s": first_spent, "decisions": decisions}
