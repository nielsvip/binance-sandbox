"""Shared MTF-conditioned funding gate — used by BOTH live (ez_positions_quick.execute_trade_wrapper)
and the vec sweep (v8_vec_sweep) so the veto logic can never drift out of sync.

Veto a NEW entry only when funding is extreme AND the higher timeframes disagree with the trade:
  LONG  veto: funding >= FUNDING_GATE_LONG_MAX  AND  (#HTF WT-bullish over 15m/1h/4h/D) <= FUNDING_GATE_MTF_LONG_MAX_BULL_TFS
  SHORT veto: funding <= FUNDING_GATE_SHORT_MIN AND  (#HTF WT-bearish over 15m/1h/4h/D) <= FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS
When FUNDING_GATE_MTF_REQUIRED is False -> naive snapshot veto (funding extreme only; legacy behavior).

Functions are scalar/array agnostic: `ind` may hold Python scalars (live per-bar _gate_ind) or numpy
arrays (vec NPZ), `funding` likewise. Returns a numpy bool (0-d for scalars, 1-d for arrays).

Data backing (9.3M bars, 24h fwd, full history): naive long-block +0.150% (HURTS) vs bull<=0 -0.912%;
naive short-block +0.427% vs bear<=1 +1.078%. Baseline all-bars +0.115%.
"""
import numpy as np

_TFS = ("15m", "1h", "4h", "D")


def _wt_counts(ind):
    bull = None
    bear = None
    for tf in _TFS:
        w1 = ind.get(f"wt1_{tf}")
        w2 = ind.get(f"wt2_{tf}")
        if w1 is None or w2 is None:
            continue
        w1 = np.asarray(w1, dtype=np.float64)
        w2 = np.asarray(w2, dtype=np.float64)
        b = (w1 > w2).astype(np.float64)
        s = (w1 < w2).astype(np.float64)
        bull = b if bull is None else bull + b
        bear = s if bear is None else bear + s
    return bull, bear


def funding_long_veto(funding, ind, config):
    th = float(getattr(config, "FUNDING_GATE_LONG_MAX", 0.0005))
    extreme = np.asarray(funding, dtype=np.float64) >= th
    if not bool(getattr(config, "FUNDING_GATE_MTF_REQUIRED", False)):
        return extreme
    bull, _ = _wt_counts(ind)
    if bull is None:
        return extreme
    k = float(getattr(config, "FUNDING_GATE_MTF_LONG_MAX_BULL_TFS", 0))
    return extreme & (bull <= k)


def funding_short_veto(funding, ind, config):
    th = float(getattr(config, "FUNDING_GATE_SHORT_MIN", -0.0005))
    extreme = np.asarray(funding, dtype=np.float64) <= th
    if not bool(getattr(config, "FUNDING_GATE_MTF_REQUIRED", False)):
        return extreme
    _, bear = _wt_counts(ind)
    if bear is None:
        return extreme
    k = float(getattr(config, "FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS", 1))
    return extreme & (bear <= k)


def funding_block_mask(npz, n, is_long, config):
    """Vec entry-mask helper: returns bool array (len n) = bars where the funding gate VETOES a new entry.
    Reads funding_rate_3m / funding_rate_5m from the NPZ; AND with ~mask to filter entry masks."""
    if not bool(getattr(config, "FUNDING_GATE_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    fr = npz.get("funding_rate_3m")
    if fr is None:
        fr = npz.get("funding_rate_5m")
    if fr is None:
        fr = npz.get("funding_rate")
    if fr is None:
        return np.zeros(n, dtype=bool)
    fr = np.asarray(fr, dtype=np.float64)
    veto = funding_long_veto(fr, npz, config) if is_long else funding_short_veto(fr, npz, config)
    out = np.asarray(veto, dtype=bool)
    if out.shape != (n,):
        out = np.zeros(n, dtype=bool)
    return out
