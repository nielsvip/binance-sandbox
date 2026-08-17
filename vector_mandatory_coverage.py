#!/usr/bin/env python3
"""Shared fail-closed coverage contract for V8 and vector research runners.

This module deliberately separates a *narrow experiment* from a claim that a
campaign covered the mandatory lifecycle queue.  It is dependency-free so both
root V8 runners and ``tools/`` vector runners can import it on S1.

It does not execute a backtest.  It only validates the receipt that must exist
before a runner is allowed to claim comprehensive coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


GROUND_RULE_MARKER = "V8_VECTOR_MANDATORY_SHORTLIST_V1"
RECEIPT_SCHEMA = "v8-vector-mandatory-coverage-v1"

# The IDs are stable receipt keys.  Keep the descriptions in sync with
# BACKTEST_BIBLE.md §0.1; do not use a free-form list that lets a runner omit a
# family by changing its spelling.
MANDATORY_SHORTLIST: dict[str, str] = {
    "STDEV_LADDER_1_TO_10": "stdev/LR ladder 1-10x whole-share quantity",
    "DONCHIAN_E02": "Donchian E02",
    "DIVERGENCE_RETEST_E05": "divergence/retest E05",
    "MTF_ATR_TRAIL": "MTF ATR trail",
    "PEAK_GIVEBACK": "peak giveback",
    "PROTECTIVE_TRAIL": "protective trail",
    "DELAYED_LOWER_TOP_EXIT": "delayed lower-top exit",
    "DELAYED_EMERGENCY_EXIT": "delayed emergency exit",
    "DIRECT_WT15_CROSS_EXIT": "direct 15m WT-cross exit",
    "LR_BAND_HARVEST_VARIANTS": "LR-band harvest variants",
    "WT_FORCE_OPEN_FRESH_CROSS": "WT force-open combinations and fresh-cross variants",
    "LR_ENTRY_PRIORITY_THRESHOLD": "LR-band entry priority/threshold combinations",
    "SMA200_EMA_DISTANCE_SLOPE": "SMA200/EMA-distance and slope filters",
    "OSCILLATOR_STYLE_FILTERS": "RSI, RSI2, Connors RSI, MFI, Minervini, Clenow",
    "BB_STDEV_BREAKOUT_RETEST": "BB squeeze/fire and STDEV breakout/retest entries",
    "MARKET_QUALITY_REGIME_FILTERS": "catalyst-volume, funding/OI, RVOL, market-quality, chop/regime filters",
    "MTF_ARROW_HTF_ALIGNMENT": "MTF arrow and HTF-alignment combinations",
    "DELTA_ENTRY_GATES": "delta entry-gate combinations",
    "RZ_KZONE_ZONE_ENTRY": "RZ/K-zone/zone-entry filters",
    "STRUCTURAL_RANGE_SHIFT_EXITS": "structural-range-shift exits",
    "RZ_TWO_PHASE_R3_HTF_FLIP": "RZ two-phase and R3 HTF-flip exits",
    "DYNAMIC_SCORE_COUNTER_EXITS": "dynamic score-counter exits",
    "DELTA_EXITS": "delta exits",
    "FROZEN_BB_DC_STOPS": "frozen BB/DC stops",
    "MI_EXHAUSTION_EXITS": "MI divergence/exhaustion/velocity/wave exits",
    "RSI_STOCH_CROSS_EXITS": "RSI/Stoch cross exits",
    "SENTIMENT_GAIN_EROSION_EXITS": "sentiment and gain-erosion exits",
    "WT_D_BOUNCE_EXITS": "WT-D bounce exits",
    "NO_LOSS_STOP_PACKS": "full no-loss/stop-pack combinations",
    "WT_EXIT_WITH_BREAKOUT_BOUNCE_REENTRY": "frequent WT exits with breakout/bounce reentry",
}

# These are executable V8/vector research entry points, as distinct from
# renderers, receipt readers, unit tests, and retired OFAT MSTR scripts.  The
# static contract test makes omission from this registry an explicit review
# decision rather than a silent new runner.  A registered runner must expose
# the fail-closed claim flags below.
REGISTERED_V8_VECTOR_RUNNERS = (
    "backtest_v8_sweep.py",
    "v8_quick_engine.py",
    "v8_vec_sweep.py",
    "v8_vec_structure_sweep.py",
    "vec_ab_sweep.py",
    "vec_mass_scan.py",
    "vec_matrix_runner.py",
    "vec_reentry_sweep.py",
    "vec_v8q_per_symbol.py",
    "run_vec_stop_sweep.py",
    "tools/chronological_vector_search.py",
    "tools/vec_baseline_campaign.py",
    "tools/vec_entry_exit_beam_adapter.py",
    "tools/vec_exposure_ladder.py",
    "tools/vec_full_scope_baseline.py",
    "tools/vec_gate_ablation.py",
    "tools/vec_screen_daemon.py",
)


def shortlist_sha256() -> str:
    payload = json.dumps(MANDATORY_SHORTLIST, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def partial_coverage_receipt(*, runner: str, modeled: set[str] | None = None,
                             note: str = "Narrow run; not comprehensive coverage.") -> dict[str, Any]:
    """Return an explicit partial receipt suitable for embedding in a result.

    ``MODELED_PENDING_CAMPAIGN`` means a path is wired but this particular run
    did not test the family's declared range.  It is deliberately not accepted
    as comprehensive evidence.
    """
    modeled = modeled or set()
    return {
        "schema": RECEIPT_SCHEMA,
        "ground_rule": GROUND_RULE_MARKER,
        "shortlist_sha256": shortlist_sha256(),
        "runner": runner,
        "coverage_status": "PARTIAL_NOT_COMPREHENSIVE",
        "families": {
            name: {
                "description": description,
                "status": "MODELED_PENDING_CAMPAIGN" if name in modeled else "UNMODELED_NEEDS_ADAPTER",
            }
            for name, description in MANDATORY_SHORTLIST.items()
        },
        "note": note,
    }


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid mandatory-coverage receipt {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid mandatory-coverage receipt {path}: expected JSON object")
    return value


def validate_comprehensive_receipt(path: Path) -> dict[str, Any]:
    """Fail closed unless every mandatory family has auditable tested evidence."""
    receipt = _load_receipt(path)
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(f"{path}: wrong schema (expected {RECEIPT_SCHEMA})")
    if receipt.get("ground_rule") != GROUND_RULE_MARKER:
        raise ValueError(f"{path}: missing {GROUND_RULE_MARKER}")
    if receipt.get("shortlist_sha256") != shortlist_sha256():
        raise ValueError(f"{path}: mandatory shortlist hash does not match current ground rule")
    if receipt.get("coverage_status") != "COMPREHENSIVE_TESTED":
        raise ValueError(f"{path}: coverage_status must be COMPREHENSIVE_TESTED")
    families = receipt.get("families")
    if not isinstance(families, dict):
        raise ValueError(f"{path}: families must be an object")
    missing = sorted(set(MANDATORY_SHORTLIST) - set(families))
    extra = sorted(set(families) - set(MANDATORY_SHORTLIST))
    if missing or extra:
        raise ValueError(f"{path}: mandatory family mismatch missing={missing} extra={extra}")
    bad: list[str] = []
    for name in MANDATORY_SHORTLIST:
        row = families[name]
        if not isinstance(row, dict) or row.get("status") != "TESTED":
            bad.append(f"{name}: status must be TESTED")
            continue
        if not row.get("read_sites"):
            bad.append(f"{name}: TESTED requires read_sites")
        if not row.get("receipt_paths"):
            bad.append(f"{name}: TESTED requires receipt_paths")
        if int(row.get("behavior_distinct_ledger_count", 0) or 0) < 1:
            bad.append(f"{name}: TESTED requires a behavior-distinct ledger")
    if bad:
        raise ValueError(f"{path}: incomplete mandatory coverage: {'; '.join(bad)}")
    return receipt


def add_coverage_claim_arguments(parser: argparse.ArgumentParser) -> None:
    """Add uniform flags to a V8/vector runner without changing narrow runs."""
    parser.add_argument(
        "--claim-comprehensive-coverage", action="store_true",
        help="Permit a comprehensive-coverage claim only with a complete mandatory receipt.",
    )
    parser.add_argument(
        "--mandatory-coverage-receipt", type=Path,
        help="JSON receipt required with --claim-comprehensive-coverage.",
    )


def enforce_coverage_claim(args: argparse.Namespace, *, runner: str) -> dict[str, Any]:
    """Return receipt metadata; refuse a comprehensive claim without evidence."""
    if not getattr(args, "claim_comprehensive_coverage", False):
        return partial_coverage_receipt(runner=runner)
    path = getattr(args, "mandatory_coverage_receipt", None)
    if path is None:
        raise SystemExit(
            "COMPREHENSIVE_COVERAGE_REFUSED: --claim-comprehensive-coverage "
            "requires --mandatory-coverage-receipt"
        )
    try:
        receipt = validate_comprehensive_receipt(path)
    except ValueError as exc:
        raise SystemExit(f"COMPREHENSIVE_COVERAGE_REFUSED: {exc}") from exc
    return {**receipt, "runner": runner, "coverage_claim": "COMPREHENSIVE_TESTED"}


def audit_registered_runner_wiring(repo_root: Path) -> list[str]:
    """Return registered entry points missing the shared fail-closed wiring."""
    missing: list[str] = []
    for relative in REGISTERED_V8_VECTOR_RUNNERS:
        path = repo_root / relative
        try:
            source = path.read_text()
        except OSError:
            missing.append(f"{relative}: missing")
            continue
        if "add_coverage_claim_arguments" not in source or "enforce_coverage_claim" not in source:
            missing.append(f"{relative}: no mandatory-coverage CLI/guard")
    return missing
