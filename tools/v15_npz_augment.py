#!/usr/bin/env python3
"""v15_npz_augment — extend-only NPZ key augmenter (no builder edits, guard-compliant).

Adds live-identical indicator keys the builder never persisted:
  crypto: funding_zscore/extreme, oi_vel_1h/4h/regime, smfi*/TF, vwap*/session,
          rsi_2_4h/D, wick/body/vol/atr per TF, kc_position/TF, formation_* (imported helper)
  stocks: + orb_high/low/position (RTH), same TF families where sources exist

Math mirrors the live emitters exactly (ez_indicators.py: compute_smfi:5304,
compute_funding_zscore:5337, compute_oi_velocity:5368, compute_vwap_from_bars:5052,
detect_bar_patterns wick/vol/atr formulas:1939+, tradier ORB:3850, RSI2 builder:2206).
Formations reuse classic_formations.ensure_npz_formation_fields (no reimplementation).

Install: tmp + npz_guard check + atomic os.replace. Existing keys/bars untouched.
Run on S1 (NPZ home). --dry-run verifies math without writing.

Usage:
  python3 tools/v15_npz_augment.py --sym UNIUSDC --mode crypto --dry-run
  python3 tools/v15_npz_augment.py --all --mode crypto
"""
import argparse, json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TFS = ("15m", "1h", "4h", "D")


def log(msg):
    print(f"[augment] {msg}", flush=True)


def _f32(a):
    import numpy as np
    return np.asarray(a, dtype=np.float64).astype(np.float32)


def _htf_native(*arrs):
    """Unique-parent index for broadcast HTF arrays + ffill-back indexer.

    HTF arrays repeat each parent candle over its base bars; history-dependent stats
    (streak, SMAs, RSI, SMFI) must run on native parents (live parity), then ffill.
    Returns (uniq_idx, bcast_idx) with out_full = out_native[bcast_idx]."""
    import numpy as np
    sig = np.zeros(len(arrs[0]), dtype=np.float64)
    for a in arrs:
        v = np.asarray(a, dtype=np.float64)
        sig = sig + np.where(np.isfinite(v), v, 0.0)
    chg = np.ones(len(sig), dtype=bool)
    chg[1:] = sig[1:] != sig[:-1]
    uniq = np.where(chg)[0]
    bcast = np.searchsorted(uniq, np.arange(len(sig)), side="right") - 1
    return uniq, np.clip(bcast, 0, max(len(uniq) - 1, 0))


def add_funding_z(d, out):
    import numpy as np
    src = d.get("funding_rate", None)
    if src is None:
        return []
    f = np.asarray(src, dtype=np.float64)
    if np.all(f == 0) or len(f) < 25:
        return []
    n = len(f)
    chg = np.zeros(n, dtype=bool)
    chg[0] = f[0] != 0
    chg[1:] = f[1:] != f[:-1]
    sp = np.flatnonzero(chg)
    sv = f[sp]
    z = np.full(n, np.nan)
    if len(sp) >= 22:
        for j in range(21, len(sp)):
            hist = sv[j - 21:j]
            mean = float(np.mean(hist))
            std = float(np.std(hist))
            cur = float(sv[j])
            zv = 0.0 if (std <= 0 or not np.isfinite(std)) else (cur - mean) / std
            lo = sp[j]
            hi = sp[j + 1] if j + 1 < len(sp) else n
            z[lo:hi] = zv
    out["funding_zscore"] = _f32(z)
    out["funding_extreme"] = (np.abs(np.where(np.isfinite(z), z, 0.0)) > 2.0).astype(np.int8)
    return ["funding_zscore", "funding_extreme"]


