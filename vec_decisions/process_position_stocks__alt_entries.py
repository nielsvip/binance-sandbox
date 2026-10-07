"""PROCESS_POSITION_STOCKS · alternate entry fire predicates (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position. Three alternate "third path" entry
fires + two scoring boosts, all pure per-bar predicates on NPZ fields:

  RZ_BREAKOUT (lines ~3012-3025), behind RZ_BREAKOUT_ENTRY_ENABLED (default False):
    bb = bb_pct_b_1h
    LONG  fires when rz_bot <= bb <= rz_bot+band   (rz_bot default 0.15, band default 0.05)
    SHORT fires when rz_top-band <= bb <= rz_top    (rz_top default 0.85)

  LR_PCTB_D_LONG (lines ~3026-3035), behind LR_PCTB_D_LONG_ENTRY_ENABLED (default False):
    LONG only; fires when lr_pct_b_D is not None AND lr_pct_b_D <= threshold (default 0.20).

  BB_RSI_STOCH_SCALP boost-fire (lines ~2892-2901), behind BB_RSI_STOCH_SCALP_ENABLED:
    bb=bb_pct_b_5m, rsi=rsi_5m, k=stoch_k_5m
    LONG  : bb<0.2 AND rsi<30 AND k<20
    SHORT : bb>0.8 AND rsi>70 AND k>80
    (this is a +score boost in live; the pure predicate is the long/short condition)

  BB_BREAKOUT boost-fire (lines ~2885-2891), behind BB_BREAKOUT_ENABLED:
    bb=bb_pct_b_{BB_BREAKOUT_TF} (default tf '1h')
    LONG : bb>1.0 ; SHORT : bb<0.0
    (also a +score boost; pure predicate is the condition)

2026-05-30 PARITY: each live scalar AND its vec twin derive from the SAME pure predicate
below — cannot drift. Pure cores take thresholds as args; no config, no state.

CLASSIFICATION: vectorized. Pure per-bar predicates on NPZ fields bb_pct_b_*, rsi_5m,
stoch_k_5m, lr_pct_b_D. No per-position state.

NPZ / indicator fields read: bb_pct_b_1h, bb_pct_b_5m, rsi_5m, stoch_k_5m, lr_pct_b_D.
All present in backtest NPZ.
"""
from typing import Tuple


# ── RZ_BREAKOUT ────────────────────────────────────────────────────────────────
def _rz_breakout_fires(bb_pct_b_1h: float, is_long: bool, rz_top: float, rz_bot: float,
                       band: float) -> bool:
    """PURE. Faithful replica of tradier_manage.py line 3018."""
    if is_long:
        return rz_bot <= bb_pct_b_1h <= rz_bot + band
    return rz_top - band <= bb_pct_b_1h <= rz_top


def _rz_params(config):
    return (float(getattr(config, "RZ_TOP_BB_THRESHOLD", 0.85)),
            float(getattr(config, "RZ_BOT_BB_THRESHOLD", 0.15)),
            float(getattr(config, "RZ_BREAKOUT_BAND", 0.05)))


