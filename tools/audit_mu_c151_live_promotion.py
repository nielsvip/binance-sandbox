#!/usr/bin/env python3
"""Fail-closed live-promotion audit for the frozen MU_LONG candidate 151.

This is deliberately separate from ``run_mu_ladder_pareto_holdout.py``.  The
70--80 receipt remains historical evidence; this tool applies a *prospective*
65--80 deployment contract to the already-sealed candidate and then checks the
extra things a research replay cannot prove:

* the evidence really used a $2,000 side-aware B&H leg and a $16,000 cap;
* completed-parent causality, side isolation, solvency and reclaim invariants;
* a formal exact-v3 schedule/accounting replay;
* freshness for a deployment decision; and
* equivalence between the frozen signal/state machine and the ordinary live
  Tradier decision path (not the engine-private research schedule route).

The command never edits live configuration.  It returns 0 only when promotion
is allowed and 2 for an ordinary fail-closed audit result.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_NAME = "MU_C151_LIVE_PROMOTION_V1_TIM65_80"
CANDIDATE_NUMBER = 151
CANDIDATE_LABEL = (
    "DAILY_DEEP_center_plateau_green_TARGET_CAP8_"
    "close_confirm_next_availability"
)
TIM_BAND = (65.0, 80.0)
BASE_UNIT_USD = 2_000.0
CAPACITY_USD = 16_000.0
MIN_BH_MULTIPLE = 2.0
MIN_EQUITY_USD = 7_500.0
MAX_DATA_AGE_HOURS = 36.0

DEFAULT_FROZEN_RECEIPT = (
    ROOT
    / "data/reports/vec_research/"
    "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260727.json"
)
DEFAULT_TIM65_RECEIPT = (
    ROOT
    / "data/reports/vec_research/"
    "MU_DAILY_DEEP_PARETO_HOLDOUT_RECEIPT_20260729_TIM65.json"
)
DEFAULT_STABILITY_RECEIPT = (
    ROOT
    / "data/reports/vec_research/MU_LADDER_STABILITY_RECEIPT_20260727.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _constant(path: Path, name: str) -> float | None:
    """Read a literal module constant without importing production code."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        try:
            return float(ast.literal_eval(value))
        except (ValueError, TypeError):
            return None
    return None


def prospective_contract() -> dict[str, Any]:
    """The deployment rule, independent of the evidence being audited."""
    return {
        "contract": CONTRACT_NAME,
        "purpose": "prospective_live_deployment_gate_for_already_sealed_candidate",
        "not_a_holdout_preregistration": True,
        "symbol": "MU",
        "side": "LONG",
        "candidate_number": CANDIDATE_NUMBER,
        "candidate_label": CANDIDATE_LABEL,
        "weighted_time_in_market_pct_each_fold": list(TIM_BAND),
        "minimum_multiple_of_positive_side_aware_bh_each_fold": MIN_BH_MULTIPLE,
        "benchmark_base_usd": BASE_UNIT_USD,
        "hard_capacity_usd": CAPACITY_USD,
        "minimum_equity_usd_each_fold": MIN_EQUITY_USD,
        "maximum_data_age_hours": MAX_DATA_AGE_HOURS,
        "required": {
            "long_short_ledgers_isolated": True,
            "completed_parent_only": True,
            "future_htf_count": 0,
            "next_strictly_later_availability_fill": True,
            "zero_capacity_breaches": True,
            "zero_exact_refusals": True,
            "reclaim_never_flat_beyond_stored_level": True,
            "exact_v3_signal_accounting_tim_capacity_parity": True,
            "ordinary_live_decision_path_parity": True,
            "current_live_engine_hash_revalidated": True,
            "fresh_data": True,
            "shadow_canary_and_rollback_snapshot_before_activation": True,
        },
        "activation_policy": "all_gates_must_pass; otherwise preserve_incumbent",
    }


def _check(name: str, passed: bool, evidence: Any, blocker: str | None = None) -> dict[str, Any]:
    row = {"name": name, "pass": bool(passed), "evidence": evidence}
    if not passed and blocker:
        row["blocker"] = blocker
    return row


