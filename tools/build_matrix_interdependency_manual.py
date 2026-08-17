#!/usr/bin/env python3
"""Build the executable SWITCH_MATRIX dependency and invalidation inventory.

This is research metadata only. It reads the sweep manifest, config defaults,
and static live read-site registry; it never writes live config, fingerprints,
matrix cells, or promotion state.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "data/param_sweep_manifest_tradier.json"
DEFAULT_JSON = ROOT / "data/reports/switch_lab_catalog_20260729.json"
DEFAULT_CSV = (
    ROOT / "data/reports/switch_lab_catalog_20260729.csv"
)

CONTRACT_NODES = {
    "CONTRACT_DATA_CAUSALITY": (
        "Frozen NPZ hash, completed-parent HTF causality, 5m native/interpolated "
        "provenance, and no future HTF reads."
    ),
    "CONTRACT_SIDE_ACCOUNT": (
        "LONG and SHORT remain separate books; account, symbol and position side "
        "are explicit and inverse-LONG inference is forbidden."
    ),
    "CONTRACT_CAPITAL_COST": (
        "Net P&L uses fills and costs; return denominator is pre-cost committed "
        "fill-notional integrated over time, never marked notional."
    ),
    "CONTRACT_BASELINE_CONTROL": (
        "Same key, side, fold, data hash, code hash and capital contract control."
    ),
}

LAYERS = {
    "DATA_SIDE_ACCOUNT": 0,
    "FAMILY_MASTER": 10,
    "CROSS_CUTTING_GUARD": 15,
    "ENTRY_SOURCE": 20,
    "ENTRY_FILTER": 30,
    "EXIT_SOURCE": 40,
    "EXIT_FILTER": 50,
    "REENTRY": 60,
    "SIZING": 70,
}

RETEST_RULES = {
    "GLOBAL_CONTRACT_ALL": {
        "invalidates": list(LAYERS),
        "requires_controls": list(CONTRACT_NODES),
        "rule": "Freeze a new campaign; every prior cell is stale.",
    },
    "FAMILY_AND_DOWNSTREAM": {
        "invalidates": [
            "FAMILY_MASTER",
            "ENTRY_SOURCE",
            "ENTRY_FILTER",
            "EXIT_SOURCE",
            "EXIT_FILTER",
            "REENTRY",
            "SIZING",
            "CROSS_CUTTING_GUARD",
        ],
        "requires_controls": [
            "CONTRACT_DATA_CAUSALITY",
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Retest every sub-setting in the family and every coherent bundle "
            "that consumes its actions."
        ),
    },
    "ENTRY_AND_DOWNSTREAM": {
        "invalidates": [
            "ENTRY_SOURCE",
            "ENTRY_FILTER",
            "EXIT_SOURCE",
            "EXIT_FILTER",
            "REENTRY",
            "SIZING",
            "CROSS_CUTTING_GUARD",
        ],
        "requires_controls": list(CONTRACT_NODES),
        "rule": (
            "Entry population changed: all exit, reentry, exposure and sizing "
            "measurements using those entries must be recomputed."
        ),
    },
    "EXIT_REENTRY_BUNDLE": {
        "invalidates": ["EXIT_SOURCE", "EXIT_FILTER", "REENTRY", "SIZING"],
        "requires_controls": [
            "CONTRACT_DATA_CAUSALITY",
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Retest with the frozen entry control and paired reentry obligation; "
            "an exit cannot be ranked independently of what reopens afterward."
        ),
    },
    "REENTRY_CYCLE": {
        "invalidates": ["EXIT_SOURCE", "EXIT_FILTER", "REENTRY", "SIZING"],
        "requires_controls": [
            "CONTRACT_DATA_CAUSALITY",
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Recompute the whole entry→exit→reentry cycle. Reentry changes the "
            "population subsequently seen by exits, including persistent "
            "exit-price/top reclaim violations."
        ),
    },
    "CAPITAL_METRIC_REPLAY": {
        "invalidates": [
            "ENTRY_SOURCE",
            "ENTRY_FILTER",
            "EXIT_SOURCE",
            "EXIT_FILTER",
            "REENTRY",
            "SIZING",
        ],
        "requires_controls": [
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Replay the coherent schedule because capacity or rejected adds can "
            "change later actions. Signal timestamps may be reused only after an "
            "exact schedule-fingerprint equality proof; fills, capacity, drawdown, "
            "return, TIM and B&H comparisons are always recomputed."
        ),
    },
    "CROSS_CUTTING_BUNDLE": {
        "invalidates": [
            "CROSS_CUTTING_GUARD",
            "ENTRY_SOURCE",
            "ENTRY_FILTER",
            "EXIT_SOURCE",
            "EXIT_FILTER",
            "REENTRY",
            "SIZING",
        ],
        "requires_controls": list(CONTRACT_NODES),
        "rule": (
            "A regime, alignment, risk or otherwise unclassified guard can alter "
            "several action families. Retest the complete coherent bundle."
        ),
    },
    "CONSERVATIVE_COHERENT_BUNDLE": {
        "invalidates": list(LAYERS),
        "requires_controls": list(CONTRACT_NODES),
        "rule": "Unknown/guard interaction: fail closed and retest the complete bundle.",
    },
}

# These booleans are genuine family/action masters even though their legacy
# names do not use the otherwise reliable ``*_ENABLED`` convention.
VERIFIED_NON_SUFFIX_MASTERS = {
    "BEAR_MARKET_MODE_TRADIER",
    "HEDGE_MODE_TRADIER",
    "REENTRY_MANDATORY",
    "STRUCTURAL_RANGE_SHIFT_EXIT",
    "TRA_DISABLE_AUGMENT",
    "TRA_DISABLE_DELTA_ENTRY",
    "TRA_NO_LOSS_EXIT",
}

# Extra prerequisites which cannot be inferred from a shared name prefix.
VERIFIED_ACTIVATION_DEPENDENCIES = {
    "MTF_DC_REJECT_EXIT_ENABLED": ["MTF_EXIT_USE_COMPOUND"],
    # SRS is a legacy family whose master ends in ``_EXIT`` rather than
    # ``_ENABLED``.  Its TF/threshold knobs therefore cannot be connected by
    # the token-prefix rule (the sub-settings omit the word ``EXIT``).
    "STRUCTURAL_RANGE_SHIFT_TF": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "STRUCTURAL_RANGE_SHIFT_K_LOW": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS": [
        "STRUCTURAL_RANGE_SHIFT_EXIT"
    ],
}

RUNTIME_FENCE_PAT = re.compile(r"(?:^|_)(?:MIN_OPEN_TS|START_TS|END_TS)(?:_|$)")
SOURCE_GROUPS = {
    "vector": ["v8_vec_sweep.py"],
    "exact_wrapper": ["backtest_v8_engine.py", "v8_quick_engine.py"],
    "live_decision": [
        "tradier_manage.py",
        "tradier_matrix_gates.py",
        "tradier_indicators.py",
        "tradier_positions.py",
        "tradier_prices.py",
        "tradier_rankings.py",
        "tradier_api.py",
        "tradier_hourly_reconfig.py",
        "utils.py",
        "wt_composite.py",
        "wt_dc_delta.py",
        "wt_dc_entry_scorer.py",
        "wt_dc_exit_scorer.py",
        "local_extremes_scorer.py",
    ],
}


def verified_descriptions() -> dict[str, str]:
    try:
        from tools.export_switch_matrix_xls import _VERIFIED_DESCRIPTIONS
    except ImportError:
        return {}
    return _VERIFIED_DESCRIPTIONS


def direct_config_read_sites() -> dict[str, dict[str, list[str]]]:
    """Find syntactic config reads, excluding comments and bare-name mentions."""
    result: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    arg_index = {"getattr": 1, "_cfg": 0, "_psym_get": 2}
    for source_group, filenames in SOURCE_GROUPS.items():
        for filename in filenames:
            path = ROOT / filename
            if not path.exists():
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Attribute) and node.attr.isupper():
                    name = node.attr
                elif isinstance(node, ast.Call):
                    func = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else ""
                    )
                    index = arg_index.get(func)
                    if (
                        index is not None
                        and len(node.args) > index
                        and isinstance(node.args[index], ast.Constant)
                        and isinstance(node.args[index].value, str)
                    ):
                        name = node.args[index].value
                if name and name.isupper():
                    result[name][source_group].add(filename)
    return {
        name: {
            group: sorted(files)
            for group, files in sorted(groups.items())
        }
        for name, groups in sorted(result.items())
    }


def registry_rows() -> dict[str, dict[str, Any]]:
    from tools import knob_registry

    knobs = knob_registry.config_knobs("tradier")
    per_sym = knob_registry.per_sym_map("tradier")
    return {
        name: {
            "family": knob_registry.family_of(name),
            "group": knob_registry.group_of(name),
            "role": knob_registry.role_of(name, value),
            "kind": (
                "bool"
                if isinstance(value, bool)
                else "str"
                if isinstance(value, str)
                else "num"
            ),
            "default": value,
            "off_value": knob_registry.off_value(name, value),
            "per_sym": bool(per_sym.get(name, False)),
        }
        for name, value in knobs.items()
    }


def tradeable_manifest_rows() -> dict[str, dict[str, Any]]:
    payload = json.loads(MANIFEST.read_text())
    return {
        name: spec
        for name, spec in payload["params"].items()
        if bool(spec.get("sweepable")) and "OPTION" not in name
    }


def effective_role(name: str, info: dict[str, Any]) -> str:
    """Correct the registry's intentionally broad bool-as-master heuristic.

    ``knob_registry.role_of`` calls almost every otherwise-unclassified boolean
    a MAIN_SWITCH. That is useful for workbook grouping, but unsafe for an
    executable dependency graph: USE/CONFIRM/FILTER booleans are conditions,
    not masters of every knob sharing their first three tokens.
    """
    role = str(info.get("role") or "SUB_SETTING")
    if role != "MAIN_SWITCH" or info.get("kind") != "bool":
        return role
    if (
        re.search(r"_ENABLED(?:_TRADIER)?$", name)
        or name.startswith("ABLATION_DISABLE_")
        or name in VERIFIED_NON_SUFFIX_MASTERS
    ):
        return "MAIN_SWITCH"
    return "FILTER"


def layer_for(name: str, info: dict[str, Any]) -> str:
    upper = name.upper()
    role = str(info.get("effective_role") or info.get("role") or "")
    group = str(info.get("group") or "OTHER")
    if any(
        token in upper
        for token in ("ACCOUNT", "POSITION_SIDE", "SIDE_MULT", "CAPITAL_BASE")
    ):
        return "DATA_SIDE_ACCOUNT"
    if role == "MAIN_SWITCH":
        return "FAMILY_MASTER"
    if "REENTRY" in upper or "RECLAIM" in upper:
        return "REENTRY"
    if group == "SIZING" or any(
        token in upper
        for token in ("SIZE", "QTY", "BUDGET", "CAPACITY", "TARGET_USD")
    ):
        return "SIZING"
    if group == "ENTRY":
        return "ENTRY_FILTER" if role in {"FILTER", "CONDITION", "TF"} else "ENTRY_SOURCE"
    if group == "EXIT":
        return "EXIT_FILTER" if role in {"FILTER", "CONDITION", "TF"} else "EXIT_SOURCE"
    return "CROSS_CUTTING_GUARD"


def _master_stem(name: str) -> str:
    return re.sub(r"_ENABLED(?:_TRADIER)?$", "", name)


def master_map(registry: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Return only defensible activation dependencies.

    The old implementation selected an arbitrary same-family boolean. Families
    are deliberately truncated to three tokens, so that invented false edges
    such as WT_DC_ENTRY_THRESHOLD -> WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED.
    A master now qualifies only when its full stem is a token-boundary prefix,
    or when a reviewed prerequisite is listed above.
    """
    result: dict[str, list[str]] = {}
    all_mains = sorted(
        (
            name
            for name, info in registry.items()
            if effective_role(name, info) == "MAIN_SWITCH"
        ),
        key=len,
        reverse=True,
    )
    for name, info in registry.items():
        if effective_role(name, info) == "MAIN_SWITCH":
            result[name] = sorted(
                set(VERIFIED_ACTIVATION_DEPENDENCIES.get(name, []))
            )
            continue
        candidates = [
            candidate
            for candidate in all_mains
            if name == _master_stem(candidate)
            or name.startswith(_master_stem(candidate) + "_")
        ]
        if candidates:
            longest = max(len(_master_stem(row)) for row in candidates)
            candidates = [
                row for row in candidates if len(_master_stem(row)) == longest
            ]
        candidates.extend(VERIFIED_ACTIVATION_DEPENDENCIES.get(name, []))
        result[name] = sorted(set(candidates))
    return result


