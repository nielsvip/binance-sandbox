"""PARITY 2026-10-06 — guard: every STOCK NPZ field the builder writes must equal what live tradier_indicators emits at the same bar (same frame).

For each symbol x TF the builder (backtest_v8_precompute.compute_tf_arrays, MODE='tradier') runs once on the klines frame; live
(tradier_indicators.IndicatorCalculator.compute) runs on the prefix ending at each cut bar.  Every field both produce is compared
(strings via the NPZ integer encodings).  Tolerance: 5e-3 absolute (float32 storage) — anything else is a live != vec disparity.
Before the staged builder patch (data/parity/precompute_live_equal_20261006.patch) this FAILS by design. Frame construction (extended-hours
15m, clock-hour 1h, completed vs forming HTF bars) is NOT covered here: both sides get the same frame.
Env: PARITY_STOCK_SYMS (default AMD), PARITY_STOCK_CUTS (default 8), PARITY_STOCK_KLINES (default <BASE_PATH>/klines_cache/tradier),
PARITY_BUILDER_MODULE (default backtest_v8_precompute).  Needs scipy-free ez/tradier imports + klines: server-side."""
import importlib
import os
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TFS = ("15m", "1h", "4h", "D")
TOL = 5e-3
_STR = {"HIDDEN_BULL": 2.0, "HIDDEN_BEAR": -2.0, "BUY": 1.0, "SELL": -1.0, "TRANSITIONING": 0.0, "green": 1.0, "red": -1.0, "neutral": 0.0}


def _num(P, k, v):
    from tradier_vec_exact import _encode_label
    if v is None and k.startswith(("wt_divergence_", "wt_cross_rising_", "wt_peak", "wt_trough", "wt_cross_value_", "wt_cross_prev_value_")):
        return 0.0
    if isinstance(v, (bool, np.bool_, int, float, np.integer, np.floating)):
        return float(v)
    if isinstance(v, str):
        if k.startswith(("wt_divergence_", "wt_signal_", "wt_wave_phase_", "ha_")) and v in _STR:
            return _STR[v]
        if k.startswith("bar_pattern_") and hasattr(P, "BAR_PATTERN_CODES"):
            return float(P.BAR_PATTERN_CODES.get(v, -99))
        if k.startswith("bar_vol_regime_") and hasattr(P, "BAR_VOL_REGIME_CODES"):
            return float(P.BAR_VOL_REGIME_CODES.get(v, -99))
        e = _encode_label(k, v)
        return float(e) if isinstance(e, (int, float)) else None
    return None


def _audit():
    P = importlib.import_module(os.environ.get("PARITY_BUILDER_MODULE", "backtest_v8_precompute"))
    import tradier_indicators as TI
    P.MODE = "tradier"
    kdir = pathlib.Path(os.environ.get("PARITY_STOCK_KLINES", str(pathlib.Path(os.environ.get("BASE_PATH", str(ROOT))) / "klines_cache" / "tradier")))
    syms = os.environ.get("PARITY_STOCK_SYMS", "AMD").split(",")
    ncut = int(os.environ.get("PARITY_STOCK_CUTS", "8"))
    bad, compared = [], 0
    for s in syms:
        for tf in TFS:
            f = P.load_klines(kdir / f"{s}_{tf}.json")
            if f is None or len(f) < 450:
                continue
            f = f.tail(2000)
            b = P.compute_tf_arrays(f, tf)
            n = len(f)
            calc = TI.IndicatorCalculator()
            for j in np.linspace(400, n - 1, ncut).astype(int):
                sl = f.iloc[: j + 1].copy()
                sl["timestamp"] = [x.isoformat() for x in sl.index]
                sl = sl.reset_index(drop=True)
                live = calc.compute(sl, s, tf, None, None, False) or {}
                for k, v in live.items():
                    if (not k.endswith("_" + tf) and not k.endswith(f"_{tf}_prev")) or k not in b:
                        continue
                    a = np.asarray(b[k])
                    if a.ndim != 1 or len(a) != n:
                        continue
                    lv = _num(P, k, v)
                    bv = float(a[j]) if a.dtype.kind in "fiub" else _num(P, k, str(a[j]))
                    if lv is None or bv is None:
                        continue
                    compared += 1
                    tol = max(TOL, 1e-6 * abs(bv))  # float32 storage: absolute 5e-3, relative 1e-6 for large magnitudes (share volumes, 1e-10-floored ratios)
                    if abs(lv - bv) > tol:
                        bad.append((s, tf, int(j), k, lv, bv))
    return compared, bad


