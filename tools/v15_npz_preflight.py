#!/usr/bin/env python3
"""V15 NPZ PREFLIGHT (USER 2026-10-15): "BEFORE any calculations start a verification of npz data, recalc if needed
while the symbol is skipped and the next good one runs, then requeue the one that could have caused 0 trades instead of
blindly following a list of symbols."

Why this exists and what it is NOT duplicating: `tools/v15_npz_prep.py` already guards the dispatch on *tail freshness
and provenance* (tail_currency, canonical_v1 klines, kline lag/heal) and refuses a *freshly built* NPZ that is too
short. What nothing checked is whether an EXISTING, fresh-tailed NPZ is structurally usable. That gap is how
`ACEUSDT_LONG_bhm14p68_gain0p00_t0_30d_matrix.xlsx` (2026-09-29, inside the 09-24..10-01 klines-tail corruption window)
got a full board's CPU and produced gain 0.00 on 0 trades.

Checks, cheapest first (a zip directory read before any array load):
  LOAD      the file opens (a truncated/BadZip NPZ fails here, not 40 minutes into a board)
  BARS      `timestamps` exists, has finite values, is non-decreasing
  SPAN      span >= MIN_WINDOW_COVERAGE (0.80) * window_days — the SAME rule evaluate_v12._validate uses to throw a
            finished board away, applied BEFORE the CPU is spent instead of after
  COLUMNS   the core price columns exist (timestamps, close, open_15m, high_15m, low_15m, close_15m, volume_15m)
  WIDTH     key count >= --min-key-frac of the median key count of this host's other NPZs (a 949-key fleet with a
            40-key outlier is a broken build, and the threshold is measured, never hardcoded)
  LIVE DATA the core price columns over the last window_days are not all-zero and not all-NaN, and close > 0 —
            an all-zero tail is exactly what yields "0 trades" with no error

Exit 0 = safe to calculate, 3 = do NOT launch this symbol (same convention as v15_npz_prep.py). Read-only: it never
rebuilds or deletes anything; with --defer it records the verdict in data/npz_deferred.json so a dispatcher can skip
the symbol, run the next good one, and requeue this one once a rebuild makes it pass.

  python3 tools/v15_npz_preflight.py --symbol ACEUSDT [--window-days 30] [--json] [--defer]
  python3 tools/v15_npz_preflight.py --all [--venue crypto] [--json]      # fleet census, read-only
"""
import argparse
import json
import os
import statistics
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(os.environ.get("V15_ROOT", str(Path(__file__).resolve().parents[1])))
IND = ROOT / "backtest_v8" / "indicators"
DEFER_FILE = ROOT / "data" / "npz_deferred.json"
MIN_WINDOW_COVERAGE = 0.80  # tools/opt/metrics.py — keep in step with the engine
CORE_COLS = ("timestamps", "close", "open_15m", "high_15m", "low_15m", "close_15m", "volume_15m")
# USER 2026-10-15: "make sure no new test starts (unless for new script version validations) with old npz files —
# update them first ... the biggest waste is in small errors like these". Measured when that was written: fresh 15m
# klines on disk (0.17 h old) while 54 of 60 sampled crypto NPZs were 4-12 h behind (median 7.69 h, one 374 h), because
# tools/v15_dispatch_job.py treats a prep slot/lock-wait timeout as ok=None and launches "with what the host has".
# A board fitted on bars that old is optimising last night's market, so staleness BLOCKS here.
# USER LAW 2026-10-20 (was 1.5 h): wall-clock age of the last bar's OPEN; one bar behind the latest closed bar peaks at 15+15+15 = 45 min, so 0.8 h (48 min) = the 20-min npz_age_min rule (tools/npz_cutoffs.MAX_NPZ_AGE_MIN)
MAX_TAIL_AGE_H = {"crypto": float(os.environ.get("V15_PREFLIGHT_MAX_TAIL_H_CRYPTO", "0.8")), "stocks": float(os.environ.get("V15_PREFLIGHT_MAX_TAIL_H_STOCKS", "20"))}
QUOTES = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def venue_of(symbol: str) -> str:
    return "crypto" if str(symbol).upper().endswith(QUOTES) else "stocks"


def npz_keys(path) -> list:
    """Key names without loading a single array (the .npz zip directory)."""
    try:
        with zipfile.ZipFile(str(path)) as z:
            return [n[:-4] if n.endswith(".npy") else n for n in z.namelist()]
    except Exception:
        return []


