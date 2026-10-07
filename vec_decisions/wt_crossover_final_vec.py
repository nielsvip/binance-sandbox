"""WT_CROSSOVER_FINAL / WT_CROSSUNDER_FINAL — faithful vec twin (lane w2-shortclose-gr).

LIVE SOURCE (tradier / stocks ONLY — ez_manage has no WT final exit, registry
name refs only at ez_manage.py:2773,6239-6240):
  tradier_manage.py TradierStopEvaluator.evaluate_stop:
    delta-gate block : lines 19955-19988 (LONG crossunder 19955-19971,
                       SHORT crossover 19972-19988)
    standalone block : lines 20010-20053 (identical predicate; runs when the
                       DELTA exit gate is disabled)
  Shared master flag: WT_CROSSUNDER_FINAL_ENABLED (covers BOTH sides; the
  SHORT fire reason is WT_CROSSOVER_FINAL_*). In live the block is preceded by
  the NOLOSS floor (NOLOSS_MIN_PROFIT_PCT_TRADIER) and layered with the
  parabolic-trend bypass (_parabolic_state, tradier_manage.py:1708-1733,
  transcribed here as parabolic_guard_mask).

Predicate transcribed operator-for-operator (SHORT / crossover — the E2 gap:
dominates live SHORT closes, 0 vec call refs):
    ltf_up      = wt1_LTF > wt2_LTF             (live: 5m, crypto-key fallback 3m;
                                                 §40 floor: neither exists in ANY
                                                 NPZ (0/400 have wt1_5m) -> finest
                                                 available WT = base TF, 15m on
                                                 current stocks NPZs)
    confirm15   = (wt1_15m > wt2_15m) OR (wt1_15m < -95)
    htf_against = (wt1_1h > wt2_1h) OR (wt1_4h > wt2_4h) OR (wt1_D > wt2_D)
    fire = ltf_up AND confirm15 AND htf_against AND NOT parabolic_downtrend
(LONG / crossunder mirrored: < / >95 / parabolic_uptrend.)

§40-FLOOR ADAPTATION (documented deviation, parent decision in MANIFEST):
live LTF is 5m WT but no NPZ carries sub-15m WT (verified S1 2026-10-04:
wt1_5m in 0/400 NPZs; NKE/AXON also lack wt1_3m). A strict 5m->3m->zeros
transcription would wire a twin that can NEVER fire (honest-zero
MISSING_ARRAYS). Per BACKTEST_BIBLE §40 floor + USER 2026-10-01 sub-15m
IGNORE rule, the LTF leg resolves to the finest available WT series
(5m -> 3m -> base TF, same allzero-missing probe the engine itself uses in
its MTF_GR block). On current stocks NPZs LTF == 15m state, so the fire
reduces to (15m cross) AND (HTF against) AND NOT parabolic — 2 effective
legs instead of live's 3. Direction of deviation: fires where live MIGHT
not have (looser LTF), but never without 15m+HTF agreement. If a future
NPZ regen adds wt_5m, the twin automatically uses it (probe order).

CURRENT VEC STATE (why this twin is needed): v12_quick_engine.py:9916-9921
computes a NON-LIVE simplified signal under the same flag name (3m cross +
stoch filter); the faithful module
vec_decisions/check_exit_candidates_stocks__wt_crossunder_final.py is imported
(v12:110) but NEVER called (0 call refs site-wide). The staged hook
(hook_wt_crossover_final.diff hunk 1) replaces the simplified block with this
twin for MODE=='tradier'; crypto keeps the legacy inline logic (no live
counterpart exists there — parent decision recorded in MANIFEST.json).

NPZ fields read: wt1/wt2_{5m,3m(fallback),15m,1h,4h,D} + parabolic guard
rsi_{4h,1h}, bb_pct_b_4h. Missing keys fall back exactly as live (WT 0.0,
RSI 50, BB 0.5) via the caller's _safe.

§39 contract: exit_mask() returns None when disabled or wrong-mode (inert ->
honest 0, integrity; the cfg stash is cleared on these paths so a reused cfg
can never leak a stale mask into a flag-off eval); else a real bool mask ORed
into exit_sig at ONE call site. Per-bar 1h/4h/D flags are stashed on
cfg.WT_FINAL_EXIT_INFO for the walk reason hook (hunk 2) — labeling only, not
a second signal. The reason hook is REQUIRED for closes to happen at all: the
generic TECHNICAL_EXIT close is suppressed unless a specific reason is
attributed (v12:13746-13747, VEC_EXIT_SIG_IS_TRIGGER default False).
"""
from __future__ import annotations

