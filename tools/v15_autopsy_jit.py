#!/usr/bin/env python3
"""v15_autopsy_jit — JUST-IN-TIME autopsy-first base producer on S1 (2026-10-13, BIBLE §94).

Why: the scheduler runs with V15_SCHED_REQUIRE_BASE=1 — a 30D board launches only once ~/v15_autopsy_first holds an
acceptable base for it (`{SS}_autopsy_base.json` with effective_switches, or a NO_RESCUE `{SS}_autopsy.json` whose
template base trades). Its producers died: s7 was destroyed (V15_AF_PULL `#S7DESTROYED_20261010#`) and the NPZ-JIT
cleanup commented V15_AF_FORCE_REFRESH (`#NPZ_JIT_ONLY_20261013#`). No file landed after 10-12 05:06, so new sides
WAITED forever (starve_audit waiting_base=27 on 2026-10-13 16:4xZ).

What: every run picks ONLY the sym_sides about to be dispatched — the scheduler's own pending list
(data/autopilot/sched_snapshot.json pending_stocks/pending_crypto) ordered by the director priority file
(~/v15_priority_syms.txt) — that still lack an acceptable base, and runs the REAL tools/v15_autopsy_first.py on them
(engine-verified, never a fabricated set; NO_RESCUE writes a report, not a base).
NPZ LAW (§91): an existing NPZ is used read-only (never refreshed here — the dispatch path refreshes it right before the
board loads it). A picked side whose symbol has NO NPZ on S1 (39 of the waiting sides on 2026-10-13: they could never get a
base, and never dispatch without one -> deadlock) gets ONE build through the dispatch path's own single-flight builder
(`flock /tmp/v15_npz_prep.lock tools/v15_npz_prep.py --symbol SYM`) immediately before the autopsy loads it — i.e. within
minutes of a load, exactly what §91 allows; at most V15_AF_JIT_NPZ_MAX (2) builds per run, V15_AF_JIT_NPZ=0 disables. V15_AF_PUSH (S1 cron */2) copies new bases to the workers.
Throttles: S1 load1 < V15_AF_JIT_MAXLOAD (default 0.75 x cores), nice 15, V15_AF_JIT_MAX sides per run (4),
V15_AF_JIT_WORKERS (2), V15_AF_JIT_RETRY_H (6) h back-off per side after an attempt that produced nothing.

Usage: .venv/bin/python tools/v15_autopsy_jit.py [--dry-run] [--max N] [--out ~/v15_autopsy_first]
Cron (S1): */15 * * * * cd /home/niels/binance-sandbox && flock -n /tmp/v15_af_jit.lock timeout 3300 .venv/bin/python tools/v15_autopsy_jit.py >> /home/niels/logs/v15_af_jit.log 2>&1 # V15_AF_JIT
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def base_ok(afd, ss):
    """Mirror of the scheduler's V15_SCHED_REQUIRE_BASE acceptance (tools/v15_fleet_scheduler.py ~1541). Returns (ok, why)."""
    bp = afd / f"{ss}_autopsy_base.json"
    try:
        if bp.exists() and "effective_switches" in bp.read_text():
            return True, "base"
        rp = afd / f"{ss}_autopsy.json"
        if not bp.exists() and rp.exists():
            rep = json.loads(rp.read_text())
            if int(((rep.get("base") or {}).get("trades") or 0)) > 0:
                return True, "no_rescue_report"
            return True, "report_zero_trades"  # terminal for this producer: rerunning the same base cannot create trades
    except Exception:
        return False, "unreadable"
    return False, "missing"


