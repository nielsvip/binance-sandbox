#!/usr/bin/env python3
"""Focused FRESH reset-to-defaults sweep of the DISABLED (neg/zero-trade) sym_sides, worst-first.

USER 2026-09-29: the now-disabled sides are the priority for the current run on the NEW reordered
templates. Neg results start from a COMPLETE RESET to the cat_side template defaults (bold col-B) —
NOT the previous losing "winning set" — so each explores fresh from defaults on the better template.
Mechanism: v15_pilot with V15_FRESH_RUN=1 + V15_TEMPLATE_DEFAULTS=1 (line 3509: overrides={} ->
template/live-config defaults only). Deliberately ignores is_complete so it reprocesses the stale
30D sheets (old data already archived to Mac V15_MAC_DONE; the fresh run overwrites atomically).

Per-side pilot cmd mirrors the herd (line 603): --template <cat_side> --seq-mode worst2best
--window-days 30 --vector-only --workers W, under V12_NPZ_CACHE=8. --shard i/n splits the list
disjointly across s1/s5 (both worst-first interleaved) so the two crypto boxes never run the same side.
"""
import argparse, os, subprocess, time, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv" / "bin" / "python"
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def is_crypto(ss):
    return ss.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIX)


def tmpl_for(ss):
    venue = "CRYPTO" if is_crypto(ss) else "STOCKS"
    side = "LONG" if ss.endswith("_LONG") else "SHORT"
    return ROOT / "SPREADSHEETS" / f"TEMPLATE_{venue}_{side}.xlsx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True)
    ap.add_argument("--max-parallel", type=int, default=5)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--progress-dir", default=str(pathlib.Path.home() / "v15_fresh_20260929" / "progress"))
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    i, n = (int(x) for x in a.shard.split("/"))
    sides = [l.strip() for l in open(a.list) if l.strip()][i::n]
    os.makedirs(a.progress_dir, exist_ok=True)
    print(f"[driver] {len(sides)} sides (shard {a.shard}) max_parallel={a.max_parallel} workers={a.workers} progress={a.progress_dir}", flush=True)
    if a.dry_run:
        for s in sides[:12]:
            print(f"  would run {s} tmpl={tmpl_for(s).name}")
        return
    running, todo, done, failed = {}, list(sides), [], []
    while todo or running:
        for ss, pp in list(running.items()):
            rc = pp.poll()
            if rc is not None:
                (done if rc == 0 else failed).append(ss)
                print(f"[driver] {'OK' if rc == 0 else 'FAIL rc=' + str(rc)} {ss} ({len(done)} done {len(failed)} fail {len(todo)} todo)", flush=True)
                del running[ss]
        while todo and len(running) < a.max_parallel:
            ss = todo.pop(0)
            if subprocess.run(["pgrep", "-f", f"v15_pilot.py --sym-side {ss} "], capture_output=True).returncode == 0:
                print(f"[driver] SKIP {ss} (pilot already running)", flush=True)
                continue
            env = dict(os.environ, V15_FRESH_RUN="1", V15_TEMPLATE_DEFAULTS="1", V15_PROGRESS_DIR=a.progress_dir, V12_NPZ_CACHE="8")
            cmd = [str(VENV), "-u", str(ROOT / "v15_pilot.py"), "--sym-side", ss, "--template", str(tmpl_for(ss)),
                   "--seq-mode", "worst2best", "--window-days", str(a.window_days), "--vector-only", "--workers", str(a.workers)]
            lf = open(f"/tmp/fresh_{ss}.log", "w")
            pp = subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=str(ROOT))
            running[ss] = pp
            print(f"[driver] LAUNCH {ss} pid={pp.pid} tmpl={tmpl_for(ss).name}", flush=True)
        time.sleep(5)
    print(f"[driver] DONE {len(done)} ok, {len(failed)} failed: {failed[:30]}", flush=True)


if __name__ == "__main__":
    main()
