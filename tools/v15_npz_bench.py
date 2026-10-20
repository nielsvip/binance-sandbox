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
  rescore  --snap N --label L                     rebuild results.json from the run's artifacts on disk (no recompute)
  compare  --snap N --a L1 --b L2                 per-side + aggregate diff, PASS/FAIL gate
Stdlib only. Runs on S2 (or any worker with the NPZs + .venv). NO-LIES: every number is read back from the pilot's own
progress JSON and delta log of that run.
"""
import argparse
import hashlib
import json
import os
import shutil
import signal
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
ALLOWED_WINDOWS = (1, 7, 20, 30, 365)  # v15_pilot --window-days whitelist
MIN_WINDOW_COVERAGE = 0.80  # tools/opt/metrics.py: a board whose NPZ covers less of the window is INVALID
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


def find_progress(sd, fit_days=None):
    """2026-10-15: the pilot names its progress JSON {symside}_v14_progress.json ONLY when --window-days is 30
    (v15_pilot.py:12119-12123); every other window writes {symside}_{window}d_progress.json. The gate always benches
    with V15_HOLDOUT_DAYS=7 (v15_version_gate.py:119), which snaps the fit window 30 -> 20, so the pilot wrote
    *_20d_progress.json while this bench globbed only *_v14_progress.json -> every side scored {"err": "no progress
    json"} with rc=0 and the gate dropped all 4 sides as "uninformative", making PASS unreachable by construction."""
    pd = Path(sd) / "progress"
    if not pd.exists():
        return None
    pats = ([f"*_{int(fit_days)}d_progress.json"] if fit_days else []) + ["*_v14_progress.json", "*_progress.json"]
    for pat in pats:
        for p in sorted(pd.glob(pat)):
            if ".npzprev_" not in p.name and ".hollowprev_" not in p.name:
                return p
    return None


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
        ho = p.get("holdout") if isinstance(p.get("holdout"), dict) else {}
        out["oos_final"] = (ho.get("final") or {}).get("gain_pct")
        out["oos_start"] = (ho.get("start") or {}).get("gain_pct")
        out["oos_template"] = (ho.get("template") or {}).get("gain_pct")
        out["oos_final_trades"] = (ho.get("final") or {}).get("trades")
        out["oos_bh"] = ho.get("bh")
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
    out.update(evals=n_ev, evals_noncached=n_nc, eval_secs=round(secs, 1))
    try:
        out["coverage"] = board_coverage(json.loads(Path(prog_path).read_text()))
    except Exception as e:
        out["coverage"] = {"err": str(e)[:120]}
    return out


COVERAGE_SKIP_KEYS = ("coarse_deferred_filters", "zero_skipped_filters", "prior_skipped_filters", "sampled_out_filters", "excluded_unwired", "type_skipped", "missing_yellows", "chain_passed_filters")


def board_coverage(p):
    """How much of the template a board really evaluated (USER 2026-10-16: corner cutting must be exposed). Main-pass
    rows only (`TAB!n:SWITCH=cand`; V17/FOCUS/ABL bookkeeping keys excluded). A cell counts as evaluated only when it has a
    result in the row's `yellows` (painted != evaluated, BIBLE §109.7); skipped cells are booked per mechanism."""
    c = {"rows": 0, "naked_rows": 0, "rows_no_naked": 0, "cells_eval": 0, "cells_skipped": 0, "zero_yellow_rows": 0, "promoted_rows": 0}
    by_mech, no_naked = {}, {}
    for k, r in (p.get("done") or {}).items():
        if not isinstance(r, dict) or "!" not in k or ":" not in k:
            continue
        head = k.split("!")[0]
        if head.startswith(("V17", "FOCUS", "ABL", "P3", "P4")):
            continue
        c["rows"] += 1
        if r.get("naked_delta") is not None or r.get("is_running"):
            c["naked_rows"] += 1
        else:
            c["rows_no_naked"] += 1
            rs = str(r.get("reason") or "?").split(":")[0][:40]
            no_naked[rs] = no_naked.get(rs, 0) + 1
        ev = set((r.get("yellows") or {}).keys())
        skipped = set()
        for s in COVERAGE_SKIP_KEYS:
            L = set(r.get(s) or [])
            if s == "coarse_deferred_filters":
                L -= set(r.get("coarse_revived") or [])
            L -= ev
            if L:
                by_mech[s] = by_mech.get(s, 0) + len(L)
            skipped |= L
        if r.get("inert_filters") and not (r.get("inert_sampled") or r.get("inert_revived")):
            L = set(r["inert_filters"]) - ev
            if L:
                by_mech["inert_unsampled"] = by_mech.get("inert_unsampled", 0) + len(L)
            skipped |= L
        c["cells_eval"] += len(ev)
        c["cells_skipped"] += len(skipped)
        c["zero_yellow_rows"] += int(not ev)
        c["promoted_rows"] += int(bool(r.get("promoted")) and not r.get("is_running"))
    c["skipped_by_mechanism"] = by_mech
    c["no_naked_by_reason"] = dict(sorted(no_naked.items(), key=lambda kv: -kv[1])[:12])
    return c


def _write_json_atomic(path, obj):
    """tmp + os.replace: an ENOSPC mid-write leaves the previous file intact instead of a 0-byte JSON that every later
    reader crashes on (2026-10-14: a truncated meta.json killed `v15_version_gate run` on all 89 following ticks)."""
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def _pgrp_alive(pgid):
    """True while any process of the group still exists (zombies of a reaped leader do not count)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def lock_holders(lock_path):
    """Pids holding an open fd on lock_path (Linux /proc scan). A flock lives as long as ANY fd of the open file
    description exists, so a pilot's forked workers keep a sacrificed pilot's DEDUP lock alive after the parent died."""
    out = []
    target = os.path.realpath(str(lock_path))
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return out
    for d in pids:
        fd_dir = f"/proc/{d}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    if os.path.realpath(os.readlink(f"{fd_dir}/{fd}")) == target:
                        out.append(int(d))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return out


def lock_is_free(lock_path):
    import fcntl
    try:
        fh = open(lock_path, "a")
    except OSError:
        return True
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        return False
    finally:
        fh.close()


def reap_pilot(pr, grace_s=60.0, kill_wait_s=30.0):
    """2026-10-20: terminate()+wait() only signalled the pilot's leader; its workers (and the DEDUP flock fd they inherit)
    outlived it and the requeued board exited after 5 s with "another v15_pilot already owns". Signal the whole process
    group (the pilot is started in its own session), poll until the group is gone, SIGKILL after the grace period."""
    pgid = pr.pid
    t0 = time.time()
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    killed = False
    while True:
        try:
            pr.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        if not _pgrp_alive(pgid):
            break
        waited = time.time() - t0
        if not killed and waited >= grace_s:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            killed = True
        if waited >= grace_s + kill_wait_s:
            break
        time.sleep(0.3)
    return round(time.time() - t0, 1), killed


def clear_side_lock(root, ss, timeout_s=60.0):
    """Verify the sacrificed side's DEDUP lock(s) are released before it is requeued: lock files under root/data/locks
    matching the side, flock-probed; any process still holding one is SIGKILLed. Stale lock files are removed.
    Returns (cleared: bool, waited_s)."""
    t0 = time.time()
    locks = sorted((Path(root) / "data" / "locks").glob(f"v15_pilot_{ss}_*.lock"))
    while True:
        busy = [lk for lk in locks if lk.exists() and not lock_is_free(lk)]
        if not busy:
            break
        for lk in busy:
            for pid in lock_holders(lk):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if time.time() - t0 >= timeout_s:
            return False, round(time.time() - t0, 1)
        time.sleep(0.5)
    for lk in locks:
        try:
            lk.unlink()
        except OSError:
            pass
    return True, round(time.time() - t0, 1)


def cmd_run(args):
    snap = BENCH / f"snap_{args.snap}"
    man = json.loads((snap / "manifest.json").read_text())
    run_dir = BENCH / "runs" / f"{args.snap}__{args.label}"
    if run_dir.exists() and not args.force:
        print(f"run exists: {run_dir} (--force to redo)", file=sys.stderr)
        return 2
    # 2026-10-20: the free-disk gate runs BEFORE any directory is built — a refused run used to have already copied the
    # 5.4 GB snapshot (root) and left ~10.8 GB per refused gate attempt (two arms) behind, starving every later bench.
    _free_mb = shutil.disk_usage(str(BENCH)).free / (1 << 20)
    if _free_mb < args.min_disk_mb:
        print(f"[bench] REFUSED: {_free_mb:.0f} MB free on {BENCH} < {args.min_disk_mb:.0f} MB — a bench that runs out of space truncates results.json/meta.json to 0 bytes (2026-10-14 ENOSPC killed the gate)", file=sys.stderr)
        return 3
    pre_existing = run_dir.exists()
    run_dir.mkdir(parents=True, exist_ok=True)
    args._running = {}
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_a: sys.exit(143))
    try:
        rc = _cmd_run_body(args, snap, man, run_dir)
    except BaseException:
        cleanup_aborted_run(run_dir, args._running, pre_existing, args.reap_grace_s)
        raise
    finally:
        signal.signal(signal.SIGTERM, prev_term)
    if rc not in (0, None):
        cleanup_aborted_run(run_dir, args._running, pre_existing, args.reap_grace_s)
    return rc


def cleanup_aborted_run(run_dir, running, pre_existing, grace_s=60.0):
    """Remove what an aborted/refused run built. Never touches a run that has a results file or is still owned by a live pilot we cannot reap."""
    for ss, (pr, _t0, log) in list(running.items()):
        try:
            reap_pilot(pr, grace_s=grace_s)
            log.close()
        except Exception:
            pass
        running.pop(ss, None)
    run_dir = Path(run_dir)
    if not run_dir.exists():
        return
    rp = run_dir / "results.json"
    if rp.exists() and rp.stat().st_size > 0:
        shutil.rmtree(run_dir / "root", ignore_errors=True)
        print(f"[bench] aborted run kept its results.json; removed only {run_dir / 'root'}", flush=True)
        return
    shutil.rmtree(run_dir, ignore_errors=True)
    print(f"[bench] aborted run left no results: removed {run_dir}", flush=True)


def _cmd_run_body(args, snap, man, run_dir):
    code = Path(args.code or SRC)
    root = build_root(snap, run_dir, code)
    pmd5 = md5(root / "v15_pilot.py")
    env_base = {k: v for k, v in os.environ.items() if not k.startswith(ENV_KEEP_PREFIX)}
    # 2026-10-16 USER "test every version side by side against the previous version so corner cutting is exposed": the
    # gate passes the champion's recorded LAUNCH env (what production really runs) instead of the env frozen into the
    # snapshot on 10-14 — scheduler/autotune env changes otherwise never reached a bench.
    env_base.update(json.loads(Path(args.env_file).read_text()) if args.env_file else (man.get("env") or {}))
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        env_base[k] = v
    # 2026-10-15: V15_HOLDOUT_DAYS truncates the NPZ in memory at (last bar - H d), so a 30 d fit window on a ~31 d
    # snapshot leaves ~24 d of bars and evaluate_v12._validate refuses every row ("npz covers 24d of the 30d window
    # (79%) - regenerate") -> 0 promoted rows -> IMPOSSIBLE -> final_gain None on BOTH sides, so the gate could never
    # decide (2026-10-14T23:10Z candidate: champ and cand both IMPOSSIBLE on every side). The fit window must be the
    # span that survives the cut; holdout_score keeps its own H-day window for the OOS number.
    _ho = float(env_base.get("V15_HOLDOUT_DAYS") or 0)
    fit_days = int(args.window_days)
    if _ho > 0:
        _want = int(args.window_days) - int(_ho)
        # Keep the requested window while the bars surviving the cut still cover it at the validator's floor
        # (metrics.MIN_WINDOW_COVERAGE = 0.80); otherwise snap DOWN to the largest allowed window, because the pilot
        # refuses anything else ("BLOCKED: only 30/20/7/1/365 allowed"). 30 d window - 7 d holdout -> 20 d fit.
        if _want < MIN_WINDOW_COVERAGE * int(args.window_days):
            fit_days = max([w for w in ALLOWED_WINDOWS if w <= _want] or [min(ALLOWED_WINDOWS)])
        print(f"[bench] holdout {_ho:.0f}d -> fit window {fit_days}d (requested {args.window_days}d, {_want}d survives the cut, coverage floor {MIN_WINDOW_COVERAGE:.0%})", flush=True)
    sides = [s for s in man["sides"] if sym_of(s) in man["npz"]]
    if args.only:
        sides = [s for s in sides if s in set(args.only.split(","))]
    queue, results = list(sides), {}
    running = args._running
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
            cmd = [str(root / ".venv" / "bin" / "python"), "-u", "v15_pilot.py", "--sym-side", ss, "--template", str(root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / template_for(ss)), "--seq-mode", "worst2best", "--window-days", str(fit_days), "--vector-only", "--workers", str(args.workers)]
            log = open(sd / "pilot.log", "w")
            pr = subprocess.Popen(["nice", "-n", str(args.nice)] + cmd, cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            running[ss] = (pr, time.time(), log)
            print(f"[bench] start {ss} pid {pr.pid}", flush=True)
        time.sleep(5)
        if running and mem_avail_mb() < args.sacrifice_mb:
            # never let the host OOM reaper pick a PRODUCTION board (2026-10-14: 9 bench pilots pushed S2 < 1.5 GB and the
            # reaper TERM'd GALAUSDT_LONG / SKLUSDT_LONG): the bench kills its own newest pilot and requeues the side
            ss_v = max(running, key=lambda k: running[k][1])
            pr_v, _t0v, log_v = running.pop(ss_v)
            _mem_v = mem_avail_mb()
            _tree_s, _hard = reap_pilot(pr_v, grace_s=args.reap_grace_s)
            log_v.close()
            _cleared, _lock_s = clear_side_lock(root, ss_v)
            if not _cleared:
                print(f"[bench] ERROR lock of {ss_v} still held after {_lock_s}s - requeue would exit on DEDUP", flush=True)
            queue.insert(0, ss_v)
            print(f"[bench] MEM {_mem_v:.0f} MB < {args.sacrifice_mb} -> sacrificed own {ss_v}, requeued", flush=True)
            print(f"[bench] requeue {ss_v} after {_tree_s + _lock_s:.1f}s ({'SIGKILL' if _hard else 'SIGTERM'} tree gone in {_tree_s}s), lock {'cleared' if _cleared else 'NOT cleared'}", flush=True)
            time.sleep(120)
            continue
        for ss, (pr, t0, log) in list(running.items()):
            rc = pr.poll()
            if rc is None and time.time() - t0 > args.timeout_s:
                reap_pilot(pr, grace_s=args.reap_grace_s)
                rc = "TIMEOUT"
            if rc is None:
                continue
            log.close()
            sd = run_dir / ss
            prog = find_progress(sd, fit_days)
            dl = next(iter((sd / "progress" / "v15_delta_log").glob("*.jsonl")), None) if (sd / "progress" / "v15_delta_log").exists() else None
            m = board_metrics(prog, dl) if prog else {"err": "no progress json", "progress_dir_had": sorted(p.name for p in (sd / "progress").glob("*")) if (sd / "progress").exists() else None}
            m.update(rc=rc, wall_s=round(time.time() - t0, 1))
            results[ss] = m
            del running[ss]
            print(f"[bench] done {ss} rc={rc} wall={m['wall_s']} gain={m.get('final_gain')}", flush=True)
            _write_json_atomic(run_dir / "results.json", {"snap": args.snap, "label": args.label, "code": str(code), "pilot_md5": pmd5, "set": args.set or [], "window_days_fit": fit_days, "holdout_days": _ho, "results": results})
    summary = {"snap": args.snap, "label": args.label, "code": str(code), "pilot_md5": pmd5, "set": args.set or [], "window_days_fit": fit_days, "holdout_days": _ho, "wall_total_s": round(time.time() - t_all, 1), "results": results}
    _write_json_atomic(run_dir / "results.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}))
    return 0


def cmd_rescore(args):
    """Rebuild results.json for a finished run from the artifacts already on disk — no board is recomputed. Recovers
    the runs whose sides were mis-scored as "no progress json" by the pre-2026-10-15 glob; rc/wall_s are preserved."""
    run_dir = BENCH / "runs" / f"{args.snap}__{args.label}"
    rp = run_dir / "results.json"
    old = json.loads(rp.read_text()) if rp.exists() else {"snap": args.snap, "label": args.label}
    fit_days = args.window_days or old.get("window_days_fit")
    prev = old.get("results") or {}
    sides = sorted(set(prev) | {d.name for d in run_dir.iterdir() if d.is_dir() and (d / "progress").exists()})
    results = {}
    for ss in sides:
        sd = run_dir / ss
        prog = find_progress(sd, fit_days)
        dl = next(iter((sd / "progress" / "v15_delta_log").glob("*.jsonl")), None) if (sd / "progress" / "v15_delta_log").exists() else None
        m = board_metrics(prog, dl) if prog else {"err": "no progress json"}
        m.update(rc=(prev.get(ss) or {}).get("rc"), wall_s=(prev.get(ss) or {}).get("wall_s"), rescored_from=prog.name if prog else None)
        results[ss] = m
        print(f"[rescore] {ss} gain={m.get('final_gain')} oos={m.get('oos_final')} rows={m.get('rows')} from={m.get('rescored_from')}", flush=True)
    out = dict(old)
    out.update(results=results, window_days_fit=fit_days, rescored_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if rp.exists():
        shutil.copy2(rp, rp.with_name(f"results.json.pre_rescore_{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"))
    _write_json_atomic(rp, out)
    print(json.dumps({k: v for k, v in out.items() if k != "results"}))
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
    oa = [(_f(a[ss].get("oos_final")), _f(b[ss].get("oos_final")), _f(b[ss].get("oos_start")), _f(b[ss].get("oos_template"))) for ss in sorted(set(a) & set(b))]
    oa = [x for x in oa if x[0] is not None and x[1] is not None]
    if oa:
        print(f"OOS (unseen holdout days): sides {len(oa)}  sum final A {sum(x[0] for x in oa):+.3f}  sum final B {sum(x[1] for x in oa):+.3f}  B-A {sum(x[1]-x[0] for x in oa):+.3f}  B beats A on {sum(1 for x in oa if x[1] > x[0] + 1e-9)}  B final-start {sum((x[1]-(x[2] or 0)) for x in oa):+.3f}  B final-template {sum((x[1]-(x[3] or 0)) for x in oa):+.3f}")
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
    r.add_argument("--env-file", help="JSON launch env replacing the snapshot's frozen env (the gate passes the champion/candidate env)")
    r.add_argument("--only")
    r.add_argument("--parallel", type=int, default=3)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--nice", type=int, default=10)
    r.add_argument("--timeout-s", type=float, default=3 * 3600)
    r.add_argument("--force", action="store_true")
    r.add_argument("--min-free-mb", type=float, default=10000.0, help="start a side only above this MemAvailable")
    r.add_argument("--reap-grace-s", type=float, default=60.0, help="seconds a sacrificed/timed-out pilot tree gets after SIGTERM before SIGKILL")
    r.add_argument("--sacrifice-mb", type=float, default=3000.0, help="below this the bench kills its own newest pilot")
    r.add_argument("--window-days", type=int, default=30, help="requested fit window; a holdout shortens it to window-holdout so the snapshot still covers it")
    r.add_argument("--min-disk-mb", type=float, default=20000.0, help="refuse to start below this free disk (ENOSPC truncates results.json/meta.json)")
    rs = sub.add_parser("rescore")
    rs.add_argument("--snap", required=True)
    rs.add_argument("--label", required=True)
    rs.add_argument("--window-days", type=int, help="fit window of that run (default: window_days_fit from results.json)")
    c = sub.add_parser("compare")
    c.add_argument("--snap", required=True)
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--tol-pp", type=float, default=0.5, help="a single side may not lose more than this (pp)")
    c.add_argument("--tol-sum-pp", type=float, default=0.0, help="summed gain of B may not be below A minus this")
    args = ap.parse_args(argv)
    return {"snapshot": cmd_snapshot, "run": cmd_run, "rescore": cmd_rescore, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