def test_stock_builder_equals_live_tradier_indicators():
    base = pathlib.Path(os.environ.get("BASE_PATH", str(ROOT)))
    kdir = pathlib.Path(os.environ.get("PARITY_STOCK_KLINES", str(base / "klines_cache" / "tradier")))
    if not (kdir / f"{os.environ.get('PARITY_STOCK_SYMS', 'AMD').split(',')[0]}_15m.json").exists():
        pytest.skip(f"no crypto klines under {kdir}")
    try:
        compared, bad = _audit()
    except ImportError as e:
        pytest.skip(f"indicator deps missing here: {e}")
    assert compared > 0
    fields = sorted({(k, tf) for _, tf, _, k, _, _ in bad})
    assert not bad, f"{len(bad)} live!=builder values over {len(fields)} field x TF, e.g. {bad[:5]}"


# ---- LIVE FRAMES (option A, BIBLE §68.2.6, 2026-10-06) -------------------------------------------------------------------------------
# Builder NPZ row j (compute_symbol return_arrays, MODE tradier) vs live IndicatorCalculator.compute on the frame LIVE has at that 15m step,
# rebuilt here independently from the raw json cache with live's own functions: 15m = filter_strict_market_hours(is_fast_tf=True) rows <= t;
# 1h = get_bundle merge(json 1h rows before the forming bin, resample_tf(15m, "1h")); 4h = merge(json 4h before the forming bin,
# resample_tf(that 1h bundle, "4h")); D = json D one row per ET date (_dedupe_daily_frame, as live get_bundle now does) before today + today's
# forming bar (regular-session 15m rows so far); every TF tail(LIVE_INDICATOR_MAX_BARS_PER_TF=600). Env: PARITY_STOCK_SYMS, PARITY_STOCK_CUTS
# (default 12 steps/sym), PARITY_STOCK_KLINES (default <BASE_PATH>/klines_cache_backtest/tradier on servers).


def _live_frames_at(TI, P, raw, t):
    import pandas as pd
    def _merge(o, n):
        if n.empty:
            return o
        if o.empty:
            return n
        c = pd.concat([o, n], ignore_index=True).drop_duplicates(subset=["timestamp"], keep="last")
        return c.sort_values("timestamp").reset_index(drop=True)
    def _ts(df):
        return pd.to_datetime(df["timestamp"].astype(str), utc=True, format="ISO8601")
    f15 = raw["15m"]
    b15 = f15.loc[_ts(f15) <= t].reset_index(drop=True)
    out = {"15m": b15}
    if len(b15) == 0:
        return {}  # step before the first live-session 15m bar (legacy grids only)
    new1 = TI.resample_tf(b15, "1h")
    lab1 = pd.Timestamp(new1["close_time"].iloc[-1])
    j1 = raw["1h"]
    b1 = _merge(j1.loc[_ts(j1) < lab1].reset_index(drop=True), new1)
    out["1h"] = b1
    new4 = TI.resample_tf(b1, "4h")
    lab4 = pd.Timestamp(new4["close_time"].iloc[-1])
    j4 = raw["4h"]
    out["4h"] = _merge(j4.loc[_ts(j4) < lab4].reset_index(drop=True), new4)
    dd = raw["D"]
    et = t.tz_convert("America/New_York")
    d_hist = dd.loc[[x.date() < et.date() for x in _ts(dd).dt.tz_convert("America/New_York")]]
    tod = _ts(b15).dt.tz_convert("America/New_York")
    m = [(x.date() == et.date()) and ((x.hour, x.minute) >= (9, 30)) and (x.hour < 16) for x in tod]
    rows = b15.loc[m]
    if len(rows):
        import pytz
        from datetime import datetime, time as dtt
        lab = pytz.timezone("America/New_York").localize(datetime.combine(et.date(), dtt(16, 0))).astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        part = pd.DataFrame({"timestamp": [lab], "open": [float(rows["open"].iloc[0])], "high": [float(rows["high"].max())], "low": [float(rows["low"].min())], "close": [float(rows["close"].iloc[-1])], "volume": [float(rows["volume"].sum())]})
        d_hist = pd.concat([d_hist[["timestamp", "open", "high", "low", "close", "volume"]], part], ignore_index=True)
    out["D"] = d_hist.reset_index(drop=True)
    return {k: (v.tail(600).reset_index(drop=True) if len(v) > 600 else v.reset_index(drop=True)) for k, v in out.items()}


