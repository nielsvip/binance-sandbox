#!/usr/bin/env python3
"""Build the next source-backed v12 entry contract handoff tranche.

The tool is planning-only: it reads the frozen missing-field map and current
sources, rejects name-only/generated wiring, and writes 100 disjoint contract
briefs for fields still undeclared in the current QuickConfig.  It never edits
an engine or runs market data.
"""
from __future__ import annotations

import argparse
import ast
import csv
import dataclasses
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAP = ROOT / "data/reports/V12_MISSING_FIELD_DEFINITION_MAP.json"
QUICK = ROOT / "v12_quick_engine.py"
TARGET = 100
ENTRY_TRANCHES = {"01_ENTRY_SIGNALS", "02_ENTRY_FILTERS_CONFIRMATIONS"}
REJECT_FUNCTION_PATTERNS = (
    "_ensure_", "_wire_weak", "_full_coverage", "_generated_config_census",
    "_apply_625_", "_apply_auto_wired", "_apply_research_only_live_gates",
)
CONFIG_READ_PATTERNS = (
    re.compile(r"\.\s*([A-Z][A-Z0-9_]{2,})\b"),
    re.compile(r"(?:getattr|_cfg|_cfg_auto|resolver)\([^\n]*?[\"']([A-Z][A-Z0-9_]{2,})[\"']"),
)
DATA_KEY_RE = re.compile(
    r"[\"']((?:open|high|low|close|volume|relative_volume|wt1|wt2|stoch_k|stoch_d|"
    r"rsi|mfi|adx|atr|bb_pct_b|bb_upper|bb_lower|dc_high|dc_low|dc_basis|"
    r"ema_[0-9]+|sma_[0-9]+|macd|macd_signal|macd_hist|lr_pct_b|lrL_slope)"
    r"(?:_[A-Za-z0-9{}]+)*)[\"']"
)
_SOURCE_CACHE: dict[Path, str] = {}
_LINES_CACHE: dict[Path, list[str]] = {}
_AST_CACHE: dict[Path, tuple[ast.AST, dict[ast.AST, ast.AST]]] = {}
_LINE_NODES_CACHE: dict[Path, dict[int, list[ast.AST]]] = {}


def source_text(path: Path) -> str:
    if path not in _SOURCE_CACHE:
        _SOURCE_CACHE[path] = path.read_text(errors="replace")
    return _SOURCE_CACHE[path]


def source_lines(path: Path) -> list[str]:
    if path not in _LINES_CACHE:
        _LINES_CACHE[path] = source_text(path).splitlines()
    return _LINES_CACHE[path]


def parsed_source(path: Path) -> tuple[ast.AST, dict[ast.AST, ast.AST]]:
    if path not in _AST_CACHE:
        tree = ast.parse(source_text(path), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        _AST_CACHE[path] = (tree, parents)
        by_line: dict[int, list[ast.AST]] = {}
        for node in ast.walk(tree):
            line = getattr(node, "lineno", None)
            if line is not None:
                by_line.setdefault(int(line), []).append(node)
        _LINE_NODES_CACHE[path] = by_line
    return _AST_CACHE[path]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def current_quick_fields() -> set[str]:
    import v12_quick_engine as engine
    return {field.name for field in dataclasses.fields(engine.QuickConfig)}


def parse_site(site: str) -> tuple[Path, int, str, str]:
    filename, line, function, kind = site.rsplit(":", 3)
    return ROOT / filename, int(line), function, kind


def good_site(site: str) -> bool:
    try:
        path, _line, function, _kind = parse_site(site)
    except ValueError:
        return False
    return path.exists() and not any(token in function for token in REJECT_FUNCTION_PATTERNS)


def source_window(path: Path, line: int, radius: int = 12) -> dict[str, Any]:
    lines = source_lines(path)
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return {
        "path": str(path.relative_to(ROOT)),
        "source_sha256": sha256(path),
        "start_line": start,
        "end_line": end,
        "text": "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1)),
    }


def field_read_node(path: Path, field: str, line: int) -> tuple[ast.AST, ast.AST, dict[ast.AST, ast.AST]] | None:
    tree, parents = parsed_source(path)
    matches = []
    for node in _LINE_NODES_CACHE[path].get(line, []):
        text = source_lines(path)[line - 1]
        if field in text:
            matches.append(node)
    if not matches:
        return None
    node = min(matches, key=lambda item: len(list(ast.walk(item))))
    return tree, node, parents


def semantic_statement(path: Path, field: str, line: int) -> dict[str, Any]:
    parsed = field_read_node(path, field, line)
    if parsed is None:
        return {"ast_available": False, "source": ""}
    _tree, node, parents = parsed
    current = node
    statement = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.If, ast.IfExp, ast.Return, ast.Expr)):
            statement = current
            if isinstance(current, (ast.If, ast.Return, ast.AugAssign)):
                break
    statement_line = getattr(statement, "lineno", line)
    statement_end = getattr(statement, "end_lineno", line)
    source = "\n".join(source_lines(path)[statement_line - 1 : statement_end])
    return {
        "ast_available": True,
        "line": getattr(statement, "lineno", line),
        "end_line": getattr(statement, "end_lineno", line),
        "node_type": type(statement).__name__,
        "source": source,
    }


