"""Static inventory of parity-blocked paper scenarios; nothing here is executable."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any


VENUES = {"crypto": ("config.py", "ez_manage.py"), "stocks": ("config_tradier.py", "tradier_manage.py")}
SIDES = ("LONG", "SHORT")
MASKS = (
    ("stocks", "config_tradier.py", "PARITY_NON_VECTORIZABLE_SWITCHES", "parity_non_vectorizable"),
    ("crypto", "ez_manage.py", "_NON_VEC_KNOBS_EZ", "non_vec_knobs"),
    ("crypto", "ez_manage.py", "SUB15M_BLOCKED_SWITCHES", "sub15m_blocked"),
    ("stocks", "tradier_manage.py", "SUB15M_BLOCKED_SWITCHES", "sub15m_blocked"),
)


def _literal(node: ast.AST) -> tuple[bool, Any]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset" and len(node.args) == 1:
        known, value = _literal(node.args[0])
        return (True, frozenset(value)) if known and isinstance(value, (list, tuple, set, frozenset)) else (False, None)
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False, None


def _scope_assignments(tree: ast.AST) -> list[tuple[str, ast.AST, int]]:
    rows = []
    bodies = [(tree.body, "module")]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in {"Config", "TradierConfig"}:
            bodies.append((node.body, node.name))
    for body, _scope in bodies:
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        rows.append((target.id, value, node.lineno))
    return rows


def _switch_defaults(tree: ast.AST) -> dict[str, dict[str, Any]]:
    result = {}
    for name, node, line in _scope_assignments(tree):
        known, value = _literal(node)
        result[name] = {"known": known, "value": value if known else None, "line": line}
    return result


def _mask(tree: ast.AST, constant: str) -> tuple[dict[str, Any], int | None]:
    matches = [(node, line) for name, node, line in _scope_assignments(tree) if name == constant]
    if not matches:
        return {}, None
    node, line = matches[-1]
    known, value = _literal(node)
    if not known:
        return {}, line
    if isinstance(value, dict):
        return value, line
    if isinstance(value, (tuple, list, set, frozenset)):
        return {key: None for key in value if isinstance(key, str)}, line
    return {}, line


def _low_tf_name(name: str) -> bool:
    # 15M is the primary decision timeframe; only sub-15m tokens are candidates.
    return re.search(r"(?i)(?:^|_)(1m|3m|5m)(?:_|$)", name) is not None


def _scenario(venue: str, side: str, switch: str | None, kind: str, default: dict[str, Any] | None, blocked: Any, provenance: list[dict[str, Any]], coverage_kind: str) -> dict[str, Any]:
    known = bool(default and default["known"])
    value = default["value"] if known else None
    default_kind = "unresolved" if not known else ("boolean" if isinstance(value, bool) else "non_boolean")
    payload = {"venue": venue, "side": side, "kind": kind, "switch": switch, "default_known": known, "default_kind": default_kind, "default_value": value, "blocked_value": blocked, "provenance": provenance, "coverage_kind": coverage_kind, "executable": False}
    payload["scenario_id"] = "paper-" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return payload


def inventory(root: Path) -> dict[str, Any]:
    root = Path(root)
    file_names = sorted({name for _, name, _, _ in MASKS} | {pair[0] for pair in VENUES.values()})
    source_bytes = {name: (root / name).read_bytes() for name in file_names}
    file_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in source_bytes.items()}
    trees = {name: ast.parse(content.decode("utf-8"), filename=name) for name, content in source_bytes.items()}
    defaults = {venue: _switch_defaults(trees[config_name]) for venue, (config_name, _) in VENUES.items()}
    masks: dict[str, dict[str, dict[str, Any]]] = {venue: {} for venue in VENUES}
    sources = {}
    for venue, filename, constant, category in MASKS:
        values, line = _mask(trees[filename], constant)
        source = f"{filename}:{constant}"
        sources[source] = {"category": category, "count": len(values), "line": line, "sha256": file_hashes[filename], "venue": venue}
        for key, blocked_value in values.items():
            masks[venue].setdefault(key, []).append({"source": source, "line": line, "blocked_value": blocked_value})

    low_candidates: dict[str, dict[str, dict[str, Any]]] = {venue: {} for venue in VENUES}
    for venue, (config_name, _) in VENUES.items():
        tree = trees[config_name]
        for name, node, line in _scope_assignments(tree):
            if _low_tf_name(name):
                low_candidates[venue][name] = {"source": f"{config_name}:low_tf_candidate", "line": line}

    scenarios = []
    for venue in VENUES:
        config_name, live_name = VENUES[venue]
        scenarios.extend(_scenario(venue, side, None, "baseline", None, None, [], "baseline") for side in SIDES)
        names = set(masks[venue]) | set(low_candidates[venue])
        for side in SIDES:
            for key in sorted(names):
                provenance = list(masks[venue].get(key, []))
                coverage_kind = "verified_mask" if provenance else "low_tf_candidate"
                if not provenance:
                    candidate = low_candidates[venue][key]
                    provenance = [{**candidate, "sha256": file_hashes[config_name]}]
                default = defaults[venue].get(key, {"known": False, "value": None, "line": None})
                if default.get("line") is not None and not any(entry["source"].startswith(config_name + ":") for entry in provenance):
                    provenance.append({"source": config_name + ":default", "line": default["line"], "sha256": file_hashes[config_name]})
                blocked_values = [entry["blocked_value"] for entry in masks[venue].get(key, [])]
                blocked_value = blocked_values[0] if blocked_values and all(value == blocked_values[0] for value in blocked_values) else blocked_values or None
                scenarios.append(_scenario(venue, side, key, "one_switch", default, blocked_value, sorted(provenance, key=lambda item: (item["source"], item["line"] or 0)), coverage_kind))

    scenarios.sort(key=lambda row: (row["venue"], row["side"], row["kind"] != "baseline", row["switch"] or ""))
    coverage = {venue: {"baseline": 0, "one_switch": 0, "verified_mask": 0, "low_tf_candidate": 0, "resolved_defaults": 0, "unresolved_defaults": 0} for venue in VENUES}
    for row in scenarios:
        coverage[row["venue"]][row["kind"]] += 1
        if row["switch"] is not None:
            coverage[row["venue"]][row["coverage_kind"]] += 1
            coverage[row["venue"]]["resolved_defaults" if row["default_known"] else "unresolved_defaults"] += 1
    return {"schema_version": 1, "executable": False, "sources": sources, "file_sha256": file_hashes, "scenarios": scenarios, "coverage": coverage}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(inventory(args.root), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
