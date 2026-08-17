#!/usr/bin/env python3
"""Fail-closed wiring audit for stale EXIT_ALGO_EXIT_ENABLED inventory row."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STALE_KEY = "ALGO_EXIT_ENABLED"
DECLARED_KEY = "EXIT_ALGO_SCORE_ENABLED"


def _annassign_value(tree: ast.AST, name: str) -> Any:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            try:
                return ast.literal_eval(node.value)
            except Exception:
                return "NON_LITERAL"
    return None


def _getattr_lines(tree: ast.AST, key: str) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "getattr":
            continue
        if len(node.args) < 2:
            continue
        if isinstance(node.args[1], ast.Constant) and node.args[1].value == key:
            lines.append(int(node.lineno))
    return sorted(lines)


def _conditional_key_lines(tree: ast.AST, key: str) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.IfExp)):
            continue
        test = node.test
        if key in ast.unparse(test):
            lines.append(int(node.lineno))
    return sorted(lines)


def _exit_scorer_calls(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else ""
        )
        if name != "calculate_signal_score":
            continue
        if any(
            keyword.arg == "is_exit"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            lines.append(int(node.lineno))
    return sorted(lines)


def _active_algo_reason_lines(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                "ALGO_EXIT" in node.value
                or "ALGO_WEAK_EXIT" in node.value
            )
        ):
            lines.append(int(node.lineno))
    return sorted(set(lines))


def audit_sources(config_source: str, manage_source: str) -> dict[str, Any]:
    config_tree = ast.parse(config_source)
    manage_tree = ast.parse(manage_source)
    stale_default = _annassign_value(config_tree, STALE_KEY)
    declared_default = _annassign_value(config_tree, DECLARED_KEY)
    stale_reads = _getattr_lines(manage_tree, STALE_KEY)
    declared_reads = _getattr_lines(manage_tree, DECLARED_KEY)
    stale_conditionals = _conditional_key_lines(manage_tree, STALE_KEY)
    declared_conditionals = _conditional_key_lines(manage_tree, DECLARED_KEY)
    exit_calls = _exit_scorer_calls(manage_tree)
    active_reasons = _active_algo_reason_lines(manage_tree)
    proven = (
        stale_default is None
        and not stale_reads
        and declared_default is False
        and bool(declared_reads)
        and not declared_conditionals
        and not exit_calls
        and not active_reasons
    )
    return {
        "classification": (
            "DISCONNECTED_DISABLED_INERT_REGISTRY_ROW"
            if proven
            else "REQUIRES_FRESH_MANUAL_AUDIT"
        ),
        "inventory_key": STALE_KEY,
        "inventory_key_declared": stale_default is not None,
        "inventory_key_default": stale_default,
        "inventory_key_read_lines": stale_reads,
        "inventory_key_conditional_lines": stale_conditionals,
        "replacement_key": DECLARED_KEY,
        "replacement_key_declared": declared_default is not None,
        "replacement_key_default": declared_default,
        "replacement_key_read_lines": declared_reads,
        "replacement_key_conditional_lines": declared_conditionals,
        "active_calculate_signal_score_is_exit_true_lines": exit_calls,
        "active_algo_exit_reason_lines": active_reasons,
        "current_exit_scorer_definition_present": (
            "if is_exit and position" in manage_source
            and 'rec = "STRONG_REDUCE"' in manage_source
        ),
        "current_exit_scorer_router_present": bool(exit_calls),
        "screen_authorized": False,
        "screen_block_reason": (
            "No active ALGO gate/router/reason exists; vectorizing a guessed "
            "compound scorer would test a new strategy under a stale live name."
        ),
        "historical_hardcoded_components": {
            "STRUCTURE_1H_DC": {
                "timeframe": "1h",
                "score_delta": -15,
            },
            "STRUCTURE_15M_DC": {
                "timeframe": "15m",
                "score_delta": -10,
            },
            "STOCH_4H_ROLL": {
                "timeframe": "4h",
                "score_delta": -5,
                "long_k_min": 60,
                "short_k_max": 20,
            },
            "PROFIT_TAKE_15M": {
                "timeframe": "15m",
                "score_delta": -5,
                "gain_pct_strictly_above": 5.0,
            },
            "BEAR_MODE_BIAS": {
                "long_score_delta": -20,
                "short_score_delta": 15,
            },
        },
        "historical_router_thresholds": {
            "strong_score_lte": -7,
            "weak_score_lte": -4,
            "recommendation_contains": "STRONG_REDUCE",
        },
        "inventory_description_mismatch": (
            "The inventory says RSI/MFI extreme + stale indicators, but the "
            "historical exit-specific penalties were DC structure, 4h Stoch "
            "roll, 15m profit turn, and optional bear-mode bias."
        ),
    }


def audit_repo(root: Path = ROOT) -> dict[str, Any]:
    return audit_sources(
        (root / "config_tradier.py").read_text(),
        (root / "tradier_manage.py").read_text(),
    )


def render_markdown(payload: dict[str, Any]) -> str:
    component_rows = []
    for name, values in payload["historical_hardcoded_components"].items():
        component_rows.append(
            f"| `{name}` | `{json.dumps(values, sort_keys=True)}` |"
        )
    return "\n".join(
        [
            "# EXIT_ALGO_EXIT_ENABLED job66 — wiring audit",
            "",
            f"Classification: **{payload['classification']}**.",
            "",
            "## Active code proof",
            "",
            f"- Stale inventory key `{STALE_KEY}` declared/read: "
            f"{payload['inventory_key_declared']}/"
            f"{bool(payload['inventory_key_read_lines'])}.",
            f"- Actual config key `{DECLARED_KEY}` default: "
            f"`{payload['replacement_key_default']}`.",
            f"- Its only active read lines: "
            f"`{payload['replacement_key_read_lines']}`; conditional/router "
            f"reads: `{payload['replacement_key_conditional_lines']}`.",
            f"- Active `calculate_signal_score(..., is_exit=True)` calls: "
            f"`{payload['active_calculate_signal_score_is_exit_true_lines']}`.",
            f"- Active ALGO exit reason constants: "
            f"`{payload['active_algo_exit_reason_lines']}`.",
            "",
            "The declared key is present only in the Group-C discoverability "
            "tuple. The old call and both ALGO return branches are comments, "
            "so toggling either name cannot emit an exit.",
            "",
            "## Historical compound pieces — inventory only",
            "",
            "| event type | hard-coded semantics |",
            "|---|---|",
            *component_rows,
            "",
            "These components are not one authorized research arm. They need "
            "separate path IDs, completed-bar definitions, and ranges before "
            "any future screen. The registry description's RSI/MFI/staleness "
            "claim does not match the historical exit-specific code.",
            "",
            "## Campaign disposition",
            "",
            "No top/bottom cohort vector screen was run. With no connected "
            "event path, such a screen would manufacture a strategy and then "
            "mislabel it as live parity. Job66 is retained red/disconnected; "
            "there are no exact-replay or matrix candidates.",
            "",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-output", type=Path)
    ap.add_argument("--markdown-output", type=Path)
    args = ap.parse_args()
    payload = audit_repo()
    if args.json_output:
        args.json_output.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n"
        )
    if args.markdown_output:
        args.markdown_output.write_text(render_markdown(payload))
    print(json.dumps(payload, sort_keys=True))
    return (
        0
        if payload["classification"]
        == "DISCONNECTED_DISABLED_INERT_REGISTRY_ROW"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