def config_default_at_read(path: Path, field: str, line: int, fallback: Any) -> dict[str, Any]:
    text = source_lines(path)[line - 1]
    # Capture the immediate literal default in getattr/_cfg-style calls.  The
    # authoritative definition remains separately preserved even when a read
    # has no literal fallback.
    pattern = re.compile(
        rf"(?:getattr\([^,]+,\s*|_cfg(?:_auto)?\()\s*[\"']{re.escape(field)}[\"']\s*,\s*([^,)]+)"
    )
    match = pattern.search(text)
    if not match:
        return {"value": fallback, "source": "AUTHORITATIVE_DEFINITION"}
    raw = match.group(1).strip()
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        value = raw
    return {"value": value, "source": f"READ_FALLBACK:{path.name}:{line}", "expression": raw}


def parent_and_composition(field: str, statement: str, window: str) -> tuple[list[str], str]:
    names = set()
    for pattern in CONFIG_READ_PATTERNS:
        names.update(pattern.findall(window))
    parents = sorted(name for name in names if name != field and name.endswith(("_ENABLED", "_GATE", "_MODE")))
    low = statement.lower()
    if re.search(r"\b(score|bonus|quality)\s*\+=", low):
        composition = "ADDITIVE_SCORE"
    elif re.search(r"\b(score|bonus|quality)\s*-=", low):
        composition = "SUBTRACTIVE_SCORE"
    elif "*=" in statement or re.search(r"\b(mult|multiplier|qty|size)\b", low):
        composition = "MULTIPLICATIVE_OR_SIZING_PARAMETER"
    elif any(operator in statement for operator in (">=", "<=", " > ", " < ")):
        composition = "THRESHOLD_PREDICATE"
    elif "if " in low or " and " in low or " or " in low:
        composition = "BOOLEAN_GATE_OR_SIGNAL"
    else:
        composition = "PARAMETER_FEEDS_NAMED_SOURCE_FUNCTION"
    return parents, composition


def data_contract(map_row: dict[str, Any], statement: str, window: str) -> tuple[list[str], str, dict[str, Any]]:
    exact = sorted(set(DATA_KEY_RE.findall(statement + "\n" + window)))
    if exact:
        arrays = exact
        basis = "LITERAL_DATA_KEYS_IN_AUTHORITATIVE_SOURCE_WINDOW"
    else:
        arrays = list(map_row.get("required_npz_arrays") or [])
        basis = "SEMANTIC_FAMILY_ARRAY_PATTERN; VECTOR_IMPLEMENTATION_MUST_BIND_AND_TEST_EXACT_KEYS"
    fallbacks = []
    for match in re.finditer(r"\.get\(\s*[\"']([^\"']+)[\"']\s*,\s*([^\)]+)\)", window):
        if match.group(1) in arrays or any(match.group(1).startswith(prefix.split("_")[0]) for prefix in arrays):
            fallbacks.append({"field": match.group(1), "expression": match.group(2).strip()})
    if fallbacks:
        missing = {
            "behavior": "MATCH_AUTHORITATIVE_SOURCE_FALLBACKS",
            "source_fallbacks": fallbacks,
            "vector_requirement": "Do not substitute zero/hash events; reproduce these fallbacks and validity predicates exactly.",
        }
    elif arrays:
        missing = {
            "behavior": "FAIL_CLOSED_UNTIL_ARRAY_BINDING_IS_EXACTLY_PROVEN",
            "source_fallbacks": [],
            "vector_requirement": "A missing/non-finite required array must not create a signal or score contribution.",
        }
    else:
        missing = {
            "behavior": "NO_DIRECT_MARKET_ARRAY_REQUIRED_BY_THIS_PARAMETER",
            "source_fallbacks": [],
            "vector_requirement": "Preserve the source control/state semantics; do not invent an indicator proxy.",
        }
    return arrays, basis, missing


