#!/usr/bin/env python3
"""Build the executable SWITCH_MATRIX dependency and invalidation inventory.

This is research metadata only. It reads the sweep manifest, config defaults,
and static live read-site registry; it never writes live config, fingerprints,
matrix cells, or promotion state.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "data/param_sweep_manifest_tradier.json"
DEFAULT_JSON = (
    ROOT / "data/reports/SWITCH_MATRIX_INTERDEPENDENCY_20260729.json"
)
DEFAULT_CSV = (
    ROOT / "data/reports/SWITCH_MATRIX_INTERDEPENDENCY_20260729.csv"
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
    "ENTRY_SOURCE": 20,
    "ENTRY_FILTER": 30,
    "EXIT_SOURCE": 40,
    "EXIT_FILTER": 50,
    "REENTRY": 60,
    "SIZING": 70,
    "OTHER_GUARD": 80,
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
            "OTHER_GUARD",
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
        "invalidates": ["REENTRY", "SIZING"],
        "requires_controls": [
            "CONTRACT_DATA_CAUSALITY",
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Recompute the whole entry→exit→reentry cycle, including persistent "
            "exit-price/top reclaim violations."
        ),
    },
    "CAPITAL_METRIC_REPLAY": {
        "invalidates": ["SIZING"],
        "requires_controls": [
            "CONTRACT_SIDE_ACCOUNT",
            "CONTRACT_CAPITAL_COST",
            "CONTRACT_BASELINE_CONTROL",
        ],
        "rule": (
            "Signals may be reusable, but fills, capacity, drawdown, return, TIM "
            "and B&H comparisons must be replayed."
        ),
    },
    "CONSERVATIVE_COHERENT_BUNDLE": {
        "invalidates": list(LAYERS),
        "requires_controls": list(CONTRACT_NODES),
        "rule": "Unknown/guard interaction: fail closed and retest the complete bundle.",
    },
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


def layer_for(name: str, info: dict[str, Any]) -> str:
    upper = name.upper()
    role = str(info.get("role") or "")
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
    return "OTHER_GUARD"


def master_map(registry: dict[str, dict[str, Any]]) -> dict[str, str]:
    families: dict[str, list[str]] = defaultdict(list)
    for name, info in registry.items():
        if info.get("role") == "MAIN_SWITCH":
            families[str(info.get("family") or name)].append(name)
    result = {}
    all_mains = sorted(
        (
            name
            for name, info in registry.items()
            if info.get("role") == "MAIN_SWITCH"
        ),
        key=len,
        reverse=True,
    )
    for name, info in registry.items():
        family = str(info.get("family") or name)
        mains = sorted(families.get(family, []))
        preferred = next(
            (row for row in mains if row == f"{family}_ENABLED"),
            next((row for row in mains if row.endswith("_ENABLED")), None),
        )
        selected = preferred or (mains[0] if len(mains) == 1 else None)
        if selected is None and info.get("role") != "MAIN_SWITCH":
            # Registry families intentionally use a short three-token prefix,
            # which can separate ATR_PARITY_ENABLED from
            # ATR_PARITY_QTY_CAP_MULT. Recover the nearest explicit master by
            # longest token-boundary prefix; never invent a master name.
            candidates = []
            for candidate in all_mains:
                stem = (
                    candidate[: -len("_ENABLED")]
                    if candidate.endswith("_ENABLED")
                    else candidate
                )
                if name.startswith(stem + "_"):
                    candidates.append((len(stem), candidate))
            if candidates:
                selected = max(candidates)[1]
        result[name] = selected or name
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
    return "CONSERVATIVE_COHERENT_BUNDLE"


def description(name: str, info: dict[str, Any]) -> str:
    try:
        from tools.export_switch_matrix_xls import _VERIFIED_DESCRIPTIONS
    except ImportError:
        _VERIFIED_DESCRIPTIONS = {}
    if name in _VERIFIED_DESCRIPTIONS:
        return _VERIFIED_DESCRIPTIONS[name]
    group = str(info.get("group") or "OTHER")
    role = str(info.get("role") or "SUB_SETTING").replace("_", " ")
    words = name.replace("_", " ").lower()
    if role == "MAIN SWITCH":
        effect = (
            f"turns the {info.get('family') or name} family on/off; disabling "
            "removes that action family and invalidates its dependent settings"
        )
    elif group == "ENTRY":
        effect = (
            "controls which bars may open/add; changing it changes the trade "
            "population consumed by every later exit and reentry"
        )
    elif "REENTRY" in name or "RECLAIM" in name:
        effect = (
            "controls reopening after an exit; it must be measured with the "
            "paired exit and persistent reclaim obligation"
        )
    elif group == "EXIT":
        effect = (
            "controls when/how exposure closes or reduces; it must be measured "
            "with a frozen entry population and paired reentry"
        )
    elif group == "SIZING":
        effect = (
            "changes requested quantity/deployment; signal count may stay fixed "
            "but fills, capacity, drawdown and return must be replayed"
        )
    else:
        effect = (
            f"controls {words}; direction is not safely inferable from the name, "
            "so use a same-fold control and fail closed"
        )
    scope = "per-symbol" if info.get("per_sym") else "global-only"
    return (
        f"{group} {role} — {effect}; off={info.get('off_value')!r}; "
        f"{scope}."
    )


def switch_guidance(layer: str, info: dict[str, Any]) -> str:
    kind = info.get("kind")
    if layer == "ENTRY_SOURCE":
        return "Enable to admit this entry source; expect more opens/TIM, then measure."
    if layer == "ENTRY_FILTER":
        return "Tighten/enable to reduce qualifying entries; loosen to restore trades."
    if layer == "EXIT_SOURCE":
        return "Enable to add closes/reduces; expect lower TIM, but rank only with reentry."
    if layer == "EXIT_FILTER":
        return "Tighten to delay/fewer exits; loosen for earlier/more exits; measure."
    if layer == "REENTRY":
        return "Enable/loosen when valid exits miss continuation; never clear the reclaim latch."
    if layer == "SIZING":
        return "Use for deployment/capacity, not to manufacture entry/exit alpha."
    if info.get("role") == "MAIN_SWITCH":
        return "Prove off/on changes the exact trade fingerprint before testing sub-settings."
    if kind == "num":
        return "Numeric polarity is signal-dependent; screen a bounded range, never guess."
    return "Treat as a guard; require a changed exact fingerprint or mark RECONNECT."


def build_payload() -> dict[str, Any]:
    manifest = tradeable_manifest_rows()
    registry = registry_rows()
    masters = master_map(registry)
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
        layer = layer_for(name, info)
        master = masters.get(name, name)
        klass = retest_class(layer, str(info.get("role")))
        dependencies = list(CONTRACT_NODES)
        if master != name:
            dependencies.append(master)
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
                "main_switch": master,
                "group": info["group"],
                "role": info["role"],
                "kind": info["kind"],
                "default": info["default"],
                "off_value": info["off_value"],
                "per_symbol_live_read": bool(info["per_sym"]),
                "consumed_by": spec.get("consumed_by") or {},
                "precedence_layer": layer,
                "precedence_order": LAYERS[layer],
                "description": description(name, info),
                "switch_guidance": switch_guidance(layer, info),
                "retest_class": klass,
                "invalidates_layers": RETEST_RULES[klass]["invalidates"],
                "requires_controls": RETEST_RULES[klass][
                    "requires_controls"
                ],
                "dependencies": dependencies,
            }
        )
    return {
        "schema_version": 1,
        "scope": "tradier sweepable non-option SWITCH_MATRIX paths",
        "source_manifest": str(MANIFEST.relative_to(ROOT)),
        "path_count": len(rows),
        "contract_nodes": CONTRACT_NODES,
        "precedence_layers": LAYERS,
        "layer_edges": [
            ["DATA_SIDE_ACCOUNT", "FAMILY_MASTER"],
            ["FAMILY_MASTER", "ENTRY_SOURCE"],
            ["ENTRY_SOURCE", "ENTRY_FILTER"],
            ["ENTRY_FILTER", "EXIT_SOURCE"],
            ["EXIT_SOURCE", "EXIT_FILTER"],
            ["EXIT_FILTER", "REENTRY"],
            ["REENTRY", "SIZING"],
            ["SIZING", "OTHER_GUARD"],
        ],
        "retest_rules": RETEST_RULES,
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
        "group",
        "role",
        "precedence_layer",
        "precedence_order",
        "retest_class",
        "description",
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