def retest_class(layer: str, role: str) -> str:
    if layer == "DATA_SIDE_ACCOUNT":
        return "GLOBAL_CONTRACT_ALL"
    if role == "MAIN_SWITCH":
        return "FAMILY_AND_DOWNSTREAM"
    if layer in {"ENTRY_SOURCE", "ENTRY_FILTER"}:
        return "ENTRY_AND_DOWNSTREAM"
    if layer in {"EXIT_SOURCE", "EXIT_FILTER"}:
        return "EXIT_REENTRY_BUNDLE"
    if layer == "REENTRY":
        return "REENTRY_CYCLE"
    if layer == "SIZING":
        return "CAPITAL_METRIC_REPLAY"
    if layer == "CROSS_CUTTING_GUARD":
        return "CROSS_CUTTING_BUNDLE"
    return "CONSERVATIVE_COHERENT_BUNDLE"


def _human_label(name: str) -> str:
    replacements = {
        "WT": "WaveTrend",
        "DC": "Donchian channel",
        "BB": "Bollinger band",
        "RSI": "RSI",
        "STOCH": "stochastic RSI",
        "ATR": "ATR",
        "EMA": "EMA",
        "SMA": "SMA",
        "MFI": "money-flow index",
        "ADX": "ADX",
        "MACD": "MACD",
        "HTF": "higher-timeframe",
        "MTF": "multi-timeframe",
        "TF": "timeframe",
        "QTY": "quantity",
        "PCT": "percent",
        "MULT": "multiplier",
        "MIN": "minimum",
        "MAX": "maximum",
    }
    return " ".join(replacements.get(token, token.lower()) for token in name.split("_"))


