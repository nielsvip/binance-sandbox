#!/usr/bin/env python3
"""V15 FROZEN-NPZ BENCH (USER 2026-10-14): "keep an archive of npz files on s2 so you can compare new versions of the
v15_pilot with old ones on the same npz files … apples with apples … make sure each version actually improves results and
does not drop the ball while speeding up backtests and skipping more rows."

Every pilot change is judged on IDENTICAL inputs: a frozen snapshot of the NPZs, the data/ knowledge files, the templates,
the per-side start bases and the production pilot env. Only the code differs. Never publishes: V15_OUT_DIR /
V15_PROGRESS_DIR / V15_ISOLATED_PROOF point every write into the run dir; eval cache starts empty per run; code is a REAL
copy (engine modules resolve ROOT via Path(__file__).resolve(), so a symlinked tree would silently read the live NPZs).

  snapshot --name N --sides A_LONG,B_SHORT,...   freeze inputs -> ~/v15_bench/snap_N/ (md5 manifest)
  run      --snap N --label L [--code DIR]        run every side of the snapshot with the code in DIR (default ~/binance-sandbox)
  compare  --snap N --a L1 --b L2                 per-side + aggregate diff, PASS/FAIL gate
Stdlib only. Runs on S2 (or any worker with the NPZs + .venv). NO-LIES: every number is read back from the pilot's own
progress JSON and delta log of that run.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
BENCH = Path(os.environ.get("V15_BENCH_DIR", str(HOME / "v15_bench")))
SRC = Path(os.environ.get("V15_BENCH_SRC", str(HOME / "binance-sandbox")))
CODE_EXCLUDE = ["data", "backtest_v8", "backtest_v7", "SPREADSHEETS", "klines_cache", "klines_cache_macbook", "klines_vault", "klines_canonical", "archive", "backups", ".venv", "backtest_framework", ".git", "logs", "from_scratch_archive_*", "*.log", "*.db", "*.sqlite", "*.npz", "*.xlsx", "__pycache__", ".history", ".elastic"]
DATA_EXCLUDE_DIRS = {"eval_cache_v1.db", "klines_3m_hist_w2", "klines_5m_hist_w2", "matrix_npz", "reports", "tradier", "sweep_results", "vec_live", "hourly_reconfig", "yellow_discovery", "s6_365", "causal_learner.db", "kpi"}
DATA_MAX_FILE = 150 * 1024 * 1024
ENV_KEEP_PREFIX = ("V15_", "V12_")
ENV_DROP = {"V15_PROGRESS_DIR", "V15_OUT_DIR", "V15_START_OVERRIDES", "V15_ISOLATED_PROOF", "V15_EVAL_CACHE_DB", "V15_FILL_SLICE_S", "V15_V17_CARRY_DIR"}
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def sym_of(ss):
    return ss.rsplit("_", 1)[0]


def is_crypto(ss):
    return sym_of(ss).upper().endswith(CRYPTO_SUFFIX)


def template_for(ss):
    side = ss.rsplit("_", 1)[1]
    return f"TEMPLATE_{'CRYPTO' if is_crypto(ss) else 'STOCKS'}_{side}.xlsx"


def capture_env(src_pid=None):
    """Production pilot env: from a running pilot's /proc environ (most faithful), else the scheduler's dispatch defaults file."""
    pids = [src_pid] if src_pid else []
    if not pids:
        try:
            out = subprocess.run(["pgrep", "-f", "python -u v15_pilot.py"], capture_output=True, text=True).stdout.split()
            pids = [int(p) for p in out]
        except Exception:
            pids = []
    for pid in pids:
        try:
            raw = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
            env = {}
            for kv in raw:
                k, _, v = kv.decode(errors="replace").partition("=")
                if k.startswith(ENV_KEEP_PREFIX) and k not in ENV_DROP:
                    env[k] = v
            if env:
                return env, f"/proc/{pid}/environ"
        except Exception:
            continue
    return {}, "none (no running pilot found; pass --env-json)"


def cmd_snapshot(args):
    snap = BENCH / f"snap_{args.name}"
    if snap.exists() and not args.force:
        print(f"snapshot exists: {snap} (use --force to rebuild)", file=sys.stderr)
        return 2
    sides = [s.strip() for s in args.sides.split(",") if s.strip()]
    (snap / "npz").mkdir(parents=True, exist_ok=True)
    (snap / "starts").mkdir(exist_ok=True)
    (snap / "templates").mkdir(exist_ok=True)
    man = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "src": str(SRC), "sides": sides, "npz": {}, "starts": {}, "templates": {}, "data_files": 0}
    for ss in sides:
        sym = sym_of(ss)
        src = SRC / "backtest_v8" / "indicators" / f"{sym}.npz"
        if not src.exists():
            print(f"[skip] {ss}: no NPZ {src}", file=sys.stderr)
            continue
        dst = snap / "npz" / f"{sym}.npz"
        if not dst.exists():
            shutil.copy2(src, dst)
        man["npz"][sym] = {"md5": md5(dst), "bytes": dst.stat().st_size, "src_mtime": src.stat().st_mtime}
        st = HOME / "v15_autopsy_first" / f"{ss}_autopsy_base.json"
        if st.exists():
            shutil.copy2(st, snap / "starts" / st.name)
            man["starts"][ss] = md5(snap / "starts" / st.name)
    for t in (SRC / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM").glob("TEMPLATE_*.xlsx"):
        shutil.copy2(t, snap / "templates" / t.name)
        man["templates"][t.name] = md5(snap / "templates" / t.name)
    ddst = snap / "data"
    for root, dirs, files in os.walk(SRC / "data"):
        rel = Path(root).relative_to(SRC / "data")
        if rel.parts and rel.parts[0] in DATA_EXCLUDE_DIRS:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not (not rel.parts and d in DATA_EXCLUDE_DIRS)]
        for f in files:
            if not rel.parts and f in DATA_EXCLUDE_DIRS:
                continue
            p = Path(root) / f
            try:
                if p.is_symlink() or p.stat().st_size > DATA_MAX_FILE or f.endswith((".tmp", "-wal", "-shm")):
                    continue
                (ddst / rel).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, ddst / rel / f)
                man["data_files"] += 1
            except Exception:
                continue
    lp = SRC / "data" / "reports" / "lifecycle_pilot"
    if lp.is_dir():
        (ddst / "reports" / "lifecycle_pilot").mkdir(parents=True, exist_ok=True)
        for p in lp.glob("*.json"):
            try:
                if p.name.endswith("_v14_progress.json") or p.stat().st_size > DATA_MAX_FILE:
                    continue
                shutil.copy2(p, ddst / "reports" / "lifecycle_pilot" / p.name)
                man["data_files"] += 1
            except Exception:
                continue
    env, how = capture_env(args.pid)
    if args.env_json:
        env = {}
        env.update(json.load(open(args.env_json)))
        how += f" + {args.env_json}"
    man["env"], man["env_source"] = env, how
    (snap / "manifest.json").write_text(json.dumps(man, indent=1))
    print(json.dumps({"snap": str(snap), "sides": len(man["npz"]), "npz_bytes": sum(v["bytes"] for v in man["npz"].values()), "starts": len(man["starts"]), "data_files": man["data_files"], "env_keys": len(env), "env_source": how}))
    return 0


def mem_avail_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 1e9


def build_root(snap, run_dir, code):
    root = run_dir / "root"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    ex = []
    for e in CODE_EXCLUDE:
        ex += ["--exclude", e]
    subprocess.run(["rsync", "-a"] + ex + [f"{code}/", f"{root}/"], check=True)
    os.symlink(SRC / ".venv", root / ".venv")
    subprocess.run(["cp", "-a", str(snap / "data"), str(root / "data")], check=True)
    ind = root / "backtest_v8" / "indicators"
    ind.mkdir(parents=True)
    now = time.time()
    for f in (snap / "npz").glob("*.npz"):
        shutil.copy2(f, ind / f.name)
        os.utime(ind / f.name, (now, now))  # age gates read mtime (ensure_npz_for_symside refreshes > 35 d); content is the frozen bytes
    tdir = root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
    tdir.mkdir(parents=True)
    for f in (snap / "templates").glob("*.xlsx"):
        shutil.copy2(f, tdir / f.name)
        shutil.copy2(f, root / "SPREADSHEETS" / f.name)  # pending-rows reader looks in SPREADSHEETS/ directly
    return root


def board_metrics(prog_path, dlog_path):
    out = {"final_gain": None, "trades": None, "tim": None, "dd": None, "sharpe": None, "verdict": None, "baseline": None, "promoted_rows": None, "rows": None}
    try:
        p = json.loads(Path(prog_path).read_text())
        out["final_gain"] = p.get("final_gain")
        out["verdict"] = p.get("verdict")
        out["baseline"] = p.get("initial_baseline_gain")
        fm = p.get("final_metrics") or p.get("final") or {}
        if isinstance(fm, dict):
            out["trades"] = fm.get("trades")
            out["tim"] = fm.get("tim_pct", fm.get("tim"))
            out["dd"] = fm.get("max_dd_pct", fm.get("dd"))
            out["sharpe"] = fm.get("pool_sharpe")
        done = p.get("done") or {}
        out["rows"] = len(done)
        out["promoted_rows"] = sum(1 for v in done.values() if isinstance(v, dict) and v.get("promoted"))
        for k in ("final_trades", "trades_final"):
            if out["trades"] is None and p.get(k) is not None:
                out["trades"] = p.get(k)
        for k, dst in (("final_tim", "tim"), ("final_dd", "dd"), ("final_max_dd_pct", "dd"), ("final_pool_sharpe", "sharpe")):
            if out[dst] is None and p.get(k) is not None:
                out[dst] = p.get(k)
    except Exception as e:
        out["err"] = str(e)[:120]
    n_ev = n_nc = 0
    secs = 0.0
    try:
        with open(dlog_path) as fh:
            for line in fh:
                if '"fn"' not in line:
                    continue
                n_ev += 1
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("cached"):
                    n_nc += 1
                    secs += float(r.get("secs") or 0.0)
    except Exception:
        pass
    out.update(evals=n_ev, evals_noncached=n_nc)
    return out


def cmd_run(args):
    snap = BENCH / f"snap_{args.snap}"
    man = json.loads((snap / "manifest.json").read_text())
    run_dir = BENCH / "runs" / f"{args.snap}__{args.label}"
    if run_dir.exists() and not args.force:
        print(f"run exists: {run_dir} (--force to redo)", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)
    code = Path(args.code or SRC)
    root = build_root(snap, run_dir, code)
    pmd5 = md5(root / "v15_pilot.py")
    env_base = {k: v for k, v in os.environ.items() if not k.startswith(ENV_KEEP_PREFIX)}
    env_base.update(man.get("env") or {})
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        env_base[k] = v
    sides = [s for s in man["sides"] if sym_of(s) in man["npz"]]
    if args.only:
        sides = [s for s in sides if s in set(args.only.split(","))]
    queue, running, results = list(sides), {}, {}
    t_all = time.time()
    while queue or running:
        while queue and len(running) < args.parallel and mem_avail_mb() >= args.min_free_mb:
            ss = queue.pop(0)
            sd = run_dir / ss
            if sd.exists():
                shutil.rmtree(sd)  # 2026-10-14: a killed run's partial progress/xlsx was ingested as "previous best" on relaunch -> every version started from a different set
            (sd / "progress").mkdir(parents=True, exist_ok=True)
            (sd / "out").mkdir(exist_ok=True)
            env = dict(env_base)
            env.update(V15_PROGRESS_DIR=str(sd / "progress"), V15_OUT_DIR=str(sd / "out"), V15_ISOLATED_PROOF="1", V15_FILL_SLICE_S="0", V15_FRESH_RUN="1", V15_EVAL_CACHE_DB=str(sd / "eval_cache.db"), V15_V17_CARRY_DIR=str(sd / "carry"))
            st = snap / "starts" / f"{ss}_autopsy_base.json"
            if st.exists():
                env["V15_START_OVERRIDES"] = str(st)
            cmd = [str(root / ".venv" / "bin" / "python"), "-u", "v15_pilot.py", "--sym-side", ss, "--template", str(root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / template_for(ss)), "--seq-mode", "worst2best", "--window-days", "30", "--vector-only", "--workers", str(args.workers)]
            log = open(sd / "pilot.log", "w")
            pr = subprocess.Popen(["nice", "-n", str(args.nice)] + cmd, cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT)
            running[ss] = (pr, time.time(), log)
            print(f"[bench] start {ss} pid {pr.pid}", flush=True)
        time.sleep(5)
        if running and mem_avail_mb() < args.sacrifice_mb:
            # never let the host OOM reaper pick a PRODUCTION board (2026-10-14: 9 bench pilots pushed S2 < 1.5 GB and the
            # reaper TERM'd GALAUSDT_LONG / SKLUSDT_LONG): the bench kills its own newest pilot and requeues the side
            ss_v = max(running, key=lambda k: running[k][1])
            pr_v, _t0v, log_v = running.pop(ss_v)
            pr_v.terminate()
            try:
                pr_v.wait(timeout=60)
            except Exception:
                pr_v.kill()
            log_v.close()
            queue.insert(0, ss_v)
            print(f"[bench] MEM {mem_avail_mb():.0f} MB < {args.sacrifice_mb} -> sacrificed own {ss_v}, requeued", flush=True)
            time.sleep(120)
            continue
        for ss, (pr, t0, log) in list(running.items()):
            rc = pr.poll()
            if rc is None and time.time() - t0 > args.timeout_s:
                pr.terminate()
                rc = "TIMEOUT"
            if rc is None:
                continue
            log.close()
            sd = run_dir / ss
            prog = next(iter((sd / "progress").glob("*_v14_progress.json")), None)
            dl = next(iter((sd / "progress" / "v15_delta_log").glob("*.jsonl")), None) if (sd / "progress" / "v15_delta_log").exists() else None
            m = board_metrics(prog, dl) if prog else {"err": "no progress json"}
            m.update(rc=rc, wall_s=round(time.time() - t0, 1))
            results[ss] = m
            del running[ss]
            print(f"[bench] done {ss} rc={rc} wall={m['wall_s']} gain={m.get('final_gain')}", flush=True)
            (run_dir / "results.json").write_text(json.dumps({"snap": args.snap, "label": args.label, "code": str(code), "pilot_md5": pmd5, "set": args.set or [], "results": results}, indent=1))
    summary = {"snap": args.snap, "label": args.label, "code": str(code), "pilot_md5": pmd5, "set": args.set or [], "wall_total_s": round(time.time() - t_all, 1), "results": results}
    (run_dir / "results.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}))
    return 0


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


def cmd_compare(args):
    a = json.loads((BENCH / "runs" / f"{args.snap}__{args.a}" / "results.json").read_text())["results"]
    b = json.loads((BENCH / "runs" / f"{args.snap}__{args.b}" / "results.json").read_text())["results"]
    rows, drops, sum_a, sum_b, wa, wb, ea, eb = [], [], 0.0, 0.0, 0.0, 0.0, 0, 0
    for ss in sorted(set(a) & set(b)):
        ga, gb = _f(a[ss].get("final_gain")), _f(b[ss].get("final_gain"))
        d = (gb - ga) if (ga is not None and gb is not None) else None
        if ga is not None and gb is not None:
            sum_a += ga
            sum_b += gb
        if d is not None and d < -args.tol_pp:
            drops.append(ss)
        if gb is None and ga is not None:
            drops.append(ss + "(no result)")
        wa += a[ss].get("wall_s") or 0
        wb += b[ss].get("wall_s") or 0
        ea += a[ss].get("evals_noncached") or 0
        eb += b[ss].get("evals_noncached") or 0
        qb = []
        if (_f(b[ss].get("trades")) or 0) < 10:
            qb.append("trades<10")
        if (_f(b[ss].get("dd")) or 0) > 30:
            qb.append("dd>30")
        rows.append((ss, ga, gb, d, a[ss].get("wall_s"), b[ss].get("wall_s"), a[ss].get("evals_noncached"), b[ss].get("evals_noncached"), b[ss].get("trades"), b[ss].get("dd"), b[ss].get("tim"), b[ss].get("sharpe"), ",".join(qb)))
    print(f"{'sym_side':22s} {'gain_A':>8s} {'gain_B':>8s} {'dB-A':>7s} {'wall_A':>7s} {'wall_B':>7s} {'ev_A':>6s} {'ev_B':>6s} trades_B dd_B tim_B sharpe_B flags")
    for r in rows:
        fm = lambda v, n=2: ("—" if v is None else (f"{v:.{n}f}" if isinstance(v, float) else str(v)))
        print(f"{r[0]:22s} {fm(r[1]):>8s} {fm(r[2]):>8s} {fm(r[3]):>7s} {fm(r[4],0):>7s} {fm(r[5],0):>7s} {fm(r[6]):>6s} {fm(r[7]):>6s} {fm(r[8])} {fm(r[9])} {fm(r[10])} {fm(r[11])} {r[12]}")
    speed = (wa / wb) if wb else None
    incomplete = [ss for ss in sorted(set(a) | set(b)) if _f((a.get(ss) or {}).get("final_gain")) is None or _f((b.get(ss) or {}).get("final_gain")) is None]
    ok = (sum_b >= sum_a - args.tol_sum_pp) and not drops and not incomplete and bool(rows)
    verdict = {"sides": len(rows), "sum_gain_A": round(sum_a, 3), "sum_gain_B": round(sum_b, 3), "sum_delta": round(sum_b - sum_a, 3), "drops_gt_tol": drops, "wall_A_s": wa, "wall_B_s": wb, "speedup_x": round(speed, 3) if speed else None, "evals_A": ea, "evals_B": eb, "tol_pp": args.tol_pp, "tol_sum_pp": args.tol_sum_pp, "incomplete": incomplete, "PASS": ok}
    print(json.dumps(verdict, indent=1))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--name", required=True)
    s.add_argument("--sides", required=True)
    s.add_argument("--pid", type=int)
    s.add_argument("--env-json")
    s.add_argument("--force", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--snap", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--code", help="code tree to bench (default ~/binance-sandbox)")
    r.add_argument("--set", action="append", help="extra env K=V for this version (e.g. V15_BRAIN=1)")
    r.add_argument("--only")
    r.add_argument("--parallel", type=int, default=3)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--nice", type=int, default=10)
    r.add_argument("--timeout-s", type=float, default=3 * 3600)
    r.add_argument("--force", action="store_true")
    r.add_argument("--min-free-mb", type=float, default=10000.0, help="start a side only above this MemAvailable")
    r.add_argument("--sacrifice-mb", type=float, default=3000.0, help="below this the bench kills its own newest pilot")
    c = sub.add_parser("compare")
    c.add_argument("--snap", required=True)
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--tol-pp", type=float, default=0.5, help="a single side may not lose more than this (pp)")
    c.add_argument("--tol-sum-pp", type=float, default=0.0, help="summed gain of B may not be below A minus this")
    args = ap.parse_args(argv)
    return {"snapshot": cmd_snapshot, "run": cmd_run, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
