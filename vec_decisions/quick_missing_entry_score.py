"""Exact per-bar score bonus masks extracted from AdvancedSignalRater entry scoring."""
import numpy as np


def _cfg(cfg, key, default):
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def bb_breakout_score_scalar(indicators, current_price, is_long, cfg, score=0.0):
    """Mirror ez_positions_quick.py AdvancedSignalRater.rate BB breakout bonus."""
    if not bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False)):
        return float(score), False
    tf = str(_cfg(cfg, "BB_BREAKOUT_TF", "1h"))
    ind = indicators or {}
    try:
        pct_b = float(ind.get(f"bb_pct_b_{tf}", 0.5))
    except (TypeError, ValueError):
        pct_b = 0.5
    try:
        sma = float(ind.get(f"sma_200_{tf}", 0))
    except (TypeError, ValueError):
        sma = 0.0
    try:
        adx = float(ind.get(f"adx_{tf}", 0))
    except (TypeError, ValueError):
        adx = 0.0
    px = float(current_price or 0.0)
    fire = adx > 25.0 and sma > 0.0 and ((is_long and pct_b > 1.0 and px > sma) or (not is_long and pct_b < 0.0 and px < sma))
    bonus = float(_cfg(cfg, "BB_BREAKOUT_SCORE", 20)) if fire else 0.0
    return float(score) + bonus, bool(fire)


def bb_breakout_score_vec(npz, n, is_long, cfg, score=None):
    """Return `(score_after_bonus, fire_mask)` for 15m-bar evaluator inputs."""
    if not bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False)):
        base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
        return base.copy(), np.zeros(n, dtype=bool)
    tf = str(_cfg(cfg, "BB_BREAKOUT_TF", "1h"))
    pct_b = _column(npz, f"bb_pct_b_{tf}", n, 0.5)
    sma = _column(npz, f"sma_200_{tf}", n, 0.0)
    adx = _column(npz, f"adx_{tf}", n, 0.0)
    px = _column(npz, "close", n, 0.0)
    if is_long:
        fire = np.isfinite(adx) & (adx > 25.0) & np.isfinite(sma) & (sma > 0.0) & np.isfinite(pct_b) & (pct_b > 1.0) & np.isfinite(px) & (px > sma)
    else:
        fire = np.isfinite(adx) & (adx > 25.0) & np.isfinite(sma) & (sma > 0.0) & np.isfinite(pct_b) & (pct_b < 0.0) & np.isfinite(px) & (px < sma)
    base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
    if base.shape != (n,):
        raise ValueError("score must have shape (n,)")
    return base + fire.astype(float) * float(_cfg(cfg, "BB_BREAKOUT_SCORE", 20)), fire


def _column(npz, key, n, default):
    try:
        if key not in npz:
            return np.full(n, default, dtype=float)
        values = np.asarray(npz[key], dtype=float).reshape(-1)
        if values.size != n:
            return np.full(n, default, dtype=float)
        return values
    except Exception:
        return np.full(n, default, dtype=float)
