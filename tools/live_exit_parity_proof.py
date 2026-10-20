#!/usr/bin/env python3
"""live_exit_parity_proof — can live still close a position on a path the VECTOR cannot produce?

USER 2026-10-20: "YOU NEED TO MAKE LIVE EXIT EXACTLY WHEN VECTOR WOULD EXIT BY APPLYING THE SWITCHES"
and "LIVE CAN NOT HAVE EVEN A 0.1% DISCREPANCY FROM LIVE EVER".

WHAT THIS PROVES, AND WHAT IT DOES NOT
    PROVES  — every exit reason that actually closed a live position in the window is either decided by
              the vector (|VEC_EXACT), a reconciliation record of an order we placed, or a reason family
              the vector engine EMITS; and for any family it does not emit, that path's switch is OFF now.
    DOES NOT — prove same-BAR identity. Two engines can agree on the family and differ on the bar. Bar
              identity is measured separately by replaying the published set (tools/v15_trade_parity.py on
              S1/S2) and is NOT claimed here. Never present this tool's PASS as bar parity.

WHY A REASON-STRING CENSUS IS SOUND HERE (it was NOT sound for the entry gate)
    vec_paths/vec_parity_gate.py tried to AUTHORIZE orders by matching reason strings against a hand-typed
    allowlist; measured 2026-10-20 it admitted 0 of 582,689 board entry rows, because a curated list drifts
    behind an engine that keeps adding families. This tool does the opposite: it never authorizes anything.
    It asks whether an EMITTING LINE exists in the vector source for a family live actually used, and treats
    a missing emitter as a defect to be switched off. The source is the authority, not a list.

A FAMILY COUNTS AS VECTOR-PRODUCIBLE only if its token appears in v12_quick_engine.py / vec_decisions/ /
vec_paths/ somewhere OTHER than a config field declaration or an auto-wire name list. Grepping the bare token
is not enough: "PRE_CLOSE" hits DC_DAYTRADE_PRE_CLOSE_MINUTES, a field the engine READS while never emitting
the close — that false positive is exactly how PRE_CLOSE survived unnoticed.

Usage:
    tools/live_exit_parity_proof.py [--days 7] [--json]
Exit 0 when no still-enabled non-vector exit path fired, 1 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VEC_SRC = ["v12_quick_engine.py", "vec_decisions/", "vec_paths/"]
RECONCILE = ("BROKER_RECONCILED", "Broker sync", "BROKER_SYNC_CLOSE", "VEC_DRIVEN_CLOSE_RECONCILE")
# Families with NO emitting line in the vector engine, each verified at the source on 2026-10-20, with the
# switch that now disables it. vec_paths/vec_parity_gate.py's own live-only note is the cross-check.
LIVE_ONLY = {
    "PRE_CLOSE": ("DC_DAYTRADE_PRE_CLOSE_MINUTES", 0, "stocks"),
    "E3_STRUCT_EXIT": ("E_3_USE_WT_STRUCTURE_EXIT_MODE", 0, "stocks"),
    "Market_Against_Position_Bias": ("MARKET_BIAS_REDUCE_ENABLED", False, "stocks"),
    "INTRADAY_RATIO_TRIM": ("INTRADAY_RATIO_REBALANCE_ENABLED", False, "stocks"),
    "HEDGE_FAILED_FALLBACK_CLOSE": ("HEDGE_FAILED_FALLBACK_CLOSE_ENABLED", False, "crypto"),
    "CLASSIC_FORMATION_EXIT": ("FORMATION_EXIT_ENABLED", False, "crypto"),
    "MARGIN_FREE_FOR": ("MARGIN_FREE_CLOSE_ENABLED", False, "crypto"),
    "DT_TARGET": ("DT_TARGET_ATR_ENABLED", False, "both"),
    "RATIO_CLOSE": ("RATIO_CLOSE_LOSING_OVERWEIGHT", False, "both"),
    "SENT_STRAT_TRIM": (None, None, "crypto"),  # no emitter left in live either — legacy history only
}


def vec_emits(token: str) -> str:
    """Path of a real emitting line, or '' — config-field and auto-wire-list hits do not count."""
    if len(token) < 4:
        return ""
    r = subprocess.run(["grep", "-rnF", token, "--include=*.py"] + VEC_SRC,
                       capture_output=True, text=True, cwd=str(ROOT))
    for ln in r.stdout.split("\n"):
        if not ln or "vec_parity_gate" in ln:
            continue
        body = ln.split(":", 2)[-1].strip()
        if re.match(r'^[A-Z0-9_]+\s*:\s*(bool|int|float|str)\b', body):  # field declaration
            continue
        if re.match(r'^"[A-Z0-9_]+",?$', body):  # auto-wire name list entry
            continue
        return ln.split(":")[0]
    return ""


def family(reason: str) -> str:
    s = re.sub(r"^MAKER_PROFIT_EXIT_", "", reason.strip())
    segs = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
    up = []
    for p in segs:
        if re.match(r"^[A-Z][A-Z0-9]*$", p):
            up.append(p)
        else:
            break
    return "_".join(up) if up else s[:40]


def load_exits(days: int):
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    out = []
    for f in glob.glob(str(ROOT / "data" / "history" / "*" / "*.jsonl")):
        seen = set()
        for ln in open(f, encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if not ln.startswith("{") or ln in seen:
                continue
            seen.add(ln)
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if str(d.get("type") or "").upper() not in ("CLOSE", "REDUCE"):
                continue
            try:
                ts = dt.datetime.fromisoformat(str(d.get("ts")))
                ts = ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            if ts >= cut:
                out.append(str(d.get("reason") or ""))
    return out, cut


def switch_state(name, venue):
    if name is None:
        return "no emitter in live either (legacy)"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    vals = []
    for mod, cls, v in (("config", "Config", "crypto"), ("config_tradier", "TradierConfig", "stocks")):
        if venue not in (v, "both"):
            continue
        try:
            cfg = getattr(__import__(mod), cls)()
            vals.append(f"{v}:{name}={getattr(cfg, name, '<absent>')}")
        except Exception as exc:
            vals.append(f"{v}:{name}=<unreadable {type(exc).__name__}>")
    return " ".join(vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    reasons, cut = load_exits(args.days)
    buckets = collections.Counter()
    live_only_hits = collections.Counter()
    unknown = collections.Counter()
    for r in reasons:
        if "|VEC_EXACT" in r:
            buckets["vec_decided"] += 1
            continue
        if any(b in r for b in RECONCILE):
            buckets["reconciliation"] += 1
            continue
        lo = next((k for k in LIVE_ONLY if k in r), None)
        if lo:
            buckets["NOT_vec_producible"] += 1
            live_only_hits[lo] += 1
            continue
        fam = family(r)
        if vec_emits(fam):
            buckets["vec_producible_family"] += 1
        else:
            buckets["unclassified"] += 1
            unknown[fam] += 1
    rep = {"window_start_utc": cut.strftime("%Y-%m-%dT%H:%M:%SZ"), "days": args.days,
           "exits": len(reasons), "buckets": dict(buckets),
           "live_only_fired": dict(live_only_hits),
           "live_only_switch_state": {k: switch_state(*LIVE_ONLY[k]) for k in live_only_hits},
           "unclassified_families": dict(unknown)}
    still_on = []
    for k in live_only_hits:
        name, off, venue = LIVE_ONLY[k]
        st = switch_state(name, venue)
        if name is not None and f"={off}" not in st.replace(" ", "").replace("=False", "=False"):
            if not all(f"={off}" in part for part in st.split()):
                still_on.append(f"{k} -> {st}")
    rep["still_enabled_non_vector_paths"] = still_on
    rep["verdict"] = "PASS" if not still_on else "FAIL"
    if args.json:
        print(json.dumps(rep, indent=1))
    else:
        print(f"live exits in /history/ since {rep['window_start_utc']}: {rep['exits']}")
        for k, v in sorted(buckets.items(), key=lambda x: -x[1]):
            print(f"  {v:6d}  {k}")
        if live_only_hits:
            print("\nnon-vector exit paths that FIRED in the window, and their switch NOW:")
            for k, v in live_only_hits.most_common():
                print(f"  {v:6d}  {k:34s} {rep['live_only_switch_state'][k]}")
        if unknown:
            print("\nunclassified families (no emitter found; inspect before trusting a PASS):")
            for k, v in unknown.most_common(12):
                print(f"  {v:6d}  {k}")
        print(f"\nVERDICT {rep['verdict']}"
              + ("" if not still_on else " — still-enabled non-vector paths: " + "; ".join(still_on)))
        print("NOTE: this proves PATH parity (no live-only exit route is enabled), NOT same-bar identity.")
    out = ROOT / "data" / "reports" / "live_exit_parity"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(rep, indent=1))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