def median_key_count(ind_dir, venue, limit=40):
    """Measured width of a healthy NPZ on THIS host, for the venue — the comparison basis for a thin build."""
    counts = []
    try:
        for p in sorted(Path(ind_dir).glob("*.npz"))[:400]:
            if venue_of(p.stem) != venue:
                continue
            n = len(npz_keys(p))
            if n:
                counts.append(n)
            if len(counts) >= limit:
                break
    except Exception:
        return None
    return int(statistics.median(counts)) if counts else None


def span_ok(span_days, window_days, coverage=MIN_WINDOW_COVERAGE):
    """Pure: does the available history cover the window the board will claim to measure?"""
    try:
        return float(span_days) >= float(coverage) * float(window_days)
    except Exception:
        return False


def verify(symbol, window_days=30, ind_dir=None, now=None, min_key_frac=0.5, median_keys=None, check_freshness=True, max_tail_age_h=None):
    """-> {ok, reasons, ...}. Never raises; an unreadable NPZ is a FAIL, never a pass-through.

    check_freshness=False is the ONE sanctioned exception (USER 2026-10-15 "unless for new script version
    validations"): the S2 version gate benches champion vs candidate on a deliberately FROZEN snapshot so both see
    identical bars. Freshness must never be enforced there, or the verifier would reject the verifier's own input."""
    import numpy as np

    now = time.time() if now is None else float(now)
    d = Path(ind_dir or IND)
    p = d / f"{str(symbol).upper()}.npz"
    out = {"symbol": str(symbol).upper(), "venue": venue_of(symbol), "path": str(p), "window_days": int(window_days), "ok": False, "reasons": []}
    if not p.exists():
        out["reasons"].append("no NPZ file")
        return out
    out["size_mb"] = round(p.stat().st_size / (1 << 20), 2)
    keys = npz_keys(p)
    out["n_keys"] = len(keys)
    if not keys:
        out["reasons"].append("unreadable/truncated NPZ (zip directory empty — BadZip)")
        return out
    missing = [c for c in CORE_COLS if c not in keys]
    if missing:
        out["reasons"].append(f"core columns missing: {','.join(missing)}")
    if median_keys is None:
        median_keys = median_key_count(d, out["venue"])
    out["median_keys_host"] = median_keys
    if median_keys and len(keys) < max(8, int(min_key_frac * median_keys)):
        out["reasons"].append(f"thin NPZ: {len(keys)} keys vs host median {median_keys} for {out['venue']}")
    try:
        z = np.load(str(p), allow_pickle=True, mmap_mode="r")
    except Exception as e:
        out["reasons"].append(f"load failed: {type(e).__name__}: {str(e)[:60]}")
        return out
    try:
        t = np.asarray(z["timestamps"], dtype="float64")
    except Exception as e:
        out["reasons"].append(f"timestamps unreadable: {type(e).__name__}")
        return out
    fin = np.isfinite(t) & (t > 0)
    out["bars"] = int(fin.sum())
    if out["bars"] == 0:
        out["reasons"].append("no finite timestamps")
        return out
    tt = t[fin]
    sec = tt / 1000.0 if float(np.nanmax(tt)) > 1e11 else tt
    out["span_days"] = round((float(sec[-1]) - float(sec[0])) / 86400.0, 2)
    out["tail_age_h"] = round((now - float(sec[-1])) / 3600.0, 2)
    if check_freshness:
        lim = float(max_tail_age_h) if max_tail_age_h is not None else MAX_TAIL_AGE_H.get(out["venue"], 20.0)
        out["max_tail_age_h"] = lim
        if out["tail_age_h"] > lim:
            out["reasons"].append(f"stale tail: last bar {out['tail_age_h']}h old > {lim}h for {out['venue']} — rebuild the NPZ before any board starts on it")
    if int(np.sum(np.diff(sec) < 0)) > 0:
        out["reasons"].append(f"timestamps not monotonic ({int(np.sum(np.diff(sec) < 0))} backward steps)")
    if not span_ok(out["span_days"], window_days):
        out["reasons"].append(f"span {out['span_days']}d < {MIN_WINDOW_COVERAGE:.0%} of the {int(window_days)}d window — a board on this NPZ is INVALID by evaluate_v12._validate")
    # the window the board would actually fit: all-zero / all-NaN there is the silent "0 trades" producer
    cut = float(sec[-1]) - float(window_days) * 86400.0
    idx = int(np.searchsorted(sec, cut, side="left"))
    win_n = len(sec) - idx
    out["bars_in_window"] = int(win_n)
    if win_n <= 0:
        out["reasons"].append("no bars inside the requested window")
        return out
    for col in ("close", "close_15m", "high_15m", "low_15m"):
        if col not in keys:
            continue
        try:
            v = np.asarray(z[col], dtype="float64")
            v = v[-win_n:] if len(v) >= win_n else v
            finite = np.isfinite(v)
            if not finite.any():
                out["reasons"].append(f"{col}: all NaN inside the window")
            elif float(np.nanmax(np.abs(v[finite]))) == 0.0:
                out["reasons"].append(f"{col}: all zero inside the window")
            elif col in ("close", "close_15m") and float(np.nanmin(v[finite])) <= 0.0:
                out["reasons"].append(f"{col}: non-positive prices inside the window")
        except Exception as e:
            out["reasons"].append(f"{col}: unreadable ({type(e).__name__})")
    out["ok"] = not out["reasons"]
    return out