def candidates(snapshot, priority, afd, npz_dir, state, now, retry_h):
    pend = list(snapshot.get("pending_stocks") or []) + list(snapshot.get("pending_crypto") or [])
    running = set(snapshot.get("running") or [])
    nonshort = set(snapshot.get("non_shortable") or [])
    order = [s for s in priority if s in pend] + [s for s in pend if s not in priority]
    out, skipped = [], {}
    for sym in order:
        if sym in running:
            continue
        for side in ("LONG", "SHORT"):
            ss = f"{sym}_{side}"
            ok, why = base_ok(afd, ss)
            if ok:
                continue
            if side == "SHORT" and sym in nonshort:
                skipped[ss] = "non_shortable"
                continue
            last = (state.get("attempts") or {}).get(ss)
            if last and now - float(last) < retry_h * 3600:
                skipped[ss] = "backoff"
                continue
            if not (npz_dir / f"{sym}.npz").exists():
                skipped[ss] = "no_npz"
                out.append((ss, True))
                continue
            out.append((ss, False))
    return out, skipped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/v15_autopsy_first"))
    ap.add_argument("--snapshot", default=str(ROOT / "data" / "autopilot" / "sched_snapshot.json"))
    ap.add_argument("--priority", default=os.path.expanduser("~/v15_priority_syms.txt"))
    ap.add_argument("--npz-dir", default=str(ROOT / "backtest_v8" / "indicators"))
    ap.add_argument("--max", type=int, default=int(os.environ.get("V15_AF_JIT_MAX", "4")))
    ap.add_argument("--workers", type=int, default=int(os.environ.get("V15_AF_JIT_WORKERS", "2")))
    ap.add_argument("--retry-h", type=float, default=float(os.environ.get("V15_AF_JIT_RETRY_H", "6")))
    ap.add_argument("--max-load", type=float, default=float(os.environ.get("V15_AF_JIT_MAXLOAD", str(0.75 * (os.cpu_count() or 4)))))
    ap.add_argument("--timeout-s", type=int, default=3000)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    afd = Path(a.out)
    afd.mkdir(parents=True, exist_ok=True)
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    try:
        snap = json.loads(Path(a.snapshot).read_text())
    except Exception as e:
        print(f"[af-jit] {stamp} no scheduler snapshot ({e}) — nothing to do", flush=True)
        return 0
    try:
        prio = [s.strip() for s in Path(a.priority).read_text().replace("\n", ",").split(",") if s.strip()]
    except Exception:
        prio = []
    state_p = afd / "_jit_state.json"
    try:
        state = json.loads(state_p.read_text())
    except Exception:
        state = {"attempts": {}}
    cands, skipped = candidates(snap, prio, afd, Path(a.npz_dir), state, now, a.retry_h)
    npz_on = os.environ.get("V15_AF_JIT_NPZ", "1") == "1"
    npz_max = int(os.environ.get("V15_AF_JIT_NPZ_MAX", "2"))
    pick, need_npz = [], []
    for ss, missing in cands:
        if len(pick) >= max(0, a.max):
            break
        sym = ss.rsplit("_", 1)[0]
        if missing:
            if not npz_on or (sym not in need_npz and len(need_npz) >= npz_max):
                continue
            if sym not in need_npz:
                need_npz.append(sym)
            skipped.pop(ss, None)
        pick.append(ss)
    load1 = os.getloadavg()[0]
    rep = {"at": stamp, "snapshot_at": snap.get("at"), "candidates": len(cands), "picked": pick, "skipped": skipped, "load1": round(load1, 2), "max_load": a.max_load}
    if not pick:
        print(f"[af-jit] {stamp} nothing waiting for a base (skipped={len(skipped)} {dict(list(skipped.items())[:8])})", flush=True)
    elif load1 >= a.max_load:
        rep["deferred"] = "load"
        print(f"[af-jit] {stamp} load1 {load1:.1f} >= {a.max_load:.1f} — deferring {len(pick)} sides {pick}", flush=True)
    elif a.dry_run:
        print(f"[af-jit] {stamp} DRY-RUN would run autopsy-first on {pick} (candidates={len(cands)})", flush=True)
    else:
        print(f"[af-jit] {stamp} autopsy-first on {pick} (candidates={len(cands)} load1={load1:.1f})", flush=True)
        built = {}
        for sym in need_npz:
            try:
                r0 = subprocess.run(["flock", "-w", os.environ.get("V15_NPZ_LOCK_WAIT_S", "600"), "/tmp/v15_npz_prep.lock", sys.executable, "tools/v15_npz_prep.py", "--symbol", sym], cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
                built[sym] = {"rc": r0.returncode, "out": (r0.stdout or "").strip().splitlines()[-1:] }
            except Exception as e:
                built[sym] = {"rc": "error", "out": [repr(e)[:200]]}
            print(f"[af-jit] JIT NPZ {sym}: {built[sym]}", flush=True)
        rep["npz_built"] = built
        pick = [ss for ss in pick if (Path(a.npz_dir) / f"{ss.rsplit('_', 1)[0]}.npz").exists()]
        if not pick:
            print("[af-jit] no picked side has an NPZ after the JIT build — nothing to run", flush=True)
            for sym in need_npz:
                for side in ("LONG", "SHORT"):
                    state.setdefault("attempts", {})[f"{sym}_{side}"] = now
            Path(str(state_p) + ".tmp").write_text(json.dumps(state, indent=1))
            os.replace(str(state_p) + ".tmp", state_p)
            return 0
        env = dict(os.environ, V12_NPZ_CACHE=os.environ.get("V12_NPZ_CACHE", "2"), V12_ALLOW_STALE_NPZ="1")
        cmd = ["nice", "-n", "15", sys.executable, "-u", "tools/v15_autopsy_first.py", "--symsides", ",".join(pick), "--out", str(afd), "--workers", str(a.workers)]
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=a.timeout_s)
            lines = [l for l in (r.stdout or "").splitlines() if l.startswith("[autopsy-first]")]
            rep["rc"] = r.returncode
            rep["result_lines"] = lines[-20:]
            for l in lines:
                print("   " + l, flush=True)
            if r.returncode != 0:
                print((r.stderr or "")[-800:], flush=True)
        except subprocess.TimeoutExpired:
            rep["rc"] = "timeout"
            print(f"[af-jit] timeout after {a.timeout_s}s", flush=True)
        for ss in pick:
            ok, why = base_ok(afd, ss)
            rep.setdefault("after", {})[ss] = why
            if not ok:
                state.setdefault("attempts", {})[ss] = now
        state["last_run"] = stamp
        tmp = str(state_p) + ".tmp"
        Path(tmp).write_text(json.dumps(state, indent=1))
        os.replace(tmp, state_p)
    try:
        out = ROOT / "data" / "reports" / "autopsy_jit_last.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, default=str))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
