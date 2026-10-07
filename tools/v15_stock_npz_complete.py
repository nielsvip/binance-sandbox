#!/usr/bin/env python3
"""v15_stock_npz_complete — precompute full-history stock NPZ from the Massive 15m klines, VALIDATE
each (binance-7b's lessons: validate via evaluate_v12._compact_to_15m, assert 15m-BASE, assert
len(timestamp_15m)==len(timestamps)), and distribute ONLY valid NPZs to the other servers.

Run on s2 (where the 15m klines + precompute live). Never distributes an NPZ that fails prepare —
so the recalc watcher never sees a broken file. Idempotent; --symbols to restrict.
"""
import argparse, json, subprocess, sys, statistics, pathlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
KLINES = ROOT / "klines_cache_macbook" / "tradier"   # the Massive full-history 15m stage dir
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
DIST_HOSTS = ["s1-int", "s5"]  # this runs on s2; push valid NPZ to the crypto/other boxes


def base_dt_seconds(npz):
    ts = npz["timestamps"]
    if len(ts) < 3:
        return None
    d = np.diff(ts.astype("float64"))
    d = d[d > 0]
    return float(statistics.median(d)) if len(d) else None


def validate(sym):
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists():
        return False, "npz missing"
    try:
        npz = np.load(p, allow_pickle=True)
    except Exception as e:
        return False, f"load fail {e}"
    if "timestamps" not in npz or "timestamp_15m" not in npz:
        return False, "no timestamps/timestamp_15m"
    if len(npz["timestamp_15m"]) != len(npz["timestamps"]):
        return False, f"len(ts_15m)={len(npz['timestamp_15m'])} != len(ts)={len(npz['timestamps'])}"
    bdt = base_dt_seconds(npz)
    if bdt is None or not (600 <= bdt <= 1200):   # 15m base = ~900s; reject 5m-base (~300s)
        return False, f"base dt {bdt}s not 15m (~900s) — likely 5m-base"
    try:
        from tools.opt.evaluate_v12 import _compact_to_15m
        _compact_to_15m(npz)   # the exact sweep-loader check; raises if unaligned
    except Exception as e:
        return False, f"_compact_to_15m raised: {str(e)[:80]}"
    days = (float(npz["timestamps"][-1]) - float(npz["timestamps"][0])) / 86400.0
    return True, f"OK 15m-base dt={bdt:.0f}s len={len(npz['timestamps'])} span={days:.0f}d"


def newest_kline_mtime(sym):
    m = 0.0
    for d in [KLINES, ROOT / "klines_cache_backtest" / "tradier", ROOT / "klines_cache_gateway" / "tradier"]:
        for tf in ("15m", "5m"):
            p = d / f"{sym}_{tf}.json"
            try:
                m = max(m, p.stat().st_mtime)
            except Exception:
                pass
    return m


def needs_recompute(sym):
    npz = NPZ_DIR / f"{sym}.npz"
    if not npz.exists():
        return True
    try:
        return newest_kline_mtime(sym) > npz.stat().st_mtime
    except Exception:
        return True


def run_pass(syms, no_dist):
    ok, bad, skipped, no15m = [], [], 0, 0
    for sym in syms:
        # only recompute a stock once its Massive 15m klines exist — otherwise precompute has no 15m
        # and would fall to 5m-base (rejected). Skip until its fetch lands; the loop retries next cycle.
        if not (KLINES / f"{sym}_15m.json").exists():
            no15m += 1
            continue
        if not needs_recompute(sym):
            skipped += 1
            continue
        r = subprocess.run([str(ROOT / ".venv/bin/python"), "backtest_v8_precompute.py",
                            "--symbol", sym, "--mode", "tradier"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=1200)
        valid, why = validate(sym)
        if not valid:
            bad.append((sym, why))
            print(f"[BAD] {sym}: {why} (precompute rc={r.returncode})")
            continue
        ok.append(sym)
        print(f"[OK ] {sym}: {why}", flush=True)
        if not no_dist:
            for h in DIST_HOSTS:
                subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=accept-new",
                                str(NPZ_DIR / f"{sym}.npz"), f"{h}:~/binance-sandbox/backtest_v8/indicators/"],
                               timeout=120)
    print(f"[stock-npz] pass done: recomputed={len(ok)} invalid={len(bad)} skipped(up-to-date)={skipped} awaiting-15m-fetch={no15m}", flush=True)
    if bad:
        print("[stock-npz] INVALID (not distributed):", bad[:20], flush=True)
    (NPZ_DIR / "_stock_npz_complete_report.json").write_text(json.dumps({"ok": ok, "bad": bad, "skipped": skipped}, indent=1, default=str))
    return ok, bad


def all_stock_symbols():
    try:
        import download_stock_klines_5m as d5
        return list(d5.SYMBOLS)
    except Exception:
        return sorted({p.name[:-len("_15m.json")] for p in KLINES.glob("*_15m.json")})


def main():
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list")
    ap.add_argument("--all-stocks", action="store_true", help="the full 122-stock universe")
    ap.add_argument("--loop", action="store_true", help="cycle forever; recompute a symbol only when its klines are newer than its NPZ (always up to date, no wasted recompute)")
    ap.add_argument("--interval", type=int, default=120, help="loop sleep seconds")
    ap.add_argument("--no-dist", action="store_true")
    args = ap.parse_args()
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.all_stocks:
        syms = all_stock_symbols()
    else:
        syms = sorted({p.name[:-len("_15m.json")] for p in KLINES.glob("*_15m.json")})
    print(f"[stock-npz] {len(syms)} symbols; loop={args.loop}", flush=True)
    while True:
        run_pass(syms, args.no_dist)
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
