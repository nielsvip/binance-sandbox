#!/usr/bin/env python3
"""V15 LIVE-FOLLOWS-BOARDS GUARD (USER 2026-10-15): "make sure live trading never diverts from the best per_sym
backtest's settings but blindly follows it for now (as we keep fixing the live trading scripts)".

It does NOT recompute anything: re-deriving "what live should trade" in a second place is exactly how two answers get
born. The single source of truth is `tools/v15_persym_golive.py --published --pin-all`, whose report already records,
per sym_side, the published board's set, what it would register, and whether live already holds it byte-identically.
This guard reads the newest such report and fails when live could be following something other than the latest board:

  STALE      the newest go-live report is older than --max-report-age-min (the hourly V15_GOLIVE_REFRESH died or the
             published_selection build on S1 failed — live then keeps whatever it had, silently)
  PENDING    sets that still need writing (registered > 0 in the newest report means live was NOT converged when it
             ran; two consecutive reports with pending sets means it is not converging at all)
  REFUSED    sym_sides where live KEEPS AN OLDER SET because go-live refused the latest board — listed by class, since
             each class is a live-money decision, not a bug to hide:
               * "zero trades on the fresh 30D eval"  registering would SILENCE that sym_side
               * "type-gate refused ALL"              a value in the published set fails its type gate
               * "golive-blocklisted"                 deliberately excluded from go-live
               * "published"                          no published board for that sym_side yet

Exit 1 + data/reports/ALERT_V15_LIVE_DIVERGENCE.txt on STALE or PENDING, and on REFUSED above --max-refused.
Read-only: reads data/daily_chain/persym_golive_*.json. Never writes live settings.
"""
import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "data" / "daily_chain"
OUT = ROOT / "data" / "reports" / "v15_live_follows_boards.json"
ALERT = ROOT / "data" / "reports" / "ALERT_V15_LIVE_DIVERGENCE.txt"


def newest_reports(n=2):
    fs = sorted(REPORTS.glob("persym_golive_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return fs[:n]


def read(p):
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        return {"path": str(p), "error": f"{type(e).__name__}: {e}"[:200]}
    s = d.get("summary") if isinstance(d.get("summary"), dict) else {}
    rt = s.get("refuse_reasons_top") or []
    refused = {}
    if isinstance(rt, list):
        for item in rt:
            try:
                refused[str(item[0])[:60]] = int(item[1])
            except Exception:
                continue
    elif isinstance(rt, dict):
        refused = {str(k)[:60]: int(v) for k, v in rt.items()}
    rows = d.get("rows") if isinstance(d.get("rows"), dict) else {}
    return {
        "path": str(p),
        "age_min": round((time.time() - p.stat().st_mtime) / 60.0, 1),
        "applied": bool(s.get("registered") is not None),
        "n": s.get("n"),
        "results": s.get("results") or {},
        "registered": s.get("registered"),
        "already_live_skipped": s.get("already_live_skipped"),
        "refused_by_class": refused,
        "changed_symsides": sorted(k for k, v in rows.items() if isinstance(v, dict) and v.get("registered") and not v.get("already_live")),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-report-age-min", type=float, default=135.0, help="hourly refresh + margin")
    ap.add_argument("--max-refused", type=int, default=80, help="sym_sides allowed to keep an older set before this alarms")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    ps = newest_reports(2)
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reports": [read(p) for p in ps], "fails": []}
    if not ps:
        rep["fails"].append("no go-live report at all — live is not being pointed at any published board")
    else:
        cur = rep["reports"][0]
        if cur.get("error"):
            rep["fails"].append(f"newest go-live report unreadable: {cur['error']}")
        else:
            if cur["age_min"] > a.max_report_age_min:
                rep["fails"].append(f"newest go-live report is {cur['age_min']:.0f} min old (> {a.max_report_age_min:.0f}) — V15_GOLIVE_REFRESH is not landing, live keeps its old sets")
            reg = cur.get("registered")
            if isinstance(reg, int) and reg > 0:
                prev = rep["reports"][1] if len(rep["reports"]) > 1 else {}
                prev_reg = prev.get("registered") if isinstance(prev.get("registered"), int) else 0
                if prev_reg and prev_reg > 0:
                    rep["fails"].append(f"two consecutive runs still had pending sets ({prev_reg} then {reg}) — live is not converging on the boards")
                else:
                    rep["note"] = f"{reg} set(s) written on the last run (normal right after a new board publishes)"
            n_ref = sum((cur.get("refused_by_class") or {}).values())
            rep["refused_total"] = n_ref
            if n_ref > a.max_refused:
                rep["fails"].append(f"{n_ref} sym_sides keep an older set because go-live refused the latest board (> {a.max_refused})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1))
    tmp.replace(OUT)
    if not a.quiet:
        cur = (rep["reports"] or [{}])[0]
        print(f"[live-follows] report {cur.get('age_min')} min old | n={cur.get('n')} | written={cur.get('registered')} already_live={cur.get('already_live_skipped')} | refused={rep.get('refused_total')} {dict(collections.Counter(cur.get('refused_by_class') or {}))} | {'OK' if not rep['fails'] else 'FAIL: ' + '; '.join(rep['fails'])}", flush=True)
    if rep["fails"]:
        ALERT.write_text(json.dumps({"at": rep["at"], "fails": rep["fails"], "fix": "run tools/v15_golive_refresh_published.sh (rebuilds data/published_selection.json on S1, then v15_persym_golive.py --published --pin-all --apply); V15_GOLIVE_SKIP_NOOP=0 disables the no-op-write skip"}, indent=1))
        return 1
    if ALERT.exists():
        ALERT.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
