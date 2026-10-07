#!/usr/bin/env python3
"""v15_parity_saturator — backfill ALL spare compute with live-vs-vector parity runs.

Per server (crypto on s1, stocks on s2/s5), continuously runs tools/v15_parity_check.py
(vector evaluate_sanitized vs live-faithful backtest_v12_engine.run_one, same frozen 30D NPZ)
on its venue's sym_sides, keeping the box saturated ONLY with spare headroom — it is niced to
the lowest priority and gated on CPU/RAM so it yields to the herds and the delta/wiring forks and
fills compute the moment it frees up. Each run is a subprocess with a hard SIGKILL timeout so a
churny/hung live-faithful eval can never block the queue. Results append to
data/reports/parity/results.jsonl (ts, sym_side, vec/live gain+trades, parity_ok, reason).
Cycles the queue forever, re-checking the least-recently-run sym_side first, so parity is
continuously re-measured as the baseline (frequent-exits-off, wiring) evolves.
"""
import json, os, pathlib, re, subprocess, sys, time

ROOT = pathlib.Path.home() / "binance-sandbox"
if not ROOT.exists():
    ROOT = pathlib.Path("/Users/niels/Documents/binance")
OUT = ROOT / "data" / "reports" / "parity"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = OUT / "results.jsonl"
LOG = "/tmp/v15_parity_saturator.log"
RUN_TIMEOUT = int(os.environ.get("PARITY_RUN_TIMEOUT", "300"))  # hard SIGKILL per run
POLL = 15


def log(m):
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {m}"
    print(line, flush=True)
    try:
        open(LOG, "a").write(line + "\n")
    except Exception:
        pass


def sys_stats():
    try:
        nproc = int(subprocess.check_output(["nproc"]).decode().strip())
    except Exception:
        nproc = 4
    try:
        load1 = os.getloadavg()[0]
    except Exception:
        load1 = nproc
    avail = 0
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                avail = int(ln.split()[1]) // 1024
                break
    except Exception:
        pass
    return nproc, load1, avail


def is_crypto(symside):
    b = symside.rsplit("_", 1)[0]
    return b.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))


def load_queue():
    # venue by hostname: crypto host (niels/s1) → crypto syms, else stocks
    me = ""
    try:
        me = subprocess.check_output(["hostname"]).decode().strip().lower()
    except Exception:
        pass
    # crypto boxes: s1 (niels) and s5 (mega crypto, 10.0.0.5); stock box: s2 (htz-v15-s2)
    crypto_host = (("niels" in me and "htz" not in me) or me in ("s1", "s5")) and me != "s2" and "htz-v15-s2" not in me
    syms = []
    for q in ["SPREADSHEETS/V15_SERVER_QUEUE_S1.txt", "SPREADSHEETS/V15_SERVER_QUEUE_S2.txt", "SPREADSHEETS/V15_FULL_354.txt"]:
        p = ROOT / q
        if p.exists():
            syms += [l.strip().upper() for l in p.read_text().splitlines() if l.strip()]
    syms = list(dict.fromkeys(syms))
    venue = [s for s in syms if is_crypto(s) == crypto_host]
    # two crypto boxes (s1 + s5): order each box's own hash-shard FIRST, then the other half —
    # minimizes s1/s5 overlap in steady state but still covers everything if one box is down.
    if crypto_host:
        import zlib
        shard = 1 if me == "s5" else 0
        venue.sort(key=lambda s: (zlib.crc32(s.encode()) % 2 != shard, s))
    return venue, crypto_host


def last_run_times():
    seen = {}
    if RESULTS.exists():
        try:
            for ln in RESULTS.read_text().splitlines()[-20000:]:
                try:
                    r = json.loads(ln)
                    seen[r["sym_side"]] = r.get("ts", 0)
                except Exception:
                    pass
        except Exception:
            pass
    return seen


