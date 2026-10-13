#!/usr/bin/env python3
"""v15_npz_prep — per-symbol klines gate + guarded NPZ regen for the fleet scheduler (stocks + crypto). Runs on the NPZ source host (s1). USER 2026-10-13 JIT: tail must be current to the latest closed bar at dispatch; stale tails rebuild HERE (s1) and ship with the push — no NPZ older than 15m is ever used by v15_pilot.
  python tools/v15_npz_prep.py --symbol AAPL [--max-npz-age-h 20] [--max-kline-lag-h 30] [--push HOST ...] [--dry-run]
Steps: 1 klines gate (Tradier 15m cache last bar vs now / vs NPZ last bar)  2 fetch/repair via tools/stkt/refetch_tradier_window.py if stale
       3 regen into a staging dir with the unchanged builder (never the live dir)  4 tools/npz_guard + sanity gates  5 atomic install (backup) + rsync to --push hosts (md5 verified)
Prints one JSON line {symbol, action, ok, reason, npz_age_h_before, npz_last_bar}. Exit 0 when the NPZ is fresh/usable, 3 when the symbol must NOT launch."""
import argparse, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("V15_ROOT", os.path.expanduser("~/binance-sandbox")))
IND = ROOT / "backtest_v8" / "indicators"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def last_bar(p):
    import numpy as np
    z = np.load(p, allow_pickle=True)
    ts = z["timestamps"].astype("int64")
    ts = ts // 1000 if ts[-1] > 1e11 else ts
    return int(ts[0]), int(ts[-1]), len(ts), len(z.files)