def add_oi_velocity(d, out, base_bars_4h=16):
    import numpy as np
    ch1 = d.get("oi_change_1h_pct", None)
    oi = d.get("oi_15m", d.get("oi_5m", None))
    if ch1 is None or oi is None:
        return []
    c1 = np.asarray(ch1, dtype=np.float64)
    o = np.asarray(oi, dtype=np.float64)
    back = np.roll(o, base_bars_4h)
    back[:base_bars_4h] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        c4 = np.where((back > 0) & np.isfinite(o), (o - back) / back * 100.0, np.nan)
    reg = np.zeros(len(c1), dtype=np.int8)
    flat = (np.abs(c1) < 0.5) & (np.abs(c4) < 1.5)
    rising = (c4 > 0) & (c1 > 0) & ~flat
    falling = (c4 < 0) & (c1 < 0) & ~flat
    reg[rising] = 1
    reg[falling] = 2
    out["oi_vel_1h"] = _f32(c1)
    out["oi_vel_4h"] = _f32(c4)
    out["oi_vel_regime"] = reg
    return ["oi_vel_1h", "oi_vel_4h", "oi_vel_regime"]


def add_smfi(d, out):
    import numpy as np
    import pandas as pd
    added = []
    for tf in TFS:
        try:
            o = np.asarray(d[f"open_{tf}"], dtype=np.float64)
            h = np.asarray(d[f"high_{tf}"], dtype=np.float64)
            lo = np.asarray(d[f"low_{tf}"], dtype=np.float64)
            c = np.asarray(d[f"close_{tf}"], dtype=np.float64)
        except KeyError:
            continue
        bc = None
        if tf != "15m":
            uq, bc = _htf_native(o, h, lo, c)
            o, h, lo, c = (a[uq] for a in (o, h, lo, c))
        if len(c) < 30:
            continue
        mid = (h + lo) / 2.0
        smart = c - mid
        retail = mid - o
        step = np.zeros_like(c)
        step[1:] = smart[1:] - retail[1:]
        sm = np.cumsum(step)
        s = pd.Series(sm)
        cs = pd.Series(c)
        sm_sma = s.rolling(20, min_periods=20).mean().bfill().fillna(0).values
        px_sma = cs.rolling(20, min_periods=20).mean().bfill().fillna(c[0] if len(c) else 0).values
        bull = (c < px_sma) & (sm > sm_sma)
        bear = (c > px_sma) & (sm < sm_sma)
        if bc is not None:
            sm, sm_sma, bull, bear = sm[bc], sm_sma[bc], bull[bc], bear[bc]
        out[f"smfi_{tf}"] = _f32(sm)
        out[f"smfi_sma_{tf}"] = _f32(sm_sma)
        out[f"smfi_bull_div_{tf}"] = bull.astype(np.int8)
        out[f"smfi_bear_div_{tf}"] = bear.astype(np.int8)
        added += [f"smfi_{tf}", f"smfi_sma_{tf}", f"smfi_bull_div_{tf}", f"smfi_bear_div_{tf}"]
    return added


def _session_ids(ts, stocks):
    import numpy as np
    import pandas as pd
    ts = np.asarray(ts, dtype=np.int64)
    if ts[-1] > 1e11:
        ts = ts // 1000
    dt = pd.to_datetime(ts, unit="s", utc=True)
    if stocks:
        day = dt.tz_convert("America/New_York").date
    else:
        day = dt.date
    _, ids = np.unique(np.asarray(day), return_inverse=True)
    return ids


