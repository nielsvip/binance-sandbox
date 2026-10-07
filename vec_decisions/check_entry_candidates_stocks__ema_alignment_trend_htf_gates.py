"""EMA_9_21 / ALIGNMENT / TREND_GATES / HTF_CONF / WT_CROSSUNDER_15M entry gates (stocks).

LIVE SOURCE: tradier_manage.py should_enter_long/short (fallback section).
  EMA_9_21 (~11950/12231): LONG block when ema_9_above_21_<tf> == 0.0;
                           SHORT block when ema_9_above_21_<tf> == 1.0.
  ALIGNMENT_GATE (~12060/12332): count = sum of side-aligned of {k1h vs d1h, k4h vs d4h,
                           k15m vs d15m, lr_trend_15m sign}; block when count < ALIGNMENT_GATE_MIN.
                           LONG uses '>'/'>0'; SHORT uses '<'/'<0'.
  TREND_GATES (~12086/12364): LONG block when not(lr_trend_15m>0); SHORT block when not(lr_trend_15m<0).
  HTF1_CONF (~12100/12378): LONG block when not(k1h>d1h); SHORT block when not(k1h<d1h).
  HTF4_CONF (~12107/12385): LONG block when not(k4h>d4h); SHORT block when not(k4h<d4h).
  WT_CROSSUNDER_15M (SHORT only ~12287): block when wt1_15m >= wt2_15m (requires mid-zone+flag; the
                           pure predicate here is the wt comparison only — zone is runtime).

Each predicate returns True == BLOCKED.
2026-05-30 PARITY: shared pure predicates drive scalar+vec.
NPZ fields: ema_9_above_21_<tf>, stoch_k/d_{1h,4h,15m}, lr_trend_15m, wt1_15m, wt2_15m — all present.
"""
from typing import Tuple


def _ema_9_21_blocks(ema9_above: float, is_long: bool) -> bool:
    """PURE: LONG block when ema9_above==0.0; SHORT block when ema9_above==1.0."""
    if is_long:
        return ema9_above == 0.0
    return ema9_above == 1.0


def _alignment_count(k1h, d1h, k4h, d4h, k15m, d15m, lr_trend_15m, is_long: bool) -> int:
    """PURE: count of side-aligned conditions (max 4)."""
    c = 0
    if is_long:
        if k1h > d1h: c += 1
        if k4h > d4h: c += 1
        if k15m > d15m: c += 1
        if lr_trend_15m > 0: c += 1
    else:
        if k1h < d1h: c += 1
        if k4h < d4h: c += 1
        if k15m < d15m: c += 1
        if lr_trend_15m < 0: c += 1
    return c


def _alignment_blocks(k1h, d1h, k4h, d4h, k15m, d15m, lr_trend_15m, is_long: bool, min_req: int) -> bool:
    return _alignment_count(k1h, d1h, k4h, d4h, k15m, d15m, lr_trend_15m, is_long) < min_req


def _trend_gate_blocks(lr_trend_15m: float, is_long: bool) -> bool:
    if is_long:
        return not (lr_trend_15m > 0)
    return not (lr_trend_15m < 0)


def _htf_conf_blocks(k: float, d: float, is_long: bool) -> bool:
    """PURE single-TF HTF confirmation block (used for both 1h and 4h)."""
    if is_long:
        return not (k > d)
    return not (k < d)


def _wt_crossunder_15m_blocks_short(wt1_15m: float, wt2_15m: float) -> bool:
    """PURE (SHORT only): block when wt1_15m >= wt2_15m."""
    return wt1_15m >= wt2_15m


def _alignment_min(config) -> int:
    return int(getattr(config, "ALIGNMENT_GATE_MIN", 4))