def audit(
    *,
    root: Path,
    frozen_receipt_path: Path,
    tim65_receipt_path: Path,
    stability_receipt_path: Path,
    as_of: datetime,
) -> dict[str, Any]:
    frozen = json.loads(frozen_receipt_path.read_text())
    tim65 = json.loads(tim65_receipt_path.read_text())
    stability = json.loads(stability_receipt_path.read_text())

    ladder_source = root / "tools/vec_band_ladder_walkforward.py"
    adapter_source = root / "tools/v8_research_ladder_adapter.py"
    runner_source = root / "tools/run_v8_research_ladder_replay.py"
    engine_source = root / "backtest_v8_engine.py"
    live_source = root / "tradier_manage.py"
    active_config = root / "data/hourly_reconfig/trb/active_config.json"
    global_config = root / "data/hourly_reconfig/per_sym_active_config.json"

    base_unit = _constant(ladder_source, "BASE_UNIT")
    capacity = _constant(ladder_source, "CAPACITY")
    exact = frozen.get("exact_v3") or {}
    final = frozen.get("final") or {}
    tim65_final = tim65.get("final") or {}
    discovery = (frozen.get("discovery") or {}).get("folds") or []

    identity_ok = (
        (frozen.get("candidate") or {}).get("number") == CANDIDATE_NUMBER
        and (frozen.get("candidate") or {}).get("label") == CANDIDATE_LABEL
        and frozen.get("inputs") == {
            key: tim65.get("inputs", {}).get(key)
            for key in frozen.get("inputs", {})
        }
    )
    tim_rows = [float(row.get("weighted_tim_pct", -1.0)) for row in discovery]
    tim_rows.append(float(final.get("weighted_tim_pct", -1.0)))
    tim_ok = len(tim_rows) == 3 and all(TIM_BAND[0] <= value <= TIM_BAND[1] for value in tim_rows)
    bh_rows = [
        (float(row.get("bh_multiple", -1.0)), float(row.get("return_pct", -1.0)))
        for row in discovery
    ]
    bh_rows.append(
        (float(final.get("bh_multiple", -1.0)), float(final.get("return_pct", -1.0)))
    )
    bh_ok = len(bh_rows) == 3 and all(
        multiple >= MIN_BH_MULTIPLE and ret > 0.0
        for multiple, ret in bh_rows
    ) and float(final.get("bh_return_pct", -1.0)) > 0.0
    equity_rows = [
        float(row.get("minimum_equity_usd", -1.0)) for row in discovery
    ] + [float(final.get("minimum_equity_usd", -1.0))]
    solvency_ok = (
        len(equity_rows) == 3
        and all(value >= MIN_EQUITY_USD for value in equity_rows)
        and all(int(row.get("clamps", -1)) == 0 for row in discovery)
        and int(final.get("clamps", -1)) == 0
        and float(tim65_final.get("fill_ratio", -1.0)) >= 0.99
        and not bool(
            (tim65_final.get("checks_all") or {}).get("no_capacity_breach") is False
        )
    )
    reclaim_checks = tim65_final.get("checks_all") or {}
    reclaim_ok = bool(reclaim_checks.get("no_flat_beyond_reclaim")) and bool(
        reclaim_checks.get("no_unfilled_reclaim")
    )
    exact_ok = (
        exact.get("status") == "PASS"
        and exact.get("signal_parity") is True
        and int(exact.get("future_htf_count", -1)) == 0
        and int(exact.get("refusals", -1)) == 0
        and int(exact.get("actions_executed", -1))
        == int(exact.get("actions_scheduled", -2))
        and abs(float(exact.get("weighted_tim_delta_pp", 999.0))) <= 1e-9
        and abs(float(exact.get("accounting_delta_bp", 999.0))) <= 1e-6
    )
    causal_ok = (
        int((frozen.get("discovery") or {}).get("future_htf_count", -1)) == 0
        and int(final.get("future_htf_count", -1)) == 0
        and int(exact.get("future_htf_count", -1)) == 0
    )
    side_ok = (
        stability.get("symbol") == "MU"
        and stability.get("side") == "LONG"
        and "SHORT" not in CANDIDATE_LABEL
    )
    capital_ok = (
        base_unit == BASE_UNIT_USD
        and capacity == CAPACITY_USD
        and float((tim65.get("deployed_capital") or {}).get("base_unit_usd", -1.0))
        == BASE_UNIT_USD
        and float((tim65.get("deployed_capital") or {}).get("hard_capacity_usd", -1.0))
        == CAPACITY_USD
        and float((tim65.get("deployed_capital") or {}).get("peak_post_fill_notional_usd", 1e99))
        <= CAPACITY_USD + 1e-6
    )

    # The sealed fold's end is explicit in the immutable stability receipt.
    end_exclusive = (
        (stability.get("untouched_final_fold") or {}).get("end_exclusive")
        or "1970-01-01"
    )
    data_end = parse_utc(f"{end_exclusive}T00:00:00Z")
    data_age_hours = max(0.0, (as_of - data_end).total_seconds() / 3600.0)
    freshness_ok = data_age_hours <= MAX_DATA_AGE_HOURS

    live_text = live_source.read_text()
    ladder_text = ladder_source.read_text()
    runner_text = runner_source.read_text()
    live_semantics = {
        "frozen_entry": (
            "newly completed D/4h/1h wt_cross_bull event; strongest target "
            "rung; next strictly later availability"
        ),
        "ordinary_live_entry": (
            "5m rebound from running low plus mtf_arrow_score; one configured "
            "LR_BAND_ENTRY_TF; immediate OPEN quantity"
        ),
        "frozen_exit": "completed 4h Donchian N=30 close then next-availability fill",
        "ordinary_live_exit": "independent live exit cascade; no C151-scoped E02 state machine",
        "frozen_reentry": "stored max(exit_fill, prior_4h_high), mandatory target reclaim",
        "ordinary_live_reentry": (
            "last broker exit price plus opposition postponement and generic "
            "order/gate cascade"
        ),
    }
    static_evidence = {
        "research_has_three_tf_loop": "for slot, tf in enumerate(TF_ORDER)" in ladder_text,
        "research_has_green_completed_bar_trigger": 'if curve.trigger == "green"' in ladder_text,
        "research_has_strict_later_fill": "next_strictly_later_index" in ladder_text,
        "live_has_5m_rebound_trigger": "_ma_confirmed = current_price >= _ma_st['lo']" in live_text,
        "live_uses_one_entry_tf": "_ld_tf = str(_cfg('LR_BAND_ENTRY_TF'" in live_text,
        "live_calls_three_tf_target_state_machine": False,
        "live_has_c151_scoped_e02_n30": False,
        "live_has_c151_exact_reclaim_state": False,
    }
    live_parity_ok = all(
        (
            static_evidence["research_has_three_tf_loop"],
            static_evidence["research_has_green_completed_bar_trigger"],
            static_evidence["research_has_strict_later_fill"],
            static_evidence["live_calls_three_tf_target_state_machine"],
            static_evidence["live_has_c151_scoped_e02_n30"],
            static_evidence["live_has_c151_exact_reclaim_state"],
        )
    )
    exact_route_is_private = (
        "--research-ladder-spec" in runner_text
        and "promotion_allowed" in runner_text
        and "False" in runner_text
    )
    current_engine_hash = sha256(engine_source)
    exact_engine_hash = str(exact.get("engine_sha256") or "")
    current_engine_revalidated = exact_engine_hash == current_engine_hash

    checks = [
        _check("candidate_identity_and_hash_chain", identity_ok, {
            "candidate": frozen.get("candidate"),
            "input_hashes": frozen.get("inputs"),
        }),
        _check("side_isolation_mu_long_only", side_ok, {
            "stability_symbol": stability.get("symbol"),
            "stability_side": stability.get("side"),
        }),
        _check("weighted_tim_each_fold_65_80", tim_ok, tim_rows),
        _check("positive_side_bh_and_at_least_2x_each_fold", bh_ok, bh_rows),
        _check("benchmark_2000_capacity_16000", capital_ok, {
            "source_constant_base_unit": base_unit,
            "source_constant_capacity": capacity,
            "receipt_deployed_capital": tim65.get("deployed_capital"),
        }),
        _check("solvency_fill_and_capacity", solvency_ok, {
            "minimum_equity_usd": equity_rows,
            "final_fill_ratio": tim65_final.get("fill_ratio"),
            "final_checks": reclaim_checks,
        }),
        _check("completed_parent_causality", causal_ok, {
            "discovery_future_htf": (frozen.get("discovery") or {}).get("future_htf_count"),
            "final_future_htf": final.get("future_htf_count"),
            "exact_future_htf": exact.get("future_htf_count"),
        }),
        _check("reentry_invariant", reclaim_ok, {
            "no_flat_beyond_reclaim": reclaim_checks.get("no_flat_beyond_reclaim"),
            "no_unfilled_reclaim": reclaim_checks.get("no_unfilled_reclaim"),
        }),
        _check("exact_v3_schedule_accounting_tim_capacity", exact_ok, exact),
        _check(
            "ordinary_live_path_semantic_parity",
            live_parity_ok,
            {"semantics": live_semantics, "static_evidence": static_evidence},
            "C151's exact schedule is injected through an engine-private route; the ordinary live entry, exit and reclaim state machines are different.",
        ),
        _check(
            "exact_engine_hash_matches_current_and_revalidated",
            current_engine_revalidated,
            {
                "exact_receipt_engine_sha256": exact_engine_hash,
                "current_backtest_v8_engine_sha256": current_engine_hash,
                "research_route_private": exact_route_is_private,
            },
            "The exact-v3 receipt is hash-bound to an older engine and has not been revalidated on the current executable.",
        ),
        _check(
            "fresh_deployment_data",
            freshness_ok,
            {
                "sealed_data_end_exclusive": end_exclusive,
                "audit_as_of_utc": as_of.isoformat(),
                "age_hours": data_age_hours,
                "maximum_age_hours": MAX_DATA_AGE_HOURS,
            },
            "The sealed evidence omits multiple current market sessions; refresh into a versioned NPZ and repeat unchanged vector plus exact/live parity.",
        ),
    ]
    failures = [row["name"] for row in checks if not row["pass"]]
    contract = prospective_contract()
    return {
        "contract": contract,
        "contract_sha256": canonical_hash(contract),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "audit_as_of_utc": as_of.isoformat(),
        "classification": (
            "PROMOTION_ALLOWED" if not failures else "LIVE_PROMOTION_BLOCKED"
        ),
        "promotion_allowed": not failures,
        "activation_performed": False,
        "activation_reason": (
            "all prospective gates passed; activation is a separate atomic step"
            if not failures
            else "fail-closed; incumbent MU_LONG configuration preserved"
        ),
        "failures": failures,
        "checks": checks,
        "evidence": {
            "frozen_70_80_receipt": {
                "path": str(frozen_receipt_path),
                "sha256": sha256(frozen_receipt_path),
                "left_unchanged": True,
            },
            "tim65_supporting_receipt": {
                "path": str(tim65_receipt_path),
                "sha256": sha256(tim65_receipt_path),
            },
            "stability_receipt": {
                "path": str(stability_receipt_path),
                "sha256": sha256(stability_receipt_path),
            },
            "ordinary_live_path": {
                "path": str(live_source),
                "sha256": sha256(live_source),
            },
            "backtest_v8_engine": {
                "path": str(engine_source),
                "sha256": current_engine_hash,
            },
            "research_adapter": {
                "path": str(adapter_source),
                "sha256": sha256(adapter_source),
            },
            "trb_active_config": {
                "path": str(active_config),
                "sha256": sha256(active_config),
            },
            "global_per_sym_config": {
                "path": str(global_config),
                "sha256": sha256(global_config),
            },
        },
        "rollback": {
            "needed": False,
            "reason": "no live mutation was made",
            "incumbent_trb_config_sha256": sha256(active_config),
            "incumbent_global_per_sym_config_sha256": sha256(global_config),
        },
        "canary": {
            "started": False,
            "reason": "canary may start only after ordinary-live semantic parity, current-engine exact replay, and freshness all pass",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--frozen-receipt", type=Path, default=DEFAULT_FROZEN_RECEIPT)
    parser.add_argument("--tim65-receipt", type=Path, default=DEFAULT_TIM65_RECEIPT)
    parser.add_argument("--stability-receipt", type=Path, default=DEFAULT_STABILITY_RECEIPT)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--contract-out", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = audit(
        root=args.root.resolve(),
        frozen_receipt_path=args.frozen_receipt.resolve(),
        tim65_receipt_path=args.tim65_receipt.resolve(),
        stability_receipt_path=args.stability_receipt.resolve(),
        as_of=parse_utc(args.as_of),
    )
    if args.contract_out:
        args.contract_out.parent.mkdir(parents=True, exist_ok=True)
        args.contract_out.write_text(
            json.dumps(result["contract"], indent=2, sort_keys=True) + "\n"
        )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "classification": result["classification"],
        "promotion_allowed": result["promotion_allowed"],
        "failures": result["failures"],
        "contract_sha256": result["contract_sha256"],
    }, sort_keys=True))
    return 0 if result["promotion_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
