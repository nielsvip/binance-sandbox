#!/usr/bin/env python3
"""per_key_priority_sweep.py — durable PER-KEY full-param-sweep DRIVER (2026-07-06).

User mandate: stop pooled-discovery pilot sweeps (8-symbol pools), redirect ALL pilot
compute to PER-KEY: for EVERY tradeable key (SYMBOL_SIDE), sweep EVERY sweepable param
in the WIDE manifest (627 crypto / 748 tradier) on that single symbol+side, producing
per-key best settings + results. REUSES tools/pilot_range_finder.py unmodified — that
tool already does the actual single-key OFAT sweep via the FOTEST Tier-2 engine and is
durable at the per-cell level (data/pilot_progress/<KEY>/<param>__<value>/result.json).
This driver only supplies: (1) the PRIORITY-ORDERED key queue, (2) sequential dispatch
so only ONE key's manifest is in flight at a time (memory-safe, coexists with the
alternating_grinder), (3) completion detection so finished keys are skipped on restart,
(4) closed-loop ingestion of each finished key into the central results DB.

DIAGNOSTIC ONLY (IMPOSTER BLOCK, CLAUDE.md): every key here is n_syms=1. Never
auto-promoted to any live override path. pilot_range_finder.py already tags its output
_meta.DIAGNOSTIC = "single-symbol range-finder — NOT for promotion"; the summary row
this driver appends to data/_diagnostic/pilot_perkey_summary.csv repeats that tag and
lives under data/_diagnostic/ per the imposter-block mandate (per-sym diagnostics MUST
be in data/_diagnostic/, NOT discoverable by the live override loader).

Queue order: (1) gainmo-priority crypto keys + gainmo-priority tradier keys, ROUND-
ROBIN interleaved so both systems' top-gain/mo symbols get swept first (2026-07 task
#7 producer: data/gainmo_priority_{crypto,tradier}.txt), THEN (2) every remaining
tradeable key (crypto: tradeable_keys.json ∪ per_sym_active_config.json; stocks:
symbols_{trb,trc}_{long,short}.json), same round-robin interleave, alphabetical within
each side. Crypto and tradier are NEVER mixed into one engine invocation — each key
carries its own mode/manifest/account/engine selection.

Usage (normally launched by tools/pilot_watchdog.sh, not by hand):
  python3 tools/per_key_priority_sweep.py --max-par 2 --min-avail 5000
  python3 tools/per_key_priority_sweep.py --dry-run           # print queue, exit
  python3 tools/per_key_priority_sweep.py --limit-keys 3       # process only first N (testing)
"""
import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SBX = Path("/home/niels/binance-sandbox")
PY = "/home/niels/.conda/envs/binance_env/bin/python"
DATA = SBX / "data"
DIAG_DIR = DATA / "_diagnostic"
LOGDIR = Path("/home/niels/logs")
LOCK_PATH = DATA / "per_key_priority_sweep.lock"
STATUS_PATH = DATA / "per_key_priority_sweep_status.json"
SUMMARY_CSV = DIAG_DIR / "pilot_perkey_summary.csv"
CENTRAL_DB = DATA / "test_results_central.db"

MANIFEST = {"crypto": DATA / "ofat_manifest_crypto_wide.json", "tradier": DATA / "ofat_manifest_tradier_wide.json"}
DEFAULT_START = "2026-04-01"
SUMMARY_COLS = ["settings", "overrides_json", "symbol", "side", "mode", "account", "trades", "pool_sharpe", "acc_gain_pct",
                "gain_vs_bh", "years", "n_syms", "n_params", "n_moved", "tag", "source_file", "ts_ingested"]


def log(msg):
    print(f"[per_key_sweep] {msg}", flush=True)


def load_json(path):
    return json.loads(Path(path).read_text())


def free_mb():
    try:
        for ln in subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines():
            if ln.startswith("Mem:"):
                return int(ln.split()[6])
    except Exception:
        pass
    return 0


def disk_avail_mb(path="/home"):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize // (1024 * 1024)
    except Exception:
        return 999999


def sweepable_param_names(mode):
    man = load_json(MANIFEST[mode])["params"]
    return {n for n, v in man.items() if v.get("sweepable") and v.get("test_values")}


def build_crypto_keys():
    """SYMBOL_SIDE -> account, unioned from tradeable_keys.json (account:SYMBOL_SIDE
    entries, first-seen account wins) and per_sym_active_config.json (fallback acct=ang)."""
    key_account = {}
    tk = load_json(SBX / "tradeable_keys.json")
    for entry in tk:
        if ":" not in entry:
            continue
        acct, rest = entry.split(":", 1)
        key_account.setdefault(rest, acct)
    psac_path = DATA / "hourly_reconfig" / "per_sym_active_config.json"
    if psac_path.exists():
        for k in load_json(psac_path):
            if k.startswith("_"):
                continue
            key_account.setdefault(k, "ang")
    return key_account