def check_rz_breakout(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not bool(getattr(config, "RZ_BREAKOUT_ENTRY_ENABLED", False)):
        return False, ""
    rz_top, rz_bot, band = _rz_params(config)
    bb = float(indicators.get("bb_pct_b_1h", 0.5) or 0.5)
    if not _rz_breakout_fires(bb, is_long, rz_top, rz_bot, band):
        return False, ""
    return True, f"RZ_BREAKOUT_{'L' if is_long else 'S'}_bb={bb:.2f}"


def check_rz_breakout_vec(config, bb_pct_b_1h_arr, is_long):
    import numpy as np
    bb = np.asarray(bb_pct_b_1h_arr, dtype=float)
    if not bool(getattr(config, "RZ_BREAKOUT_ENTRY_ENABLED", False)):
        return np.zeros(len(bb), dtype=bool)
    rz_top, rz_bot, band = _rz_params(config)
    if is_long:
        return (bb >= rz_bot) & (bb <= rz_bot + band)
    return (bb >= rz_top - band) & (bb <= rz_top)


# ── LR_PCTB_D_LONG ─────────────────────────────────────────────────────────────
def _lr_pctb_d_long_fires(lr_pct_b_D: float, is_long: bool, threshold: float) -> bool:
    """PURE. Faithful replica of tradier_manage.py line 3029 (LONG only)."""
    if not is_long:
        return False
    return lr_pct_b_D <= threshold


def check_lr_pctb_d_long(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not bool(getattr(config, "LR_PCTB_D_LONG_ENTRY_ENABLED", False)):
        return False, ""
    if not is_long:
        return False, ""
    lr = indicators.get("lr_pct_b_D")
    if lr is None:
        return False, ""
    thr = float(getattr(config, "LR_PCTB_D_LONG_ENTRY_THRESHOLD", 0.20))
    if not _lr_pctb_d_long_fires(float(lr), is_long, thr):
        return False, ""
    return True, f"LR_PCTB_D_LONG_bb={float(lr):.4f}"


def check_lr_pctb_d_long_vec(config, lr_pct_b_D_arr, is_long):
    """NaN entries (=missing lr_pct_b_D, the live None case) yield False."""
    import numpy as np
    lr = np.asarray(lr_pct_b_D_arr, dtype=float)
    if (not bool(getattr(config, "LR_PCTB_D_LONG_ENTRY_ENABLED", False))) or (not is_long):
        return np.zeros(len(lr), dtype=bool)
    thr = float(getattr(config, "LR_PCTB_D_LONG_ENTRY_THRESHOLD", 0.20))
    return (~np.isnan(lr)) & (lr <= thr)


# ── BB_RSI_STOCH_SCALP ─────────────────────────────────────────────────────────
def _bb_rsi_stoch_scalp_fires(bb: float, rsi: float, k: float, is_long: bool) -> bool:
    """PURE. Faithful replica of tradier_manage.py lines 2896-2898."""
    if is_long:
        return bb < 0.2 and rsi < 30 and k < 20
    return bb > 0.8 and rsi > 70 and k > 80


def check_bb_rsi_stoch_scalp(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not bool(getattr(config, "BB_RSI_STOCH_SCALP_ENABLED", False)):
        return False, ""
    bb = float(indicators.get("bb_pct_b_5m", 0.5) or 0.5)
    rsi = float(indicators.get("rsi_5m", 50) or 50)
    k = float(indicators.get("stoch_k_5m", 50) or 50)
    if not _bb_rsi_stoch_scalp_fires(bb, rsi, k, is_long):
        return False, ""
    return True, f"BB_RSI_STOCH(bb={bb:.2f}rsi={rsi:.0f}k={k:.0f})"


def check_bb_rsi_stoch_scalp_vec(config, bb_pct_b_5m_arr, rsi_5m_arr, stoch_k_5m_arr, is_long):
    import numpy as np
    bb = np.asarray(bb_pct_b_5m_arr, dtype=float)
    if not bool(getattr(config, "BB_RSI_STOCH_SCALP_ENABLED", False)):
        return np.zeros(len(bb), dtype=bool)
    rsi = np.asarray(rsi_5m_arr, dtype=float)
    k = np.asarray(stoch_k_5m_arr, dtype=float)
    if is_long:
        return (bb < 0.2) & (rsi < 30) & (k < 20)
    return (bb > 0.8) & (rsi > 70) & (k > 80)


# ── BB_BREAKOUT ────────────────────────────────────────────────────────────────
def _bb_breakout_fires(bb: float, is_long: bool) -> bool:
    """PURE. Faithful replica of tradier_manage.py line 2888."""
    if is_long:
        return bb > 1.0
    return bb < 0.0


def check_bb_breakout(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not bool(getattr(config, "BB_BREAKOUT_ENABLED", False)):
        return False, ""
    tf = getattr(config, "BB_BREAKOUT_TF", "1h")
    bb = float(indicators.get(f"bb_pct_b_{tf}", 0.5) or 0.5)
    if not _bb_breakout_fires(bb, is_long):
        return False, ""
    return True, f"BB_BREAK({tf}={bb:.2f})"


def check_bb_breakout_vec(config, bb_pct_b_tf_arr, is_long):
    """bb_pct_b_tf_arr must be the bb_pct_b for config.BB_BREAKOUT_TF (caller selects the column)."""
    import numpy as np
    bb = np.asarray(bb_pct_b_tf_arr, dtype=float)
    if not bool(getattr(config, "BB_BREAKOUT_ENABLED", False)):
        return np.zeros(len(bb), dtype=bool)
    if is_long:
        return bb > 1.0
    return bb < 0.0