def npz_data_condition(arrays: list[str], basis: str) -> str:
    if not arrays:
        return "N/A_DATA_NO_DIRECT_MARKET_ARRAY"
    if basis == "LITERAL_DATA_KEYS_IN_AUTHORITATIVE_SOURCE_WINDOW":
        return "REQUIRED_ARRAYS_LITERAL_SOURCE_KEYS"
    return "N/A_DATA_EXACT_KEY_BINDING_UNPROVEN; USE_LISTED_PATTERNS_AND_FAIL_CLOSED"


def semantics_summary(field: str, definition: dict[str, Any] | None, composition: str, site: str) -> str:
    default = None if not definition else definition.get("default")
    return (
        f"Read {field} with authoritative default {default!r} at {site}; "
        f"apply it only through the cited source statement using {composition}. "
        "The cited statement/window is the normative semantic evidence; no proxy, hash, or synthetic event is permitted."
    )


def build() -> dict[str, Any]:
    source_map = json.loads(MAP.read_text())
    current = current_quick_fields()
    candidates = [
        row for row in source_map["rows"]
        if row.get("operational_field")
        and row.get("recommended_disjoint_vector_tranche") in ENTRY_TRANCHES
        and row["field"] not in current
    ]
    candidates.sort(key=lambda row: (row["recommended_disjoint_vector_tranche"], row["field"]))
    accepted, rejected, deferred = [], [], []
    for row in candidates:
        reasons = []
        if not row.get("authoritative_definition_sites"):
            reasons.append("NO_AUTHORITATIVE_CONFIG_DEFINITION")
        sites = [
            site for site in (
                list(row.get("existing_live_read_sites") or [])
                + list(row.get("existing_vector_read_sites") or [])
            ) if good_site(site)
        ]
        # Live operational semantics are preferred; an existing causal vector
        # reader is used only when no live site survives the generated-router
        # rejection.
        sites.sort(key=lambda site: (0 if any(site.startswith(f"{name}:") for name in (
            "tradier_manage.py", "ez_manage.py", "ez_positions_quick.py", "ez_reentry.py"
        )) else 1, site))
        if not sites:
            reasons.append("NO_NON_GENERATED_VECTOR_OR_LIVE_READ_SITE")
        if reasons:
            rejected.append({"field": row["field"], "reasons": reasons})
            continue
        if len(accepted) >= TARGET:
            deferred.append({"field": row["field"], "reason": "AFTER_NEXT_100_BOUNDARY"})
            continue
        site = sites[0]
        path, line, function, kind = parse_site(site)
        window = source_window(path, line)
        statement = semantic_statement(path, row["field"], line)
        parents, composition = parent_and_composition(row["field"], statement.get("source", ""), window["text"])
        arrays, array_basis, missing = data_contract(row, statement.get("source", ""), window["text"])
        read_default = config_default_at_read(
            path, row["field"], line, row.get("authoritative_default")
        )
        accepted.append({
            "contract_index": len(accepted) + 1,
            "field": row["field"],
            "family": row["family"],
            "disjoint_vector_tranche": row["recommended_disjoint_vector_tranche"],
            "authoritative_type": row["authoritative_type"],
            "authoritative_default": row["authoritative_default"],
            "authoritative_grid": row["authoritative_grid"],
            "authoritative_definition_selected": row["authoritative_definition_selected"],
            "authoritative_definition_sites": row["authoritative_definition_sites"],
            "authoritative_semantics": semantics_summary(
                row["field"], row.get("authoritative_definition_selected"), composition, site
            ),
            "authoritative_read_site": site,
            "authoritative_read_kind": kind,
            "authoritative_function": function,
            "authoritative_semantic_statement": statement,
            "authoritative_source_window": window,
            "read_missing_config_behavior": read_default,
            "parent_gates_or_modes_in_source_window": parents,
            "score_or_gate_composition": composition,
            "required_npz_arrays": arrays,
            "required_npz_arrays_basis": array_basis,
            "npz_data_condition": npz_data_condition(arrays, array_basis),
            "side_applicability": row["side_applicability"],
            "venue_applicability": row["venue_applicability"],
            "timeframe_applicability": row["timeframe_applicability"],
            "missing_market_data_behavior": missing,
            "implementation_guard": "NO_SYNTHETIC_HASH_PROXY; EXACT_CAUSAL_SOURCE_TWIN_ONLY",
        })
    if len(accepted) != TARGET:
        raise RuntimeError(f"INSUFFICIENT_SOURCE_BACKED_ENTRY_CONTRACTS:{len(accepted)}")
    tranche_counts = Counter(row["disjoint_vector_tranche"] for row in accepted)
    return {
        "schema": "v12-next-entry-contract-tranche-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS_SOURCE_BACKED_CONTRACT_BRIEFS_NOT_IMPLEMENTED",
        "selection": {
            "source_map": str(MAP.relative_to(ROOT)),
            "source_map_sha256": sha256(MAP),
            "current_v12_quick_engine_sha256": sha256(QUICK),
            "current_quickconfig_fields": len(current),
            "historical_baseline_quickconfig_fields": source_map["counts"]["baseline_quickconfig_fields"],
            "candidate_rows_still_undeclared": len(candidates),
            "accepted_contracts": len(accepted),
            "rejected_without_source_semantics": len(rejected),
            "deferred_after_next_100": len(deferred),
            "disjoint_tranche_counts": dict(sorted(tranche_counts.items())),
        },
        "claims": {
            "no_engine_or_live_edits": True,
            "no_backtests": True,
            "generated_or_weak_wiring_counted_as_semantics": False,
            "synthetic_or_hash_proxy_allowed": False,
            "contracts_are_disjoint_by_field": len({row["field"] for row in accepted}) == len(accepted),
            "full_parity_complete": False,
        },
        "contracts": accepted,
        "rejected": rejected,
        "deferred": deferred,
    }


