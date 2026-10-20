#!/usr/bin/env python3
"""klines_tail_push - SENDER of an incremental klines_canonical tail bundle (see tools/klines_tail_apply.py for the why and the contract).

  python3 tools/klines_tail_push.py --to s1-pub:binance-sandbox --to 10.0.0.4:binance-sandbox [--tfs 15m,1h,4h,D] [--rows 12]

Per target it remembers the last bar it delivered per file (data/klines_tail_push_state.json), builds the bundle from only the files whose last
stored bar is newer than that (reading the final 12 KB of each file, never parsing a whole store file), pipes it over ssh to
`python3 tools/klines_tail_apply.py` on the target. State advances only on a clean apply. First contact with a target sends every file's tail.
Prints one JSON line per target {to, files, rows, ssh_s, ok, apply:{..}}. Fail-open: a failed target never blocks the others or the daemon.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import klines_canonical as K  # noqa: E402


def build(store, state_t, tfs, rows_n):
    files, newstate = {}, {}
    for p in sorted(Path(store).glob("*_*.json")):
        name = p.name
        if name.startswith(".") or name.startswith("_"):
            continue
        tf = name[:-5].rsplit("_", 1)[-1]
        if tf not in tfs:
            continue
        tr = K.tail_rows(p, n=rows_n)
        if not tr:
            continue
        last = K.ts_ms(tr[-1]["timestamp"])
        if last > state_t.get(name, 0):
            files[name] = tr
            newstate[name] = last
    return files, newstate


def push_one(store, to, state, tfs, rows_n, timeout):
    host, _, root = to.partition(":")
    root = root or "binance-sandbox"
    st = state.setdefault(to, {})
    files, newstate = build(store, st, tfs, rows_n)
    out = {"to": to, "files": len(files), "rows": sum(len(v) for v in files.values())}
    if not files:
        out.update(ok=True, note="nothing new")
        return out
    payload = json.dumps({"v": 1, "src": os.uname().nodename, "files": files})
    t0 = time.time()
    try:
        r = subprocess.run(["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", host,
                            f"cd {root} && python3 tools/klines_tail_apply.py"], input=payload, capture_output=True, text=True, timeout=timeout)
        out["ssh_s"] = round(time.time() - t0, 2)
        line = (r.stdout.strip().splitlines() or [""])[-1]
        ap = json.loads(line) if line.startswith("{") else {}
        out["apply"] = {k: ap.get(k) for k in ("appended_files", "appended_rows", "noop", "corrected", "missing_store", "took_s", "ok")}
        out["apply"]["gap"] = len(ap.get("gap") or [])
        out["apply"]["errors"] = (ap.get("errors") or [])[:3]
        out["ok"] = r.returncode == 0 and bool(ap.get("ok")) and not ap.get("errors")
        if ap.get("gap") and os.environ.get("KLINES_TAIL_GAP_RSYNC", "1") == "1":
            # receiver is more than --rows bars behind: ship just those whole files (--update: a receiver another source already advanced is left alone)
            srcs = [str(Path(store) / g) for g in ap["gap"][:60]]
            rr = subprocess.run(["rsync", "-a", "--update", "--delay-updates", "--timeout=120", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=10"] + srcs + [f"{host}:{root}/klines_canonical/"], capture_output=True, text=True, timeout=600)
            out["gap_rsync"] = {"files": len(srcs), "rc": rr.returncode}
        if r.returncode == 0 and ap.get("ok"):
            gap = set(ap.get("gap") or [])
            for n, v in newstate.items():
                if n not in gap and not any(n in e for e in (ap.get("errors") or [])):
                    st[n] = v
        else:
            out["stderr"] = (r.stderr or "")[-200:]
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        out.update(ok=False, error=f"{type(e).__name__} {e}"[:160], ssh_s=round(time.time() - t0, 2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(K.STORE))
    ap.add_argument("--to", action="append", required=True, help="host[:remote_root] (default root binance-sandbox)")
    ap.add_argument("--tfs", default="15m,1h,4h,D")
    ap.add_argument("--rows", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--state", default=str(K.ROOT / "data" / "klines_tail_push_state.json"))
    a = ap.parse_args()
    sp = Path(a.state)
    try:
        state = json.loads(sp.read_text())
    except (OSError, ValueError):
        state = {}
    rc = 0
    for to in a.to:
        out = push_one(a.store, to, state, set(a.tfs.split(",")), a.rows, a.timeout)
        out["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(json.dumps(out), flush=True)
        rc = rc or (0 if out.get("ok") else 1)
    try:
        sp.parent.mkdir(parents=True, exist_ok=True)
        tmp = sp.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, sp)
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