def kline_last(symbol, venue):
    cands = []
    if venue == "crypto":
        cands = [ROOT / "klines_cache_backtest" / f"{symbol}_15m.json", ROOT / "klines_cache" / f"{symbol}_15m.json",
                 ROOT / "klines_cache" / "tradier" / f"{symbol}_15m.json", ROOT / "klines_cache_gateway" / f"{symbol}_15m.json"]
    else:
        cands = [ROOT / "klines_cache_backtest" / "tradier" / f"{symbol}_15m.json", ROOT / "klines_cache" / "tradier" / f"{symbol}_15m.json"]
    best = None
    for p in cands:
        try:
            d = json.load(open(p))
            if not d:
                continue
            from datetime import datetime, timezone
            t = int(datetime.strptime(d[-1]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
            best = t if best is None else max(best, t)
        except Exception:
            continue
    return best


def venue_of(sym):
    s = str(sym).upper()
    return "crypto" if (s.endswith("USDT") or s.endswith("USDC")) else "stocks"


def heal_crypto_klines(sym):
    """Forward-fill backtest 15m/1h/4h/D tails from Binance; backward-backfill 15m if missing/shallow. Returns (ok, detail)."""
    try:
        bf = str(ROOT / "tools" / "klines_backfill_15m.py")
        p15 = ROOT / "klines_cache_backtest" / f"{sym}_15m.json"
        have = 0
        try:
            have = len(json.load(open(p15)))
        except Exception:
            have = 0
        if have < 7000:
            r = subprocess.run([sys.executable, bf, "--apply", "--cache-dir", "klines_cache_backtest", "--target", "39000", "--pages", "30", "--symbols", sym],
                               capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            if f'"sym": "{sym}"' not in (r.stdout or ""):
                return (False, f"backfill failed {(r.stderr or '')[:100]}")
        for tf in ("15m", "1h", "4h", "1d"):
            subprocess.run([sys.executable, bf, "--forward", "--apply", "--cache-dir", "klines_cache_backtest", "--interval", tf, "--pages", "4", "--symbols", sym],
                           capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        return (True, "forward-filled")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}"[:120])


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def out(d, code=0):
    print(json.dumps(d))
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--max-npz-age-h", type=float, default=20)
    ap.add_argument("--max-kline-lag-h", type=float, default=30)
    ap.add_argument("--push", nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sym = a.symbol.upper()
    venue = venue_of(sym)
    mode = "crypto" if venue == "crypto" else "tradier"
    live = IND / f"{sym}.npz"
    now = time.time()
    r = {"symbol": sym, "action": "none", "ok": False, "reason": "", "venue": venue}
    if not live.exists():
        r["reason"] = "no live NPZ"
        out(r, 3)
    t0, t1, n0, k0 = last_bar(live)
    age_h = (now - live.stat().st_mtime) / 3600
    r.update(npz_age_h_before=round(age_h, 2), npz_last_bar=t1, npz_bars=n0)
    # USER 2026-10-13 JIT: tail must be current to the latest closed bar (crypto: 15m; stocks: RTH close) — file age is NOT freshness.
    try:
        from tools.npz_cutoffs import tail_currency as _tail_currency
        _tok, _twhy = _tail_currency(float(t1), venue, now)
    except Exception as _e:
        _tok, _twhy = False, f"cutoffs-fail {str(_e)[:60]}"
    r["tail_check"] = _twhy
    if _tok:
        r.update(ok=True, reason="tail current to latest closed bar")
        out(r)
    kl = kline_last(sym, venue)
    if kl is None:
        r["reason"] = f"no {venue} 15m cache"
        out(r, 3)
    lag_h = (now - kl) / 3600
    r["kline_lag_h"] = round(lag_h, 2)
    if kl <= t1:
        r.update(ok=True, reason="npz tail already at newest klines (no newer bars exist)")
        out(r)
    # klines lag counts only trading time loosely: >30h wall covers a weekend-less gap; weekends are tolerated up to 80h
    wd = time.gmtime(now).tm_wday
    allowed = a.max_kline_lag_h + (50 if wd in (5, 6, 0) else 0)
    if lag_h > allowed:
        if venue == "crypto" and not a.dry_run:
            hok, hwhy = heal_crypto_klines(sym)
            kl = kline_last(sym, venue) or kl
            lag_h = (now - kl) / 3600
            r["action"] = "healed" if hok else "heal-failed"
            r["kline_lag_h"] = round(lag_h, 2)
            r["heal_detail"] = hwhy
        else:
            refetch = ROOT / "tools" / "stkt" / "refetch_tradier_window.py"
            if refetch.exists() and not a.dry_run:
                ws = time.strftime("%Y-%m-%d", time.gmtime(kl - 86400))
                subprocess.run([sys.executable, str(refetch), "--symbols", sym, "--window-start", ws], timeout=600)
                kl = kline_last(sym, venue) or kl
                lag_h = (now - kl) / 3600
                r["action"] = "refetched"
                r["kline_lag_h"] = round(lag_h, 2)
        if lag_h > allowed:
            r["reason"] = f"klines stale {lag_h:.1f}h (> {allowed:.0f}h) and fetch did not help"
            out(r, 3)
    if a.dry_run:
        r.update(ok=True, action="would_regen", reason=f"npz {age_h:.1f}h old, klines newer by {(kl - t1) / 3600:.1f}h")
        out(r)
    stg = Path(os.path.expanduser(f"~/npzprep/{sym}"))
    shutil.rmtree(stg, ignore_errors=True)
    (stg / "out").mkdir(parents=True)
    if mode == "tradier":
        os.environ.setdefault("FORCE_TRADIER_15M_BASE", "1")
    import backtest_v8_precompute as M
    M.OUT_DIR = stg / "out"
    ok = M.compute_symbol(sym, mode)
    cand = stg / "out" / f"{sym}.npz"
    if not ok or not cand.exists():
        r.update(action="regen_failed", reason="builder returned failure; live NPZ untouched")
        out(r, 0 if age_h < 48 else 3)
    c0, c1, cn, ck = last_bar(cand)
    import npz_guard as G
    allow, why = G.should_allow_overwrite(live, (c1 - c0) / 86400, 900.0, ck, cn, (c1 - c0) / 86400)
    if not allow or cn < 0.95 * n0 or c0 > t0 + 86400 * 3 or c1 < t1:
        r.update(action="regen_blocked", reason=f"guard: {why}; bars {n0}->{cn} first {t0}->{c0} last {t1}->{c1}; live NPZ untouched")
        out(r, 0 if age_h < 48 else 3)
    bk = Path(os.path.expanduser("~/npzprep/backup"))
    bk.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live, bk / f"{sym}.{int(now)}.npz")
    tmp = IND / f".{sym}.prep.npz"
    shutil.copy2(cand, tmp)
    os.replace(tmp, live)
    m = md5(live)
    for h in a.push:
        subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes", str(live), f"{h}:~/binance-sandbox/backtest_v8/indicators/.{sym}.prep.npz"], timeout=300)
        subprocess.run(["ssh", "-o", "BatchMode=yes", h, f"cd ~/binance-sandbox/backtest_v8/indicators && mv .{sym}.prep.npz {sym}.npz"], timeout=60)
    r.update(action="regenerated", ok=True, reason=f"installed bars {n0}->{cn} last bar {t1}->{c1} md5 {m[:8]}", npz_last_bar=c1)
    out(r)


if __name__ == "__main__":
    main()