def build_stock_keys():
    """SYMBOL_SIDE -> account, from symbols_{trb,trc}_{long,short}.json. trb wins on
    overlap (trb = core stocks book per project convention)."""
    key_account = {}
    files = {("trb", "LONG"): "symbols_trb_long.json", ("trb", "SHORT"): "symbols_trb_short.json",
             ("trc", "LONG"): "symbols_trc_long.json", ("trc", "SHORT"): "symbols_trc_short.json"}
    for (acct, side), fname in files.items():
        p = SBX / fname
        if not p.exists():
            continue
        for sym in load_json(p):
            key_account.setdefault(f"{sym}_{side}", acct)
    return key_account


def priority_keys(gainmo_file, key_account):
    if not Path(gainmo_file).exists():
        return []
    syms = [ln.strip() for ln in Path(gainmo_file).read_text().splitlines() if ln.strip()]
    out = []
    for sym in syms:
        for side in ("LONG", "SHORT"):
            k = f"{sym}_{side}"
            if k in key_account and k not in out:
                out.append(k)
    return out


def build_queue():
    crypto_acct = build_crypto_keys()
    stock_acct = build_stock_keys()
    crypto_pri = priority_keys(DATA / "gainmo_priority_crypto.txt", crypto_acct)
    stock_pri = priority_keys(DATA / "gainmo_priority_tradier.txt", stock_acct)
    crypto_rest = sorted(k for k in crypto_acct if k not in crypto_pri)
    stock_rest = sorted(k for k in stock_acct if k not in stock_pri)
    queue = []
    for c, t in itertools.zip_longest(crypto_pri, stock_pri):
        if c is not None:
            queue.append(("crypto", c, crypto_acct[c], "priority"))
        if t is not None:
            queue.append(("tradier", t, stock_acct[t], "priority"))
    for c, t in itertools.zip_longest(crypto_rest, stock_rest):
        if c is not None:
            queue.append(("crypto", c, crypto_acct[c], "remaining"))
        if t is not None:
            queue.append(("tradier", t, stock_acct[t], "remaining"))
    return queue


def out_path_for(key):
    return DATA / f"pilot_ranges_{key}.json"


def key_is_complete(mode, key, required):
    p = out_path_for(key)
    if not p.exists():
        return False
    try:
        d = load_json(p)
    except Exception:
        return False
    have = set(d.get("switches", {}).keys())
    missing = required - have
    return len(missing) == 0


