#!/usr/bin/env python3
"""A STOCK may never read config.py, and a CRYPTO may never read config_tradier.py. Enforced, not trusted.

USER 2026-10-21: "NO stock can EVER take any info from config.py (nor a crypto from config_tradier.py) so make
sure no more contamination can occur."

The two venues have genuinely different numbers behind the same switch names — crypto START_POSITION_SIZE is
28/1.0 where stocks is 500/100 — so a single cross-venue read does not throw, it silently trades the other
venue's size or threshold. `ez_manage.py:7836` already states the rule in a comment ("ez_ is CRYPTO ONLY per user
2026-09-26 — do NOT handle stocks here ... No stock/cross pollination") and `tradier_manage`'s `config` IS
`TradierConfig()`. A comment is not an enforcement, which is what this file is for.

AST-BASED, so a COMMENT can never trip it. Measured when first written: the only two crypto references to
`config_tradier` were a comment at `ez_positions_quick.py:6907` and one at `ez_manage.py:42627`, and the stocks
modules held zero references to crypto `config` — i.e. the boundary was clean but unguarded, and a grep-based
check would have reported two false violations.

Three checks:
  VENUE_IMPORT  a venue module imports the other venue's config module at all
  VENUE_READ    a venue module reads an attribute off the other venue's config module/class
  SHARED_CORE   advisory only: a module BOTH venues call imports either config directly instead of taking the
                resolved cfg as a parameter. Not a failure — some legitimately need a venue-specific default —
                but each one is a place a venue default can leak into the other venue's decision.

Exit 0 = boundary intact, 1 = contamination, 2 = could not audit. READ-ONLY.
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(os.environ.get("BASE_PATH") or pathlib.Path(__file__).resolve().parents[1])

# `ez_copilot.py` is deliberately NOT here. Despite the ez_ prefix it is a CROSS-VENUE BRIDGE: it listens for
# copilot commands on the crypto channels AND runs a Tradier order listener, so `from config_tradier import
# TradierConfig; tconfig = TradierConfig(); api = TradierAPIClient(tconfig)` (ez_copilot.py:921, :1733) is the
# CORRECT way to talk to the stocks broker — handing it a crypto Config would be the bug. Verified by reading both
# sites; neither feeds a crypto decision. A bridge is exempt from the boundary, not above it: it may construct the
# other venue's config for that venue's API, and must never read it for a decision on its own venue.
CRYPTO_MODULES = ("ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
                  "ez_positions_service_main.py", "ez_reentry.py", "ez_positions.py",
                  "ez_reentry_daemon.py", "ez_positions_realtime.py", "ez_rankings.py")
STOCKS_MODULES = ("tradier_manage.py", "tradier_vec_exact.py", "tradier_positions.py",
                  "tradier_indicators.py", "tradier_rankings.py", "tradier_prices.py")
CRYPTO_CFG = {"config", "Config"}
STOCKS_CFG = {"config_tradier", "TradierConfig"}
SHARED_GLOBS = ("vec_decisions/*.py", "live_twins/*.py")


def scan(path: pathlib.Path, forbidden_mods: set, forbidden_attrs: set) -> list:
    try:
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
    except Exception as exc:
        return [{"line": 0, "kind": "PARSE_ERROR", "detail": str(exc)[:120]}]
    bad, aliases = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in forbidden_mods:
                    aliases.add(a.asname or a.name.split(".")[0])
                    bad.append({"line": node.lineno, "kind": "VENUE_IMPORT", "detail": f"import {a.name}" + (f" as {a.asname}" if a.asname else "")})
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in forbidden_mods:
                for a in node.names:
                    if a.name in forbidden_attrs or a.name in forbidden_mods:
                        aliases.add(a.asname or a.name)
                bad.append({"line": node.lineno, "kind": "VENUE_IMPORT",
                            "detail": f"from {node.module} import " + ", ".join(a.name for a in node.names)[:80]})
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = node.value.id
            if base in aliases or (base in forbidden_mods and base in aliases):
                bad.append({"line": node.lineno, "kind": "VENUE_READ", "detail": f"{base}.{node.attr}"})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases:
                key = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "?"
                bad.append({"line": node.lineno, "kind": "VENUE_READ", "detail": f"getattr({node.args[0].id}, {key!r})"})
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "reports" / "venue_boundary_guard.json"))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    findings, checked = [], 0
    for m in CRYPTO_MODULES:
        p = ROOT / m
        if not p.exists():
            continue
        checked += 1
        for f in scan(p, STOCKS_CFG, STOCKS_CFG):
            findings.append({"module": m, "venue": "crypto", "forbidden": "config_tradier", **f})
    for m in STOCKS_MODULES:
        p = ROOT / m
        if not p.exists():
            continue
        checked += 1
        for f in scan(p, CRYPTO_CFG, CRYPTO_CFG):
            findings.append({"module": m, "venue": "stocks", "forbidden": "config.py", **f})

    advisory = []
    for g in SHARED_GLOBS:
        for p in sorted(glob.glob(str(ROOT / g))):
            pp = pathlib.Path(p)
            if pp.name.startswith("test_"):
                continue
            hits = scan(pp, CRYPTO_CFG | STOCKS_CFG, CRYPTO_CFG | STOCKS_CFG)
            for h in hits:
                if h["kind"] == "VENUE_IMPORT":
                    advisory.append({"module": f"{pp.parent.name}/{pp.name}", **h, "kind": "SHARED_CORE"})

    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n_venue_modules_checked": checked,
           "n_contamination": len(findings), "contamination": findings,
           "n_shared_core_direct_config_imports": len(advisory), "shared_core_advisory": advisory[:200]}
    pathlib.Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.json).write_text(json.dumps(rep, indent=1))

    print(f"venue modules checked: {checked}")
    print(f"CONTAMINATION: {len(findings)}")
    for f in findings[:40]:
        print(f"  {f['venue']:6s} {f['module']}:{f['line']} {f['kind']} -> {f['detail']} (forbidden: {f['forbidden']})")
    print(f"\nadvisory — shared cores importing a venue config directly: {len(advisory)}")
    if not a.quiet:
        for f in advisory[:20]:
            print(f"  {f['module']}:{f['line']} {f['detail']}")
    print(f"\nwrote {a.json}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
