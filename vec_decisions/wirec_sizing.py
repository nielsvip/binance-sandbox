"""WIRING LANE C — vec twins of calculate_final_order_quantity sizing deltas (batch L1c).

LIVE SOURCE (read-only reference): ez_manage.py::calculate_final_order_quantity
  FG (bc141, :41620-41643): if FG_SIZING_ENABLED (live default False both): fg=int(redis
    news_sentiment_meta.fear_greed.value, 50); fg<=FG_FEAR_THRESHOLD(25) -> score+=40;
    elif fg>=FG_GREED_THRESHOLD(75) -> score-=60. No redis -> no change.
  ATR (bc130, :41701-41714): if ATR_ADAPTIVE_STOP_ENABLED (crypto False / tradier True):
    risk=atr_TF*ATR_ADAPTIVE_STOP_MULT(2.0)/px*100 (TF=ATR_ADAPTIVE_STOP_TF '1h');
    risk>5 -> score-=40; elif risk>3 -> score-=15. (tradier _apply_research_only refs are
    `and False` dead guards; _wire_625 stubs — crypto path is the live one.)
  score->qty (:41715-41721): mult=clamp(1+clamp(score,-200,200)/250, 0.1, 3.0); qty=base*mult.

TWIN: fg_atr_size_mult -> per-bar qty multiplier. Models ONLY the FG+ATR deltas on a
neutral base (rest of live score unmodelled — documented; delta direction AND magnitude
exact at neutral base). Masters OFF (vec defaults) -> all 1.0.
APPROXIMATIONS: (1) NPZ fg<=0 treated as missing (unpopulated zero-fill; live no-redis
no-change — same fail-open direction; a true fg=0 reading is indistinguishable); fg key
order fear_greed_15m then fear_greed. (2) ATR price = close_15m (live current_price).
(3) ATR tradier DIVERGENCE: live True, vec False until promotion (sole vec consumer).
BIBLE: §43 parity gate; §17 (FG aligned; ATR-tradier divergence noted); §18 honest-0.
"""
from __future__ import annotations

import numpy as np


def _safe(npz, key, n, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _sone(npz, key, n, i, default=0.0):
    v = npz.get(key) if hasattr(npz, 'get') else None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return float(v[i])
        except Exception:
            return default
    return default


def _fg_col(npz, n):
    for k in ('fear_greed_15m', 'fear_greed'):
        v = npz.get(k) if hasattr(npz, 'get') else None
        if v is not None and isinstance(v, np.ndarray) and len(v) == n:
            return v.astype(np.float64)
    return None


def fg_atr_size_mult(npz, n, cfg):
    """Vector twin -> float[n] qty multiplier. Defaults -> all 1.0."""
    one = np.ones(n, dtype=np.float64)
    fg_on = bool(getattr(cfg, 'FG_SIZING_ENABLED', False))
    atr_on = bool(getattr(cfg, 'ATR_ADAPTIVE_STOP_ENABLED', False))
    if not fg_on and not atr_on:
        return one
    score = np.zeros(n, dtype=np.float64)
    if fg_on:
        col = _fg_col(npz, n)
        if col is not None:
            try:
                fear_thr = float(getattr(cfg, 'FG_FEAR_THRESHOLD', 25))
            except Exception:
                fear_thr = 25.0
            try:
                greed_thr = float(getattr(cfg, 'FG_GREED_THRESHOLD', 75))
            except Exception:
                greed_thr = 75.0
            fg = np.floor(col).astype(np.float64)  # live int(value)
            has = col > 0
            score = np.where(has & (fg <= fear_thr), 40.0,
                             np.where(has & (fg >= greed_thr), -60.0, score))
    if atr_on:
        tf = str(getattr(cfg, 'ATR_ADAPTIVE_STOP_TF', '1h') or '1h')
        try:
            mult = float(getattr(cfg, 'ATR_ADAPTIVE_STOP_MULT', 2.0))
        except Exception:
            mult = 2.0
        atr = _safe(npz, f'atr_{tf}', n, 0.0)
        px = _safe(npz, 'close_15m', n, 0.0)
        ok = (atr > 0) & (px > 0)
        risk = np.zeros(n, dtype=np.float64)
        risk[ok] = atr[ok] * mult / px[ok] * 100.0
        score = np.where(ok & (risk > 5.0), score - 40.0,
                         np.where(ok & (risk > 3.0), score - 15.0, score))
    clamped = np.clip(score, -200.0, 200.0)
    return np.clip(1.0 + clamped / 250.0, 0.1, 3.0)


def fg_atr_size_mult_scalar(npz, n, i, cfg):
    """Scalar reference for bar i. Returns float."""
    fg_on = bool(getattr(cfg, 'FG_SIZING_ENABLED', False))
    atr_on = bool(getattr(cfg, 'ATR_ADAPTIVE_STOP_ENABLED', False))
    if not fg_on and not atr_on:
        return 1.0
    score = 0.0
    if fg_on:
        col = _fg_col(npz, n)
        if col is not None:
            try:
                fv = float(col[i])
            except Exception:
                fv = 0.0
            if fv > 0:
                try:
                    fear_thr = float(getattr(cfg, 'FG_FEAR_THRESHOLD', 25))
                except Exception:
                    fear_thr = 25.0
                try:
                    greed_thr = float(getattr(cfg, 'FG_GREED_THRESHOLD', 75))
                except Exception:
                    greed_thr = 75.0
                import math
                fgi = math.floor(fv)
                if fgi <= fear_thr:
                    score += 40.0
                elif fgi >= greed_thr:
                    score -= 60.0
    if atr_on:
        tf = str(getattr(cfg, 'ATR_ADAPTIVE_STOP_TF', '1h') or '1h')
        try:
            mult = float(getattr(cfg, 'ATR_ADAPTIVE_STOP_MULT', 2.0))
        except Exception:
            mult = 2.0
        atr = _sone(npz, f'atr_{tf}', n, i, 0.0)
        px = _sone(npz, 'close_15m', n, i, 0.0)
        if atr > 0 and px > 0:
            risk = atr * mult / px * 100.0
            if risk > 5.0:
                score -= 40.0
            elif risk > 3.0:
                score -= 15.0
    clamped = max(-200.0, min(200.0, score))
    return max(0.1, min(3.0, 1.0 + clamped / 250.0))