def add_vwap(d, out, stocks):
    import numpy as np
    added = []
    try:
        h = np.asarray(d["high_15m"], dtype=np.float64)
        lo = np.asarray(d["low_15m"], dtype=np.float64)
        c = np.asarray(d["close_15m"], dtype=np.float64)
        v = np.asarray(d["volume_15m"], dtype=np.float64)
        ts = np.asarray(d["timestamps"])
    except KeyError:
        return []
    tp = (h + lo + c) / 3.0
    valid = (c > 0) & (v > 0)
    sess = _session_ids(ts, stocks)
    n = len(c)
    vwap = np.full(n, np.nan)
    dist = np.zeros(n)
    up1 = np.full(n, np.nan)
    lo1 = np.full(n, np.nan)
    up2 = np.full(n, np.nan)
    lo2 = np.full(n, np.nan)
    for s in np.unique(sess):
        idx = np.where((sess == s) & valid)[0]
        if len(idx) < 2:
            continue
        tpi, voli = tp[idx], v[idx]
        cum_tpv = np.cumsum(tpi * voli)
        cum_v = np.cumsum(voli)
        vw = cum_tpv / np.maximum(cum_v, 1e-10)
        sq = np.cumsum(((tpi - vw) ** 2) * voli)
        var = sq / np.maximum(cum_v, 1e-10)
        sd = np.sqrt(np.maximum(var, 0))
        vwap[idx] = vw
        up1[idx] = vw + sd
        lo1[idx] = vw - sd
        up2[idx] = vw + 2 * sd
        lo2[idx] = vw - 2 * sd
        dist[idx] = np.where(vw > 0, (c[idx] - vw) / vw * 100.0, 0.0)
    out["vwap"] = _f32(np.where(np.isfinite(vwap), vwap, c))
    out["vwap_upper1"] = _f32(np.where(np.isfinite(up1), up1, c))
    out["vwap_lower1"] = _f32(np.where(np.isfinite(lo1), lo1, c))
    out["vwap_upper2"] = _f32(np.where(np.isfinite(up2), up2, c))
    out["vwap_lower2"] = _f32(np.where(np.isfinite(lo2), lo2, c))
    out["vwap_distance_pct"] = _f32(dist)
    return ["vwap", "vwap_upper1", "vwap_lower1", "vwap_upper2", "vwap_lower2", "vwap_distance_pct"]


def add_rsi2(d, out):
    import numpy as np
    import pandas as pd
    added = []
    for tf in ("4h", "D"):
        cl = d.get(f"close_{tf}", None)
        if cl is None:
            continue
        c1 = np.asarray(cl, dtype=np.float64)
        uq, bc = _htf_native(c1)
        c1 = c1[uq]
        dd = np.diff(c1, prepend=c1[0])
        g = np.where(dd > 0, dd, 0.0)
        lo = np.where(dd < 0, -dd, 0.0)
        ag = pd.Series(g).ewm(alpha=1.0 / 2, adjust=False).mean().values
        al = pd.Series(lo).ewm(alpha=1.0 / 2, adjust=False).mean().values
        rs = ag / np.where(al > 0, al, 1e-10)
        out[f"rsi_2_{tf}"] = ((100.0 - 100.0 / (1.0 + rs))[bc]).astype(np.float32)
        added.append(f"rsi_2_{tf}")
    return added


def add_orb(d, out):
    import numpy as np
    added = []
    try:
        h = np.asarray(d["high_15m"], dtype=np.float64)
        lo = np.asarray(d["low_15m"], dtype=np.float64)
        c = np.asarray(d["close_15m"], dtype=np.float64)
        ts = np.asarray(d["timestamps"])
    except KeyError:
        return []
    sess = _session_ids(ts, True)
    n = len(c)
    oh = np.full(n, np.nan)
    ol = np.full(n, np.nan)
    for s in np.unique(sess):
        idx = np.where(sess == s)[0]
        if len(idx) < 1:
            continue
        first = idx[:1]
        hi = float(np.max(h[first]))
        lw = float(np.min(lo[first]))
        if hi > 0 and lw > 0 and hi > lw:
            oh[idx] = hi
            ol[idx] = lw
    pos = np.where((oh > ol) & np.isfinite(oh), (c - ol) / np.maximum(oh - ol, 1e-10), 0.5)
    out["orb_high"] = _f32(np.where(np.isfinite(oh), oh, c))
    out["orb_low"] = _f32(np.where(np.isfinite(ol), ol, c))
    out["orb_position"] = _f32(pos)
    return ["orb_high", "orb_low", "orb_position"]


