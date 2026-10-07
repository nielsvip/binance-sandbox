"""gap_risk_exit.py — SHARED scalar+vectorized GAP_RISK_EXIT predicate.

Design from prior audit (GAP_RISK_EXIT Synthesis — Design from 4 Audits).

Spec:
  Short gap-up: open > prev_close . Long gap-down: open < prev_close .
  Gap retraces to prev_close (fill touch), then continuation risk: close
  position when (A) price re-breaks open in gap direction OR (B) makes
  higher-high (short) / lower-low (long) after retrace. Intraday structure
  risk exit, not a take-profit. Fully switchable, default OFF.

State Machine (vectorized + live identical):
  STATE 0: GAP_DETECTED = (SHORT and open>prev_close) OR (LONG and open<prev_close)
  STATE 1: RETRACED = GAP_DETECTED and (SHORT: low <= prev_close) OR (LONG: high >= prev_close)
  STATE 2: EXIT_TRIGGER = RETRACED and (COND_A or COND_B)
    COND_A (open_reclaim): SHORT: close/high > open | LONG: close/low < open
    COND_B (structure_break): SHORT: high > max(high since gap_open) | LONG: low < min(low since gap_open)

Vectorized on NPZ arrays (open_D, close_D_prev). Scalars share pure predicate.
Defaults OFF until proven per TEMPLATE.xlsx law.

Switches (all bool, default False):
  GAP_RISK_EXIT_ENABLED            master
  GAP_RISK_EXIT_SHORT_ENABLED      directional short
  GAP_RISK_EXIT_LONG_ENABLED       directional long
  GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED  COND_A (alias GAP_RISK_EXIT_COND_A_ENABLED)
  GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED COND_B (alias GAP_RISK_EXIT_COND_B_ENABLED)

Live scalar: tradier_manage.py gap block (mirror)
Vector: v12_quick_engine.compute_exit_signals
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

_EPS = 1e-9


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _gap_risk_fires(
    is_long: bool,
    open_d: float,
    prev_close: float,
    low: float,
    high: float,
    close: float,
    prior_max_high: float,
    prior_min_low: float,
    cond_a_enabled: bool,
    cond_b_enabled: bool,
    has_retraced: bool,
) -> tuple[bool, bool]:
    """Pure per-bar predicate.

    Returns (retraced_now, exit_now). Caller must maintain has_retraced and
    prior max/min across bars. gap_detected checked outside.
    """
    # retraced this bar
    if is_long:
        retraced_now = high >= prev_close
        cond_a = (close < open_d) or (low < open_d)
        # structure break: lower-low after retrace
        cond_b = (prior_min_low > 0) and (low < prior_min_low)
    else:
        retraced_now = low <= prev_close
        cond_a = (close > open_d) or (high > open_d)
        cond_b = (prior_max_high > 0) and (high > prior_max_high)

    retraced_seen = has_retraced or retraced_now
    if not retraced_seen:
        return retraced_now, False
    fire_a = cond_a_enabled and cond_a
    fire_b = cond_b_enabled and cond_b
    if fire_a or fire_b:
        return retraced_now, True
    return retraced_now, False


def gap_risk_exit_should_exit(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
    current_price: float = 0.0,
    prior_max_high: float = 0.0,
    prior_min_low: float = 0.0,
    has_retraced: bool = False,
) -> bool:
    """Live scalar: whether GAP_RISK_EXIT triggers on this bar.

    Stateless per-bar; caller may pass prior_max_high/prior_min_low/has_retraced
    for structure-break tracking. Without history, COND_B will not fire.
    """
    if not bool(getattr(cfg, "GAP_RISK_EXIT_ENABLED", False)):
        return False
    if is_long:
        if not bool(getattr(cfg, "GAP_RISK_EXIT_LONG_ENABLED", False)):
            return False
    else:
        if not bool(getattr(cfg, "GAP_RISK_EXIT_SHORT_ENABLED", False)):
            return False

    # Resolve cond enables with aliases
    cond_a_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED", getattr(cfg, "GAP_RISK_EXIT_COND_A_ENABLED", False)))
    # also support legacy alias without _ENABLED suffix fallback? handled via getattr defaults
    if not cond_a_enabled:
        # check alternate name
        cond_a_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_COND_A_ENABLED", False))
    cond_b_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED", getattr(cfg, "GAP_RISK_EXIT_COND_B_ENABLED", False)))
    if not cond_b_enabled:
        cond_b_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_COND_B_ENABLED", False))

    if not cond_a_enabled and not cond_b_enabled:
        return False

    # NPZ daily fields: open_D / close_D_prev (fallbacks for older names)
    open_d = _num(indicators, "open_D", _num(indicators, "open_d", _num(indicators, "open", 0.0)))
    prev_close = _num(indicators, "close_D_prev", _num(indicators, "close_prev", _num(indicators, "prev_close", 0.0)))
    if open_d == 0 or prev_close == 0:
        # try current_price as close fallback not for gap detection
        return False

    gap_detected = (open_d < prev_close) if is_long else (open_d > prev_close)
    if not gap_detected:
        return False

    low = _num(indicators, "low", _num(indicators, "low_D", 0.0))
    high = _num(indicators, "high", _num(indicators, "high_D", 0.0))
    close = float(current_price) if current_price else _num(indicators, "close", _num(indicators, "close_D", 0.0))
    # if low/high missing, derive from close
    if low == 0:
        low = close
    if high == 0:
        high = close

    _, fire = _gap_risk_fires(is_long, open_d, prev_close, low, high, close, prior_max_high, prior_min_low, cond_a_enabled, cond_b_enabled, has_retraced)
    return fire


# aliases
gap_risk_should_exit = gap_risk_exit_should_exit
gap_risk_fires = gap_risk_exit_should_exit


def gap_risk_exit_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool = False,
) -> np.ndarray:
    """Vectorized GAP_RISK_EXIT mask. SAME predicate as scalar.

    Vectorized on NPZ arrays (open_D, close_D_prev). Uses base-tf high/low/close
    for intraday retrace / reclaim checks. Stateful scan for retraced persistence
    and running max/min (COND_B).

    Returns bool ndarray shape (n,): True where exit should fire.
    If is_long is None, returns union of both directions (not used by engine,
    but kept for test convenience).
    """
    if not bool(getattr(cfg, "GAP_RISK_EXIT_ENABLED", False)):
        return np.zeros(n, dtype=bool)

    # cond enables (support both naming conventions)
    cond_a_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED", False)) or bool(getattr(cfg, "GAP_RISK_EXIT_COND_A_ENABLED", False))
    cond_b_enabled = bool(getattr(cfg, "GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED", False)) or bool(getattr(cfg, "GAP_RISK_EXIT_COND_B_ENABLED", False))
    if not cond_a_enabled and not cond_b_enabled:
        return np.zeros(n, dtype=bool)

    def _arr(key: str, default: float = 0.0) -> np.ndarray:
        if key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.size < n:
                tmp = np.full(n, default, dtype=float)
                tmp[: min(n, a.size)] = a[: min(n, a.size)]
                a = tmp
            else:
                a = a[:n]
            return np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        return np.full(n, default, dtype=float)

    # Daily gap fields
    if "open_D" in npz:
        open_d = _arr("open_D")
    elif "open_d" in npz:
        open_d = _arr("open_d")
    else:
        # fallback to base open or daily open alternative
        open_d = _arr("open")
        if np.all(open_d == 0):
            open_d = _arr("open_D", 0.0)

    if "close_D_prev" in npz:
        prev_close = _arr("close_D_prev")
    elif "close_prev" in npz:
        prev_close = _arr("close_prev")
    elif "prev_close" in npz:
        prev_close = _arr("prev_close")
    else:
        prev_close = _arr("close_D_prev", 0.0)

    # Intraday arrays for retrace/reclaim/structure
    # Prefer base-tf? Use close/high/low generic (v12 _safe uses close without tf)
    # Try several keys
    if "close" in npz:
        close = _arr("close")
    elif "close_D" in npz:
        close = _arr("close_D")
    else:
        close = _arr("close", 0.0)

    if "high" in npz:
        high = _arr("high")
    elif "high_D" in npz:
        high = _arr("high_D")
    else:
        high = close.copy()

    if "low" in npz:
        low = _arr("low")
    elif "low_D" in npz:
        low = _arr("low_D")
    else:
        low = close.copy()

    # Also try base_tf specific if generic is zero and npz has close_3m etc.
    if np.all(close == 0):
        for k in ("close_3m", "close_5m", "close_1h"):
            if k in npz:
                close = _arr(k)
                break
    if np.all(high == 0):
        for k in ("high_3m", "high_5m", "high_1h"):
            if k in npz:
                high = _arr(k)
                break
        if np.all(high == 0):
            high = close.copy()
    if np.all(low == 0):
        for k in ("low_3m", "low_5m", "low_1h"):
            if k in npz:
                low = _arr(k)
                break
        if np.all(low == 0):
            low = close.copy()

    out = np.zeros(n, dtype=bool)

    # Handle is_long=None as union (test helper)
    if is_long is None:
        # compute both directions and OR
        a = gap_risk_exit_vec(npz, n, cfg, is_long=True)
        b = gap_risk_exit_vec(npz, n, cfg, is_long=False)
        return a | b

    # Directional gate
    if is_long:
        if not bool(getattr(cfg, "GAP_RISK_EXIT_LONG_ENABLED", False)):
            return out
    else:
        if not bool(getattr(cfg, "GAP_RISK_EXIT_SHORT_ENABLED", False)):
            return out

    # Stateful scan per bar for identical live behavior
    has_retraced = False
    max_high = 0.0
    min_low = 0.0
    in_gap = False

    # For efficiency, use python loop (n typically < 100k, still fast enough and causal)
    for i in range(n):
        od = float(open_d[i])
        pc = float(prev_close[i])
        if od == 0 or pc == 0 or not math.isfinite(od) or not math.isfinite(pc):
            # reset episode on missing data
            has_retraced = False
            max_high = 0.0
            min_low = 0.0
            in_gap = False
            continue

        gap = (od < pc) if is_long else (od > pc)
        if not gap:
            has_retraced = False
            max_high = 0.0
            min_low = 0.0
            in_gap = False
            continue

        # gap continues or new gap
        hi = float(high[i]) if i < high.size else 0.0
        lo = float(low[i]) if i < low.size else 0.0
        cl = float(close[i]) if i < close.size else 0.0
        if hi == 0:
            hi = cl
        if lo == 0:
            lo = cl

        if not in_gap:
            # start new episode
            in_gap = True
            has_retraced = False
            max_high = hi
            min_low = lo
        else:
            # update running max/min before checking cond_b? cond_b should compare against prior max (excluding current)
            # So check before updating, then update
            pass

        # retraced this bar
        if is_long:
            retraced_now = hi >= pc
            cond_a = (cl < od) or (lo < od)
            cond_b = (min_low > 0) and (lo < min_low)
        else:
            retraced_now = lo <= pc
            cond_a = (cl > od) or (hi > od)
            cond_b = (max_high > 0) and (hi > max_high)

        retraced_seen = has_retraced or retraced_now

        # update has_retraced for next bar (include current)
        if retraced_now:
            has_retraced = True

        # Only fire if retraced seen
        if retraced_seen:
            fire = (cond_a_enabled and cond_a) or (cond_b_enabled and cond_b)
            if fire:
                out[i] = True

        # update running extremes after evaluating cond_b (so current bar not self-comparing)
        if hi > max_high:
            max_high = hi
        if min_low == 0 or lo < min_low:
            # for short, min_low tracks low; for long also track min
            # keep smallest low
            if lo != 0:
                min_low = lo if min_low == 0 else min(min_low, lo)

    return out


# ── GAP_RISK_REENTRY (gap close reentry — vectorized) ──
# After GAP_RISK_EXIT reduce/close, remain flagged for reentry when gap fills (price back to prev_close) within max_days.
# Vectorized scan mirrors live: exit → flag with fill_target=prev_close + max_days window → reenter on fill.
def gap_risk_reentry_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool = False,
) -> np.ndarray:
    """Vectorized GAP_RISK_REENTRY mask — reenter when gap fills within max_days after gap exit.

    Stateful: tracks gap exit bars, then watches for price returning to prev_close within max_days.
    Mirrors live _gap_reentry_pending logic (evaluate_reentry/evaluate_augment) — gap fill = prev_close.
    Respects GAP_RISK_REENTRY_ENABLED / ON_FILL / MAX_DAYS / REQUIRE_TREND. Volume exception: not used here.
    Returns bool ndarray where reentry should fire. Higher is never better for shorts — short fill is price <= prev_close.
    """
    if not bool(getattr(cfg, "GAP_RISK_REENTRY_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "GAP_RISK_REENTRY_ON_FILL", True)):
        return np.zeros(n, dtype=bool)
    max_days = int(getattr(cfg, "GAP_RISK_REENTRY_MAX_DAYS", 5) or 5)
    require_trend = bool(getattr(cfg, "GAP_RISK_REENTRY_REQUIRE_TREND", False))
    out = np.zeros(n, dtype=bool)
    # Get daily arrays
    def _arr(key: str, default: float = 0.0) -> np.ndarray:
        if key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.size < n:
                tmp = np.full(n, default, dtype=float)
                tmp[: min(n, a.size)] = a[: min(n, a.size)]
                a = tmp
            else:
                a = a[:n]
            return np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        return np.full(n, default, dtype=float)
    open_d = _arr("open_D") if "open_D" in npz else _arr("open")
    prev_close = _arr("close_D_prev") if "close_D_prev" in npz else _arr("prev_close")
    close = _arr("close") if "close" in npz else _arr("close_D")
    low = _arr("low") if "low" in npz else close.copy()
    high = _arr("high") if "high" in npz else close.copy()
    # Need gap exit mask to know where gap exits happened — compute via gap_risk_exit_vec
    # But to avoid circular import, we inline gap detection and use same logic: gap exit is where gap_risk_exit_vec fires
    # For reentry, we track pending gap fills after each gap exit
    # Use gap_risk_exit_vec to find exit bars
    exit_mask = gap_risk_exit_vec(npz, n, cfg, is_long=is_long)
    # Also need wt for trend if required
    wt1_d = _arr("wt1_D") if "wt1_D" in npz else np.zeros(n)
    wt2_d = _arr("wt2_D") if "wt2_D" in npz else np.zeros(n)
    # Stateful scan: pending gap fill targets with expiry
    pending: list[tuple[int, float, float]] = []  # (exit_idx, fill_target, gap_open)
    bars_per_day = 78 if "close_5m" in str(npz.keys()) or n > 1000 else 1  # approximate: 78 5m bars per day
    # More robust: use actual daily bars count? If n is daily, bars_per_day=1. If intraday, estimate.
    # Use max_days * bars_per_day as window
    max_bars = max_days * (78 if n > 500 else 1) if max_days else 5
    for i in range(n):
        # Expire pending older than max_bars
        pending = [(idx, tgt, go) for (idx, tgt, go) in pending if i - idx <= max_bars]
        # If exit just happened, add new pending (even if position reduced — vector sees exit bar)
        if exit_mask[i]:
            od = float(open_d[i]) if i < open_d.size else 0.0
            pc = float(prev_close[i]) if i < prev_close.size else 0.0
            if od != 0 and pc != 0:
                # fill target is prev_close
                pending.append((i, float(pc), float(od)))
        # Check reentry: price back to fill_target within 1% — higher is never better for shorts
        if pending:
            cl = float(close[i]) if i < close.size else 0.0
            lo = float(low[i]) if i < low.size else cl
            hi = float(high[i]) if i < high.size else cl
            # For short gap-up: fill when price <= prev_close (price down to fill)
            # For long gap-down: fill when price >= prev_close
            for (p_idx, fill_target, gap_open) in list(pending):
                filled = False
                if not is_long:
                    # short: higher is never better — fill is price down, so check low/close <= fill
                    filled = (cl <= fill_target * 1.01) or (lo <= fill_target)
                else:
                    filled = (cl >= fill_target * 0.99) or (hi >= fill_target)
                if filled:
                    if require_trend:
                        w1 = float(wt1_d[i]) if i < wt1_d.size else 0.0
                        w2 = float(wt2_d[i]) if i < wt2_d.size else 0.0
                        if is_long:
                            if not (w1 < w2):
                                continue
                        else:
                            if not (w1 > w2):
                                continue
                    out[i] = True
                    # Remove this pending after reentry (gap closed)
                    pending = [p for p in pending if p[0] != p_idx]
                    break
    return out

gap_risk_reentry_vec = gap_risk_reentry_vec

# alias for generic vec name
gap_risk_vec = gap_risk_exit_vec
