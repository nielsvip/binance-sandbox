"""SINGLE SOURCE OF TRUTH for the breakout/sma force-open ENTRY signal — imported by BOTH the live
watchdog (ez_manage.momentum_sma_watchdog_loop, scalar/per-bar) AND the backtest (v8_vec_sweep / htf
engine, vector/per-bar-array). Same logic, same fields → entries are identical by construction.

USER 2026-06-02 "trades 100% identical between backtest and live". The ONLY way to guarantee that is a
shared function; any inline re-implementation reintroduces drift.

ENTRY (flat → open) when wt1_15m is moving in favor AND price either breaks the prev-bar 1h Donchian OR is
beyond sma_200_15m by `pct` percent:
  LONG :  wt1_15m > wt1_15m_prev  AND  (px > dc_high_1h_prev   OR  px > sma_200_15m*(1+pct/100))
  SHORT:  wt1_15m < wt1_15m_prev  AND  (px < dc_low_1h_prev    OR  px < sma_200_15m*(1-pct/100))

The vector form shifts every input by one bar (prev-bar decision inputs) for no-lookahead, exactly mirroring
the live dict which already carries *_prev fields. Parity asserted by tools/breakout_opener_parity.py.

2026-06-03 USER MANDATE — the live watchdog (ez_manage.momentum_sma_watchdog_loop) was upgraded to the
comprehensive force-opener (multi-TF Donchian ladder + sma±pct/WT-cross + escalating augment). The functions
below are the SINGLE SOURCE the v8 engines (v8_quick_engine / backtest_v8_engine / v8_vec_sweep / per_sym)
MUST import so backtest entries == live by construction. KEEP IN SYNC with the live inline logic until live
is refactored to import these directly. Live uses 3m WT cross (wt1_3m vs wt2_3m); NPZ base TF is 5m so the
backtest analog uses wt1_5m/wt2_5m (documented base-TF difference — there is NO wt1_3m in NPZ).
  REQ3 DC breakout (NO wt filter):  LONG px>=dc_high_{tf} (or dc_high_crossover_{tf});
                                    SHORT px<=dc_low_{tf} (or dc_low_crossunder_{tf}). TFs 15m/1h/4h/D.
                                    Size = base_usd * mult[largest tf hit], capped. (No Weekly DC exists.)
  REQ1 SMA+WT:  LONG px>sma_200_15m*(1+pct/100) AND wt1_base>wt2_base; SHORT mirror.
"""
import numpy as np

FIELDS = ("current_price", "sma_200_15m", "wt1_15m", "wt1_15m_prev", "dc_high_1h_prev", "dc_low_1h_prev")

DC_LADDER_TFS = ("15m", "1h", "4h", "D")  # ordered small→large; W absent from live + NPZ


def dc_breakout_largest_tf_scalar(ind: dict, is_long: bool, tfs=DC_LADDER_TFS) -> str:
    """REQ3 live form. Returns the LARGEST TF whose Donchian is broken (price beyond the channel OR the
    precomputed crossover/crossunder flag), or '' if none. NO WaveTrend filter. `ind` is the live dict."""
    def f(k, d=0.0):
        try:
            v = ind.get(k)
            return float(v) if v is not None else d
        except Exception:
            return d
    px = f("current_price")
    hit = ""
    if px <= 0:
        return hit
    for tf in tfs:
        if is_long:
            lvl = f(f"dc_high_{tf}")
            if (lvl > 0 and px >= lvl) or bool(ind.get(f"dc_high_crossover_{tf}", False)):
                hit = tf
        else:
            lvl = f(f"dc_low_{tf}")
            if (lvl > 0 and px <= lvl) or bool(ind.get(f"dc_low_crossunder_{tf}", False)):
                hit = tf
    return hit


def dc_breakout_signal_vec(nd: dict, is_long: bool, tf: str) -> np.ndarray:
    """REQ3 backtest form for ONE timeframe. Decision inputs are PREV-bar (shift 1) — no lookahead.
    Returns a bool array: bar i broke the {tf} Donchian. Caller ladders across TFs for size."""
    close = np.asarray(nd.get("close"), float)
    n = len(close)
    px = np.concatenate([[close[0]], close[:-1]])
    def prev(key):
        a = nd.get(key)
        if a is None:
            return None
        a = np.asarray(a, float)
        return np.concatenate([[a[0]], a[:-1]])
    if is_long:
        lvl = prev(f"dc_high_{tf}")
        xover = nd.get(f"dc_high_crossover_{tf}")
        sig = (lvl is not None) & np.zeros(n, dtype=bool) if lvl is None else ((lvl > 0) & (px >= lvl))
        if xover is not None:
            xa = np.asarray(xover, bool)
            sig = sig | np.concatenate([[xa[0]], xa[:-1]])
        return (px > 0) & sig
    lvl = prev(f"dc_low_{tf}")
    xunder = nd.get(f"dc_low_crossunder_{tf}")
    sig = np.zeros(n, dtype=bool) if lvl is None else ((lvl > 0) & (px <= lvl))
    if xunder is not None:
        xa = np.asarray(xunder, bool)
        sig = sig | np.concatenate([[xa[0]], xa[:-1]])
    return (px > 0) & sig