def write_csv(path: Path, contracts: list[dict[str, Any]]) -> None:
    fields = (
        "contract_index", "field", "family", "disjoint_vector_tranche",
        "authoritative_type", "authoritative_default", "authoritative_grid",
        "authoritative_semantics", "authoritative_read_site", "authoritative_function",
        "parent_gates_or_modes_in_source_window", "score_or_gate_composition",
        "required_npz_arrays", "required_npz_arrays_basis", "side_applicability",
        "npz_data_condition",
        "venue_applicability", "timeframe_applicability", "read_missing_config_behavior",
        "missing_market_data_behavior", "implementation_guard",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for raw in contracts:
            row = {key: raw.get(key) for key in fields}
            for key, value in list(row.items()):
                if isinstance(value, (list, dict)):
                    row[key] = json.dumps(value, sort_keys=True, separators=(",", ":"))
            writer.writerow(row)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path, default=ROOT / "data/reports/V12_NEXT_100_ENTRY_CONTRACT_TRANCHE.json")
    parser.add_argument("--csv-out", type=Path, default=ROOT / "data/reports/V12_NEXT_100_ENTRY_CONTRACT_TRANCHE.csv")
    args = parser.parse_args()
    payload = build()
    atomic(args.json_out, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    write_csv(args.csv_out, payload["contracts"])
    print(json.dumps({"status": payload["status"], "selection": payload["selection"], "claims": payload["claims"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