def grid_audit(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    default = spec.get("default")
    values = list(spec.get("test_values") or [])

    def equal(a: Any, b: Any) -> bool:
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
        return type(a) is type(b) and a == b

    type_name = str(spec.get("type") or "")
    reasons = []
    if not any(equal(default, value) for value in values):
        reasons.append("DEFAULT_CONTROL_MISSING")
    if type_name in {"int", "float"} and any(isinstance(value, bool) for value in values):
        reasons.append("BOOLEAN_VALUE_FOR_NUMERIC_KNOB")
    if RUNTIME_FENCE_PAT.search(name):
        reasons.append("RUNTIME_FENCE_NOT_ALPHA")
    return {
        "default_control_present": "DEFAULT_CONTROL_MISSING" not in reasons,
        "grid_repair_reasons": reasons,
        "test_values": values,
    }


def testability(
    name: str,
    info: dict[str, Any],
    spec: dict[str, Any],
    grid: dict[str, Any],
    direct_sites: dict[str, list[str]],
) -> dict[str, Any]:
    consumed = spec.get("consumed_by") or {}
    live = bool(consumed.get("live"))
    vector = bool(consumed.get("vec"))
    tier2 = bool(consumed.get("tier2"))
    backends = []
    if vector:
        backends.append("VECTOR_DIAGNOSTIC")
    if tier2 or live:
        backends.append("EXACT_V8")
    blockers = list(grid["grid_repair_reasons"])
    if not live:
        blockers.append("NO_LIVE_DECISION_READ")
        binding_evidence = "NO_LIVE_DECISION_READ"
    elif direct_sites.get("live_decision"):
        binding_evidence = "DIRECT_LIVE_CONFIG_READ"
    else:
        binding_evidence = "TOKEN_ONLY_LIVE_REFERENCE"
        blockers.append("DIRECT_BINDING_PROOF_REQUIRED")
    deployment_scope = (
        "NONDEPLOYABLE_NO_LIVE_READ"
        if not live
        else "PER_SYMBOL"
        if info.get("per_sym")
        else "GLOBAL_ONLY"
    )
    if grid["grid_repair_reasons"]:
        readiness = "BLOCKED_GRID_REPAIR"
    elif not live:
        readiness = "RESEARCH_ONLY_NONDEPLOYABLE"
    elif binding_evidence == "TOKEN_ONLY_LIVE_REFERENCE":
        readiness = "BINDING_PROBE_REQUIRED"
    elif vector:
        readiness = "READY_VECTOR_SCREEN_THEN_EXACT"
    else:
        readiness = "READY_EXACT_ONLY"
    return {
        "screen_backends": backends,
        "differential_readiness": readiness,
        "deployment_scope": deployment_scope,
        "static_read_sites": direct_sites,
        "binding_evidence": binding_evidence,
        "promotion_blockers": blockers,
        "exact_proof_required": (
            "Requested typed value must resolve exactly and change the exact "
            "trade/action fingerprint versus the same-contract control."
        ),
    }


def description(name: str, info: dict[str, Any]) -> str:
    _VERIFIED_DESCRIPTIONS = verified_descriptions()
    if name in _VERIFIED_DESCRIPTIONS:
        return _VERIFIED_DESCRIPTIONS[name]
    # Glossary expansions for mouse-over tooltips (CT, ABLATION, CLENOW)
    if name.startswith("CT_CHOP"):
        return (
            "CT = Counter Trend — Chop filter (4H) — detects sideways chop (ADX_4H <20, BB width <2%, volume flat). "
            "When enabled, BLOCKS new entries in choppy regimes to avoid whipsaw; when disabled, allows choppy entries "
            "(more trades, more noise). Live: tradier_manage.py: evaluate_entry() → _is_chop_4h() checks ADX_4H + bb_width_4h; "
            "if chop true, gate returns BLOCK. No live trade if blocked."
        )
    if name.startswith("CT_"):
        return (
            "CT = Counter Trend — Counter-trend entry family. Gating params like CT_15M_MOMENTUM_GATE_ENABLED "
            "require bullish 15m momentum when entering long. Live: tradier_manage.py per-bar gate; Vector: v8_quick_engine.py "
            "checks wt_momentum_state_15m etc. When enabled, filters entries."
        )
    if name.startswith("ABLATION_DISABLE"):
        family = name.replace("ABLATION_DISABLE_", "")
        return (
            f"Ablation = ablation study — disables a subsystem to measure its P&L contribution; when enabled, that family ({family}) "
            f"is turned off to see if it was helping or hurting. Live: _apply_research_only_live_gates() blocks that family's entries. "
            f"Vector: v8_quick_engine _apply_auto_wired_params hash-flips 14% bars. Enable to measure contribution."
        )
    if name.startswith("CLENOW"):
        return (
            "Clenow = Andreas Clenow momentum/trend filter (stocks, 12mo momentum + volatility) — trend-following, 12-mo momentum + volatility stop; "
            "True = require Clenow trend (price above SMA200 and momentum score above threshold), False = ignore. "
            "Live: tradier_manage.py _apply_research_only_live_gates checks price vs SMA200 and clenow_score vs CLENOW_GATE_MIN_SCORE. "
            "Vector: v8_quick_engine checks close vs sma200_D and rsi_1h vs threshold."
        )
    group = str(info.get("group") or "OTHER")
    role = str(
        info.get("effective_role") or info.get("role") or "SUB_SETTING"
    ).replace("_", " ")
    label = _human_label(name)
    if role == "MAIN SWITCH":
        effect = (
            f"turns the {label} action family on/off; its OFF control must remove "
            "that family's actions before any dependent setting is ranked"
        )
    elif group == "ENTRY":
        effect = (
            f"sets the {label} entry condition/value used to admit opens or adds; "
            "changing it changes the trade population consumed by every later "
            "exit and reentry"
        )
    elif "REENTRY" in name or "RECLAIM" in name:
        effect = (
            f"sets the {label} reopening condition/value after an exit; it must "
            "be measured with the paired exit and persistent reclaim obligation"
        )
    elif group == "EXIT":
        effect = (
            f"sets the {label} close/reduce condition or value; it must be "
            "measured with a frozen entry population and paired reentry"
        )
    elif group == "SIZING":
        effect = (
            f"sets the {label} deployment/quantity value; signal count may stay "
            "fixed but fills, capacity, drawdown and return must be replayed"
        )
    else:
        effect = (
            f"sets the cross-cutting {label} regime/alignment/risk condition or "
            "value; because it may gate several action families, use a same-fold "
            "complete-bundle control"
        )
    scope = "per-symbol" if info.get("per_sym") else "global-only"
    return (
        f"{group} {role} — {effect}; off={info.get('off_value')!r}; "
        f"{scope}."
    )


def switch_guidance(layer: str, info: dict[str, Any]) -> str:
    kind = info.get("kind")
    role = info.get("effective_role") or info.get("role")
    if layer == "ENTRY_SOURCE":
        if kind == "bool":
            return "Toggle to admit/remove this entry source; then measure opens and TIM."
        return "Sweep a bounded typed range; entry-count polarity is empirical."
    if layer == "ENTRY_FILTER":
        return "Tighten to reduce qualifying entries; loosen to restore trades; verify polarity."
    if layer == "EXIT_SOURCE":
        if kind == "bool":
            return "Toggle to add/remove closes or reductions; rank only with reentry."
        return "Sweep a bounded typed range; rank close timing only with paired reentry."
    if layer == "EXIT_FILTER":
        return "Tighten to delay/fewer exits; loosen for earlier/more exits; measure."
    if layer == "REENTRY":
        return "Enable/loosen when valid exits miss continuation; never clear the reclaim latch."
    if layer == "SIZING":
        return "Use for deployment/capacity, not to manufacture entry/exit alpha."
    if role == "MAIN_SWITCH":
        return "Prove off/on changes the exact trade fingerprint before testing sub-settings."
    if kind == "num":
        return "Numeric polarity is signal-dependent; screen a bounded range, never guess."
    if layer == "CROSS_CUTTING_GUARD":
        return "Treat as a cross-cutting guard; retest the complete bundle or mark RECONNECT."
    return "Require a changed exact fingerprint or mark RECONNECT."


def build_payload() -> dict[str, Any]:
    manifest = tradeable_manifest_rows()
    registry = registry_rows()
    masters = master_map(registry)
    reviewed_descriptions = verified_descriptions()
    read_sites = direct_config_read_sites()
    rows = []
    edges = []
    for name, spec in sorted(manifest.items()):
        info = dict(registry.get(name) or {})
        if not info:
            default = spec.get("default")
            info = {
                "family": name,
                "group": "OTHER",
                "role": "SUB_SETTING",
                "kind": spec.get("type") or type(default).__name__,
                "default": default,
                "off_value": None,
                "per_sym": False,
            }
        info["registry_role"] = info.get("role")
        info["effective_role"] = effective_role(name, info)
        layer = layer_for(name, info)
        activation_dependencies = masters.get(name, [])
        primary_master = (
            activation_dependencies[0] if activation_dependencies else None
        )
        klass = retest_class(layer, str(info.get("effective_role")))
        grid = grid_audit(name, spec)
        capability = testability(name, info, spec, grid, read_sites.get(name, {}))
        path_description = description(name, info)
        if "RUNTIME_FENCE_NOT_ALPHA" in grid["grid_repair_reasons"]:
            path_description = (
                "RUNTIME CONTROL — this timestamp fences when an exit may operate; "
                "it is not an alpha path and must not be optimized as one. "
                + path_description
            )
        if capability["deployment_scope"] == "NONDEPLOYABLE_NO_LIVE_READ":
            path_description += (
                " RESEARCH ONLY — no current live-decision read site was found, "
                "so a backtest result cannot be deployed as this knob."
            )
        elif capability["binding_evidence"] == "TOKEN_ONLY_LIVE_REFERENCE":
            path_description += (
                " BINDING UNPROVEN — the name appears in live sources but no direct "
                "config read was found; require an exact differential fingerprint."
            )
        dependencies = list(CONTRACT_NODES)
        dependencies.extend(activation_dependencies)
        for master in activation_dependencies:
            edges.append(
                {
                    "from": master,
                    "to": name,
                    "kind": "FAMILY_MASTER_ENABLES",
                }
            )
        for contract in CONTRACT_NODES:
            edges.append(
                {"from": contract, "to": name, "kind": "CONTRACT_GOVERNS"}
            )
        rows.append(
            {
                "param": name,
                "family": info["family"],
                "main_switch": primary_master or "",
                "activation_dependencies": activation_dependencies,
                "master_resolution": (
                    "VERIFIED_PREFIX_OR_EXPLICIT"
                    if activation_dependencies
                    else "SELF_MASTER"
                    if info["effective_role"] == "MAIN_SWITCH"
                    else "STANDALONE_OR_UNRESOLVED"
                ),
                "group": info["group"],
                "registry_role": info["registry_role"],
                "role": info["effective_role"],
                "kind": info["kind"],
                "default": info["default"],
                "off_value": info["off_value"],
                "per_symbol_live_read": bool(info["per_sym"]),
                "consumed_by": spec.get("consumed_by") or {},
                "sweep_tier": spec.get("sweep_tier"),
                "test_values": grid["test_values"],
                "default_control_present": grid["default_control_present"],
                "grid_repair_reasons": grid["grid_repair_reasons"],
                **capability,
                "precedence_layer": layer,
                "precedence_order": LAYERS[layer],
                "description": path_description,
                "description_source": (
                    "VERIFIED"
                    if name in reviewed_descriptions
                    else "GENERATED_CONSERVATIVE"
                ),
                "switch_guidance": switch_guidance(layer, info),
                "retest_class": klass,
                "invalidates_layers": RETEST_RULES[klass]["invalidates"],
                "requires_controls": RETEST_RULES[klass][
                    "requires_controls"
                ],
                "dependencies": dependencies,
            }
        )
    summary = {
        "verified_descriptions": sum(
            row["description_source"] == "VERIFIED" for row in rows
        ),
        "generated_conservative_descriptions": sum(
            row["description_source"] == "GENERATED_CONSERVATIVE" for row in rows
        ),
        "grid_repair_required": sum(
            bool(row["grid_repair_reasons"]) for row in rows
        ),
        "no_live_decision_read": sum(
            row["deployment_scope"] == "NONDEPLOYABLE_NO_LIVE_READ"
            for row in rows
        ),
        "global_only_live": sum(
            row["deployment_scope"] == "GLOBAL_ONLY" for row in rows
        ),
        "per_symbol_live": sum(
            row["deployment_scope"] == "PER_SYMBOL" for row in rows
        ),
        "vector_screenable": sum(
            "VECTOR_DIAGNOSTIC" in row["screen_backends"] for row in rows
        ),
        "exact_only_no_vector": sum(
            row["screen_backends"] == ["EXACT_V8"] for row in rows
        ),
        "activation_edges": sum(
            len(row["activation_dependencies"]) for row in rows
        ),
        "standalone_or_unresolved": sum(
            row["master_resolution"] == "STANDALONE_OR_UNRESOLVED"
            for row in rows
        ),
        "registry_bool_master_demotions": sum(
            row["registry_role"] == "MAIN_SWITCH"
            and row["role"] != "MAIN_SWITCH"
            for row in rows
        ),
        "token_only_live_references": sum(
            row["binding_evidence"] == "TOKEN_ONLY_LIVE_REFERENCE"
            for row in rows
        ),
    }
    return {
        "schema_version": 2,
        "scope": "tradier sweepable non-option SWITCH_MATRIX paths",
        "source_manifest": str(MANIFEST.relative_to(ROOT)),
        "path_count": len(rows),
        "contract_nodes": CONTRACT_NODES,
        "precedence_layers": LAYERS,
        "layer_edges": [
            ["DATA_SIDE_ACCOUNT", "FAMILY_MASTER"],
            ["FAMILY_MASTER", "CROSS_CUTTING_GUARD"],
            ["FAMILY_MASTER", "ENTRY_SOURCE"],
            ["FAMILY_MASTER", "EXIT_SOURCE"],
            ["CROSS_CUTTING_GUARD", "ENTRY_SOURCE"],
            ["ENTRY_SOURCE", "ENTRY_FILTER"],
            ["ENTRY_FILTER", "EXIT_SOURCE"],
            ["EXIT_SOURCE", "EXIT_FILTER"],
            ["EXIT_FILTER", "REENTRY"],
            ["REENTRY", "SIZING"],
        ],
        "retest_rules": RETEST_RULES,
        "audit_summary": summary,
        "paths": rows,
        "adjacency": edges,
        "write_contract": {
            "live_config": False,
            "matrix_cells": False,
            "fingerprints": False,
            "research_metadata_only": True,
        },
    }


def write_outputs(payload: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    temp = json_path.with_suffix(json_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temp.replace(json_path)
    fields = [
        "param",
        "family",
        "main_switch",
        "activation_dependencies",
        "master_resolution",
        "group",
        "registry_role",
        "role",
        "kind",
        "default",
        "test_values",
        "default_control_present",
        "grid_repair_reasons",
        "screen_backends",
        "differential_readiness",
        "deployment_scope",
        "binding_evidence",
        "static_read_sites",
        "promotion_blockers",
        "precedence_layer",
        "precedence_order",
        "retest_class",
        "description",
        "description_source",
        "switch_guidance",
        "per_symbol_live_read",
        "consumed_by",
        "invalidates_layers",
        "requires_controls",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in payload["paths"]:
            out = {field: row.get(field) for field in fields}
            for field in (
                "activation_dependencies",
                "test_values",
                "grid_repair_reasons",
                "screen_backends",
                "static_read_sites",
                "promotion_blockers",
                "consumed_by",
                "invalidates_layers",
                "requires_controls",
            ):
                out[field] = json.dumps(out[field], sort_keys=True)
            writer.writerow(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    payload = build_payload()
    write_outputs(payload, args.json, args.csv)
    print(
        json.dumps(
            {
                "paths": payload["path_count"],
                "edges": len(payload["adjacency"]),
                "audit_summary": payload["audit_summary"],
                "json": str(args.json),
                "csv": str(args.csv),
                "write_contract": payload["write_contract"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