import numpy as np


def _f(cfg, name, default):
    try:
        return float(getattr(cfg, name, default))
    except Exception:
        return float(default)


def _base_tf(npz, cfg):
    """Mirror of engine _base_tf (v12:4019): native lowest TF by close_{tf}
    presence. Preferred BASE_TF first, then 3m/5m/15m/1h/4h/D."""
    try:
        preferred = str(getattr(cfg, "BASE_TF", "15m") or "15m")
    except Exception:
        preferred = "15m"
    for tf in [preferred] + [t for t in ("3m", "5m", "15m", "1h", "4h", "D") if t != preferred]:
        try:
            if hasattr(npz, "get"):
                v = npz.get(f"close_{tf}")
            elif f"close_{tf}" in npz.files:
                v = npz[f"close_{tf}"]
            else:
                continue
            a = np.asarray(v, dtype=float)
            if a.ndim > 0 and a.size > 0 and np.count_nonzero(a) > a.size * 0.5:
                return tf
        except Exception:
            continue
    return preferred


def parabolic_guard_mask(npz, n, is_long, cfg, _safe):
    """Bool mask True where live _parabolic_state SUPPRESSES the WT fire.

    LONG: parabolic uptrend (rsi_4h>=70 & rsi_1h>=65 & bb_pct_b_4h>=0.90).
    SHORT: parabolic downtrend (rsi_4h<=30 & rsi_1h<=35 & bb_pct_b_4h<=0.10).
    Thresholds + master PARABOLIC_PROTECTION_ENABLED read from cfg with live
    defaults. (The eob/eos legs of _parabolic_state are not used by the WT
    blocks and are not transcribed.)
    """
    z = np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "PARABOLIC_PROTECTION_ENABLED", True)):
        return z
    r4 = np.asarray(_safe(npz, "rsi_4h", n, 50), dtype=float)
    r1 = np.asarray(_safe(npz, "rsi_1h", n, 50), dtype=float)
    bb4 = np.asarray(_safe(npz, "bb_pct_b_4h", n, 0.5), dtype=float)
    if is_long:
        return (r4 >= _f(cfg, "PARABOLIC_RSI_4H_MIN", 70.0)) & \
               (r1 >= _f(cfg, "PARABOLIC_RSI_1H_MIN", 65.0)) & \
               (bb4 >= _f(cfg, "PARABOLIC_BB_PCT_B_4H_MIN", 0.90))
    return (r4 <= _f(cfg, "PARABOLIC_RSI_4H_MAX", 30.0)) & \
           (r1 <= _f(cfg, "PARABOLIC_RSI_1H_MAX", 35.0)) & \
           (bb4 <= _f(cfg, "PARABOLIC_BB_PCT_B_4H_MAX", 0.10))


