#!/usr/bin/env python3
"""auto_promote.py — Tier-2 → Tier-3 candidate validator.

Watches autonomous_search worker outputs across MacBook + S1 + S2.
Picks candidates that pass CLAUDE.md decision-grade thresholds, runs each through
backtest_v8_engine.py (the REAL test calling actual live functions) on full data,
and writes promotion candidates to data/auto_promote/ for user review.

NEVER auto-applies to live config. Surface only.
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
DEFAULT_OUT_DIR = REPO / "data" / "auto_promote"
PY_LOCAL = "/opt/anaconda3/envs/binance_env/bin/python"

THRESH = {
    "tradier": {
        "min_pool_sharpe": 1.0,
        "min_trades": 200,
        "max_dd_pct": 35.0,
        "min_gain_per_yr": 20.0,
        "min_sym_sharpe": 0.5,
    },
    "crypto": {
        "min_pool_sharpe": 1.0,
        "min_trades": 200,
        "max_dd_pct": 40.0,
        "min_gain_per_yr": 30.0,
        "min_sym_sharpe": 0.5,
    },
}

VAL_PROFILES = {
    "smoke":  {"start": "2025-10-01", "n_syms": 12,  "timeout": 600},
    "medium": {"start": "2024-01-01", "n_syms": 48,  "timeout": 1800},
    "full":   {"start": "2022-01-01", "n_syms": 0,   "timeout": 3600},
}

CRYPTO_VAL_SYMS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOTUSDT,AVAXUSDT,MATICUSDT,LINKUSDT,UNIUSDT,LTCUSDT"
TRADIER_VAL_SYMS = "AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg, *, file=sys.stdout):
    print(f"[{utcnow()}] {msg}", file=file, flush=True)


def hash_overrides(overrides):
    s = json.dumps(overrides, sort_keys=True)
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def collect_remote_csvs(host, remote_root, local_dest):
    local_dest.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-az", "--update",
           "--include=*/", "--include=autonomous_*.csv", "--exclude=*",
           f"{host}:{remote_root}/", str(local_dest) + "/"]
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        log(f"rsync {host} failed: {e.stderr.decode()[:200] if e.stderr else e}", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        log(f"rsync {host} timeout", file=sys.stderr)
        return False


def parse_csv_for_winners(csv_path, mode, threshold, seen_hashes):
    out = []
    if not csv_path.exists() or csv_path.stat().st_size < 200:
        return out
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ps = float(row.get("pool_sharpe") or 0)
                    trades = int(row.get("trades") or 0)
                    dd = float(row.get("max_dd_pct") or 0)
                    gain_yr = float(row.get("gain_per_yr") or 0)
                    sym_s = float(row.get("sym_sharpe") or 0)
                    overrides_str = row.get("overrides_json") or "{}"
                    if not overrides_str.strip().startswith("{"):
                        continue
                    overrides = json.loads(overrides_str)
                except (ValueError, json.JSONDecodeError):
                    continue
                if ps < threshold["min_pool_sharpe"]: continue
                if trades < threshold["min_trades"]: continue
                if dd > threshold["max_dd_pct"]: continue
                if gain_yr < threshold["min_gain_per_yr"]: continue
                if sym_s < threshold["min_sym_sharpe"]: continue
                if not overrides: continue
                h = hash_overrides(overrides)
                if h in seen_hashes: continue
                seen_hashes.add(h)
                out.append({
                    "hash": h,
                    "mode": mode,
                    "pool_sharpe": ps,
                    "sym_sharpe": sym_s,
                    "trades": trades,
                    "max_dd_pct": dd,
                    "gain_per_yr": gain_yr,
                    "acc_gain_pct": float(row.get("acc_gain_pct") or 0),
                    "avg_gain_trade": float(row.get("avg_gain_trade") or 0),
                    "deflated_sharpe": float(row.get("deflated_sharpe") or 0),
                    "src_csv": str(csv_path),
                    "src_iter": int(row.get("iter") or -1),
                    "overrides": overrides,
                })
    except Exception as e:
        log(f"parse {csv_path}: {e}", file=sys.stderr)
    return out


def run_v8_validation(mode, baseline_path, overrides, profile, work_dir):
    profile_cfg = VAL_PROFILES[profile]
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(baseline_path) as f:
        merged = json.load(f)
    merged.update(overrides)
    override_path = work_dir / "merged_override.json"
    with open(override_path, "w") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
    syms = ""
    if profile_cfg["n_syms"] and profile_cfg["n_syms"] <= 12:
        syms = CRYPTO_VAL_SYMS if mode == "crypto" else TRADIER_VAL_SYMS
    elif profile_cfg["n_syms"] == 48:
        symfile = REPO / "backtest_48_symbols.json"
        if symfile.exists():
            with open(symfile) as f:
                slist = json.load(f)
            syms = ",".join(slist[:48])
    log_path = work_dir / "v8_run.log"
    cmd = [PY_LOCAL, "-u", str(REPO / "backtest_v8_engine.py"),
           "--mode", mode,
           "--account", "ang" if mode == "crypto" else "trb",
           "--start", profile_cfg["start"],
           "--capital", "1000" if mode == "crypto" else "70000"]
    if syms:
        cmd += ["--symbols", syms]
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    env["V8_SKIP_PROCESS_POSITION"] = "1"
    env["V8_RATE_GUARD_DISABLED"] = "1"
    t0 = time.time()
    try:
        with open(log_path, "w") as lf:
            subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT,
                           timeout=profile_cfg["timeout"], cwd=str(REPO))
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "elapsed_s": time.time() - t0, "log": str(log_path)}
    except Exception as e:
        return {"status": "error", "error": str(e), "elapsed_s": time.time() - t0, "log": str(log_path)}
    result = {"status": "done", "elapsed_s": time.time() - t0, "log": str(log_path)}
    try:
        with open(log_path) as f:
            for line in f:
                if "V8_RESULT:" in line:
                    parts = line.split("V8_RESULT:", 1)[1].strip().split()
                    for p in parts:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            try:
                                result[f"v8_{k}"] = float(v)
                            except ValueError:
                                pass
    except Exception:
        pass
    return result


def write_candidate(out_dir, candidate, validation):
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"candidate_{candidate['mode']}_{ts}_{candidate['hash']}.json"
    rec = {
        "discovered_at": utcnow(),
        "mode": candidate["mode"],
        "tier2_metrics": {
            "pool_sharpe": candidate["pool_sharpe"],
            "sym_sharpe": candidate["sym_sharpe"],
            "trades": candidate["trades"],
            "max_dd_pct": candidate["max_dd_pct"],
            "gain_per_yr": candidate["gain_per_yr"],
            "acc_gain_pct": candidate["acc_gain_pct"],
            "avg_gain_trade": candidate["avg_gain_trade"],
            "deflated_sharpe": candidate["deflated_sharpe"],
        },
        "tier3_validation": validation,
        "src_csv": candidate["src_csv"],
        "src_iter": candidate["src_iter"],
        "overrides": candidate["overrides"],
    }
    path = out_dir / fname
    with open(path, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--interval", type=int, default=600)
    ap.add_argument("--max-validations-per-cycle", type=int, default=2)
    ap.add_argument("--validation-profile", choices=list(VAL_PROFILES), default="smoke")
    ap.add_argument("--baseline-tradier", type=Path,
                    default=REPO / "data/baselines/tradier_2p4860_genuine.json")
    ap.add_argument("--baseline-crypto", type=Path,
                    default=REPO / "data/baselines/crypto_REAL_WINNER_baseline.json")
    ap.add_argument("--watch-modes", default="crypto,tradier",
                    help="Comma-separated modes to watch")
    ap.add_argument("--once", action="store_true",
                    help="One cycle then exit")
    ap.add_argument("--no-remote-pull", action="store_true",
                    help="Skip rsync from S1/S2 (local-only)")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    state_file = args.out_dir / "state.json"
    state = {"seen_hashes": [], "validations_run": 0, "candidates_written": 0}
    if state_file.exists():
        try:
            state = json.load(open(state_file))
        except Exception:
            pass
    seen_hashes = set(state.get("seen_hashes", []))
    modes = [m.strip() for m in args.watch_modes.split(",") if m.strip()]
    log(f"auto_promote start. modes={modes} interval={args.interval}s "
        f"profile={args.validation_profile} max_per_cycle={args.max_validations_per_cycle} "
        f"out_dir={args.out_dir}")

    cycle = 0
    while True:
        cycle += 1
        log(f"=== cycle {cycle} ===")
        if not args.no_remote_pull:
            mirror = args.out_dir / "mirror"
            mirror.mkdir(parents=True, exist_ok=True)
            collect_remote_csvs("s1-int",
                                "/home/niels/binance-sandbox/data/autonomous",
                                mirror / "s1")
            collect_remote_csvs("s2-int",
                                "/home/niels/binance-sandbox/data/autonomous",
                                mirror / "s2")

        candidates_by_mode = {m: [] for m in modes}
        roots = [REPO / "data/autonomous"]
        if not args.no_remote_pull:
            roots += [args.out_dir / "mirror" / "s1", args.out_dir / "mirror" / "s2"]
        for root in roots:
            if not root.exists(): continue
            for csv_path in root.rglob("autonomous_*.csv"):
                if "tradier" in csv_path.name.lower(): mode = "tradier"
                elif "crypto" in csv_path.name.lower(): mode = "crypto"
                else: continue
                if mode not in modes: continue
                winners = parse_csv_for_winners(csv_path, mode, THRESH[mode], seen_hashes)
                candidates_by_mode[mode].extend(winners)

        for mode in modes:
            cands = candidates_by_mode[mode]
            cands.sort(key=lambda c: c["pool_sharpe"], reverse=True)
            log(f"{mode}: {len(cands)} new candidates above threshold "
                f"(min_pool_sharpe={THRESH[mode]['min_pool_sharpe']}, "
                f"min_trades={THRESH[mode]['min_trades']}, "
                f"max_dd={THRESH[mode]['max_dd_pct']})")
            for c in cands[:3]:
                log(f"  TOP {mode} ps={c['pool_sharpe']:.3f} sym_s={c['sym_sharpe']:.3f} "
                    f"trades={c['trades']} dd={c['max_dd_pct']:.2f}% "
                    f"gain_yr={c['gain_per_yr']:.1f}% h={c['hash']} src={Path(c['src_csv']).parent.name}")

        validations_this_cycle = 0
        for mode in modes:
            if validations_this_cycle >= args.max_validations_per_cycle: break
            for c in candidates_by_mode[mode]:
                if validations_this_cycle >= args.max_validations_per_cycle: break
                baseline = args.baseline_crypto if mode == "crypto" else args.baseline_tradier
                if not baseline.exists():
                    log(f"baseline missing: {baseline}", file=sys.stderr)
                    continue
                workdir = args.out_dir / "validations" / f"{mode}_{c['hash']}"
                log(f"VALIDATING {mode} ps={c['pool_sharpe']:.3f} h={c['hash']} "
                    f"profile={args.validation_profile} -> {workdir}")
                val = run_v8_validation(mode, baseline, c["overrides"],
                                        args.validation_profile, workdir)
                state["validations_run"] += 1
                validations_this_cycle += 1
                v8sharpe = val.get("v8_sharpe_pt", val.get("v8_sharpe", "?"))
                log(f"  v8 result: status={val['status']} sharpe_pt={v8sharpe} "
                    f"elapsed={val['elapsed_s']:.1f}s")
                tier2 = c["pool_sharpe"]
                tier3 = val.get("v8_sharpe_pt", val.get("v8_sharpe", 0)) or 0
                worth_keeping = (val["status"] == "done"
                                 and isinstance(tier3, (int, float))
                                 and tier3 >= max(0.5, tier2 * 0.5))
                if worth_keeping:
                    p = write_candidate(args.out_dir, c, val)
                    state["candidates_written"] += 1
                    log(f"  WROTE candidate: {p.name}")
                else:
                    log(f"  REJECTED: tier-3 sharpe={tier3} fell below 50% of tier-2 ps={tier2:.3f}")

        state["seen_hashes"] = sorted(seen_hashes)[-5000:]
        with open(state_file, "w") as f: json.dump(state, f, indent=2)
        log(f"cycle {cycle} done. total_seen={len(seen_hashes)} "
            f"validations_run={state['validations_run']} "
            f"candidates_written={state['candidates_written']}")
        if args.once: break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
