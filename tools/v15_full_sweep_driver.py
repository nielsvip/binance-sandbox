#!/usr/bin/env python3
"""Full-universe 30D sweep driver (USER 2026-09-29): worst-first BY SYMBOL, disabled symbols first.
Each symbol runs LONG+SHORT TOGETHER (concurrent — the symbol's NPZ is shared via OS page cache, so it
is not re-read from disk for the second side). Per-side base policy: a now-disabled (neg/zero) side is a
COMPLETE RESET to the cat_side template defaults (V15_TEMPLATE_DEFAULTS=1); every other side is FRESH
best-base (V15_FRESH_RUN=1). Live parity at DONE is skipped on servers (V15_SKIP_LIVE_AT_DONE=1) —
parity forward-tests run on the Mac. Saturates the box (keep >85% CPU). --shard i/n splits crypto s1/s5.

One pass = one complete 30D run of all sym_sides. After a pass: recalc v15_avg_delta -> rebuild
templates -> run again (pass 2) -> 365D. Sheets land in the default SPREADSHEETS/V15_V16_CELL_BY_CELL
(harvest path) and progress in data/reports/lifecycle_pilot (avg_delta path).
"""
import argparse, os, subprocess, time, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv" / "bin" / "python"
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def is_crypto(sym):
    return sym.endswith(CRYPTO_SUFFIX)


def tmpl(sym, side):
    v = "CRYPTO" if is_crypto(sym) else "STOCKS"
    return ROOT / "SPREADSHEETS" / f"TEMPLATE_{v}_{side}.xlsx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True)
    ap.add_argument("--disabled-set", required=True)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--max-parallel", type=int, default=3, help="concurrent SYMBOLS (each = up to 2 pilots)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--progress-dir", default=str(pathlib.Path.home() / "v15_run1_20260929" / "progress"),
                    help="ISOLATED fresh progress dir — empty => the pilot's ALREADY-FINISHED guard does not fire, so every side reruns fresh on the new template (bump per pipeline pass)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all-cols", action="store_true", help="USER 2026-09-30: every filter column per row (s5 complete-sheet run)")
    a = ap.parse_args()
    os.makedirs(a.progress_dir, exist_ok=True)
    # singleton per host — race-proof against the */5 cron relaunch (pgrep guard alone is not atomic)
    import fcntl
    global _DRV_LOCK
    _DRV_LOCK = open("/tmp/v15_full_sweep_driver.lock", "w")
    try:
        fcntl.flock(_DRV_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[fullsweep] another driver holds the lock — exiting", flush=True)
        return
    _DRV_LOCK.write(str(os.getpid()))
    _DRV_LOCK.flush()
    i, n = (int(x) for x in a.shard.split("/"))
    syms = [l.strip() for l in open(a.order) if l.strip()][i::n]
    disabled = set(l.strip() for l in open(a.disabled_set) if l.strip())
    print(f"[fullsweep] {len(syms)} symbols (shard {a.shard}) max_parallel_symbols={a.max_parallel} workers={a.workers}", flush=True)
    if a.dry_run:
        for s in syms[:10]:
            for side in ("LONG", "SHORT"):
                ss = f"{s}_{side}"
                print(f"  {ss} {'DEFAULTS' if ss in disabled else 'bestbase'} {tmpl(s, side).name}")
        return

    def launch_side(sym, side):
        ss = f"{sym}_{side}"
        if subprocess.run(["pgrep", "-f", f"v15_pilot.py --sym-side {ss} "], capture_output=True).returncode == 0:
            return None
        env = dict(os.environ, V15_FRESH_RUN="1", V15_INGEST_BEST="1", V15_ADAPT_BASELINE="1", V15_SKIP_LIVE_AT_DONE="1", V12_NPZ_CACHE="8", V15_PROGRESS_DIR=a.progress_dir)
        if a.all_cols:
            env["V15_ALL_FILTER_COLS"] = "1"
        if ss in disabled and os.environ.get("V15_DISABLED_TEMPLATE_DEFAULTS") == "1":
            env["V15_TEMPLATE_DEFAULTS"] = "1"
        cmd = [str(VENV), "-u", str(ROOT / "v15_pilot.py"), "--sym-side", ss, "--template", str(tmpl(sym, side)),
               "--seq-mode", "worst2best", "--window-days", str(a.window_days), "--vector-only", "--workers", str(a.workers)]
        lf = open(f"/tmp/sweep_{ss}.log", "w")
        return subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=str(ROOT))

    running, todo, done = {}, list(syms), 0
    while todo or running:
        for sym, pps in list(running.items()):
            if all(p.poll() is not None for p in pps):
                done += 1
                del running[sym]
                print(f"[fullsweep] SYM done {sym} ({done} done, {len(todo)} todo, {len(running)} running)", flush=True)
        while todo and len(running) < a.max_parallel:
            sym = todo.pop(0)
            pps = [p for p in (launch_side(sym, "LONG"), launch_side(sym, "SHORT")) if p]
            if pps:
                running[sym] = pps
                tag = "[DEFAULTS-side]" if any(f"{sym}_{s}" in disabled for s in ("LONG", "SHORT")) else ""
                print(f"[fullsweep] LAUNCH {sym} L+S ({len(pps)} pilots) {tag}", flush=True)
        time.sleep(5)
    # pass-complete marker (next to the iso progress dir) — the wrapper stops relaunching this pass and
    # the pipeline controller advances run1 -> avg_delta -> rebuild -> run2. Shard-aware: mark per shard.
    try:
        mk = pathlib.Path(a.progress_dir).parent / f"PASS_COMPLETE_shard{i}of{n}"
        mk.write_text(f"{done} symbols {a.shard}\n")
    except Exception as _e:
        print(f"[fullsweep] marker warn {_e}", flush=True)
    print(f"[fullsweep] PASS COMPLETE {done} symbols (marker {mk})", flush=True)


if __name__ == "__main__":
    main()
