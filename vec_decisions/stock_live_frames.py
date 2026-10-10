"""PARITY LOOP STOCKS 2026-10-06 — option A (director decision, USER "live != vec disparity must be impossible"): the stock NPZ is
built on the SAME frames live tradier_indicators computes on, one 15m step at a time.

Live (tradier_indicators.TradierBarManager.get_bundle + run_cycle) at a moment t inside 15m bar j:
  15m  timesales filtered by filter_strict_market_hours(is_fast_tf=True) (08:30..16:00 ET bar labels), merged into the json cache
  1h   merge_klines(json 1h, resample_tf(bundle 15m, "1h"))  -> 09:30-anchored bins, the bin holding t is FORMING (partial)
  4h   merge_klines(json 4h, resample_tf(bundle 1h, "4h"))   -> get_4h_bin session bins built from the 1h rows that carry close_time
                                                                (the freshly resampled ones; json-only 1h rows are NaT there), FORMING
  D    json D (one row per ET date via the builder's _dedupe_daily_frame, same function live applies) + today's FORMING bar
  every TF clipped to the last LIVE_INDICATOR_MAX_BARS_PER_TF (600) rows, then IndicatorCalculator.compute -> value of the last row.
step_frames() rebuilds exactly that bundle for every 15m row j (json history rows only when their label is strictly before the
forming bin — no future rows), with the live functions themselves (filter_strict_market_hours, resample_tf, the get_bundle merge).
The forming D bar is today's regular-session (09:30 <= label < 16:00 ET) 15m rows up to j, labelled 16:00 ET like the json D rows.
forming_values() evaluates a per-frame array function (backtest_v8_precompute.compute_tf_arrays) on each step frame and keeps the
value of the last row -> per-15m-step arrays. W/M are unchanged (previous fully closed bar, already equal to live)."""
from __future__ import annotations

import multiprocessing as _mp
import os
from datetime import datetime, time as dt_time, timedelta
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone("America/New_York")
OHLCV = ("open", "high", "low", "close", "volume")
TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
LIVE_CLIP = 600
STEP_TFS = ("15m", "1h", "4h", "D")


