"""WT cross-TF composite family — ONE formula for the NPZ builder and live tradier_indicators (USER 2026-10-06: live≠vec disparity must be impossible).

Formula = backtest_v8_precompute.py WT composite pass (~1941-2023, the backtest truth):
  TFs 15m, 1h, 4h, D, W, M (stock NPZ has no 5m/3m/1m WT); a TF counts only when its fields exist.
  wt_bull_alignment = #TF with wt_bullish_{tf} > 0, wt_bear_alignment = #TF with wt_bullish_{tf} <= 0 (wt_bullish = wt1 > wt2).
  wt_composite_long  = sum_tf max(0,  wt_score_{tf}) * w_tf, wt_composite_short = sum_tf max(0, -wt_score_{tf}) * w_tf,
  w = {15m: 2, 1h: 3, 4h: 4, D: 5, W: 2, M: 1}; wt_score = float32(wt1 - wt2); accumulation in float32 exactly like the builder's in-place add.
  wt_composite_delta = float32(long - short); wt_composite_bias = 1 / -1 / 0 (long > short / short > long / equal). No clamp, no hysteresis.
W/M WaveTrend for live = tradier_indicators.wavetrend on D bars resampled W-MON / MS (left label, left closed), read with the tradier lag-2 as-of rule
(_broadcast_asof_indices: the previous fully closed W/M bar) — same as the builder (precompute ~1849-1866 + 82-112).
Works on scalars (live dict) and arrays (NPZ)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np

COMPOSITE_TFS = ("15m", "1h", "4h", "D", "W", "M")
WEIGHTS = {"15m": 2, "1h": 3, "4h": 4, "D": 5, "W": 2, "M": 1}
BIAS_LABEL = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}


def _num(v: Any) -> Optional[np.ndarray]:
    if v is None:
        return None
    try:
        a = np.asarray(v, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    return a


def composite_fields(get: Callable[[str], Any], tfs=COMPOSITE_TFS) -> Dict[str, Any]:
    """get(key) -> scalar / array / None.  Returns numpy scalars (0-d) for scalar inputs, arrays for array inputs."""
    bull = bear = None
    comp_l = comp_s = None
    for tf in tfs:
        w1, w2 = _num(get(f"wt1_{tf}")), _num(get(f"wt2_{tf}"))
        b = _num(get(f"wt_bullish_{tf}"))
        if b is None and w1 is not None and w2 is not None:
            b = (w1 > w2).astype(np.float64)
        if b is not None:
            bull = (b > 0).astype(np.int8) if bull is None else (bull + (b > 0).astype(np.int8)).astype(np.int8)
            bear = (b <= 0).astype(np.int8) if bear is None else (bear + (b <= 0).astype(np.int8)).astype(np.int8)
        s = _num(get(f"wt_score_{tf}"))
        if s is None and w1 is not None and w2 is not None:
            s = np.asarray(np.float32(w1 - w2) if np.ndim(w1) == 0 else (w1 - w2).astype(np.float32), dtype=np.float64)
        if s is not None:
            s = np.asarray(s, dtype=np.float64)
            w = WEIGHTS.get(tf, 1)
            if comp_l is None:
                comp_l = np.zeros(np.shape(s), dtype=np.float32)
                comp_s = np.zeros(np.shape(s), dtype=np.float32)
            comp_l = (comp_l.astype(np.float64) + np.maximum(0, s) * w).astype(np.float32)
            comp_s = (comp_s.astype(np.float64) + np.maximum(0, -s) * w).astype(np.float32)
    out: Dict[str, Any] = {}
    if bull is not None:
        out["wt_bull_alignment"] = bull
        out["wt_bear_alignment"] = bear
    if comp_l is not None:
        out["wt_composite_long"] = comp_l
        out["wt_composite_short"] = comp_s
        out["wt_composite_delta"] = (comp_l - comp_s).astype(np.float32)
        out["wt_composite_bias"] = np.where(comp_l > comp_s, 1, np.where(comp_s > comp_l, -1, 0)).astype(np.int8)
    return out


def scalar_fields(get: Callable[[str], Any], tfs=COMPOSITE_TFS) -> Dict[str, Any]:
    """live dict form: python scalars; wt_composite_bias as the live label ('LONG'/'SHORT'/'NEUTRAL')."""
    f = composite_fields(get, tfs)
    out: Dict[str, Any] = {}
    for k in ("wt_bull_alignment", "wt_bear_alignment"):
        if k in f:
            out[k] = int(np.asarray(f[k]).item())
    for k in ("wt_composite_long", "wt_composite_short", "wt_composite_delta"):
        if k in f:
            out[k] = float(np.asarray(f[k]).item())
    if "wt_composite_bias" in f:
        out["wt_composite_bias"] = BIAS_LABEL[int(np.asarray(f["wt_composite_bias"]).item())]
    return out


def wm_frames_from_daily(df_daily):
    """D bars (DatetimeIndex) -> {'W': weekly, 'M': monthly} exactly like the builder's tradier D->W/M synthesis."""
    out = {}
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for tf, rule in (("W", "W-MON"), ("M", "MS")):
        try:
            r = df_daily.resample(rule, label="left", closed="left").agg(agg).dropna()
        except Exception:
            r = None
        if r is not None and len(r) >= 20:
            out[tf] = r
    return out


def asof_index_tradier(source_ts, t: float) -> int:
    """builder _broadcast_asof_indices for tradier 1h/4h/D/W/M (lag 2): last fully closed left-labelled bar at time t; -1 = not yet available."""
    src = np.asarray(source_ts, dtype=np.int64)
    return int(min(int(np.searchsorted(src, np.int64(t), side="right")) - 2, len(src) - 1))