def record_deferral(res, path=None):
    """Append/refresh the symbol's verdict so a dispatcher can skip it now and requeue it once it passes.
    Never raises; the ledger is advisory, the preflight's exit code is authoritative."""
    f = Path(path or DEFER_FILE)
    try:
        cur = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        cur = {}
    sym = res["symbol"]
    if res.get("ok"):
        prev = cur.pop(sym, None)
        if prev is not None:
            cur.setdefault("_cleared", {})[sym] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "was": prev.get("reasons")}
    else:
        prev = cur.get(sym) or {}
        cur[sym] = {"first_seen": prev.get("first_seen") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "deferrals": int(prev.get("deferrals") or 0) + 1, "reasons": res.get("reasons"), "bars": res.get("bars"), "span_days": res.get("span_days"), "rebuild_cmd": f"backtest_v8_precompute.py --symbol {sym} --mode {res.get('venue')}"}
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True))
        tmp.replace(f)
    except Exception:
        pass
    return cur.get(sym)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true", help="census over every NPZ on this host (read-only)")
    ap.add_argument("--venue", choices=("crypto", "stocks"))
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--min-key-frac", type=float, default=0.5)
    ap.add_argument("--ind-dir", default=None)
    ap.add_argument("--defer", action="store_true", help="record the verdict in data/npz_deferred.json for skip-and-requeue")
    ap.add_argument("--ignore-freshness", action="store_true", help="ONLY for new-script-version validation on a frozen snapshot (S2 version gate): skip the stale-tail rule")
    ap.add_argument("--max-tail-age-h", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = Path(a.ind_dir or IND)
    if a.all:
        syms = sorted(p.stem for p in d.glob("*.npz") if not a.venue or venue_of(p.stem) == a.venue)
        med = {v: median_key_count(d, v) for v in ({a.venue} if a.venue else {"crypto", "stocks"})}
        bad, res = [], []
        for s in syms:
            r = verify(s, a.window_days, d, min_key_frac=a.min_key_frac, median_keys=med.get(venue_of(s)), check_freshness=not a.ignore_freshness, max_tail_age_h=a.max_tail_age_h)
            res.append(r)
            if not r["ok"]:
                bad.append(r)
            if a.defer:
                record_deferral(r)
        if a.json:
            print(json.dumps({"checked": len(res), "bad": len(bad), "bad_symbols": [{k: r.get(k) for k in ("symbol", "bars", "span_days", "n_keys", "tail_age_h", "reasons")} for r in bad]}, indent=1))
        else:
            print(f"[npz-preflight] checked {len(res)} NPZ | would BLOCK {len(bad)}")
            for r in bad[:40]:
                print(f"   BLOCK {r['symbol']:14} bars={r.get('bars')} span={r.get('span_days')}d keys={r.get('n_keys')} :: {'; '.join(r['reasons'])[:120]}")
        return 3 if bad else 0
    if not a.symbol:
        ap.error("--symbol or --all required")
    r = verify(a.symbol, a.window_days, d, min_key_frac=a.min_key_frac, check_freshness=not a.ignore_freshness, max_tail_age_h=a.max_tail_age_h)
    if a.defer:
        record_deferral(r)
    print(json.dumps(r) if a.json else f"[npz-preflight] {r['symbol']} {'OK' if r['ok'] else 'BLOCK'} bars={r.get('bars')} span={r.get('span_days')}d in_window={r.get('bars_in_window')} keys={r.get('n_keys')} tail={r.get('tail_age_h')}h :: {'; '.join(r['reasons']) or 'usable'}")
    return 0 if r["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
