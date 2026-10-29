#!/usr/bin/env python3
"""stock_npz_health — keep the stock NPZs (backtest_v8/indicators) intact: find STALE and NaN-holed files and repair them.

USER 2026-10-29: "add the stale and NaN stock repair to the fleet scheduler ... so it keeps its data intact".
Run ON the host that owns the stock NPZs (the fleet scheduler launches it over ssh; see v15_fleet_scheduler._stock_npz_health_kick).

Classes (stock symbols only: symbols_trb_long.json + symbols_trb_short.json in the root):
  STALE  newest bar older than STALE_H hours (default 96 h = weekend + holiday).
  NAN    any non-finite value in the last NAN_TAIL bars of KEY_FIELDS.
  OK     neither.

Repair rules (no file is ever replaced by a worse one):
  STALE -> tools/stock_npz_refresh.py (its own gate: newer last bar, no shorter history, backup + rollback on failure).
  NAN   -> precompute a fresh copy to a stage dir (tools/stock_npz_precompute_relaxed.py, --mode tradier) and install it
           ONLY when it has strictly fewer NaN tail values, is not shorter, and its last bar is not older. The current file is
           backed up first and the install is an atomic os.replace.

usage:
  python3 tools/stock_npz_health.py --scan                 # report only, JSON on stdout (one line)
  python3 tools/stock_npz_health.py --repair [--max 6]     # scan, then repair at most --max symbols this run
"""
import argparse
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IND = ROOT / "backtest_v8" / "indicators"
STAGE = Path.home() / "npz_health_stage"
BACKUP = Path.home() / "npz_health_backup"
REPORT = ROOT / "data" / "reports" / "stock_npz_health.json"
KEY_FIELDS = ("atr_15m", "relative_volume_15m", "rsi_15m", "stoch_k_15m", "k_15m")
NAN_TAIL = 200
STALE_H = float(os.environ.get("STOCK_NPZ_STALE_H", "96"))
PY = sys.executable


def stock_symbols():
    syms = []
    for name in ("symbols_trb_long.json", "symbols_trb_short.json"):
        p = ROOT / name
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        items = data.get("symbols", data) if isinstance(data, dict) else data
        for s in items:
            s = str(s.get("symbol", s) if isinstance(s, dict) else s).upper()
            if s and s not in syms:
                syms.append(s)
    return syms


def _to_epoch(v):
    if isinstance(v, (int, float, np.integer, np.floating)):
        x = float(v)
        return x / 1000.0 if x > 1e12 else x
    s = str(v).strip().replace("Z", "+00:00")
    return dt.datetime.fromisoformat(s).timestamp()


def last_epoch(z):
    ts = z["timestamps"]
    if len(ts) == 0:
        return 0.0
    return _to_epoch(ts[-1])


def nan_tail(z):
    n = 0
    for k in KEY_FIELDS:
        if k in z.files:
            a = np.asarray(z[k][-NAN_TAIL:], dtype=float)
            n += int((~np.isfinite(a)).sum())
    return n


def inspect(path, now):
    try:
        with np.load(path, allow_pickle=True) as z:
            if "timestamps" not in z.files:
                return {"status": "UNREADABLE", "reason": "no timestamps"}
            last = last_epoch(z)
            n_bars = int(len(z["timestamps"]))
            nans = nan_tail(z)
    except Exception as exc:
        return {"status": "UNREADABLE", "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
    age_h = (now - last) / 3600.0 if last else float("inf")
    if age_h > STALE_H:
        status = "STALE"
    elif nans:
        status = "NAN"
    else:
        status = "OK"
    return {"status": status, "age_h": round(age_h, 1), "nan_tail": nans, "bars": n_bars, "last_epoch": last}


def repair_stale(syms):
    lst = STAGE.parent / "npz_health_stale_symbols.txt"
    lst.write_text(",".join(syms) + "\n")
    cmd = [PY, str(ROOT / "tools" / "stock_npz_refresh.py"), "--symbols-file", str(lst)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=7200)
    return {"returncode": r.returncode, "tail": (r.stdout or "")[-600:] + (r.stderr or "")[-300:]}


def repair_nan(sym, cur_info):
    STAGE.mkdir(parents=True, exist_ok=True)
    BACKUP.mkdir(parents=True, exist_ok=True)
    pre = subprocess.run([PY, str(ROOT / "tools" / "stock_npz_precompute_relaxed.py"), "--mode", "tradier",
                          "--symbols", sym, "--out-dir", str(STAGE)], cwd=str(ROOT), capture_output=True, text=True, timeout=3600)
    staged = STAGE / f"{sym}.npz"
    if pre.returncode != 0 or not staged.exists():
        return {"installed": False, "reason": f"precompute rc={pre.returncode}"}
    cur_path = IND / f"{sym}.npz"
    with np.load(staged, allow_pickle=True) as z:
        s_nan, s_bars, s_last = nan_tail(z), int(len(z["timestamps"])), last_epoch(z)
    cur_nan, cur_bars, cur_last = cur_info["nan_tail"], cur_info["bars"], cur_info["last_epoch"]
    if not (s_nan < cur_nan and s_bars >= cur_bars and s_last >= cur_last):
        return {"installed": False, "reason": "staged not strictly better", "staged_nan": s_nan, "cur_nan": cur_nan,
                "staged_bars": s_bars, "cur_bars": cur_bars}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    shutil.copy2(cur_path, BACKUP / f"{sym}.{stamp}.npz")
    tmp = IND / f".{sym}.health.tmp.npz"
    shutil.copy2(staged, tmp)
    os.replace(tmp, cur_path)
    return {"installed": True, "staged_nan": s_nan, "cur_nan": cur_nan, "bars": s_bars, "backup": str(BACKUP / f"{sym}.{stamp}.npz")}


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true")
    mode.add_argument("--repair", action="store_true")
    ap.add_argument("--max", type=int, default=6, help="max symbols repaired per run")
    args = ap.parse_args()
    now = time.time()
    syms = stock_symbols()
    rows = {}
    for s in syms:
        p = IND / f"{s}.npz"
        rows[s] = inspect(p, now) if p.exists() else {"status": "MISSING"}
    stale = [s for s, r in rows.items() if r["status"] == "STALE"]
    nans = [s for s, r in rows.items() if r["status"] == "NAN"]
    summary = {"ts": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "host": os.uname().nodename,
               "symbols": len(syms), "stale": len(stale), "nan": len(nans),
               "missing": sum(1 for r in rows.values() if r["status"] == "MISSING"),
               "unreadable": sum(1 for r in rows.values() if r["status"] == "UNREADABLE"), "repairs": []}
    if args.repair:
        budget = args.max
        if stale and budget > 0:
            take = stale[:budget]
            res = repair_stale(take)
            summary["repairs"].append({"kind": "STALE", "symbols": take, **res})
            budget -= len(take)
        for s in nans[: max(0, budget)]:
            try:
                res = repair_nan(s, rows[s])
            except Exception as exc:
                res = {"installed": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
            summary["repairs"].append({"kind": "NAN", "symbol": s, **res})
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({**summary, "detail": rows}, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
