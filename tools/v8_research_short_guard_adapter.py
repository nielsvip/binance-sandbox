#!/usr/bin/env python3
"""Fail-closed exact replay adapter for frozen SHORT guard candidates.

The source campaign had reversed SHORT slippage.  This adapter never treats
those metrics as evidence: it binds the immutable source result, records the
invalidation, and deterministically rebuilds only the explicitly scheduled
candidate/profile with the canonical adverse-fill contract.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from tools import vec_asymmetric_short_campaign as base
from tools import vec_short_guard_decomposition as guard
from tools import vec_top_exit_campaign as top
from tools.v8_research_ladder_adapter import (
    LadderReplayAdapter,
    LadderReplayError,
    SPEC_VERSION,
    _canonical_sha256,
    _write_schedule_deterministic,
    audit_execution_provenance,
    sha256_file,
)
from tools.research_availability_clock import CLOCK_CONTRACT


SPEC_KIND = "V8_RESEARCH_SHORT_GUARD_REPLAY"
REASON_PREFIX = SPEC_KIND
ALLOWED = {
    ("ARM", "G21_BULL_STATE_BLOCK"),
    ("LRCX", "G14_BULL_D_CANDLE"),
}


def _source_row(
    payload: dict[str, Any], symbol: str, profile_label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    for item in payload["results"]:
        if item["symbol"] != symbol:
            continue
        for row in item["profiles"]:
            if row["profile"]["label"] == profile_label:
                return item, row
    raise LadderReplayError(f"missing frozen row {symbol}/{profile_label}")


def _corrected_replay(
    source_result: Path,
    *,
    symbol: str,
    profile_label: str,
    npz_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    payload = json.loads(source_result.read_text())
    item, row = _source_row(payload, symbol, profile_label)
    if (symbol, profile_label) not in ALLOWED:
        raise LadderReplayError("candidate/profile is not on the frozen exact shortlist")
    if row["status"] != "VECTOR_SURVIVOR_EXACT_PENDING":
        raise LadderReplayError("source row is not a frozen exact-pending survivor")
    candidate = base.Candidate(**row["selected_candidate"])
    profile = guard.GuardProfile(**row["profile"])
    data = top._load_execution(
        symbol, npz_path.resolve().parent, "2024-03-26", "ladder", base.FINAL_END
    )
    if not data.contract["valid"]:
        data.z.close()
        raise LadderReplayError(f"invalid NPZ contract: {data.contract['errors']}")
    htfs = {tf: top._compress_htf(data, tf) for tf in ("15m", "1h", "4h", "D")}
    events = guard._events(data, htfs, candidate, profile)
    if events["source_future_count"] != 0:
        data.z.close()
        raise LadderReplayError("future completed HTF source in corrected replay")
    corrected_folds: dict[str, dict[str, Any]] = {}
    schedule: list[dict[str, Any]] | None = None
    left = right = -1
    for name, fold_left, fold_right in base._windows(data):
        fold_metrics = base._simulate(
            data,
            events,
            candidate,
            fold_left,
            fold_right,
            float(payload["manifest"]["costs"]["commission_bps_one_way"]),
            float(payload["manifest"]["costs"]["slippage_bps_one_way"]),
            emit_schedule=name == "FINAL",
        )
        if name == "FINAL":
            schedule = fold_metrics.pop("schedule_events")
            left, right = fold_left, fold_right
        corrected_folds[name] = fold_metrics
    if schedule is None:
        data.z.close()
        raise LadderReplayError("missing frozen FINAL fold")
    if not all(
        corrected_folds[name]["beats_opportunity_benchmark"]
        for name in ("D1", "D2")
    ):
        data.z.close()
        raise LadderReplayError(
            "frozen candidate fails fixed-$2k opportunity benchmark in a "
            "discovery fold after corrected adverse fills"
        )
    metrics = corrected_folds["FINAL"]
    if metrics["open_mtm_return_pct"] != 0.0:
        data.z.close()
        raise LadderReplayError("corrected frozen fold ends open; exact close semantics undefined")
    return metrics, schedule, (
        data, left, right, payload, item, row, events, corrected_folds
    )


def build_spec_and_schedule(
    artifact_dir: str | Path,
    *,
    symbol: str,
    profile_label: str,
    schedule_path: str | Path,
    account: str = "trb",
    npz_path: str | Path,
) -> dict[str, Any]:
    artifact = Path(artifact_dir).resolve()
    source_result = artifact / "result.json"
    symbol = symbol.upper()
    metrics, events, context = _corrected_replay(
        source_result,
        symbol=symbol,
        profile_label=profile_label,
        npz_path=Path(npz_path),
    )
    (
        data, left, right, payload, item, row, event_audit, corrected_folds
    ) = context
    try:
        provenance = audit_execution_provenance(data, left=left, right=right)
        if not provenance["safe_for_exact_engine"]:
            raise LadderReplayError(str(provenance["reason"]))
        actual_npz_sha = sha256_file(data.path)
        schedule = _write_schedule_deterministic(schedule_path, events)
        expected = {
            "capital_return_pct": metrics["capital_return_pct"],
            "account_return_pct": metrics["account_return_pct"],
            "binary_tim_pct": metrics["time_in_market_pct"],
            "exposure_weighted_tim_pct": metrics["exposure_weighted_tim_pct"],
            "rows": metrics["rows"],
            "peak_post_fill_notional_usd": metrics[
                "peak_post_fill_notional_usd"
            ],
            "requested_notional_usd": metrics["requested_notional_usd"],
            "filled_notional_usd": metrics["filled_notional_usd"],
            "clamp_count": metrics["clamp_count"],
        }
        selection_inputs = {
            "symbol": symbol,
            "book": item["book"],
            "profile": row["profile"],
            "selected_candidate": row["selected_candidate"],
            "selection_score": row["selection_score"],
            "selected_on_discovery_only": row["selected_on_discovery_only"],
        }
        return {
            "kind": SPEC_KIND,
            "version": SPEC_VERSION,
            "reason_prefix": REASON_PREFIX,
            "promotion_allowed": False,
            "matrix_written": False,
            "source_artifact": str(artifact),
            "source_result": str(source_result),
            "expected_source_result_sha256": sha256_file(source_result),
            "source_result_invalidation": "INVALIDATED_REVERSED_SHORT_SLIPPAGE",
            "symbol": symbol,
            "side": "SHORT",
            "account": account,
            "event_schedule": str(schedule),
            "expected_schedule_sha256": sha256_file(schedule),
            "entry_schedule": {
                "path": str(schedule),
                "sha256": sha256_file(schedule),
                "ordered_events": len(events),
                "executable_actions": len(events),
                "fill_rule": "first_strictly_later_availability_open",
            },
            "npz_path": str(Path(data.path).resolve()),
            "expected_npz_sha256": actual_npz_sha,
            "source_npz": {
                "path": str(Path(data.path).resolve()),
                "sha256": actual_npz_sha,
                "contract": data.contract,
                "execution_provenance": provenance,
            },
            "availability_clock": {
                "kind": CLOCK_CONTRACT,
                "ordering": "availability_ts_then_source_row_index",
                "window_rows_sha256": provenance[
                    "availability_source_rows_sha256"
                ],
                "native_rows_at_source_ts": True,
                "synthetic_rows_at_parent_close_ts": True,
                "next_rth_fill": "first_strictly_later_availability",
                "preserve_duplicate_availability_rows": True,
            },
            "validation_start": base.FINAL_START,
            "validation_end_exclusive": base.FINAL_END,
            "source_start": "2024-03-26",
            "fold_boundaries": {
                "fold": "FINAL_UNTOUCHED",
                "train_start": "2024-03-26",
                "train_end_exclusive": base.FINAL_START,
                "validation_start": base.FINAL_START,
                "validation_end_exclusive": base.FINAL_END,
            },
            "selection_provenance": {
                "selected_from_training_only": True,
                "validation_or_final_metrics_used_for_selection": False,
                "selection_inputs": selection_inputs,
                "selection_inputs_sha256": _canonical_sha256(selection_inputs),
            },
            "guard_profile": row["profile"],
            "guard_profile_sha256": _canonical_sha256(row["profile"]),
            "selected_candidate": row["selected_candidate"],
            "selected_candidate_sha256": _canonical_sha256(
                row["selected_candidate"]
            ),
            "guard_audit": event_audit["guard_audit"],
            "emergency_invariants": {
                key: True for key in guard.EMERGENCY_KEYS
            },
            "emergency_exit_count": metrics["emergency_exit_count"],
            "semantics": "add",
            "base_unit_usd": base.BASE_USD,
            "account_equity_usd": base.ACCOUNT_USD,
            "hard_capacity_usd": base.CAPACITY_USD,
            "commission_bps_one_way": payload["manifest"]["costs"][
                "commission_bps_one_way"
            ],
            "slippage_bps_one_way": payload["manifest"]["costs"][
                "slippage_bps_one_way"
            ],
            "expected_metrics": expected,
            "corrected_adverse_fill_metrics": metrics,
            "corrected_adverse_fill_folds": corrected_folds,
            "contaminated_source_metrics": row["untouched_final"],
            "expected_counts": {
                "entry_fills": sum(e["type"] in {"ENTRY", "AUGMENT"} for e in events),
                "technical_exits": sum(e["type"] == "EXIT" for e in events),
                "mtm_final": 0,
                "actions": len(events),
                "capacity_no_fill_audits": 0,
            },
            "accounting_tolerance_bp": 1e-4,
        }
    finally:
        data.z.close()


class ShortGuardReplayAdapter(LadderReplayAdapter):
    """Ladder execution route with SHORT-specific immutable signal validation."""

    def _validate_spec(self) -> None:
        if self.spec.get("kind") != SPEC_KIND:
            raise LadderReplayError("wrong SHORT guard spec kind")
        required = {
            "source_result", "expected_source_result_sha256", "source_npz",
            "entry_schedule", "fold_boundaries", "selection_provenance",
            "guard_profile", "guard_profile_sha256", "selected_candidate",
            "selected_candidate_sha256", "availability_clock",
            "emergency_invariants", "expected_metrics", "expected_counts",
            "hard_capacity_usd", "commission_bps_one_way",
            "slippage_bps_one_way", "event_schedule",
            "expected_schedule_sha256", "expected_npz_sha256", "symbol",
            "side", "account",
        }
        missing = sorted(required - set(self.spec))
        if missing:
            raise LadderReplayError(f"missing SHORT guard spec fields: {missing}")
        if self.spec.get("promotion_allowed") or self.spec.get("matrix_written"):
            raise LadderReplayError("SHORT guard replay must remain research-only")
        if self.spec["side"] != "SHORT":
            raise LadderReplayError("SHORT guard adapter cannot replay LONG")
        if (self.spec["symbol"], self.spec["guard_profile"]["label"]) not in ALLOWED:
            raise LadderReplayError("spec is not one of the two frozen candidates")
        if sha256_file(self.spec["source_result"]) != self.spec[
            "expected_source_result_sha256"
        ]:
            raise LadderReplayError("source result hash mismatch")
        if _canonical_sha256(self.spec["guard_profile"]) != self.spec[
            "guard_profile_sha256"
        ]:
            raise LadderReplayError("guard profile hash mismatch")
        if _canonical_sha256(self.spec["selected_candidate"]) != self.spec[
            "selected_candidate_sha256"
        ]:
            raise LadderReplayError("candidate hash mismatch")
        if set(self.spec["emergency_invariants"]) != set(guard.EMERGENCY_KEYS):
            raise LadderReplayError("emergency invariant inventory mismatch")
        if not all(self.spec["emergency_invariants"].values()):
            raise LadderReplayError("an emergency/solvency guard was disabled")

    def validate_npz(self, npz_path: str | Path) -> str:
        if sha256_file(npz_path) != self.spec["expected_npz_sha256"]:
            raise LadderReplayError("NPZ fingerprint mismatch")
        metrics, events, context = _corrected_replay(
            Path(self.spec["source_result"]),
            symbol=self.spec["symbol"],
            profile_label=self.spec["guard_profile"]["label"],
            npz_path=Path(npz_path),
        )
        data, left, right, *_ = context
        try:
            provenance = audit_execution_provenance(data, left=left, right=right)
            if provenance["availability_source_rows_sha256"] != self.spec[
                "availability_clock"
            ]["window_rows_sha256"]:
                raise LadderReplayError("availability clock hash mismatch")
            if _canonical_sha256(events) != _canonical_sha256(self.events):
                raise LadderReplayError("recomputed signal/fill schedule mismatch")
            corrected = self.spec["corrected_adverse_fill_metrics"]
            for key, expected in corrected.items():
                if key in {"trade_returns_pct", "exit_reason_counts"}:
                    if metrics[key] != expected:
                        raise LadderReplayError(f"corrected metric mismatch: {key}")
                elif isinstance(expected, (int, float)) and abs(
                    float(metrics[key]) - float(expected)
                ) > max(1e-9, abs(float(expected)) * 1e-9):
                    raise LadderReplayError(f"corrected metric mismatch: {key}")
            self.runtime_clock = [
                {
                    "availability_ts": int(data.ts[i]),
                    "source_ts": int(data.source_ts[i]),
                    "source_row_index": int(data.full_indices[i]),
                    "clock_index": int(i),
                }
                for i in range(left, right)
            ]
        finally:
            data.z.close()
        return self.spec["expected_npz_sha256"]

    def accounting_audit(
        self, executed_trades: list[dict[str, Any]]
    ) -> dict[str, Any]:
        audit = super().accounting_audit(executed_trades)
        pnl = float(audit["total_pnl_dollars"])
        actual_account = 100.0 * pnl / float(self.spec["account_equity_usd"])
        expected_account = float(
            self.spec["expected_metrics"]["account_return_pct"]
        )
        account_delta_bp = 100.0 * (actual_account - expected_account)
        audit.update({
            "headline_contract": (
                "capital_return_pct=P&L/$2k fixed B&H unit; "
                "account_return_pct=P&L/$10k solvency ledger"
            ),
            "expected_account_return_pct": expected_account,
            "actual_account_return_pct": actual_account,
            "account_delta_bp": account_delta_bp,
        })
        audit["status"] = (
            "PASS"
            if audit["status"] == "PASS"
            and abs(account_delta_bp)
            <= float(self.spec["accounting_tolerance_bp"])
            else "FAIL"
        )
        return audit