def record(symside, out_path, killed, rc=None):
    txt = ""
    try:
        txt = pathlib.Path(out_path).read_text()
    except Exception:
        pass
    m = re.search(r"PARITY \S+ (PASS|FAIL) :: (.+?) :: vec_gain=(\S+) vec_trades=(\S+) live_gain=(\S+) live_trades=(\S+)", txt)
    rec = {"ts": int(time.time()), "sym_side": symside, "killed": killed, "rc": rc}
    if m:
        rec.update({"parity_ok": m.group(1) == "PASS", "reason": m.group(2)[:120],
                    "vec_gain": m.group(3), "vec_trades": m.group(4),
                    "live_gain": m.group(5), "live_trades": m.group(6)})
    else:
        m2 = re.search(r"PARITY \S+ (PASS|FAIL) :: (.+?)(?: ::|$)", txt)
        if m2:
            rec.update({"parity_ok": m2.group(1) == "PASS", "reason": m2.group(2)[:120]})
        elif killed:
            rec.update({"parity_ok": None, "reason": "loop_timeout_killed"})
        elif rc in (124, 137):
            rec.update({"parity_ok": None, "reason": f"run_timeout_{RUN_TIMEOUT}s (live too slow/churn)"})
        else:
            rec.update({"parity_ok": None, "reason": "no_parity_line"})
    with open(RESULTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"[result] {symside} ok={rec.get('parity_ok')} {rec.get('reason','')}")


def main():
    max_parallel = int(os.environ.get("PARITY_MAX_PARALLEL", "3"))
    min_avail = int(os.environ.get("PARITY_MIN_AVAIL_M", "1500"))
    load_frac = float(os.environ.get("PARITY_LOAD_FRAC", "0.90"))  # only use spare headroom
    py = str(ROOT / ".venv/bin/python")
    if not pathlib.Path(py).exists():
        py = sys.executable
    venue, crypto = load_queue()
    log(f"[start] venue={'crypto' if crypto else 'stocks'} syms={len(venue)} max_par={max_parallel} timeout={RUN_TIMEOUT}s")
    running = {}  # symside -> (proc, start, out_path)
    while True:
        # reap finished / timed-out
        for ss in list(running):
            proc, start, out_path = running[ss]
            if proc.poll() is not None:
                record(ss, out_path, killed=False, rc=proc.returncode)
                del running[ss]
            elif time.time() - start > RUN_TIMEOUT:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                record(ss, out_path, killed=True)
                del running[ss]
        nproc, load1, avail = sys_stats()
        headroom = (load1 / max(1, nproc) < load_frac) and (avail > min_avail)
        while headroom and len(running) < max_parallel:
            seen = last_run_times()
            cand = sorted([s for s in venue if s not in running], key=lambda s: seen.get(s, 0))
            if not cand:
                break
            ss = cand[0]
            out_path = f"/tmp/parity_run_{ss}.out"
            # shell-level `timeout -s KILL` makes each run self-bounded even if this saturator dies
            # (no orphaned churny parity run can hang forever); the poll-loop kill below is a backup.
            # 2026-10-04 parity-harness (staged): V8_PRESERVE_DEBOUNCE=1 enables
            # BT_PRESERVE_DEBOUNCE_ACROSS_BARS in the scalar run so per-bar cooldowns
            # expire by simulated wall-clock (live cadence) instead of being wiped every
            # bar (churn: 1775 closes/900s ZENUSDT -> run_timeout_300s). Harness-only:
            # the BT_ flag is read solely inside backtest_v12_engine; live never sees it.
            cmd = f"cd {ROOT} && BASE_PATH={ROOT} V8_PRESERVE_DEBOUNCE=1 nice -n 19 timeout -s KILL {RUN_TIMEOUT} {py} -u tools/v15_parity_check.py --sym-side {ss} > {out_path} 2>&1"
            proc = subprocess.Popen(["bash", "-c", cmd], preexec_fn=os.setsid)
            running[ss] = (proc, time.time(), out_path)
            log(f"[launch] {ss} (running {len(running)}/{max_parallel} load {load1:.1f}/{nproc} avail {avail}M)")
            nproc, load1, avail = sys_stats()
            headroom = (load1 / max(1, nproc) < load_frac) and (avail > min_avail)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
