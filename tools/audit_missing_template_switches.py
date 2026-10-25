#!/usr/bin/env python3
"""Read-only inventory of registry switches against the four active templates."""
import argparse
import ast
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
VENUES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
TABS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL",
        "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY",
        "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
        "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
        "GLOBAL_RISK_GATES")


def source_assignments(path, cls):
    tree = ast.parse(path.read_text())
    nodes = next((node.body for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == cls), [])
    result = {}
    for node in nodes:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        try:
            result[name] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError):
            pass
    return result


def scan_templates():
    found = {}
    for venue in VENUES:
        path = TEMPLATE_DIR / f"TEMPLATE_{venue}.xlsx"
        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
        found[venue] = defaultdict(lambda: defaultdict(list))
        for tab in TABS:
            if tab not in book.sheetnames:
                continue
            for row in book[tab].iter_rows(min_row=3, max_col=12):
                name = row[0].value
                if isinstance(name, str) and name.strip() and not name.startswith(" "):
                    found[venue][tab][name.strip()].append({"row": row[0].row,
                        "option": row[1].value, "is_default": row[11].value,
                        "bold": bool(row[1].font.bold)})
        book.close()
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data/reports/template_missing_inventory")
    args = parser.parse_args()
    registry_file = ROOT / "data/wiring/vec_function_registry.json.gz"
    registry_data = json.load(gzip.open(registry_file))
    registry = registry_data["registry"]
    bible_file = ROOT / "data/SWITCH_BIBLE.json"
    bible_data = json.loads(bible_file.read_text())
    bible = bible_data["switches"]
    templates = scan_templates()
    crypto = source_assignments(ROOT / "config.py", "Config")
    stocks = source_assignments(ROOT / "config_tradier.py", "TradierConfig")
    quick = source_assignments(ROOT / "v12_quick_engine.py", "QuickConfig")
    rows = []
    for name, entry in sorted(registry.items()):
        if not isinstance(entry, dict):
            continue
        home = entry.get("suggested_home_tab") or ""
        options = entry.get("valid_options") or []
        live = entry.get("live_counterpart") or []
        vec = entry.get("read_at") or []
        if isinstance(live, str):
            live = [live]
        if isinstance(vec, str):
            vec = [vec]
        live_real = any(item and item != "NONE" and "(<module>)" not in item for item in live)
        vec_real = any(item and item != "NONE" and "data/wiring/staged/" not in item for item in vec)
        for venue in VENUES:
            side = venue.rsplit("_", 1)[1]
            current = bible.get(name, {})
            applicable_sides = current.get("sides") or entry.get("sides") or "both"
            if applicable_sides != "both" and side not in applicable_sides:
                continue
            venue_config = crypto if venue.startswith("CRYPTO") else stocks
            cfg_present = name in venue_config
            if not cfg_present:
                continue
            tabs_present = [tab for tab in TABS if name in templates[venue][tab]]
            expected_rows = templates[venue][home][name] if home in TABS else []
            all_rows = [item for tab in tabs_present for item in templates[venue][tab][name]]
            default_value = venue_config[name]
            row = {"switch": name, "venue_side": venue, "home_tab": home,
                   "present_tabs": ";".join(tabs_present), "row_count": len(all_rows),
                   "options_existing": json.dumps([r["option"] for r in all_rows], default=str),
                   "options_registry": json.dumps(options, default=str),
                   "config_default": repr(default_value), "quick_default": repr(quick.get(name, "<missing>")),
                   "bold_values": json.dumps([r["option"] for r in all_rows if r["bold"]], default=str),
                   "written_default_values": json.dumps([r["option"] for r in all_rows if r["is_default"] == "YES"], default=str),
                   "live_counterpart": ";".join(live), "vec_reads": ";".join(vec),
                   "registry_status": entry.get("status", ""),
                   "current_bible_status": (current.get("status") or {}).get("crypto" if venue.startswith("CRYPTO") else "stocks", ""),
                   "current_bible_live_reads": (current.get("live_read_count") or {}).get("crypto" if venue.startswith("CRYPTO") else "stocks", 0),
                   "current_bible_vec_reads": (current.get("vec_read_count") or {}).get("reachable", 0),
                   "unwired_twin": name in registry_data.get("twin_modules_not_called", {}),
                   "options_need_review": bool(entry.get("options_need_review")),
                   "candidate_ready": bool(home in TABS and len(options) >= 2 and live_real and vec_real and name in quick),
                   "source": str(registry_file.relative_to(ROOT))}
            if not all_rows:
                row["gap"] = "MISSING_GROUP"
            elif len(all_rows) < 2:
                row["gap"] = "LESS_THAN_TWO_ROWS"
            elif home in TABS and not expected_rows:
                row["gap"] = "WRONG_TAB"
            elif len([r for r in all_rows if r["is_default"] == "YES"]) != 1 or len([r for r in all_rows if r["bold"]]) != 1:
                row["gap"] = "DEFAULT_MARKER_MISMATCH"
            elif next(r["option"] for r in all_rows if r["is_default"] == "YES") != default_value:
                row["gap"] = "CONFIG_DEFAULT_MISMATCH"
            else:
                row["gap"] = "OK"
            rows.append(row)
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (output / "inventory.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows(rows)
    summary = {"generated_utc": datetime.now(timezone.utc).strftime("%FT%TZ"),
               "registry_generated": registry_data.get("generated"),
               "bible_generated": bible_data.get("meta", {}).get("generated"),
               "template_dir": str(TEMPLATE_DIR), "counts_by_gap": dict(Counter(r["gap"] for r in rows)),
               "missing_unique": len({r["switch"] for r in rows if r["gap"] == "MISSING_GROUP"}),
               "missing_per_venue": {venue: sum(r["gap"] == "MISSING_GROUP" and r["venue_side"] == venue for r in rows) for venue in VENUES},
               "missing_candidate_ready": sum(r["gap"] == "MISSING_GROUP" and r["candidate_ready"] for r in rows),
               "registry_uncalled_twins": len(registry_data.get("twin_modules_not_called", {})),
               "unwired_twin_unique": len({r["switch"] for r in rows if r["unwired_twin"]}),
               "note": "Registry reads and counterpart labels are only leads; candidate_ready is not behavioral parity proof."}
    disparity_file = ROOT / "data/vector_disparity_queue.jsonl"
    if disparity_file.exists():
        disparity = []
        for line in disparity_file.open():
            try:
                disparity.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        summary["vec_last_override_events"] = sum(r.get("kind") == "VEC_LAST_OVERRIDE" for r in disparity)
        summary["vec_last_refuse_events"] = sum(r.get("kind") == "VEC_LAST_REFUSE" for r in disparity)
        summary["vec_last_override_gates"] = dict(Counter(g for r in disparity if r.get("kind") == "VEC_LAST_OVERRIDE" for g in r.get("gates", [])).most_common())
        summary["dictate_only_block_events"] = sum(r.get("kind") == "VEC_DICTATE_ONLY_BLOCK" for r in disparity)
        summary["disparity_source"] = str(disparity_file.relative_to(ROOT))
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    missing = sorted({r["switch"] for r in rows if r["gap"] == "MISSING_GROUP"})
    not_ready = sorted({r["switch"] for r in rows if r["gap"] == "MISSING_GROUP" and not r["candidate_ready"]})
    report = ["# Missing switch inventory", "",
              f"Generated {summary['generated_utc']} from the four active `TEMPLATE_FINAL_NORM` files, the current config source, switch bible generated {summary['bible_generated']}, and registry generated {summary['registry_generated']}.", "",
              "## Template gap counts", "",
              f"- {summary['missing_unique']} unique registry fields have no group in at least one applicable venue/side ({summary['counts_by_gap']['MISSING_GROUP']} venue/side gaps).",
              f"- {summary['counts_by_gap']['LESS_THAN_TWO_ROWS']} existing venue/side groups contain fewer than two rows.",
              f"- {summary['counts_by_gap']['WRONG_TAB']} groups are in a different tab from the registry suggestion; this can be intentional and is not an automatic add.",
              f"- {summary['counts_by_gap']['CONFIG_DEFAULT_MISMATCH']} groups disagree with their venue config default; the template may be the current authority, so these require source reconciliation.",
              f"- {summary['missing_candidate_ready']} missing venue/side groups pass only a static field/options/reference precheck. None has been counted as parity proven.", "",
              f"- The older registry separately labels {summary['registry_uncalled_twins']} vector twin modules as uncalled; {summary['unwired_twin_unique']} of those overlap missing template groups. This is a code wiring list, not a row-add count.", "",
              "Venue/side gaps: " + ", ".join(f"{k} {v}" for k, v in summary["missing_per_venue"].items()) + ".", "",
              "## Backstop evidence", "",
              f"`{summary.get('disparity_source', '')}` records {summary.get('vec_last_override_events', 0)} VEC_LAST_OVERRIDE, {summary.get('vec_last_refuse_events', 0)} VEC_LAST_REFUSE, and {summary.get('dictate_only_block_events', 0)} VEC_DICTATE_ONLY_BLOCK events. These are event counts, not switch counts.", "",
              "VEC_LAST_OVERRIDE gate labels: " + ", ".join(f"{k} {v}" for k, v in summary.get("vec_last_override_gates", {}).items()) + ".", "",
              "## Fields requiring manual wiring or option review", "",
              ", ".join(f"`{name}`" for name in not_ready) + ".", "",
              "## All absent field names", "",
              ", ".join(f"`{name}`" for name in missing) + ".", "",
              "The companion `inventory.csv` contains every venue/side field, existing options, config and QuickConfig values, bold/written defaults, registry sources, and a static readiness flag. Registry references may be stale or unreachable; each addition still requires a live-path trace, a vector predicate, a ledger flip, scalar parity, and the prescribed S1 switch-adder.", ""]
    (output / "inventory.md").write_text("\n".join(report))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