def sma_wt_cross_signal_scalar(ind: dict, is_long: bool, pct: float, base_tf: str = "3m") -> bool:
    """REQ1 live form. sma_200_15m ±pct% + base-TF WT cross in favor (wt1_base vs wt2_base)."""
    def f(k, d=0.0):
        try:
            v = ind.get(k)
            return float(v) if v is not None else d
        except Exception:
            return d
    px = f("current_price"); sma = f("sma_200_15m")
    if px <= 0 or sma <= 0:
        return False
    w1 = f(f"wt1_{base_tf}"); w2 = f(f"wt2_{base_tf}")
    if is_long:
        return px > sma * (1.0 + pct / 100.0) and w1 > w2
    return px < sma * (1.0 - pct / 100.0) and w1 < w2


def sma_wt_cross_signal_vec(nd: dict, is_long: bool, pct: float, base_tf: str = "5m") -> np.ndarray:
    """REQ1 backtest form. NPZ base TF is 5m → wt1_5m/wt2_5m. Prev-bar inputs, no lookahead."""
    close = np.asarray(nd.get("close"), float)
    n = len(close)
    px = np.concatenate([[close[0]], close[:-1]])
    def prev(key):
        a = nd.get(key)
        if a is None:
            return None
        a = np.asarray(a, float)
        return np.concatenate([[a[0]], a[:-1]])
    sma = prev("sma_200_15m"); w1 = prev(f"wt1_{base_tf}"); w2 = prev(f"wt2_{base_tf}")
    if sma is None or w1 is None or w2 is None:
        return np.zeros(n, dtype=bool)
    valid = (px > 0) & (sma > 0)
    if is_long:
        return valid & (px > sma * (1.0 + pct / 100.0)) & (w1 > w2)
    return valid & (px < sma * (1.0 - pct / 100.0)) & (w1 < w2)


def entry_signal_scalar(ind: dict, is_long: bool, pct: float) -> bool:
    """LIVE per-bar form. `ind` is the live indicator dict (already has *_prev fields)."""
    def f(k, d=0.0):
        try:
            v = ind.get(k)
            return float(v) if v is not None else d
        except Exception:
            return d
    px = f("current_price"); sma = f("sma_200_15m")
    w1 = f("wt1_15m"); w1p = f("wt1_15m_prev", w1)
    if px <= 0 or sma <= 0:
        return False
    if is_long:
        wt_ok = w1 > w1p
        dch = f("dc_high_1h_prev", f("dc_high_1h"))
        return bool(wt_ok and ((dch > 0 and px > dch) or px > sma * (1.0 + pct / 100.0)))
    wt_ok = w1 < w1p
    dcl = f("dc_low_1h_prev", f("dc_low_1h"))
    return bool(wt_ok and ((dcl > 0 and px < dcl) or px < sma * (1.0 - pct / 100.0)))


def entry_signal_vec(nd: dict, is_long: bool, pct: float) -> np.ndarray:
    """BACKTEST array form. `nd` holds base-TF-aligned arrays. Decision inputs are PREV-bar (shift 1) so
    bar i uses only information available entering bar i — identical to the live dict's *_prev semantics."""
    close = np.asarray(nd.get("close"), float)
    n = len(close)
    def prev(key, fallback=None):
        a = nd.get(key)
        if a is None:
            a = nd.get(fallback) if fallback else None
        if a is None:
            return None
        a = np.asarray(a, float)
        return np.concatenate([[a[0]], a[:-1]])
    px = np.concatenate([[close[0]], close[:-1]])            # prev close = decision price
    sma = prev("sma_200_15m"); w1 = prev("wt1_15m")
    w1p = prev("wt1_15m_prev")
    if w1p is None and w1 is not None:                        # derive prev-of-prev if not stored
        w1p = np.concatenate([[w1[0]], w1[:-1]])
    dch = prev("dc_high_1h_prev", "dc_high_1h")
    dcl = prev("dc_low_1h_prev", "dc_low_1h")
    if sma is None or w1 is None or w1p is None:
        return np.zeros(n, dtype=bool)
    valid = (px > 0) & (sma > 0)
    if is_long:
        wt_ok = w1 > w1p
        dc_trig = (dch > 0) & (px > dch) if dch is not None else np.zeros(n, dtype=bool)
        sma_trig = px > sma * (1.0 + pct / 100.0)
        return valid & wt_ok & (dc_trig | sma_trig)
    wt_ok = w1 < w1p
    dc_trig = (dcl > 0) & (px < dcl) if dcl is not None else np.zeros(n, dtype=bool)
    sma_trig = px < sma * (1.0 - pct / 100.0)
    return valid & wt_ok & (dc_trig | sma_trig)
