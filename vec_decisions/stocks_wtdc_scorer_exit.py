"""Stocks live evaluate_stop WT_DC scorer exit — vec twin (lane D parity, 2026-10-06).

LIVE (tradier_manage.evaluate_stop, at tradier defaults; first-fire order):
  - OPENING_BUFFER (:20150)            -> no exit in the first 30 min after 09:30 ET
  - STOCK_MIN_HOLD (:20799-20820)      -> hold < TRADIER_MIN_HOLD_MINUTES (240 L) / _SHORT (60 S)
                                          unless px < dc_low_15m (L) / px > dc_high_15m (S)
  - NOLOSS_HOLD (:21224-21248)         -> gain < NOLOSS_MIN_PROFIT_PCT_TRADIER (0.01) holds unless
                                          NOLOSS_BYPASS_WT_5OF5_ENABLED and >= MIN_TFS (3) of
                                          5m/15m/1h/4h/D WT are against
  - WT_DC exit scorer (:21473-21519)   -> wt_dc_exit_scorer.score_exit N-of-5 tiers (100 full / 40
                                          partial) >= WT_DC_EXIT_THRESHOLD (30) closes, unless a
                                          parabolic move in the trade's direction (_parabolic_state)
  - score < threshold                  -> SCORER_HOLD early return (MULTI_TF_EXIT never reached)
  MULTI_TF_EXIT (evaluate_multi_tf_exit) is reached only when WT_DC_EXIT_ENABLED is False.

Vec: the N-of-5 hit count reuses vec_decisions.lane_vec_exitscorer._hits (same conditions as
the live scorer). 5m WT keys do not exist in the 15m vec (min_decision_tf_guard maps *_5m to the
*_15m twin), so the NOLOSS bypass count reads the guarded 5m key (= 15m) — documented approximation.
"""
from __future__ import annotations

import numpy as np


def _f(cfg, name, default):
    try:
        v = getattr(cfg, name, default)
        if isinstance(v, bool):
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def score_array(npz, n, is_long, cfg):
    """Live score_exit tier per bar: FULL if hits>=min, PARTIAL if hits>=max(min-1,3), else 0."""
    import vec_decisions.lane_vec_exitscorer as _lves
    hits = _lves._hits(npz, n, is_long, cfg)
    mn = int(_f(cfg, 'EXIT_SCORER_MIN_CONDITIONS', 5))
    full = _f(cfg, 'EXIT_SCORER_FULL_SCORE', 100.0)
    part = _f(cfg, 'EXIT_SCORER_PARTIAL_SCORE', 40.0)
    return np.where(hits >= mn, full, np.where(hits >= max(mn - 1, 3), part, 0.0)).astype(float)


def live_score(hits, cfg):
    mn = int(_f(cfg, 'EXIT_SCORER_MIN_CONDITIONS', 5))
    if hits >= mn:
        return _f(cfg, 'EXIT_SCORER_FULL_SCORE', 100.0)
    if hits >= max(mn - 1, 3):
        return _f(cfg, 'EXIT_SCORER_PARTIAL_SCORE', 40.0)
    return 0.0


def parabolic_masks(npz, n, cfg, safe):
    """Live tradier _parabolic_state (:1727) pp_up / pp_dn with `float(x or default)` coercion."""
    if not bool(getattr(cfg, 'PARABOLIC_PROTECTION_ENABLED', True)):
        z = np.zeros(n, dtype=bool)
        return z, z
    r4 = np.asarray(safe(npz, 'rsi_4h', n, 50.0), dtype=float)
    r1 = np.asarray(safe(npz, 'rsi_1h', n, 50.0), dtype=float)
    bb4 = np.asarray(safe(npz, 'bb_pct_b_4h', n, 0.5), dtype=float)
    r4 = np.where((r4 == 0) | ~np.isfinite(r4), 50.0, r4)
    r1 = np.where((r1 == 0) | ~np.isfinite(r1), 50.0, r1)
    bb4 = np.where((bb4 == 0) | ~np.isfinite(bb4), 0.5, bb4)
    up = (r4 >= _f(cfg, 'PARABOLIC_RSI_4H_MIN', 70.0)) & (r1 >= _f(cfg, 'PARABOLIC_RSI_1H_MIN', 65.0)) & (bb4 >= _f(cfg, 'PARABOLIC_BB_PCT_B_4H_MIN', 0.90))
    dn = (r4 <= _f(cfg, 'PARABOLIC_RSI_4H_MAX', 30.0)) & (r1 <= _f(cfg, 'PARABOLIC_RSI_1H_MAX', 35.0)) & (bb4 <= _f(cfg, 'PARABOLIC_BB_PCT_B_4H_MAX', 0.10))
    return up, dn


def min_hold_minutes(cfg, is_long):
    if is_long:
        return _f(cfg, 'TRADIER_MIN_HOLD_MINUTES', 240.0)
    return _f(cfg, 'TRADIER_MIN_HOLD_MINUTES_SHORT', 60.0)


def min_hold_blocks(held_minutes, px, dc_low_15m, dc_high_15m, is_long, hold_min):
    """Live STOCK_MIN_HOLD: True = still inside the hold (exit refused)."""
    bypass = False
    if is_long:
        bypass = dc_low_15m > 0 and px > 0 and px < dc_low_15m
    else:
        bypass = dc_high_15m > 0 and px > 0 and px > dc_high_15m
    return (held_minutes < hold_min) and not bypass


def against_count_array(npz, n, is_long, safe):
    cnt = np.zeros(n, dtype=int)
    for tf in ('5m', '15m', '1h', '4h', 'D'):
        w1 = np.asarray(safe(npz, 'wt1_%s' % tf, n, 0.0), dtype=float)
        w2 = np.asarray(safe(npz, 'wt2_%s' % tf, n, 0.0), dtype=float)
        cnt += ((w1 < w2) if is_long else (w1 > w2)).astype(int)
    return cnt


def noloss_holds(gain, against, cfg):
    """Live NOLOSS_HOLD (strict trb/trc): True = exit refused."""
    floor = _f(cfg, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', 0.01)
    if not (floor > 0 and gain < floor):
        return False
    if bool(getattr(cfg, 'NOLOSS_BYPASS_WT_5OF5_ENABLED', False)) and against >= int(_f(cfg, 'NOLOSS_BYPASS_WT_5OF5_MIN_TFS', 5)):
        return False
    return True