# ── scalar wrappers ──
def check_ema_9_21(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not getattr(config, "EMA_9_21_FILTER_ENABLED", False):
        return False, ""
    tf = getattr(config, "EMA_9_21_TIMEFRAME", "5m")
    ema9 = float(indicators.get(f"ema_9_above_21_{tf}", -1) or -1)
    if _ema_9_21_blocks(ema9, is_long):
        return True, f"EMA_9_21_BLOCK_{'LONG' if is_long else 'SHORT'}_{tf}"
    return False, ""


def check_alignment_gate(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not getattr(config, "ALIGNMENT_GATE_MIN", 0) > 0:
        return False, ""
    k1h = float(indicators.get("stoch_k_1h", 50)); d1h = float(indicators.get("stoch_d_1h", 50))
    k4h = float(indicators.get("stoch_k_4h", 50)); d4h = float(indicators.get("stoch_d_4h", 50))
    k15m = float(indicators.get("stoch_k_15m", 50)); d15m = float(indicators.get("stoch_d_15m", 50))
    lr = float(indicators.get("lr_trend_15m", 0))
    if _alignment_blocks(k1h, d1h, k4h, d4h, k15m, d15m, lr, is_long, _alignment_min(config)):
        return True, f"ALIGNMENT_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_trend_gate(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    if not getattr(config, "TREND_GATES", False):
        return False, ""
    lr = float(indicators.get("lr_trend_15m", 0))
    if _trend_gate_blocks(lr, is_long):
        return True, f"TREND_GATE_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_htf_conf(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """Combined HTF1+HTF4 confirmation block (whichever flags are enabled)."""
    blocked = False
    if getattr(config, "HTF1_CONF", False):
        k1h = float(indicators.get("stoch_k_1h", 50)); d1h = float(indicators.get("stoch_d_1h", 50))
        blocked = blocked or _htf_conf_blocks(k1h, d1h, is_long)
    if getattr(config, "HTF4_CONF", False):
        k4h = float(indicators.get("stoch_k_4h", 50)); d4h = float(indicators.get("stoch_d_4h", 50))
        blocked = blocked or _htf_conf_blocks(k4h, d4h, is_long)
    if blocked:
        return True, f"HTF_CONF_BLOCK_{'LONG' if is_long else 'SHORT'}"
    return False, ""


def check_wt_crossunder_15m_short(config, indicators: dict) -> Tuple[bool, str]:
    """SHORT-only WT 15m crossunder gate (pure wt comparison; zone gating is runtime)."""
    wt1 = float(indicators.get("wt1_15m", 0) or 0)
    wt2 = float(indicators.get("wt2_15m", 0) or 0)
    if _wt_crossunder_15m_blocks_short(wt1, wt2):
        return True, "WT_CROSSUNDER_15M_BLOCK_SHORT"
    return False, ""


# ── vec masks (True == blocked) ──
def check_ema_9_21_vec(config, ema9_above_arr, is_long):
    import numpy as np
    e = np.asarray(ema9_above_arr, dtype=float)
    if is_long:
        return e == 0.0
    return e == 1.0


def check_alignment_gate_vec(config, k1h, d1h, k4h, d4h, k15m, d15m, lr_trend_15m, is_long):
    import numpy as np
    k1h = np.asarray(k1h, dtype=float); d1h = np.asarray(d1h, dtype=float)
    k4h = np.asarray(k4h, dtype=float); d4h = np.asarray(d4h, dtype=float)
    k15m = np.asarray(k15m, dtype=float); d15m = np.asarray(d15m, dtype=float)
    lr = np.asarray(lr_trend_15m, dtype=float)
    if is_long:
        cnt = (k1h > d1h).astype(int) + (k4h > d4h).astype(int) + (k15m > d15m).astype(int) + (lr > 0).astype(int)
    else:
        cnt = (k1h < d1h).astype(int) + (k4h < d4h).astype(int) + (k15m < d15m).astype(int) + (lr < 0).astype(int)
    return cnt < _alignment_min(config)


def check_trend_gate_vec(config, lr_trend_15m_arr, is_long):
    import numpy as np
    lr = np.asarray(lr_trend_15m_arr, dtype=float)
    if is_long:
        return ~(lr > 0)
    return ~(lr < 0)


def check_htf1_conf_vec(config, k1h_arr, d1h_arr, is_long):
    import numpy as np
    k = np.asarray(k1h_arr, dtype=float); d = np.asarray(d1h_arr, dtype=float)
    if is_long:
        return ~(k > d)
    return ~(k < d)


def check_htf4_conf_vec(config, k4h_arr, d4h_arr, is_long):
    import numpy as np
    k = np.asarray(k4h_arr, dtype=float); d = np.asarray(d4h_arr, dtype=float)
    if is_long:
        return ~(k > d)
    return ~(k < d)


def check_wt_crossunder_15m_short_vec(config, wt1_15m_arr, wt2_15m_arr):
    import numpy as np
    w1 = np.asarray(wt1_15m_arr, dtype=float); w2 = np.asarray(wt2_15m_arr, dtype=float)
    return w1 >= w2
