#!/usr/bin/env python3
"""V20 training-domain census: which live switches the network can see, and which it structurally cannot.

The network's training domain is EXACTLY the switches that have a TEMPLATE row, because a row is the only thing that
produces a delta-log eval and an eval is the only thing a head can learn from. A switch that is live, vectorised and
costing money, but has no row, is invisible to V20 for ever — no amount of extra corpus reaches it.

Measured 2026-10-26: 3,710 testable QuickConfig fields, 1,278 template switches union, so 2,458 fields (2,340 after
dropping infrastructure and ablation names) are outside the domain. That gap, not model quality, is what blocks a V21.

Why this is a tool and not a one-off query: the sanctioned adder `tools/v15_switch_add.py` is detector-blind — it
missed 9 switches on 2026-10-24 that had to be wired by hand, because it does not see a switch read through a
VARIABLE-held name (`flag = "X_ENABLED"; getattr(cfg, flag)`) and it treats forward-only vector absence as absence.
This census therefore counts a name as live-read if it appears as an attribute access OR inside a getattr OR as a bare
uppercase string literal in a live manager, which is a deliberate over-count: a false positive costs one review, a
false negative costs a switch that trades unmeasured for ever.

Read-only. Writes data/learn/v20_domain_gap_<CAT_SIDE>.json (own cat_side only, like every other learn artefact) plus
a summary. Never edits a template, a config or a workbook.

    python3 tools/v20_domain_census.py                      # summary to stdout
    python3 tools/v20_domain_census.py --write              # + the four gap artefacts
    python3 tools/v20_domain_census.py --rank-by-evidence   # order the gap by measured live loss
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SWITCH_RE = re.compile(r"[A-Z][A-Z0-9_]{3,}")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")
INFRA_RE = re.compile(
    r"^(ACCOUNT|AI_PREMARKET|LOG_|BASE_|BACKUP|SERVICE|TELEGRAM|API_|WS_|REDIS|DB_|PATH|DIR_|HOST|PORT|USER|SECRET|TOKEN|EMAIL|SLACK|METRICS_|DEBUG|VERBOSE|DRY_RUN|TEST_|SIM_)"
    r"|(_PATH|_DIR|_FILE|_URL|_HOST|_PORT|_KEY|_SECRET|_TOKEN|_ACCOUNTS|_ACCOUNT)$"
)
ABLATION_RE = re.compile(r"^ABLATION_")
TESTABLE = ("bool", "number", "enum_or_str")


def _upper_assigns(path: Path) -> dict:
    """Every uppercase name assigned at module level or as a class attribute, with its literal default."""
    out: dict = {}
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Name) and NAME_RE.match(target.id):
                try:
                    out[target.id] = ast.literal_eval(value) if value is not None else None
                except Exception:
                    out.setdefault(target.id, "<expr>")
    return out


def _class_fields(path: Path, class_name: str) -> dict:
    out: dict = {}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                target, value = None, None
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    target, value = stmt.target.id, stmt.value
                elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    target, value = stmt.targets[0].id, stmt.value
                if target and NAME_RE.match(target):
                    try:
                        out[target] = ast.literal_eval(value) if value is not None else None
                    except Exception:
                        out[target] = "<expr>"
    return out


def _live_reads(path: Path) -> set:
    """Names a live manager reads. Deliberately over-inclusive: attribute access, getattr with a literal, AND every
    bare uppercase string literal — that last clause is what catches the variable-held `getattr(cfg, flag)` reads the
    sanctioned adder is blind to."""
    found = set()
    source = path.read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and NAME_RE.match(node.attr or ""):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME_RE.match(node.value):
            found.add(node.value)
    return found


def value_kind(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        if value == "<expr>":
            return "expr"
        return "path_or_secret" if re.search(r"/|\.json|\.npz|\.log", value) else "enum_or_str"
    if isinstance(value, (list, tuple, set, dict)):
        return "container"
    return "other"


def template_switches(path: Path) -> tuple[set, int]:
    from openpyxl import load_workbook

    switches, rows = set(), 0
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            if "BASELINE" in sheet.title.upper():
                continue
            for row in sheet.iter_rows(min_row=3, max_col=1, values_only=True):
                name = (row[0] or "") if isinstance(row[0], str) else ""
                if NAME_RE.match(name.strip()):
                    switches.add(name.strip())
                    rows += 1
    finally:
        workbook.close()
    return switches, rows


def binding_switches(cat_side: str, learn_dir: Path) -> set:
    path = learn_dir / f"binding_map_{cat_side}.json"
    if not path.exists():
        return set()
    bindings = json.loads(path.read_text())
    meta = bindings.get("meta") or {}
    if str(meta.get("cat_side") or cat_side) != cat_side:
        raise ValueError(f"CAT_SIDE_MISMATCH: {path} is {meta.get('cat_side')}, asked for {cat_side}")
    return set(bindings.get("switches") or {})


def losing_switches(cat_side: str, learn_dir: Path) -> dict:
    """{switch: worst (most negative) interval lower bound} from losing_live_switches_<CS>.json — measured live loss."""
    path = learn_dir / f"losing_live_switches_{cat_side}.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else data
    worst: dict = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("switch") or "")
        confidence = item.get("ci") or []
        try:
            bound = float(confidence[0])
        except Exception:
            bound = float(item.get("mean") or 0.0)
        if name and (name not in worst or bound < worst[name]):
            worst[name] = bound
    return worst


def census(learn_dir: Path, spreadsheets: Path) -> dict:
    crypto_config = _upper_assigns(ROOT / "config.py")
    stock_config = _upper_assigns(ROOT / "config_tradier.py")
    quick = _class_fields(ROOT / "v12_quick_engine.py", "QuickConfig")
    crypto_reads = _live_reads(ROOT / "ez_manage.py")
    stock_reads = _live_reads(ROOT / "tradier_manage.py")

    templates, template_rows = {}, {}
    for cat_side in CAT_SIDES:
        venue = "CRYPTO" if cat_side.startswith("CRYPTO") else "STOCKS"
        side = "LONG" if cat_side.endswith("LONG") else "SHORT"
        path = spreadsheets / f"TEMPLATE_{venue}_{side}.xlsx"
        templates[cat_side], template_rows[cat_side] = template_switches(path) if path.exists() else (set(), 0)

    union = set().union(*templates.values()) if templates else set()
    testable = {name for name, value in quick.items() if value_kind(value) in TESTABLE}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "surfaces": {
            "quickconfig_fields": len(quick),
            "quickconfig_testable": len(testable),
            "config_py_declared": len(crypto_config),
            "config_tradier_declared": len(stock_config),
            "ez_manage_reads": len(crypto_reads),
            "tradier_manage_reads": len(stock_reads),
            "template_union": len(union),
            "template_per_cat_side": {cat: len(sw) for cat, sw in templates.items()},
            "template_rows_per_cat_side": template_rows,
        },
        "per_cat_side": {},
    }

    for cat_side in CAT_SIDES:
        is_crypto = cat_side.startswith("CRYPTO")
        declared = crypto_config if is_crypto else stock_config
        reads = crypto_reads if is_crypto else stock_reads
        mapped = binding_switches(cat_side, learn_dir)
        losing = losing_switches(cat_side, learn_dir)
        gap = []
        for name in sorted(testable - templates[cat_side]):
            if INFRA_RE.search(name) or ABLATION_RE.match(name):
                continue
            gap.append({
                "switch": name,
                "family": name.split("_")[0],
                "kind": value_kind(quick.get(name)),
                "quickconfig_default": quick.get(name) if not isinstance(quick.get(name), (list, dict, set, tuple)) else "<container>",
                "declared_in_venue_config": name in declared,
                "read_by_live_manager": name in reads,
                "in_binding_map": name in mapped,
                "measured_live_loss_lb": losing.get(name),
            })
        dead_rows = sorted(templates[cat_side] - set(quick))
        report["per_cat_side"][cat_side] = {
            "cat_side": cat_side,
            "template_switches": len(templates[cat_side]),
            "binding_map_switches": len(mapped),
            "gap_behaviour_switches": len(gap),
            "gap_live_read": sum(1 for g in gap if g["read_by_live_manager"]),
            "gap_with_measured_live_loss": sum(1 for g in gap if g["measured_live_loss_lb"] is not None),
            "dead_rows_no_vector_field": dead_rows,
            "top_families": Counter(g["family"] for g in gap).most_common(15),
            "gap": gap,
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="V20 training-domain census")
    parser.add_argument("--learn-dir", default=str(ROOT / "data/learn"))
    parser.add_argument("--spreadsheets", default=str(ROOT / "SPREADSHEETS"))
    parser.add_argument("--write", action="store_true", help="write data/learn/v20_domain_gap_<CS>.json")
    parser.add_argument("--rank-by-evidence", action="store_true", help="order each gap by measured live loss first")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    learn_dir = Path(args.learn_dir)
    report = census(learn_dir, Path(args.spreadsheets))
    surfaces = report["surfaces"]
    print(f"QuickConfig fields {surfaces['quickconfig_fields']} ({surfaces['quickconfig_testable']} testable) | "
          f"config.py {surfaces['config_py_declared']} | config_tradier {surfaces['config_tradier_declared']} | "
          f"template union {surfaces['template_union']}")
    for cat_side, block in report["per_cat_side"].items():
        gap = block["gap"]
        if args.rank_by_evidence:
            gap = sorted(gap, key=lambda g: (g["measured_live_loss_lb"] is None, g["measured_live_loss_lb"] or 0.0, g["switch"]))
            block["gap"] = gap
        print(f"\n{cat_side}: template {block['template_switches']} | map {block['binding_map_switches']} | "
              f"OUTSIDE THE DOMAIN {block['gap_behaviour_switches']} "
              f"({block['gap_live_read']} live-read, {block['gap_with_measured_live_loss']} with measured live loss) | "
              f"dead rows {len(block['dead_rows_no_vector_field'])}")
        print("  families: " + ", ".join(f"{name} {count}" for name, count in block["top_families"][:10]))
        for item in gap[: args.limit]:
            loss = f" live_loss_lb={item['measured_live_loss_lb']:.4f}" if item["measured_live_loss_lb"] is not None else ""
            print(f"    {item['switch']:52s} {item['kind']:12s} default={str(item['quickconfig_default'])[:18]:18s}{loss}")
        if args.write:
            out = learn_dir / f"v20_domain_gap_{cat_side}.json"
            payload = {"meta": {"cat_side": cat_side, "generated_at": report["generated_at"], "contract": "v20_domain_gap_v1", "surfaces": surfaces}, **block}
            out.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
            print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