def already_running(out_path):
    try:
        r = subprocess.run(["pgrep", "-f", f"pilot_range_finder.py.*{out_path.name}"],
                            capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return False


def write_status(**kw):
    kw["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        STATUS_PATH.write_text(json.dumps(kw, indent=1))
    except Exception:
        pass


def ingest_key_result(mode, symbol, side, account, out_path, start_date):
    """Append a diagnostic per-key summary row (canonical-ish columns, n_syms=1, tagged)
    to data/_diagnostic/pilot_perkey_summary.csv, then invoke build_test_results_db.py
    so the FULL_PARAM_MATRIX xls + gainmo producer pick it up (closed loop)."""
    try:
        d = load_json(out_path)
    except Exception as e:
        log(f"ingest: cannot read {out_path}: {e}")
        return
    meta = d.get("_meta", {})
    bh_pct = meta.get("bh_pct")
    baseline_pnl = meta.get("baseline_pnl")
    baseline_sharpe = meta.get("baseline_sharpe")
    n_params = meta.get("n_params")
    switches = d.get("switches", {})
    n_moved = sum(1 for r in switches.values() if r.get("moved"))
    gain_vs_bh = None
    if baseline_pnl is not None and bh_pct is not None and bh_pct > 0:
        gain_vs_bh = round(baseline_pnl / bh_pct, 3)
    trades = None
    base_result = DATA / "pilot_progress" / f"{symbol}_{side}" / "__BASELINE__" / "result.json"
    if base_result.exists():
        try:
            trades = load_json(base_result).get("trades")
        except Exception:
            pass
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        years = round((datetime.now(timezone.utc) - start_dt).days / 365.25, 3)
    except Exception:
        years = None
    row = {
        "symbol": symbol, "side": side, "mode": mode, "account": account,
        "trades": trades, "pool_sharpe": baseline_sharpe, "acc_gain_pct": baseline_pnl,
        "gain_vs_bh": gain_vs_bh, "years": years, "n_syms": 1, "n_params": n_params,
        "n_moved": n_moved, "tag": "[DIAGNOSTIC ONLY · n_syms=1] pilot_per_key_ofat",
        "source_file": str(out_path), "ts_ingested": datetime.now(timezone.utc).isoformat(),
    }
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not SUMMARY_CSV.exists()
    # Bible 12.5: settings columns. Rotate an old-header CSV so the new header applies.
    try:
        import sys as _s
        _s.path.insert(0, str(SBX / "tools"))
        import provenance_lib as _pl
        _ovr = row.get("overrides_json") or "{}"
        import json as _j
        _prov = {"effective_diff": _j.loads(_ovr) if isinstance(_ovr, str) else _ovr, "stamp": _pl.stamp(str(SBX)), "window": {"start": row.get("start", "?")}}
        row["settings"] = _pl.compact_settings(_prov)
        row.setdefault("overrides_json", _ovr)
    except Exception:
        row.setdefault("settings", "settings_unknown=true")
        row.setdefault("overrides_json", "")
    if SUMMARY_CSV.exists():
        try:
            first = open(SUMMARY_CSV).readline()
            if "settings" not in first:
                SUMMARY_CSV.rename(SUMMARY_CSV.with_suffix(".pre125.csv"))
                new_file = True
        except Exception:
            pass
    with open(SUMMARY_CSV, "a", newline="") as fh:
        import csv
        w = csv.DictWriter(fh, fieldnames=SUMMARY_COLS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)
    try:
        subprocess.run([PY, str(SBX / "tools" / "build_test_results_db.py"), "--db", str(CENTRAL_DB),
                        "--sources", str(DIAG_DIR), "--host-label", "s1"],
                       cwd=str(SBX), capture_output=True, text=True, timeout=300)
        log(f"ingested {symbol}_{side} into {CENTRAL_DB.name}")
    except Exception as e:
        log(f"ingest DB call failed for {symbol}_{side}: {e}")


def run_key(mode, key, account, start, max_par, min_avail, timeout, queue_pos, total):
    symbol, side = key.rsplit("_", 1)
    out_path = out_path_for(key)
    if already_running(out_path):
        log(f"[{queue_pos}/{total}] {key} already has a running pilot_range_finder — waiting")
        while already_running(out_path):
            time.sleep(30)
        return
    while free_mb() < min_avail:
        log(f"waiting for memory (free={free_mb()}MB < {min_avail}MB) before starting {key}")
        time.sleep(30)
    if disk_avail_mb() < 3000:
        log(f"LOW DISK ({disk_avail_mb()}MB avail) — skipping new key launch this cycle, will retry")
        time.sleep(300)
        return
    cmd = [PY, "-u", str(SBX / "tools" / "pilot_range_finder.py"),
           "--mode", mode, "--account", account, "--symbol", symbol, "--side", side,
           "--start", start, "--manifest", str(MANIFEST[mode]),
           "--max-par", str(max_par), "--min-avail", str(min_avail),
           "--timeout", str(timeout), "--out", str(out_path)]
    log(f"[{queue_pos}/{total}] START {mode} {key} account={account} -> {out_path.name}")
    write_status(current_key=key, mode=mode, account=account, queue_pos=queue_pos,
                 total_keys=total, state="running", out_file=str(out_path))
    t0 = time.time()
    logf = LOGDIR / f"pilot_perkey_{key}.log"
    with open(logf, "a") as lf:
        lf.write(f"\n=== driver launch {datetime.now(timezone.utc).isoformat()} ===\n")
        lf.flush()
        r = subprocess.run(cmd, cwd=str(SBX), stdout=lf, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0
    log(f"[{queue_pos}/{total}] pilot_range_finder exited rc={r.returncode} for {key} after {elapsed:.0f}s")
    if out_path.exists():
        try:
            ingest_key_result(mode, symbol, side, account, out_path, start)
        except Exception as e:
            log(f"ingest failed for {key}: {e}")
    write_status(current_key=key, mode=mode, account=account, queue_pos=queue_pos,
                 total_keys=total, state="finished_cycle", out_file=str(out_path), elapsed_s=elapsed)


def acquire_lock():
    import fcntl
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another instance holds the lock — exiting")
        sys.exit(0)
    return fh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-par", type=int, default=2)
    ap.add_argument("--min-avail", type=int, default=5000)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-keys", type=int, default=0)
    a = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    queue = build_queue()
    if a.limit_keys:
        queue = queue[: a.limit_keys]
    log(f"queue built: {len(queue)} keys total")
    for i, (mode, key, acct, tier) in enumerate(queue[:15], 1):
        log(f"  #{i} [{tier}] {mode} {key} (account={acct})")
    if a.dry_run:
        return
    lock_fh = acquire_lock()  # noqa: F841  (kept alive for process lifetime)
    required = {"crypto": sweepable_param_names("crypto"), "tradier": sweepable_param_names("tradier")}
    total = len(queue)
    while True:
        pending = 0
        for i, (mode, key, acct, tier) in enumerate(queue, 1):
            if key_is_complete(mode, key, required[mode]):
                continue
            pending += 1
            run_key(mode, key, acct, a.start, a.max_par, a.min_avail, a.timeout, i, total)
        if pending == 0:
            log("ALL KEYS COMPLETE for current queue snapshot — rebuilding queue (gainmo priorities may have shifted) and sleeping 30min")
            write_status(state="idle_all_complete", total_keys=total)
            time.sleep(1800)
            queue = build_queue()
            total = len(queue)


if __name__ == "__main__":
    main()