def exit_mask(npz, n, is_long, cfg, close, _safe):
    """Faithful WT final-exit fire mask, or None when inert.

    Inert (None) when WT_CROSSUNDER_FINAL_ENABLED is off (integrity: flag-off
    flips read exactly 0) or when MODE != 'tradier' (no live counterpart;
    crypto keeps the legacy inline logic).
    """
    if not bool(getattr(cfg, "WT_CROSSUNDER_FINAL_ENABLED", True)):
        try:
            cfg.WT_FINAL_EXIT_INFO = None
        except Exception:
            pass
        return None
    if str(getattr(cfg, "MODE", "crypto")).lower() != "tradier":
        try:
            cfg.WT_FINAL_EXIT_INFO = None
        except Exception:
            pass
        return None
    w1_5 = np.asarray(_safe(npz, "wt1_5m", n, 0.0), dtype=float)
    w2_5 = np.asarray(_safe(npz, "wt2_5m", n, 0.0), dtype=float)
    if w1_5.sum() == 0 and w2_5.sum() == 0:
        # Live per-key fallback: 5m missing -> 3m (tradier_manage 19945-19946).
        # In-engine precedent for the all-zero probe: v12 MTF_GR block.
        w1_5 = np.asarray(_safe(npz, "wt1_3m", n, 0.0), dtype=float)
        w2_5 = np.asarray(_safe(npz, "wt2_3m", n, 0.0), dtype=float)
    if w1_5.sum() == 0 and w2_5.sum() == 0:
        # §40 floor: no sub-15m WT in ANY NPZ -> finest available = base TF
        # (mirrors engine _base_tf: preferred BASE_TF, then close_{tf}
        # presence probe). 15m on current stocks NPZs.
        _btf = _base_tf(npz, cfg)
        w1_5 = np.asarray(_safe(npz, f"wt1_{_btf}", n, 0.0), dtype=float)
        w2_5 = np.asarray(_safe(npz, f"wt2_{_btf}", n, 0.0), dtype=float)
    w1_15 = np.asarray(_safe(npz, "wt1_15m", n, 0.0), dtype=float)
    w2_15 = np.asarray(_safe(npz, "wt2_15m", n, 0.0), dtype=float)
    w1_1h = np.asarray(_safe(npz, "wt1_1h", n, 0.0), dtype=float)
    w2_1h = np.asarray(_safe(npz, "wt2_1h", n, 0.0), dtype=float)
    w1_4h = np.asarray(_safe(npz, "wt1_4h", n, 0.0), dtype=float)
    w2_4h = np.asarray(_safe(npz, "wt2_4h", n, 0.0), dtype=float)
    w1_D = np.asarray(_safe(npz, "wt1_D", n, 0.0), dtype=float)
    w2_D = np.asarray(_safe(npz, "wt2_D", n, 0.0), dtype=float)
    if is_long:
        ltf = w1_5 < w2_5
        confirm15 = (w1_15 < w2_15) | (w1_15 > 95)
        f1h = w1_1h < w2_1h
        f4h = w1_4h < w2_4h
        fD = w1_D < w2_D
    else:
        ltf = w1_5 > w2_5
        confirm15 = (w1_15 > w2_15) | (w1_15 < -95)
        f1h = w1_1h > w2_1h
        f4h = w1_4h > w2_4h
        fD = w1_D > w2_D
    fire = ltf & confirm15 & (f1h | f4h | fD)
    try:
        fire = fire & ~np.asarray(parabolic_guard_mask(npz, n, is_long, cfg, _safe), dtype=bool)
    except Exception:
        pass
    try:
        cfg.WT_FINAL_EXIT_INFO = {
            "mask": np.asarray(fire, dtype=bool),
            "f1h": np.asarray(f1h, dtype=bool),
            "f4h": np.asarray(f4h, dtype=bool),
            "fD": np.asarray(fD, dtype=bool),
        }
    except Exception:
        pass
    return np.asarray(fire, dtype=bool)


def close_reason(is_long, f1h, f4h, fD, gain_pct, hold_min):
    """Live-style reason string (tradier_manage 19987-19988 / 20052-20053)."""
    tag = "WT_CROSSUNDER_FINAL" if is_long else "WT_CROSSOVER_FINAL"
    return (f"{tag}_5m_15m_1h{bool(f1h)}_4h{bool(f4h)}_D{bool(fD)}_"
            f"g{gain_pct:.2f}%_hold{hold_min:.0f}m_MANDATORY_REENTRY")