def to_live_frame(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """DatetimeIndex OHLCV frame -> live json-style frame (timestamp strings in the live writer format)."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    out = pd.DataFrame({c: df[c].astype(float).values for c in OHLCV if c in df.columns})
    out.insert(0, "timestamp", idx.tz_convert("UTC").strftime(TS_FMT))
    return out


def from_live_frame(lf: pd.DataFrame) -> pd.DataFrame:
    """live frame (timestamp strings) -> DatetimeIndex OHLCV frame, row order kept (live computes on the row order it has)."""
    if lf is None or len(lf) == 0:
        return pd.DataFrame(columns=list(OHLCV))
    idx = pd.to_datetime(lf["timestamp"].astype(str), utc=True, format="ISO8601")
    return pd.DataFrame({c: pd.to_numeric(lf[c], errors="coerce").values.astype(float) for c in OHLCV if c in lf.columns}, index=pd.DatetimeIndex(idx, name="timestamp_dt"))


def bundle_merge(df_old: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    """verbatim tradier_indicators.get_bundle.merge_klines (nested there, not importable)."""
    if df_new.empty:
        return df_old
    if df_old.empty:
        return df_new
    if "timestamp" not in df_old.columns:
        return df_new
    if "timestamp" not in df_new.columns:
        return df_old
    combined = pd.concat([df_old, df_new], ignore_index=True).drop_duplicates(subset=["timestamp"], keep="last")
    return combined.sort_values("timestamp").reset_index(drop=True)


def live_rth_15m(df15: pd.DataFrame) -> pd.DataFrame:
    """the live 15m session filter (tradier_indicators.filter_strict_market_hours, is_fast_tf=True) on a DatetimeIndex frame."""
    from tradier_indicators import filter_strict_market_hours
    out = from_live_frame(filter_strict_market_hours(to_live_frame(df15), is_fast_tf=True))
    return out[~out.index.duplicated(keep="last")].sort_index()


def label_1h(idx_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """tradier_indicators.resample_tf(.., '1h') bin label: ET shifted by 09:30, hourly left bins (ET offset is whole hours)."""
    sh = pd.Timedelta(hours=9, minutes=30)
    return (idx_utc.tz_convert("UTC") - sh).floor("h") + sh


def _get_4h_bin(dt):
    """verbatim tradier_indicators.resample_tf get_4h_bin (nested there)."""
    t = dt.time()
    d = dt.date()
    if t < dt_time(9, 30):
        if dt.weekday() == 0:
            prev_d = (dt - timedelta(days=3)).date()
        elif dt.weekday() == 6:
            prev_d = (dt - timedelta(days=2)).date()
        else:
            prev_d = (dt - timedelta(days=1)).date()
        return pd.Timestamp(datetime.combine(prev_d, dt_time(16, 0))).tz_localize(ET)
    elif t < dt_time(13, 0):
        return pd.Timestamp(datetime.combine(d, dt_time(9, 30))).tz_localize(ET)
    elif t < dt_time(16, 0):
        return pd.Timestamp(datetime.combine(d, dt_time(13, 0))).tz_localize(ET)
    else:
        return pd.Timestamp(datetime.combine(d, dt_time(16, 0))).tz_localize(ET)


def label_4h(lab1h_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([_get_4h_bin(x) for x in lab1h_utc.tz_convert(ET)]).tz_convert("UTC")


def label_d(idx_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """json D label convention: the ET date at 16:00 ET."""
    et = idx_utc.tz_convert(ET)
    return pd.DatetimeIndex([ET.localize(datetime.combine(x.date(), dt_time(16, 0))) for x in et]).tz_convert("UTC")


def _agg_row(rows: pd.DataFrame, label_str: str) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": [label_str], "open": [float(rows["open"].iloc[0])], "high": [float(rows["high"].max())], "low": [float(rows["low"].min())], "close": [float(rows["close"].iloc[-1])], "volume": [float(rows["volume"].sum())]})


class StepFrames:
    """per-15m-step live bundles (see module doc). f15 = live-filtered 15m (base grid); json_* = the authentic cache frames."""

    def __init__(self, f15: pd.DataFrame, json_1h: Optional[pd.DataFrame], json_4h: Optional[pd.DataFrame], json_d: Optional[pd.DataFrame], clip: int = LIVE_CLIP):
        from tradier_indicators import resample_tf as _rs
        self._rs = _rs
        self.clip = int(clip)
        self.f15 = f15
        self.lf15 = to_live_frame(f15)
        self.n = len(f15)
        idx = f15.index if f15.index.tz is not None else f15.index.tz_localize("UTC")
        self.ts = idx.tz_convert("UTC")
        self.b1 = label_1h(self.ts)
        self.b4 = label_4h(self.b1)
        self.bd = label_d(self.ts)
        et = self.ts.tz_convert(ET)
        tod = et.hour * 60 + et.minute
        self.rth = (tod >= 9 * 60 + 30) & (tod < 16 * 60)
        self.et_date = np.array([x.date() for x in et])
        self.j1 = to_live_frame(json_1h)
        self.j4 = to_live_frame(json_4h)
        self.j1_ts = pd.to_datetime(self.j1["timestamp"], utc=True, format="ISO8601").values if len(self.j1) else np.array([], dtype="datetime64[ns]")
        self.j4_ts = pd.to_datetime(self.j4["timestamp"], utc=True, format="ISO8601").values if len(self.j4) else np.array([], dtype="datetime64[ns]")
        if json_d is not None and len(json_d):
            d = json_d.sort_index()
            self.jd = to_live_frame(d)
            self.jd_date = np.array([x.date() for x in d.index.tz_convert(ET)])
        else:
            self.jd = pd.DataFrame()
            self.jd_date = np.array([])

    def _tail(self, lf: pd.DataFrame) -> pd.DataFrame:
        return lf.tail(self.clip).reset_index(drop=True) if len(lf) > self.clip else lf

    def bundle(self, j: int, tfs=STEP_TFS) -> Dict[str, pd.DataFrame]:
        """live bundle at 15m step j (clipped, live json-style frames)."""
        out: Dict[str, pd.DataFrame] = {}
        b15 = self.lf15.iloc[: j + 1]
        if "15m" in tfs:
            out["15m"] = self._tail(b15)
        if "1h" in tfs or "4h" in tfs:
            b1 = self.b1[j].to_datetime64()
            hist1 = self.j1.loc[self.j1_ts < b1] if len(self.j1) else self.j1
            new1 = self._rs(b15, "1h")
            bundle1 = bundle_merge(hist1, new1) if not new1.empty else hist1
            if "1h" in tfs:
                out["1h"] = self._tail(bundle1)
            if "4h" in tfs:
                b4 = self.b4[j].to_datetime64()
                hist4 = self.j4.loc[self.j4_ts < b4] if len(self.j4) else self.j4
                new4 = self._rs(bundle1, "4h")
                bundle4 = bundle_merge(hist4, new4) if not new4.empty else hist4
                out["4h"] = self._tail(bundle4)
        if "D" in tfs:
            day = self.et_date[j]
            hist = self.jd.loc[self.jd_date < day] if len(self.jd) else self.jd
            lo = int(np.searchsorted(self.et_date, day, side="left"))
            sel = np.arange(lo, j + 1)
            sel = sel[self.rth[lo: j + 1]]
            if len(sel):
                part = _agg_row(self.lf15.iloc[sel], self.bd[j].strftime(TS_FMT))
                bundled = pd.concat([hist, part], ignore_index=True) if len(hist) else part
            else:
                bundled = hist.reset_index(drop=True)
            out["D"] = self._tail(bundled)
        return out

    def forming_label(self, tf: str) -> np.ndarray:
        """epoch seconds of the bin each step's last row belongs to (timestamp_<tf> token: changes when the parent bar changes)."""
        lab = {"15m": self.ts, "1h": self.b1, "4h": self.b4, "D": self.bd}[tf]
        return np.array([int(x.timestamp()) for x in lab], dtype=np.int64)


_CTX: Dict[str, object] = {}


def _eval_range(args):
    j0, j1 = args
    sf: StepFrames = _CTX["sf"]
    fn: Callable = _CTX["fn"]
    tfs = _CTX["tfs"]
    rows: List[Dict[str, Dict[str, object]]] = []
    for j in range(j0, j1):
        b = sf.bundle(j, tfs)
        per_tf: Dict[str, Dict[str, object]] = {}
        for tf in tfs:
            lf = b.get(tf)
            if lf is None or len(lf) == 0:
                per_tf[tf] = {}
                continue
            arrs = fn(from_live_frame(lf), tf)
            per_tf[tf] = {k: v[-1] for k, v in arrs.items() if isinstance(v, np.ndarray) and v.ndim == 1 and len(v) == len(lf)}
        rows.append(per_tf)
    return rows


def forming_values(sf: StepFrames, fn: Callable[[pd.DataFrame, str], Dict[str, np.ndarray]], tfs=STEP_TFS, workers: Optional[int] = None) -> Dict[str, Dict[str, np.ndarray]]:
    """{tf: {field: array(n)}} — fn evaluated on every step's live frame, value of the last row. Steps without a frame / field -> 0."""
    n = sf.n
    if workers is None:
        workers = int(os.environ.get("NPZ_STOCK_STEP_WORKERS", "1") or 1)
    if _mp.current_process().daemon:
        workers = 1
    _CTX.update(sf=sf, fn=fn, tfs=tuple(tfs))
    chunk = max(1, -(-n // max(1, workers * 4)))
    ranges = [(a, min(n, a + chunk)) for a in range(0, n, chunk)]
    if workers > 1 and n > 1:
        with _mp.get_context("fork").Pool(workers) as pool:
            parts = pool.map(_eval_range, ranges)
    else:
        parts = [_eval_range(r) for r in ranges]
    rows = [r for p in parts for r in p]
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf in tfs:
        keys: Dict[str, np.dtype] = {}
        for r in rows:
            for k, v in r[tf].items():
                if k not in keys:
                    keys[k] = np.asarray(v).dtype
        res: Dict[str, np.ndarray] = {}
        for k, dt in keys.items():
            a = np.zeros(n, dtype=dt) if dt.kind in "biufc" else np.empty(n, dtype=object)
            for j, r in enumerate(rows):
                v = r[tf].get(k)
                if v is not None:
                    a[j] = v
            res[k] = a
        out[tf] = res
    return out


def stock_live_extras(df: pd.DataFrame, tf: str) -> Dict[str, float]:
    """last-row LIVE values (tradier_indicators.IndicatorCalculator.compute + live_parity_keys.derive_bar_keys, same functions) for the fields the
    builder otherwise derives in compute_symbol post-passes from broadcast/full-history arrays: lr_trend/linearity/slope_close (linreg_features),
    lr_upper/lower/pct_b (linreg_channel 2.5), lrL_pct_b/slope/r2 (LR_CHANNEL_LONG_LENGTHS / STDEV_SLOPE_LOOKBACK_15M), t_up/tco/tcu (hull),
    close_3bar/close_5bar/ema_9_above_21/volume_sma_1h/choppiness_4h."""
    import tradier_indicators as TI
    out: Dict[str, float] = {}
    close = df["close"].astype(float).reset_index(drop=True)
    try:
        sl, lin = TI.linreg_features(close, TI.LINREG_LENGTH)
        if sl is not None:
            out[f"lr_trend_{tf}"] = sl
        if lin is not None:
            out[f"linearity_{tf}"] = lin
            out[f"slope_close_{tf}"] = sl
        if tf in ("5m", "15m", "1h", "4h", "D"):
            u, l_, pb = TI.linreg_channel(close, TI.LINREG_LENGTH, std_mult=2.5)
            if pb is not None:
                out[f"lr_upper_{tf}"] = u
                out[f"lr_lower_{tf}"] = l_
                out[f"lr_pct_b_{tf}"] = pb
            L = (getattr(TI.config, "LR_CHANNEL_LONG_LENGTHS", None) or {}).get(tf)
            if not L and tf in ("5m", "15m"):
                L = int(getattr(TI.config, "STDEV_SLOPE_LOOKBACK_15M", 96))
            if L and len(close) >= int(L):
                _u, _l, lpb = TI.linreg_channel(close, int(L), std_mult=2.5)
                sp, rv, _ = TI.calculate_regression_slope_line(close.iloc[-int(L):])
                if lpb is not None and sp is not None:
                    out[f"lrL_pct_b_{tf}"] = lpb
                    out[f"lrL_slope_{tf}"] = round(float(sp), 6)
                    out[f"lrL_r2_{tf}"] = round(float(rv), 6)
        t_up, tco, tcu = TI.hull_trend_indicators(close, length_short=9, length_long=21)
        for k, v in ((f"t_up_{tf}", t_up), (f"tco_{tf}", tco), (f"tcu_{tf}", tcu)):
            if v is not None:
                out[k] = v
    except Exception:
        pass
    try:
        import live_parity_keys as LPK
        LPK.derive_bar_keys(df.reset_index(drop=True), tf, out)
    except Exception:
        pass
    return out


def builder_step_fn(compute_tf_arrays: Callable) -> Callable:
    """per-frame function for forming_values: the builder arrays + the live extras (as last-row values)."""
    def _fn(df: pd.DataFrame, tf: str) -> Dict[str, np.ndarray]:
        arrs = compute_tf_arrays(df, tf, last_only=True)
        n = len(df)
        for k, v in stock_live_extras(df, tf).items():
            if isinstance(v, (bool, np.bool_)):
                v = int(v)
            if isinstance(v, (int, float, np.integer, np.floating)) and np.isfinite(float(v)):
                a = np.zeros(n, dtype=np.int8 if isinstance(v, (int, np.integer)) and k.startswith(("t_up_", "tco_", "tcu_", "ema_9_above_21_")) else np.float64)
                a[-1] = v
                arrs[k] = a
        return arrs
    return _fn
