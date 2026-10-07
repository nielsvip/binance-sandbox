#!/usr/bin/env python3
"""Daily 7-day LIVE vs VECTOR parity rollup (launchd com.niels.daily-parity-test, daily).

Recreated 2026-10-06: the launchd job pointed at tools/daily_parity_test.py, which no longer existed anywhere
(Mac, S1, backups/, git) -> python exit 2 every day since 2026-08-30. Same outputs as the last good run
(data/parity/daily_parity_YYYYMMDD.md + .csv) but measured the parity way: tools/forward_parity/live_vs_vec.py over a
--since-days window (live fills vs vector decisions on the same 15m bars, same per-sym set) for crypto and stocks.

Per sym_side: live fills/exits, vector events, matched, vec_only, live_only, PASS/FAIL, live realized avg %/exit vs vector
avg %/exit in the same window (per-exit means only; no Sharpe is emitted here). --alert appends FAIL/sign-flip lines to
logs/daily_parity_alerts.log. Exit 0 when the report was written (parity FAILs are data, not a job failure).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT_DIR = ROOT / "data" / "parity"


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def run_mode(mode: str, hours: float, workers: int) -> dict:
    out = ROOT / "data" / "forward_parity" / f"{mode}_{int(hours / 24)}d.json"
    cmd = [PY, "-m", "tools.forward_parity.live_vs_vec", "--mode", mode, "--window-hours", str(hours), "--workers", str(workers), "--out", str(out)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=3600, env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
    print(r.stdout[-800:])
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        return {}
    return json.loads(out.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=float, default=7.0)
    ap.add_argument("--alert", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--modes", default="crypto,stocks")
    a = ap.parse_args()
    hours = a.since_days * 24.0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ymd = datetime.now(timezone.utc).strftime("%Y%m%d")
    rows, heads, alerts = [], [], []
    for mode in [m.strip() for m in a.modes.split(",") if m.strip()]:
        d = run_mode(mode, hours, a.workers)
        if not d:
            heads.append(f"- {mode}: RUN FAILED (see launchd err log)")
            alerts.append(f"{mode} run failed")
            continue
        st = d.get("status_counts", {})
        rg = d.get("regime") or {}
        heads.append(f"- {mode}: regime {rg.get('regime')} (judged from {rg.get('start_utc', d.get('window_start'))}) · {d['n_keys']} keys (tradeable + open) · status {st} · VEC_ONLY {d.get('vec_only_totals')} · LIVE_ONLY {d.get('live_only_totals')}")
        for day, t in sorted((d.get("sizing_totals_by_day") or {}).items()):
            heads.append(f"  - sizing {day}: exits {t['exits']} (comparable {t['comparable_exits']}) · live PnL ${t['live_pnl_usd']:+.2f} vs vec-size ${t['vecsize_pnl_usd']:+.2f} → live-sizing alpha ${t['sizing_alpha_usd']:+.2f}")
        for r in d.get("rows", []):
            lp, vp = r.get("live_pnl") or [], r.get("vec_pnl") or []
            lm, vm = _mean(lp), _mean(vp)
            flip = lm is not None and vm is not None and len(lp) >= 5 and len(vp) >= 5 and (lm > 0) != (vm > 0)
            rows.append({"mode": mode, "key": r["key"], "status": r["status"], "fail_classes": "|".join(r.get("fail_classes", [])), "live_fills": r.get("live_fills", 0), "vec_events": r.get("vec_events", 0), "matched": r.get("matched", 0), "vec_only": sum((r.get("vec_only") or {}).values()), "live_only": sum((r.get("live_only") or {}).values()), "live_exits": len(lp), "live_avg_exit_pct": round(lm, 4) if lm is not None else "", "vec_exits": len(vp), "vec_avg_exit_pct": round(vm, 4) if vm is not None else "", "sign_flip": flip, "judged_window": r.get("stale_window") or f"last {a.since_days:g}d", "set_source": r.get("set_source", "")})
            if a.alert and (flip or (r["status"] == "FAIL" and (r.get("matched", 0) == 0) and (r.get("live_fills", 0) + r.get("vec_events", 0)) >= 5)):
                alerts.append(f"{mode} {r['key']} {r['status']} {r.get('fail_classes')} live_avg={lm} vec_avg={vm} flip={flip}")
    cols = list(rows[0].keys()) if rows else ["mode", "key", "status"]
    with open(OUT_DIR / f"daily_parity_{ymd}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    L = [f"# Daily parity — LIVE vs VECTOR, trailing {a.since_days:g} days", ""]
    L.append(f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')} | engine: tools/forward_parity/live_vs_vec.py (same bars, same per-sym set)")
    L.extend(heads)
    n_fail = sum(1 for r in rows if r["status"] == "FAIL")
    n_pass = sum(1 for r in rows if "PASS" in r["status"])
    L.append(f"- Totals: PASS {n_pass} · FAIL {n_fail} · sign flips (>=5 exits each side) {sum(1 for r in rows if r['sign_flip'])} · alerts {len(alerts)}")
    L.append("")
    L.append("| mode | key | status | classes | live fills | vec events | matched | vec_only | live_only | live avg %/exit (n) | vec avg %/exit (n) | window |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["status"] != "FAIL", -(r["vec_only"] + r["live_only"]), r["key"])):
        if r["status"] in ("PASS_IDLE", "STALE_PASS_IDLE", "NO_DATA") and not r["live_fills"]:
            continue
        L.append(f"| {r['mode']} | {r['key']} | {r['status']} | {r['fail_classes']} | {r['live_fills']} | {r['vec_events']} | {r['matched']} | {r['vec_only']} | {r['live_only']} | {r['live_avg_exit_pct']} ({r['live_exits']}) | {r['vec_avg_exit_pct']} ({r['vec_exits']}) | {r['judged_window']} |")
    L.append("\nPer-exit means are per sym_side (n_syms=1) = DIAGNOSTIC ONLY; they show direction of drift, not strategy quality.")
    (OUT_DIR / f"daily_parity_{ymd}.md").write_text("\n".join(L) + "\n")
    print(f"wrote {OUT_DIR / f'daily_parity_{ymd}.md'}")
    print(f"wrote {OUT_DIR / f'daily_parity_{ymd}.csv'}")
    if a.alert and alerts:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "daily_parity_alerts.log", "a") as f:
            for x in alerts:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {x}\n")
        print(f"[ALERT] {len(alerts)} parity alerts -> logs/daily_parity_alerts.log")
    try:
        sys.path.insert(0, str(ROOT))
        from tools.forward_parity import dashboard
        dashboard.build()
    except Exception as e:
        print(f"[dashboard] rebuild failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
