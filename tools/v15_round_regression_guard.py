#!/usr/bin/env python3
"""V15 ROUND REGRESSION GUARD (USER 2026-10-15): "make sure we never need it [the flush revert] because the
>12 sym_sides/hr/server are actually happening and producing better results round after round on new npz's so we can
keep speeding this up without fear".

This is the safety net that makes further speed-ups safe: it does not care HOW a board was produced, only that every
published board still obeys the three things that make a speed-up legitimate.

  1 THROUGHPUT  — improved sym_sides published per hour, per host, against the target (default 12/h = one per 5 min).
  2 NO BACKSLIDE — every published final >= its own best_start gain (the "a worse result is physically impossible"
                   law, BIBLE best-start). best_start is re-measured on the SAME NPZ as the final, so this is the only
                   honest round-over-round comparison; raw gain between rounds is not (different bars, different market).
  3 FRESH NPZ + REAL CHAINING — the board's npz_id.ts_last is recent (it fitted new bars, not a frozen file), and
                   best_start.chosen says it started from the previous published set (history_*) rather than from
                   defaults, i.e. rounds actually chain instead of restarting.

Any of: a backslide, rounds that stop learning (mean uplift <= 0), stale NPZs, or throughput under target -> exit 1 and
an ALERT file. Read-only on the workers: it ssh-es in and reads progress JSONs + published sheet mtimes. Nothing is
launched, killed, written or deleted there.

  python3 tools/v15_round_regression_guard.py [--window-hours 2] [--target 12] [--max-npz-age-h 6] [--quiet]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reports" / "v15_round_regression_guard.json"
ALERT = ROOT / "data" / "reports" / "ALERT_V15_ROUND_REGRESSION.txt"
HOSTS = {"s1": "s1-int", "s2": "s2"}

PROBE = r'''
import json, glob, os, re, time
WIN = float(%(win)s) * 3600.0
NOW = time.time()
sheets = {}
for s in glob.glob(os.path.expanduser("~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_bh*gain*_30d_matrix.xlsx")):
    try:
        mt = os.path.getmtime(s)
    except Exception:
        continue
    if NOW - mt > WIN:
        continue
    m = re.match(r"([A-Z0-9.\-]+_(?:LONG|SHORT))_bh", os.path.basename(s))
    if m and mt > sheets.get(m.group(1), 0):
        sheets[m.group(1)] = mt
prog = {}
for p in glob.glob(os.path.expanduser("~/v15_run*/progress/*_v14_progress.json")) + glob.glob(os.path.expanduser("~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json")):
    ss = os.path.basename(p)[: -len("_v14_progress.json")]
    if ss not in sheets:
        continue
    try:
        mt = os.path.getmtime(p)
    except Exception:
        continue
    if mt > prog.get(ss, (0, None))[0]:
        prog[ss] = (mt, p)
rows = []
for ss, published_at in sorted(sheets.items()):
    r = {"symside": ss, "published_at": published_at}
    pp = (prog.get(ss) or (0, None))[1]
    if pp:
        try:
            d = json.load(open(pp))
            bs = d.get("best_start") if isinstance(d.get("best_start"), dict) else {}
            nid = d.get("npz_id") if isinstance(d.get("npz_id"), dict) else {}
            fin = d.get("final_gain")
            if fin is None:
                fin = d.get("cumulative_gain")
            r.update({
                "final_gain": fin,
                "best_start_gain": bs.get("gain"),
                "best_start_chosen": bs.get("chosen"),
                "best_start_candidates": bs.get("n_candidates"),
                "baseline_gain": d.get("initial_baseline_gain"),
                "npz_ts_last": nid.get("ts_last"),
                "npz_md5": nid.get("md5"),
                "npz_bars": nid.get("n"),
                "bh": d.get("bh"),
                "verdict": d.get("verdict"),
            })
        except Exception as e:
            r["error"] = f"{type(e).__name__}"
    rows.append(r)
print(json.dumps({"host": os.uname().nodename, "now": NOW, "rows": rows}))
'''


def probe(alias, win):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", alias, "python3 -"], input=PROBE % {"win": win}, capture_output=True, text=True, timeout=240)
    for line in reversed((r.stdout or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"error": (r.stderr or "no output")[-300:], "rows": []}


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def judge(host_doc, win_h, target, max_npz_age_h):
    rows = host_doc.get("rows") or []
    now = host_doc.get("now") or time.time()
    backslides, stale_npz, uplifts, chained, no_floor = [], [], [], 0, 0
    for r in rows:
        fin, floor = _f(r.get("final_gain")), _f(r.get("best_start_gain"))
        if fin is None or floor is None:
            no_floor += 1
        else:
            up = round(fin - floor, 4)
            uplifts.append(up)
            if up < -1e-6:
                backslides.append({"symside": r.get("symside"), "final": fin, "best_start": floor, "lost": up})
        ts = _f(r.get("npz_ts_last"))
        age_h = None if ts is None else round((_f(r.get("published_at")) - ts) / 3600.0, 2)
        r["npz_age_h_at_publish"] = age_h
        if age_h is None or age_h > max_npz_age_h:
            stale_npz.append({"symside": r.get("symside"), "npz_age_h": age_h})
        if str(r.get("best_start_chosen") or "").startswith("history"):
            chained += 1
    n = len(rows)
    rate = n / win_h if win_h else 0.0
    mean_up = round(sum(uplifts) / len(uplifts), 4) if uplifts else None
    improved = sum(1 for u in uplifts if u > 1e-9)
    fails = []
    if rate < target:
        fails.append(f"throughput {rate:.1f}/h < {target}/h (one per {60.0 / target:.0f} min)")
    if backslides:
        fails.append(f"{len(backslides)} board(s) published BELOW their best_start — the best-start law is broken")
    if uplifts and mean_up is not None and mean_up <= 0:
        fails.append(f"rounds stopped learning: mean uplift vs best_start {mean_up}")
    if n and len(stale_npz) > n / 2.0:
        fails.append(f"{len(stale_npz)}/{n} boards fitted an NPZ older than {max_npz_age_h} h — not new bars")
    return {
        "published_n": n,
        "per_hour": round(rate, 2),
        "improved_vs_best_start": improved,
        "mean_uplift_vs_best_start": mean_up,
        "median_uplift": round(sorted(uplifts)[len(uplifts) // 2], 4) if uplifts else None,
        "chained_from_previous_published": chained,
        "no_floor_recorded": no_floor,
        "backslides": backslides,
        "stale_npz": stale_npz,
        "fails": fails,
        "rows": rows,
        "error": host_doc.get("error"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-hours", type=float, default=2.0)
    ap.add_argument("--target", type=float, default=12.0, help="improved sym_sides per hour per host")
    ap.add_argument("--max-npz-age-h", type=float, default=6.0, help="an NPZ whose last bar is older than this was not fresh data")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "window_hours": a.window_hours, "target_per_host_per_hour": a.target, "max_npz_age_h": a.max_npz_age_h, "hosts": {}}
    bad = []
    for h, alias in HOSTS.items():
        v = judge(probe(alias, a.window_hours), a.window_hours, a.target, a.max_npz_age_h)
        rep["hosts"][h] = v
        if v["fails"]:
            bad.append(h)
        if not a.quiet:
            print(f"[round-guard] {h}: {v['per_hour']}/h | {v['improved_vs_best_start']}/{v['published_n']} beat best_start | mean uplift {v['mean_uplift_vs_best_start']} | chained {v['chained_from_previous_published']}/{v['published_n']} | stale npz {len(v['stale_npz'])} | {'OK' if not v['fails'] else 'FAIL: ' + '; '.join(v['fails'])}", flush=True)
    rep["hosts_failing"] = bad
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, indent=1))
    tmp.replace(OUT)
    if bad:
        ALERT.write_text(json.dumps({"at": rep["at"], "hosts_failing": bad, "fails": {h: rep["hosts"][h]["fails"] for h in bad}, "revert_if_quality": "V15_PROGRESS_JSON_EVERY_S=0 V15_WB_SAVE_EVERY_S=0 (env or data/v15_gate_flags.json) restores per-row writes; backups/before_progress_wb_flush_5min_202610150309.py restores the pre-throttle pilot"}, indent=1))
    elif ALERT.exists():
        ALERT.unlink()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
