#!/usr/bin/env python3
"""Produce the fail-closed per_sym → Quick → V12 wiring contract.

This is intentionally stricter than an inventory: every non-orchestration
override currently live must be a causal Quick read *and* a V12/live decision
read.  A quick-only setting is not eligible for a sweep; it cannot be allowed
to create a result which V12 cannot reproduce.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_v12_live_vector_parity import build
from tools.opt.lifecycle_pilot import load_live_recipes


OUT = ROOT / "data/reports/lifecycle_pilot/per_sym_parity_contract.json"
CONTROL_ONLY = {
    "LONG_ENABLED", "SHORT_ENABLED", "MODE", "BASE_TF", "SYMBOL", "ACCOUNT",
    "DRY_RUN", "NOTES", "VERSION", "UPDATED_AT",
}


def is_low_tf(name: str, value: Any) -> bool:
    text = f"{name}={value}".upper()
    return "_3M" in text or "_5M" in text or str(value).lower() in {"3m", "5m"}


def build_contract() -> dict[str, Any]:
    inventory = build()
    by_switch = {row["switch"]: row for row in inventory["rows"]}
    rows: list[dict[str, Any]] = []
    for symside, recipe in sorted(load_live_recipes().items()):
        for switch, value in sorted(recipe["overrides"].items()):
            inv = by_switch.get(switch)
            control = switch in CONTROL_ONLY
            quick = bool(inv and inv["vector_causal_read"])
            v12 = bool(inv and inv["live_read"])
            if control:
                status = "CONTROL_ONLY"
            elif is_low_tf(switch, value):
                status = "LOW_TF_FORBIDDEN"
            elif not inv:
                status = "NOT_DECLARED_IN_QUICK"
            elif not quick:
                status = "QUICK_NOT_CAUSAL"
            elif not v12:
                status = "V12_NOT_HOOKED"
            else:
                status = "HOOKED_BOTH"
            rows.append({
                "symside": symside, "switch": switch, "value": value,
                "source": recipe["source"], "status": status,
                "quick_causal": quick, "v12_live_read": v12,
                "inventory_status": inv["status"] if inv else "NOT_IN_QUICKCONFIG",
            })
    blockers = [row for row in rows if row["status"] not in {"HOOKED_BOTH", "CONTROL_ONLY"}]
    return {
        "schema": "per-sym-quick-v12-contract-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pass": not blockers,
        "rule": "Every live non-control per_sym override must be causal in v12_quick_engine and read by backtest_v12/live decision code; 3m/5m decisions are forbidden for 15m research.",
        "summary": {"overrides": len(rows), "blockers": len(blockers), "symsides": len(load_live_recipes())},
        "blockers": blockers,
        "rows": rows,
        "inventory_source_sha256": inventory["source_sha256"],
    }


def write(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temp.replace(path)
    csv_path = path.with_suffix(".csv")
    with csv_path.with_suffix(".tmp").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symside", "switch", "value", "source", "status", "quick_causal", "v12_live_read", "inventory_status"])
        writer.writeheader(); writer.writerows(payload["rows"])
    csv_path.with_suffix(".tmp").replace(csv_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    payload = build_contract(); write(payload, args.out)
    print(json.dumps({"pass": payload["pass"], "summary": payload["summary"]}, indent=2))
    return 0 if payload["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
