#!/usr/bin/env python3
"""Recalculate sheets as NPZs complete (USER 2026-09-29: "start recalculating everything as klines come in for the
incomplete npz's"). Runs on EACH server; read-only on NPZs.

Every --interval s: md5 every NPZ in backtest_v8/indicators. A changed/new file is VALIDATED with the sweep's own
loader (evaluate_v12.prepare 30D + one evaluate — rejects misaligned timestamp_15m etc., the 2026-09-29 breakage).
Valid -> every live-book sym_side of that symbol (per_sym stocks + crypto books) is queued; a side runs on exactly one
server (stable hash of the name mod 3 -> s1/s2/s5; no repeats). Each queued side: FRESH adaptive v15_pilot 30D sheet
(isolated dir) and then tools/v15_365_cycle.py (§58: both 30D and 365D valid+positive). Results/logs under --work.
First pass only records md5s (baseline) unless --initial-all.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOSTS = ["niels", "s2", "s5"]  # s1 hostname is "niels"
BOOKS = [ROOT / "data/hourly_reconfig/per_sym_active_config_stocks.json", ROOT / "data/hourly_reconfig/per_sym_active_config.json"]


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def book_sides():
    out = {}
    for b in BOOKS:
        try:
            for ss in json.load(open(b)):
                if ss.endswith(("_LONG", "_SHORT")):
                    out.setdefault(ss.rsplit("_", 1)[0], []).append(ss)
        except Exception:
            pass
    return out


def validate(ss):
    from tools.opt import evaluate_v12 as E
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as EPS
    try:
        p = E.prepare(ss, 30)
        if p is None:
            return False, "prepare None"
        r = EPS(p, {}, 30) or {}
        return (r.get("gain_pct") is not None), f"trades {r.get('trades')} valid {r.get('valid')} {r.get('invalid_reason') or ''}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


def run_side(ss, work, workers):
    is_crypto = ss.split("_")[0].endswith(("USDT", "USDC", "USD1"))
    tmpl = f"SPREADSHEETS/TEMPLATE_{'CRYPTO' if is_crypto else 'STOCKS'}_{ss.rsplit('_', 1)[1]}.xlsx"
    d = work / "sheets"
    (d / "progress").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, V15_FRESH_RUN="1", V15_PROGRESS_DIR=str(d / "progress"), V15_SKIP_LIVE_AT_DONE="1")
    out_x = ROOT / "SPREADSHEETS" / "V15_RECALC" / f"{ss}_30d_matrix.xlsx"
    out_x.parent.mkdir(parents=True, exist_ok=True)
    with open(d / "logs" / f"{ss}.log", "w") as lf:
        subprocess.run([sys.executable, "-u", str(ROOT / "v15_pilot.py"), "--sym-side", ss, "--template", tmpl, "--seq-mode", "worst2best", "--window-days", "30", "--vector-only", "--workers", str(workers), "--out", str(out_x)], cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
    prog = d / "progress" / f"{ss}_v14_progress.json"
    if prog.exists() and not json.load(open(prog)).get("diagnostic_only"):
        with open(d / "logs" / f"{ss}_cycle.log", "w") as lf:
            subprocess.run([sys.executable, "-u", str(ROOT / "tools" / "v15_365_cycle.py"), "--sym-side", ss, "--progress", str(prog), "--template", tmpl, "--work", str(work / "cycle"), "--workers", str(workers)], cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT)
    with open(work / "results.txt", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {socket.gethostname()} {ss} sheet={'ok' if prog.exists() else 'none'}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/home/niels/v15_recalc")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--initial-all", action="store_true")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    me = socket.gethostname()
    state_p = work / "npz_md5.json"
    state = json.load(open(state_p)) if state_p.exists() else {}
    first = not state and not a.initial_all
    ind = ROOT / "backtest_v8" / "indicators"
    while True:
        changed = []
        for p in sorted(ind.glob("*.npz")):
            try:
                h = md5(p)
            except Exception:
                continue
            if state.get(p.name) != h:
                if not first:
                    changed.append(p.stem)
                state[p.name] = h
        state_p.write_text(json.dumps(state))
        if first:
            print(f"[RECALC] {me} baseline recorded for {len(state)} NPZs", flush=True)
            first = False
        sides = book_sides()
        todo = []
        for sym in changed:
            for ss in sides.get(sym, []):
                if HOSTS[zlib.crc32(ss.encode()) % 3] != me:
                    continue
                ok, why = validate(ss)
                with open(work / "validate.txt", "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {me} {ss} {'VALID' if ok else 'REJECTED'} {why}\n")
                print(f"[RECALC] {me} {ss} NPZ changed -> {'VALID' if ok else 'REJECTED'} {why}", flush=True)
                if ok:
                    todo.append(ss)
        if todo:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=a.parallel) as ex:
                list(ex.map(lambda x: run_side(x, work, a.workers), todo))
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