def _raw_bundle(TI, P, kdir, s):
    import json
    import pandas as pd
    raw = {}
    srcs = [kdir] + [pathlib.Path(x) for x in os.environ.get("PARITY_STOCK_KLINES_FILL", "").split(",") if x]
    for tf in TFS:
        parts = []
        for d in srcs:  # same source union as the builder (primary wins, gateway/macbook fill missing bars)
            fp = d / f"{s}_{tf}.json"
            if fp.exists():
                x = pd.DataFrame(json.load(open(fp)))
                if len(x) >= 30 and "timestamp" in x.columns:
                    parts.append(x)
        df = pd.concat(parts, ignore_index=True)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        _pts = pd.to_datetime(df["timestamp"].astype(str), utc=True, format="ISO8601")
        df = df.loc[~_pts.duplicated(keep="first")].reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(str), utc=True, format="ISO8601").dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if tf == "15m":
            df = TI.filter_strict_market_hours(df, is_fast_tf=True)
        if tf == "D":
            ix = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
            df.index = pd.DatetimeIndex(ix).rename(None)
            df = df.sort_index()
            df = df[~df.index.duplicated(keep="last")]
            df = P._dedupe_daily_frame(df)
        raw[tf] = df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    return raw


def _audit_live_frames():
    import pandas as pd
    P = importlib.import_module(os.environ.get("PARITY_BUILDER_MODULE", "backtest_v8_precompute"))
    import tradier_indicators as TI
    base = pathlib.Path(os.environ.get("BASE_PATH", str(P.BASE_PATH)))
    kdir = pathlib.Path(os.environ.get("PARITY_STOCK_KLINES", str(base / "klines_cache_backtest" / "tradier")))
    syms = os.environ.get("PARITY_STOCK_SYMS", "AMD").split(",")
    ncut = int(os.environ.get("PARITY_STOCK_CUTS", "12"))
    bad, compared, per = [], 0, {}
    for s in syms:
        _cache = os.environ.get("PARITY_STOCK_BUILT_DIR")
        _cp = pathlib.Path(_cache) / f"out_{s}.npz" if _cache else None
        if _cp is not None and _cp.exists():
            merged = dict(np.load(_cp, allow_pickle=True))
        else:
            merged = P.compute_symbol(s, "tradier", return_arrays=True)
        assert isinstance(merged, dict), f"{s}: builder returned {merged!r}"
        ts = np.asarray(merged["timestamps"], dtype=np.int64)
        raw = _raw_bundle(TI, P, kdir, s)
        calc = TI.IndicatorCalculator()
        for j in np.unique(np.linspace(0, len(ts) - 1, ncut).astype(int)):
            t = pd.Timestamp(int(ts[j]), unit="s", tz="UTC")
            frames = _live_frames_at(TI, P, raw, t)
            for tf, fr in frames.items():
                if len(fr) < 30:
                    continue
                live = calc.compute(fr.copy(), s, tf, None, None, False) or {}
                for k, v in live.items():
                    if (not k.endswith("_" + tf) and not k.endswith(f"_{tf}_prev")) or k not in merged:
                        continue
                    a = np.asarray(merged[k])
                    if a.ndim != 1 or len(a) != len(ts):
                        continue
                    lv = _num(P, k, v)
                    bv = float(a[j]) if a.dtype.kind in "fiub" else _num(P, k, str(a[j]))
                    if lv is None or bv is None:
                        continue
                    compared += 1
                    tol = max(TOL, 1e-6 * abs(bv))
                    ok = abs(lv - bv) <= tol
                    per.setdefault((tf, k), [0, 0])[0 if ok else 1] += 1
                    if not ok:
                        bad.append((s, tf, int(j), k, lv, bv))
    return compared, bad, per


def test_stock_builder_equals_live_on_live_frames():
    try:
        compared, bad, per = _audit_live_frames()
    except ImportError as e:
        pytest.skip(f"indicator deps missing here: {e}")
    except FileNotFoundError as e:
        pytest.skip(f"no stock klines here: {e}")
    assert compared > 0
    fields = sorted({(tf, k) for _, tf, _, k, _, _ in bad})
    assert not bad, f"{len(bad)} live!=builder values on LIVE frames over {len(fields)} field x TF, e.g. {bad[:5]} fields {fields[:20]}"
