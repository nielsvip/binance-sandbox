"""NPZ freshness gate (USER 2026-10-12 standing rule).

Every NPZ must hold >=365 days of data AND end at the latest 15m bar
(crypto) / latest RTH session (stocks) BEFORE any test rerun on it or
any sync to s2/s5/s6. Exit 0 = all pass. Exit 1 = violations listed.

Usage (S1, the NPZ source):
  python3 tools/npz_freshness_gate.py [--dir backtest_v8/indicators] [--fix-cmd]
"""
import numpy as np, os, glob, sys, time, datetime

MIN_DAYS = 365
MAX_LAG_BARS_CRYPTO = 2
STOCK_SUFFIX_MARK = (".npz",)

def main():
    d = sys.argv[sys.argv.index("--dir") + 1] if "--dir" in sys.argv else "backtest_v8/indicators"
    if not os.path.isdir(d):
        print(f"GATE_FAIL dir missing: {d}"); return 1
    files = sorted(glob.glob(os.path.join(d, "*.npz")))
    now = time.time()
    latest_15m = (now // 900) * 900
    short, stale, bad = [], [], []
    for f in files:
        name = os.path.basename(f)
        try:
            z = np.load(f, allow_pickle=False)
            keys = [k for k in z.files if "time" in k.lower() or k in ("ts", "t")]
            ts = np.asarray(z[keys[0]], dtype=np.float64).ravel() if keys else np.array([])
            ts = ts[np.isfinite(ts)]
            if ts.size == 0: bad.append(f"{name} no-timestamps"); continue
            if ts[-1] > 1e12: ts = ts / 1000.0
            span_days = (ts[-1] - ts[0]) / 86400.0
            # stocks: no USDT/USDC suffix -> freshness vs last RTH close, not 15m bar
            is_stock = not any(s in name.upper() for s in ("USDT", "USDC", "BUSD"))
            if span_days < MIN_DAYS: short.append(f"{name} {span_days:.0f}d")
            if is_stock:
                # RTH session freshness: last bar within 4 days (weekend-proof)
                if (now - ts[-1]) / 86400.0 > 4: stale.append(f"{name} stock-lag{(now-ts[-1])/86400.0:.1f}d")
            else:
                if (latest_15m - ts[-1]) / 900.0 > MAX_LAG_BARS_CRYPTO:
                    stale.append(f"{name} lag{(latest_15m-ts[-1])/900.0:.0f}bars")
        except Exception as e:
            bad.append(f"{name} ERR {e}")
    badnames = {s.split()[0] for s in short + stale + bad}
    ok = len(files) - len(badnames)
    print(f"GATE files={len(files)} ok={ok} short={len(short)} stale={len(stale)} bad={len(bad)}")
    for lst, tag in ((short, "SHORT"), (stale, "STALE"), (bad, "BAD")):
        for s in lst[:60]: print(f"  {tag} {s}")
    if "--fix-cmd" in sys.argv and (short or stale):
        syms = sorted({s.split()[0].replace(".npz", "") for s in short + stale})
        print("--- S1 refresh commands ---")
        for s in syms:
            mode = "stocks" if not any(x in s.upper() for x in ("USDT", "USDC", "BUSD")) else "crypto"
            print(f"  python3 backtest_v8_precompute.py --symbol {s} --mode {mode}")
    return 0 if not (short or stale or bad) else 1

if __name__ == "__main__":
    sys.exit(main())