def add_wicks(d, out):
    import numpy as np
    import pandas as pd
    added = []
    for tf in TFS:
        try:
            o = np.asarray(d[f"open_{tf}"], dtype=np.float64)
            h = np.asarray(d[f"high_{tf}"], dtype=np.float64)
            lo = np.asarray(d[f"low_{tf}"], dtype=np.float64)
            c = np.asarray(d[f"close_{tf}"], dtype=np.float64)
            v = np.asarray(d[f"volume_{tf}"], dtype=np.float64)
        except KeyError:
            continue
        bc = None
        if tf != "15m":
            uq, bc = _htf_native(o, h, lo, c, v)
            o, h, lo, c, v = (a[uq] for a in (o, h, lo, c, v))
        rng = np.maximum(h - lo, 1e-10)
        body = np.abs(c - o)
        uw = h - np.maximum(o, c)
        lw = np.minimum(o, c) - lo
        _uw = np.round(uw / rng, 3)
        _lw = np.round(lw / rng, 3)
        _bo = np.round(body / rng, 3)
        vs = pd.Series(v)
        avg = vs.shift(1).rolling(20, min_periods=1).mean().values
        _vr = np.round(np.where(avg > 0, v / np.maximum(avg, 1e-10), 1.0), 2)
        hl = pd.Series(h - lo)
        _ar = hl.rolling(50, min_periods=1).apply(lambda x: round(float((x < x.iloc[-1]).sum()) / max(len(x), 1), 3), raw=False).values
        if bc is not None:
            _uw, _lw, _bo, _vr, _ar = (_uw[bc], _lw[bc], _bo[bc], _vr[bc], _ar[bc])
        out[f"bar_upper_wick_{tf}"] = _f32(_uw)
        out[f"bar_lower_wick_{tf}"] = _f32(_lw)
        out[f"bar_body_ratio_{tf}"] = _f32(_bo)
        out[f"bar_vol_ratio_{tf}"] = _f32(_vr)
        out[f"bar_atr_rank_{tf}"] = _f32(_ar)
        sgn = np.sign(c - o)
        run = np.ones(len(c), dtype=np.int8)
        run[sgn == 0] = 0
        for lag in range(1, 7):
            extend = (sgn == np.roll(sgn, lag)) & (sgn != 0)
            extend[:lag] = False
            run = np.where(extend & (run == lag), lag + 1, run)
        hh = (h[2:] > h[1:-1]) & (h[1:-1] > h[:-2])
        hl_ = (lo[2:] > lo[1:-1]) & (lo[1:-1] > lo[:-2])
        ll = (lo[2:] < lo[1:-1]) & (lo[1:-1] < lo[:-2])
        lh = (h[2:] < h[1:-1]) & (h[1:-1] < h[:-2])
        sbull = np.zeros(len(c), dtype=np.int8)
        sbear = np.zeros(len(c), dtype=np.int8)
        sbull[2:] = (hh & hl_).astype(np.int8)
        sbear[2:] = (ll & lh).astype(np.int8)
        _streak = (run * np.sign(sgn)).astype(np.int8)
        if bc is not None:
            _streak, sbull, sbear = (_streak[bc], sbull[bc], sbear[bc])
        out[f"bar_streak_{tf}"] = _streak
        out[f"bar_swing_bull_{tf}"] = sbull
        out[f"bar_swing_bear_{tf}"] = sbear
        added += [f"bar_upper_wick_{tf}", f"bar_lower_wick_{tf}", f"bar_body_ratio_{tf}", f"bar_vol_ratio_{tf}", f"bar_atr_rank_{tf}", f"bar_streak_{tf}", f"bar_swing_bull_{tf}", f"bar_swing_bear_{tf}"]
    return added


