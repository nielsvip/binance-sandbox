#!/usr/bin/env python3
"""v15_npz_coverage_guard — EVERY tradeable sym_side must have an NPZ store. No exceptions.

USER 2026-10-15: "if they are tradeable they need npz's".

Without an NPZ a tradeable sym_side cannot be swept, cannot be re-tested, and cannot be compared against its cat_side
defaults (tools/v15_persym_vs_catside.py reports NPZ_MISSING and measures nothing). The dangerous part is that the
absence is SILENT: a missing store looks like "0 trades", which reads as "this config earns nothing" rather than
"this config was never measured". On 2026-10-15 that hid 8 actively traded stock symbols (COHR, CRK, KGC, RGLD, WMT,
WPM, XLE, XOP) with no store at all.

Tradeable is read from what the live system actually trades:
  crypto  tradeable_keys.json                            ("acct:SYM_SIDE")
  stocks  symbols_tr{a,b,c}_{long,short}.json            (per-account live lists)
The whole-universe files (symbols_tradier.json, *_recommended, *last_known_good) are NOT tradeable and are ignored.

Exit code 1 when anything tradeable is missing a store, so a cron/guardian notices. --precompute builds the missing
ones via backtest_v8_precompute.py (crypto and stocks dispatched separately, since --mode differs). Read-only
otherwise; NPZ age is reported but never an error — NPZ are rebuilt just-in-time by design (NPZ JIT law).

Usage:
  python3 tools/v15_npz_coverage_guard.py                      # report
  python3 tools/v15_npz_coverage_guard.py --precompute         # report + build what is missing
  python3 tools/v15_npz_coverage_guard.py --indicators-dir /path/to/backtest_v8/indicators
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD")
STOCK_LISTS = ("symbols_tra_long.json", "symbols_tra_short.json", "symbols_trb_long.json",
               "symbols_trb_short.json", "symbols_trc_long.json", "symbols_trc_short.json")


def is_crypto(symbol):
    return symbol.upper().endswith(CRYPTO_SUFFIX)


def _items(path):
    try:
        d = json.load(open(path))
    except Exception:
        return []
    return d if isinstance(d, list) else list(d)


def tradeable_symbols(root):
    crypto, stocks = set(), set()
    for raw in _items(root / "tradeable_keys.json"):
        key = str(raw).split(":", 1)[-1]
        sym = key.rsplit("_", 1)[0] if key.rsplit("_", 1)[-1].upper() in ("LONG", "SHORT") else key
        (crypto if is_crypto(sym) else stocks).add(sym)
    for name in STOCK_LISTS:
        for raw in _items(root / name):
            key = str(raw).split(":", 1)[-1]
            sym = key.rsplit("_", 1)[0] if key.rsplit("_", 1)[-1].upper() in ("LONG", "SHORT") else key
            if sym and sym.replace(".", "").isalpha():
                stocks.add(sym)
    # tokenized stocks are excluded everywhere (data/excluded_tokenized_stocks.json)
    try:
        excl = set(json.load(open(root / "data" / "excluded_tokenized_stocks.json")))
        crypto -= excl
        stocks -= excl
    except Exception:
        pass
    return sorted(crypto), sorted(stocks)


def have_npz(indicators_dir, symbol):
    hits = glob.glob(str(Path(indicators_dir) / f"{symbol}_*.npz")) + \
           glob.glob(str(Path(indicators_dir) / f"{symbol}.npz"))
    return hits



ROUND_PROCS = ("v15_pilot.py", "v15_local_herd.py", "v15_overnight_herd.py", "v12_pilot_sheet_runner.py")


def round_active():
    """Names of pilot/herd processes running now — the round owns the box while any of these are up.

    USER 2026-10-16 ("run it leaner we have to recompute all sym_sides tonight", then approved gating): this guard is
    a safety net, and during a full recompute it is also REDUNDANT — the round rebuilds the very NPZ it would build,
    so precomputing in parallel is duplicated work competing for the same cores. Coverage is still measured and still
    exits 1 while a hole exists; only the BUILD is deferred until the round is idle. --force-precompute overrides.
    """
    found = []
    for name in ROUND_PROCS:
        try:
            r = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        pids = [x for x in r.stdout.split() if x.strip().isdigit()]
        if pids:
            found.append(f"{name}x{len(pids)}")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--indicators-dir", default=str(ROOT / "backtest_v8" / "indicators"))
    ap.add_argument("--precompute", action="store_true")
    ap.add_argument("--force-precompute", action="store_true",
                    help="build even while a pilot/herd round is running (default: defer, the round rebuilds them anyway)")
    # USER 2026-10-16 "run it leaner we have to recompute all sym_sides tonight": this guard is a SAFETY NET, not
    # the round. A full recompute owns the box tonight, so the net must never compete with it for RAM/CPU — it
    # fills a hole slower rather than stealing a worker from the herd. Raise with --workers when the box is idle.
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "npz_coverage_guard.json"))
    a = ap.parse_args()
    root = Path(a.root)
    ind = Path(a.indicators_dir)
    if not ind.is_dir():
        print(f"indicators dir not found: {ind} — run this on the backtest host (S1)", file=sys.stderr)
        return 2
    crypto, stocks = tradeable_symbols(root)
    now = time.time()
    rows, missing_c, missing_s = {}, [], []
    for sym, venue in [(s, "crypto") for s in crypto] + [(s, "stocks") for s in stocks]:
        hits = have_npz(ind, sym)
        if hits:
            newest = max(os.path.getmtime(h) for h in hits)
            rows[sym] = {"venue": venue, "npz": len(hits), "age_h": round((now - newest) / 3600, 1)}
        else:
            rows[sym] = {"venue": venue, "npz": 0, "age_h": None}
            (missing_c if venue == "crypto" else missing_s).append(sym)
    total = len(crypto) + len(stocks)
    missing = missing_c + missing_s
    print(f"indicators dir : {ind}")
    print(f"tradeable      : {len(crypto)} crypto + {len(stocks)} stocks = {total}")
    print(f"with an NPZ    : {total - len(missing)}")
    print(f"MISSING        : {len(missing)}" + (f"  crypto={missing_c}" if missing_c else "") +
          (f"  stocks={missing_s}" if missing_s else ""))
    stale = sorted(((v["age_h"], k) for k, v in rows.items() if v["age_h"] is not None), reverse=True)[:5]
    if stale:
        print("oldest stores (age is NOT an error — NPZ are rebuilt just-in-time): " +
              ", ".join(f"{k} {h:.0f}h" for h, k in stale))
    out = {"_meta": {"built_at": datetime.now(timezone.utc).isoformat(), "indicators_dir": str(ind),
                     "n_tradeable": total, "n_missing": len(missing),
                     "missing_crypto": missing_c, "missing_stocks": missing_s}, "symbols": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"report -> {a.out}")
    if missing and a.precompute:
        busy = [] if a.force_precompute else round_active()
        if busy:
            print(f"DEFERRED precompute of {len(missing)} symbol(s): round active ({', '.join(busy)}) — it rebuilds "
                  f"these NPZ itself; the next run after the round goes idle will fill anything still missing")
            return 1 if missing else 0
        py = sys.executable
        for venue, syms, mode in (("crypto", missing_c, "crypto"), ("stocks", missing_s, "tradier")):
            if not syms:
                continue
            cmd = [py, "-u", str(root / "backtest_v8_precompute.py"), "--symbols", ",".join(syms),
                   "--mode", mode, "--workers", str(a.workers)]
            print(f"precompute {venue}: {' '.join(cmd)}")
            subprocess.run(cmd, cwd=str(root))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
