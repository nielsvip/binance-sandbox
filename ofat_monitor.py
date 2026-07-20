#!/usr/bin/env python3
"""ofat_monitor.py — status + improvement-hunt report for the OFAT param screens.

Runs on S1. For each mode (crypto, tradier) it:
  * counts coverage: cells done vs total (sweepable params x their test_values) -> %, ETA
  * reads the baseline pool_sharpe and ranks every cell whose delta_vs_baseline > 0
    (i.e. moving that one knob off its current value RAISED pooled Sharpe in the screen)
  * checks runner liveness, server load, free mem, and recent OOM kills
  * writes data/ofat_report.md and prints a short summary

NO-LIES: the screen samples ~16 syms (< the 48-crypto / 100-stock publishable floor),
so every pool_sharpe here is DIAGNOSTIC. Improvements are SHORTLIST candidates only —
each must be re-confirmed by a Tier-2 run at the sample floor before any promotion. This
report never writes a live config and never promotes.
"""
import csv
import glob
import json
import subprocess
import time
from pathlib import Path

SBX = Path("/home/niels/binance-sandbox")
LOGS = Path("/home/niels/logs")
MODES = ["crypto", "tradier"]


def _load_manifest(mode):
    p = SBX / f"data/param_sweep_manifest_{mode}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get("params", {})


def _total_cells(man):
    n = 0
    for v in man.values():
        if v.get("sweepable") and v.get("test_values"):
            n += len(v["test_values"])
    return n


def _read_screen(mode):
    p = LOGS / f"ofat_{mode}_screen.csv"
    rows = []
    base = None
    if p.exists():
        for r in csv.DictReader(p.open()):
            rows.append(r)
            if r["param"] == "__BASELINE__":
                try:
                    base = float(r["pool_sharpe"])
                except (ValueError, TypeError):
                    pass
    return rows, base


def _f(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _server():
    free = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout
    avail = used = total = 0
    for ln in free.splitlines():
        if ln.startswith("Mem:"):
            p = ln.split()
            total, used, avail = int(p[1]), int(p[2]), int(p[6])
    load = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    oom = subprocess.run("dmesg 2>/dev/null | grep -i 'killed process' | tail -3",
                         shell=True, capture_output=True, text=True).stdout.strip()
    return total, used, avail, load, oom


def main():
    lines = [f"# OFAT param-screen report — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}", ""]
    total, used, avail, load, oom = _server()
    lines.append(f"**Server**: mem used {used}/{total}MB · avail {avail}MB · {load}")
    if oom:
        lines.append(f"**⚠ recent OOM kills**:\n```\n{oom}\n```")
    lines.append("")
    summary = []
    for mode in MODES:
        man = _load_manifest(mode)
        tot = _total_cells(man)
        rows, base = _read_screen(mode)
        done = len([r for r in rows if r["param"] != "__BASELINE__"])
        alive = subprocess.run(["pgrep", "-fc", f"engine_ofat_screen.py --mode {mode}"],
                               capture_output=True, text=True).stdout.strip()
        pct = (100.0 * done / tot) if tot else 0.0
        # improvement hunt
        improvements = []
        for r in rows:
            if r["param"] == "__BASELINE__":
                continue
            d = _f(r["delta_vs_baseline"])
            ps = _f(r["pool_sharpe"])
            tr = _f(r["trades"])
            if d is not None and d > 0 and ps is not None and (tr or 0) >= 30:
                improvements.append((d, ps, r["param"], r["value"], int(tr or 0), r.get("n_syms", "")))
        improvements.sort(reverse=True)
        # parity/effect audit: which swept params actually MOVED the real Tier-2 run
        # (delta != 0 for any value) vs inert (every value identical to baseline -> the
        # override had no effect in backtest context; baseline not faithful to that knob).
        by_param = {}
        for r in rows:
            if r["param"] == "__BASELINE__":
                continue
            d = _f(r["delta_vs_baseline"])
            by_param.setdefault(r["param"], []).append(abs(d) if d is not None else 0.0)
        moved = sum(1 for k, ds in by_param.items() if any(x > 1e-9 for x in ds))
        inert = sum(1 for k, ds in by_param.items() if ds and all(x <= 1e-9 for x in ds))
        lines.append(f"## {mode}")
        lines.append(f"- coverage: **{done}/{tot} cells ({pct:.1f}%)** · runner_alive={alive} · "
                     f"baseline pool_sharpe={base if base is not None else 'pending'} "
                     f"(DIAGNOSTIC, ~16 syms < floor)")
        lines.append(f"- effect audit (params with >=1 value scored): MOVED real run={moved} · "
                     f"INERT (override no-effect — needs live-only state / dead in backtest)={inert}")
        lines.append(f"- improvement candidates (delta>0, trades>=30): **{len(improvements)}**")
        if improvements:
            lines.append(f"- TOP gain/sharpe levers to confirm at floor (Tier-2 full syms before any promote):")
            for d, ps, p_, v, tr, ns in improvements[:15]:
                cur = _f(man.get(p_, {}).get("default"))
                lines.append(f"    - `{p_}` {cur}→**{v}**  pool_sharpe {ps:+.4f} (Δ{d:+.4f}) "
                             f"trades={tr} n_syms={ns}")
        lines.append("")
        summary.append(f"{mode}: {done}/{tot} ({pct:.0f}%) alive={alive} improv={len(improvements)}")
    rep = SBX / "data/ofat_report.md"
    rep.write_text("\n".join(lines) + "\n")
    print("REPORT -> " + str(rep))
    print(f"server: avail {avail}MB · {load.split('load average:')[-1].strip()}")
    for s in summary:
        print("  " + s)


if __name__ == "__main__":
    main()