def add_kc_position(d, out):
    import numpy as np
    added = []
    for tf in ("15m", "1h", "4h", "D"):
        try:
            up = np.asarray(d[f"kc_upper_{tf}"], dtype=np.float64)
            dn = np.asarray(d[f"kc_lower_{tf}"], dtype=np.float64)
            c = np.asarray(d[f"close_{tf}"], dtype=np.float64)
        except KeyError:
            continue
        pos = np.where(up > dn, (c - dn) / np.maximum(up - dn, 1e-10), 0.5)
        out[f"kc_position_{tf}"] = _f32(np.clip(pos, -0.5, 1.5))
        added.append(f"kc_position_{tf}")
    return added


def add_formations(d, out):
    try:
        from classic_formations import ensure_npz_formation_fields
    except Exception as e:
        log(f"formations skipped (import): {e}")
        return []
    try:
        added = ensure_npz_formation_fields(d)
    except Exception as e:
        log(f"formations skipped (compute): {e}")
        return []
    out.update(added)
    return sorted(added.keys())


def augment_file(path, stocks, dry_run=False, force=()):
    import numpy as np
    from tools.npz_guard import should_allow_overwrite
    d = dict(np.load(path, allow_pickle=True))
    n0, k0 = len(next(iter(d.values()))), len(d)
    out = {}
    added = []
    _force = set(force or ())
    if not stocks:
        added += add_funding_z(d, out)
        added += add_oi_velocity(d, out)
    added += add_orb(d, out)
    added += add_smfi(d, out)
    added += add_vwap(d, out, stocks)
    added += add_rsi2(d, out)
    added += add_wicks(d, out)
    added += add_kc_position(d, out)
    added += add_formations(d, out)
    fresh = {k: v for k, v in out.items() if k not in d or k in _force}
    for k, v in fresh.items():
        assert len(v) == n0, f"{k} len {len(v)} != {n0}"
    if dry_run:
        return {"would_add": sorted(fresh.keys()), "skipped_existing": sorted(set(out) - set(fresh))}
    if not fresh:
        return {"added": [], "note": "nothing missing"}
    tmp = str(path) + ".augment.tmp.npz"
    merged = dict(d)
    merged.update(fresh)
    np.savez_compressed(tmp, **merged)
    ts = np.asarray(merged["timestamps"], dtype=np.int64)
    if ts[-1] > 1e11:
        ts = ts // 1000
    span = float(ts[-1] - ts[0]) / 86400.0
    dt = float(np.median(np.diff(ts.astype(float)))) if len(ts) > 1 else 900.0
    ok, reason = should_allow_overwrite(pathlib.Path(path), span, dt, len(merged), candidate_n=n0, candidate_ts_span_days=span)
    if not ok:
        os.remove(tmp)
        raise ValueError(f"npz_guard: {reason}")
    os.replace(tmp, path)
    return {"added": sorted(fresh.keys())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--mode", choices=["crypto", "stocks"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-keys", default="", help="comma-separated keys to recompute even if present")
    a = ap.parse_args()
    _force_cli = tuple(k.strip() for k in a.force_keys.split(",") if k.strip())
    indir = ROOT / "backtest_v8" / "indicators"
    syms = []
    if a.sym:
        syms = [a.sym]
    elif a.all:
        syms = sorted(p.stem for p in indir.glob("*.npz"))
    else:
        ap.error("need --sym or --all")
    stocks = a.mode == "stocks"
    t0 = time.time()
    total = 0
    for i, sym in enumerate(syms):
        p = indir / f"{sym}.npz"
        if not p.exists():
            log(f"{sym}: no file, skip")
            continue
        try:
            r = augment_file(str(p), stocks, a.dry_run, _force_cli)
            n = len(r.get("added", r.get("would_add", [])))
            total += n
            log(f"[{i + 1}/{len(syms)}] {sym}: +{n} keys {r.get('added', r.get('would_add', []))[:6]}")
        except Exception as e:
            log(f"{sym}: FAILED {type(e).__name__}: {e}"[:200])
    log(f"done: {total} keys in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
