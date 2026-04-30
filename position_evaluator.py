"""Pure-function evaluators extracted from ez_manage.py for use in BOTH live trading
and vectorized backtesting. The scalar versions are byte-equivalent to the live code
so the live function reduces to: fetch I/O → call _core → return Signal.

The vectorized versions consume the per-symbol NPZ dict (numpy arrays) and return
per-bar fire arrays plus block_id / qty_mult / conviction. v8_quick_engine and
backtest_v8_engine call the vec versions directly.

KEY INVARIANT: scalar and vec MUST produce identical fire decisions on the same bar
given the same indicators. Regression test enforces this.

— Phase 2 ship: evaluate_reentry only. Other evaluators follow in Phase 3.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import numpy as np


@dataclass
class ReentrySignal:
    """Compatible with ez_manage.Signal — drop-in for action='REENTRY' returns."""
    action: str = 'REENTRY'
    reason: str = ''
    conviction: float = 0.0
    quantity: float = 0.0
    reduction_amount: float = 0.0


def _sf(x: Any, default: float = 0.0) -> float:
    """Safe float fetch — mirrors ez_manage.safe_fetch_float."""
    try:
        if x is None:
            return float(default)
        v = float(x)
        if v != v:  # NaN
            return float(default)
        return v
    except (TypeError, ValueError):
        return float(default)


def _ha_val(h: Any) -> int:
    """Convert HA candle to numeric: green/1 → 1, red/-1 → -1, else 0."""
    if h == 'green' or h == 1:
        return 1
    if h == 'red' or h == -1:
        return -1
    return 0


# ════════════════════════════════════════════════════════════════════════════════
# DC HIGH/LOW BREAK RETEST — used by B04
# ════════════════════════════════════════════════════════════════════════════════

def check_dc_high_break_retest_core(i: Dict[str, Any], current_price: float, is_long: bool) -> Tuple[bool, str, float]:
    """Scalar-equivalent of ez_manage.check_dc_high_break_retest.
    LONG: dc_high expanded > dc_high_ant by >1.5%, price retested level, k_3m turning up
    SHORT: mirror.
    Returns (fire, reason, qty_mult)."""
    if current_price <= 0:
        return False, "NO_PRICE", 1.0
    k_3m = _sf(i.get('stoch_k_3m'), 50)
    d_3m = _sf(i.get('stoch_d_3m'), 50)
    k_3m_prev = _sf(i.get('k_3m_prev'), k_3m)
    tfs = [('4h', 2.0), ('1h', 1.5), ('15m', 1.0), ('3m', 0.7)]
    for tf, mult in tfs:
        if is_long:
            dc_high = _sf(i.get(f'dc_high_{tf}'), 0)
            dc_high_ant = _sf(i.get(f'dc_high_{tf}_ant'), 0)
            if dc_high <= 0 or dc_high_ant <= 0:
                continue
            bp = (dc_high - dc_high_ant) / dc_high_ant
            if bp < 0.015:
                continue
            if not (dc_high_ant * 0.99 <= current_price <= dc_high_ant * 1.005):
                continue
            if not (k_3m > d_3m and k_3m > k_3m_prev):
                continue
            return True, f"DC_HIGH_RETEST_{tf.upper()}_bp={bp*100:.1f}%_level={dc_high_ant:.6f}_mult={mult:.1f}x", mult
        else:
            dc_low = _sf(i.get(f'dc_low_{tf}'), 0)
            dc_low_ant = _sf(i.get(f'dc_low_{tf}_ant'), 0)
            if dc_low <= 0 or dc_low_ant <= 0:
                continue
            bp = (dc_low_ant - dc_low) / dc_low_ant
            if bp < 0.015:
                continue
            if not (dc_low_ant * 0.995 <= current_price <= dc_low_ant * 1.01):
                continue
            if not (k_3m < d_3m and k_3m < k_3m_prev):
                continue
            return True, f"DC_LOW_RETEST_{tf.upper()}_bp={bp*100:.1f}%_level={dc_low_ant:.6f}_mult={mult:.1f}x", mult
    return False, "NO_DC_RETEST", 1.0


def check_dc_high_break_retest_vec(npz: Dict[str, np.ndarray], current_price: np.ndarray, is_long: bool, ltf: str = '3m') -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized DC retest. Returns (fire_mask, qty_mult) per bar.
    Iterates 4h→1h→15m→ltf, FIRST hit wins (mirrors scalar precedence)."""
    n = len(current_price)
    fire = np.zeros(n, dtype=bool)
    qty_mult = np.ones(n, dtype=np.float32)
    k = npz.get(f'stoch_k_{ltf}', np.full(n, 50.0, dtype=np.float32))
    d = npz.get(f'stoch_d_{ltf}', np.full(n, 50.0, dtype=np.float32))
    k_prev = np.roll(k, 1)
    k_prev[0] = k[0]
    k = np.nan_to_num(k, nan=50.0)
    d = np.nan_to_num(d, nan=50.0)
    k_prev = np.nan_to_num(k_prev, nan=50.0)
    tfs = [('4h', 2.0), ('1h', 1.5), ('15m', 1.0), (ltf, 0.7)]
    for tf, mult in tfs:
        if is_long:
            dch = npz.get(f'dc_high_{tf}', np.zeros(n, dtype=np.float32))
            dca = npz.get(f'dc_high_{tf}_ant', np.zeros(n, dtype=np.float32))
            valid = (dch > 0) & (dca > 0)
            bp = np.where(dca > 0, (dch - dca) / np.maximum(dca, 1e-9), 0.0)
            zone = (current_price >= dca * 0.99) & (current_price <= dca * 1.005)
            kcond = (k > d) & (k > k_prev)
            hit = valid & (bp >= 0.015) & zone & kcond & (~fire)
        else:
            dcl = npz.get(f'dc_low_{tf}', np.zeros(n, dtype=np.float32))
            dca = npz.get(f'dc_low_{tf}_ant', np.zeros(n, dtype=np.float32))
            valid = (dcl > 0) & (dca > 0)
            bp = np.where(dca > 0, (dca - dcl) / np.maximum(dca, 1e-9), 0.0)
            zone = (current_price >= dca * 0.995) & (current_price <= dca * 1.01)
            kcond = (k < d) & (k < k_prev)
            hit = valid & (bp >= 0.015) & zone & kcond & (~fire)
        fire = fire | hit
        qty_mult = np.where(hit, np.float32(mult), qty_mult)
    return fire, qty_mult


# ════════════════════════════════════════════════════════════════════════════════
# REENTRY — 9 blocks, scalar-pure
# ════════════════════════════════════════════════════════════════════════════════

def evaluate_reentry_core(
    i: Dict[str, Any],
    is_long: bool,
    current_price: float,
    config: Any,
    re_qty_base: float,
) -> Optional[ReentrySignal]:
    """Pure scalar reentry evaluator — extracted from ez_manage.evaluate_reentry
    lines 16896-16966. Caller is responsible for: position lookup, eligibility gate,
    ii() indicator fetch, current_price resolution, position-size cap.
    Returns None or ReentrySignal of the FIRST matching block (precedence preserved).
    """
    wt1_3m = _sf(i.get('wt1_3m'), 0); wt2_3m = _sf(i.get('wt2_3m'), 0)
    wt1_15m = _sf(i.get('wt1_15m'), 0); wt2_15m = _sf(i.get('wt2_15m'), 0)
    wt1_1h = _sf(i.get('wt1_1h'), 0); wt2_1h = _sf(i.get('wt2_1h'), 0)
    wt_vel_3m = _sf(i.get('wt_velocity_3m'), 0); wt_vel_15m = _sf(i.get('wt_velocity_15m'), 0)
    wt_vel_1h = _sf(i.get('wt_velocity_1h'), 0)
    k_3m = _sf(i.get('stoch_k_3m'), 50); d_3m = _sf(i.get('stoch_d_3m'), 50)
    k_15m = _sf(i.get('stoch_k_15m'), 50); k_1h = _sf(i.get('stoch_k_1h'), 50)
    k_3m_prev = _sf(i.get('k_3m_prev'), k_3m)
    dc_high_4h = _sf(i.get('dc_high_4h'), 0); dc_low_4h = _sf(i.get('dc_low_4h'), 0)
    dc_high_1h = _sf(i.get('dc_high_1h'), 0); dc_high_15m = _sf(i.get('dc_high_15m'), 0)
    dc_low_1h = _sf(i.get('dc_low_1h'), 0); dc_low_15m = _sf(i.get('dc_low_15m'), 0)
    ha_3m = i.get('ha_3m', 'neutral'); ha_15m = i.get('ha_15m', 'neutral'); ha_1h = i.get('ha_1h', 'neutral')

    # B15: STRONG TREND CONTINUATION
    if getattr(config, 'REENTRY_B15_STRONG_TREND_ENABLED', True):
        if is_long and dc_high_4h > 0 and current_price > dc_high_4h and wt_vel_1h > 2.0 and k_1h < 85:
            return ReentrySignal(reason=f'B15_STRONG_TREND_LONG_dc4h={dc_high_4h:.4f}_vel1h={wt_vel_1h:.1f}', conviction=95.0, quantity=re_qty_base)
        if not is_long and dc_low_4h > 0 and current_price < dc_low_4h and wt_vel_1h < -2.0 and k_1h > 15:
            return ReentrySignal(reason=f'B15_STRONG_TREND_SHORT_dc4h={dc_low_4h:.4f}_vel1h={wt_vel_1h:.1f}', conviction=95.0, quantity=re_qty_base)
    # B04: DC HIGH BREAK RETEST
    if getattr(config, 'REENTRY_B04_DC_RETEST_ENABLED', True):
        ok, reason, mult = check_dc_high_break_retest_core(i, current_price, is_long)
        if ok:
            qty = config.START_POSITION_SIZE * mult / max(current_price, 1e-9)
            return ReentrySignal(reason=f'B04_DC_RETEST_{reason}', conviction=88.0, quantity=qty)
    # B11: DC CHANNEL BREAKOUT
    if getattr(config, 'REENTRY_B11_DC_BREAK_ENABLED', True):
        if is_long and dc_high_1h > 0 and current_price > dc_high_1h * 1.001 and wt1_15m > wt2_15m:
            return ReentrySignal(reason=f'B11_DC_BREAK_LONG_dc1h={dc_high_1h:.4f}', conviction=88.0, quantity=re_qty_base)
        if not is_long and dc_low_1h > 0 and current_price < dc_low_1h * 0.999 and wt1_15m < wt2_15m:
            return ReentrySignal(reason=f'B11_DC_BREAK_SHORT_dc1h={dc_low_1h:.4f}', conviction=88.0, quantity=re_qty_base)
    # B02: BC156 BOTTOM BOUNCE
    if getattr(config, 'REENTRY_B02_BC156_BOTTOM_ENABLED', True):
        wt_bull_count = sum(1 for tf in ['3m', '15m', '1h', '4h']
                            if (bool(i.get(f'wt_bullish_{tf}', False)) == is_long))
        if is_long and wt1_15m < -20 and wt_vel_15m > 0 and wt1_1h > wt2_1h and wt_bull_count >= 2:
            return ReentrySignal(reason=f'B02_BC156_BOTTOM_LONG_wt15m={wt1_15m:.0f}_vel15m={wt_vel_15m:.1f}', conviction=85.0, quantity=re_qty_base * 1.5)
        if not is_long and wt1_15m > 20 and wt_vel_15m < 0 and wt1_1h < wt2_1h and wt_bull_count >= 2:
            return ReentrySignal(reason=f'B02_BC156_BOTTOM_SHORT_wt15m={wt1_15m:.0f}_vel15m={wt_vel_15m:.1f}', conviction=85.0, quantity=re_qty_base * 1.5)
    # B12: WT MOMENTUM
    if getattr(config, 'REENTRY_B12_WT_MOM_ENABLED', True):
        if is_long and wt1_3m > wt2_3m and wt1_15m > wt2_15m and wt1_1h > wt2_1h and wt_vel_3m > 1.0:
            return ReentrySignal(reason=f'B12_WT_MOM_LONG_vel3m={wt_vel_3m:.1f}', conviction=75.0, quantity=re_qty_base)
        if not is_long and wt1_3m < wt2_3m and wt1_15m < wt2_15m and wt1_1h < wt2_1h and wt_vel_3m < -1.0:
            return ReentrySignal(reason=f'B12_WT_MOM_SHORT_vel3m={wt_vel_3m:.1f}', conviction=75.0, quantity=re_qty_base)
    # B14: HA TREND CONFIRMATION
    if getattr(config, 'REENTRY_B14_HA_TREND_ENABLED', True):
        if is_long and _ha_val(ha_3m) == 1 and _ha_val(ha_15m) == 1 and _ha_val(ha_1h) == 1 and k_3m < 60:
            return ReentrySignal(reason=f'B14_HA_TREND_LONG_k3m={k_3m:.0f}', conviction=70.0, quantity=re_qty_base)
        if not is_long and _ha_val(ha_3m) == -1 and _ha_val(ha_15m) == -1 and _ha_val(ha_1h) == -1 and k_3m > 40:
            return ReentrySignal(reason=f'B14_HA_TREND_SHORT_k3m={k_3m:.0f}', conviction=70.0, quantity=re_qty_base)
    # B10: STOCHASTIC REVERSAL
    if getattr(config, 'REENTRY_B10_STOCH_REV_ENABLED', True):
        if is_long and k_3m_prev <= d_3m and k_3m > d_3m and k_3m < 25 and k_15m < 40:
            return ReentrySignal(reason=f'B10_STOCH_REV_LONG_k3m={k_3m:.0f}_k15m={k_15m:.0f}', conviction=72.0, quantity=re_qty_base)
        if not is_long and k_3m_prev >= d_3m and k_3m < d_3m and k_3m > 75 and k_15m > 60:
            return ReentrySignal(reason=f'B10_STOCH_REV_SHORT_k3m={k_3m:.0f}_k15m={k_15m:.0f}', conviction=72.0, quantity=re_qty_base)
    # B01: WT 2/3 IN FAVOR (default OFF)
    if getattr(config, 'REENTRY_B01_WT_2of3_ENABLED', False):
        if is_long:
            wf = int(wt1_3m > wt2_3m) + int(wt1_15m > wt2_15m) + int(wt1_1h > wt2_1h)
        else:
            wf = int(wt1_3m < wt2_3m) + int(wt1_15m < wt2_15m) + int(wt1_1h < wt2_1h)
        if wf >= 2:
            return ReentrySignal(reason=f'B01_WT_2of3_{wf}of3', conviction=85.0, quantity=re_qty_base)
    # B09: SNAPBACK (default OFF)
    if getattr(config, 'REENTRY_B09_SNAPBACK_ENABLED', False):
        if is_long and dc_low_15m > 0 and current_price < dc_low_15m * 1.003 and wt_vel_3m > 0.5 and k_3m < 30:
            return ReentrySignal(reason='B09_SNAPBACK_LONG', conviction=65.0, quantity=re_qty_base)
        if not is_long and dc_high_15m > 0 and current_price > dc_high_15m * 0.997 and wt_vel_3m < -0.5 and k_3m > 70:
            return ReentrySignal(reason='B09_SNAPBACK_SHORT', conviction=65.0, quantity=re_qty_base)
    return None


# Block-id constants — kept stable so vec results align with diagnostic dumps.
B15_STRONG_TREND = 15
B04_DC_RETEST = 4
B11_DC_BREAK = 11
B02_BC156_BOTTOM = 2
B12_WT_MOM = 12
B14_HA_TREND = 14
B10_STOCH_REV = 10
B01_WT_2OF3 = 1
B09_SNAPBACK = 9


def evaluate_reentry_vec(
    npz: Dict[str, np.ndarray],
    is_long: bool,
    config: Any,
    ltf: str = '3m',
) -> Dict[str, np.ndarray]:
    """Vectorized reentry: returns per-bar dict with keys
        fire (bool), block_id (int8), conviction (float32), qty_mult (float32).
    First-match precedence preserved across the 9 blocks (B15→B09).

    Caller is responsible for: eligibility mask, position-size cap, cooldown gap.
    The fire mask is the *raw block-fire* signal — gates layer on top.
    """
    close = npz.get('close')
    if close is None:
        # fallback to LTF close
        close = npz.get(f'close_{ltf}')
    n = len(close)
    cp = np.asarray(close, dtype=np.float32)

    def f(name: str, default: float = 0.0) -> np.ndarray:
        a = npz.get(name)
        if a is None:
            return np.full(n, default, dtype=np.float32)
        a = np.asarray(a, dtype=np.float32)
        if len(a) != n:
            # length mismatch — just trim/pad with default
            out = np.full(n, default, dtype=np.float32)
            m = min(len(a), n)
            out[:m] = a[:m]
            a = out
        return np.nan_to_num(a, nan=default)

    wt1_3m = f(f'wt1_{ltf}'); wt2_3m = f(f'wt2_{ltf}')
    wt1_15m = f('wt1_15m'); wt2_15m = f('wt2_15m')
    wt1_1h = f('wt1_1h'); wt2_1h = f('wt2_1h')
    wt_vel_3m = f(f'wt_velocity_{ltf}'); wt_vel_15m = f('wt_velocity_15m')
    wt_vel_1h = f('wt_velocity_1h')
    k_3m = f(f'stoch_k_{ltf}', 50.0); d_3m = f(f'stoch_d_{ltf}', 50.0)
    k_15m = f('stoch_k_15m', 50.0); k_1h = f('stoch_k_1h', 50.0)
    k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
    dc_high_4h = f('dc_high_4h'); dc_low_4h = f('dc_low_4h')
    dc_high_1h = f('dc_high_1h'); dc_high_15m = f('dc_high_15m')
    dc_low_1h = f('dc_low_1h'); dc_low_15m = f('dc_low_15m')
    ha_3m = npz.get(f'ha_{ltf}', np.zeros(n, dtype=np.int8))
    ha_15m = npz.get('ha_15m', np.zeros(n, dtype=np.int8))
    ha_1h = npz.get('ha_1h', np.zeros(n, dtype=np.int8))
    wt_bull_3m = npz.get(f'wt_bullish_{ltf}', np.zeros(n, dtype=np.int8)).astype(bool)
    wt_bull_15m = npz.get('wt_bullish_15m', np.zeros(n, dtype=np.int8)).astype(bool)
    wt_bull_1h = npz.get('wt_bullish_1h', np.zeros(n, dtype=np.int8)).astype(bool)
    wt_bull_4h = npz.get('wt_bullish_4h', np.zeros(n, dtype=np.int8)).astype(bool)

    fire = np.zeros(n, dtype=bool)
    block_id = np.zeros(n, dtype=np.int8)
    conviction = np.zeros(n, dtype=np.float32)
    qty_mult = np.ones(n, dtype=np.float32)

    def commit(mask: np.ndarray, bid: int, conv: float, qm: float):
        nonlocal fire, block_id, conviction, qty_mult
        m = mask & (~fire)
        fire = fire | m
        block_id = np.where(m, np.int8(bid), block_id)
        conviction = np.where(m, np.float32(conv), conviction)
        qty_mult = np.where(m, np.float32(qm), qty_mult)

    # B15
    if getattr(config, 'REENTRY_B15_STRONG_TREND_ENABLED', True):
        if is_long:
            commit((dc_high_4h > 0) & (cp > dc_high_4h) & (wt_vel_1h > 2.0) & (k_1h < 85), B15_STRONG_TREND, 95.0, 1.0)
        else:
            commit((dc_low_4h > 0) & (cp < dc_low_4h) & (wt_vel_1h < -2.0) & (k_1h > 15), B15_STRONG_TREND, 95.0, 1.0)
    # B04
    if getattr(config, 'REENTRY_B04_DC_RETEST_ENABLED', True):
        b04_fire, b04_mult = check_dc_high_break_retest_vec(npz, cp, is_long, ltf=ltf)
        m = b04_fire & (~fire)
        fire = fire | m
        block_id = np.where(m, np.int8(B04_DC_RETEST), block_id)
        conviction = np.where(m, np.float32(88.0), conviction)
        qty_mult = np.where(m, b04_mult, qty_mult)
    # B11
    if getattr(config, 'REENTRY_B11_DC_BREAK_ENABLED', True):
        if is_long:
            commit((dc_high_1h > 0) & (cp > dc_high_1h * 1.001) & (wt1_15m > wt2_15m), B11_DC_BREAK, 88.0, 1.0)
        else:
            commit((dc_low_1h > 0) & (cp < dc_low_1h * 0.999) & (wt1_15m < wt2_15m), B11_DC_BREAK, 88.0, 1.0)
    # B02
    if getattr(config, 'REENTRY_B02_BC156_BOTTOM_ENABLED', True):
        if is_long:
            wt_bull_cnt = wt_bull_3m.astype(np.int8) + wt_bull_15m.astype(np.int8) + wt_bull_1h.astype(np.int8) + wt_bull_4h.astype(np.int8)
            commit((wt1_15m < -20) & (wt_vel_15m > 0) & (wt1_1h > wt2_1h) & (wt_bull_cnt >= 2), B02_BC156_BOTTOM, 85.0, 1.5)
        else:
            wt_bear_cnt = (~wt_bull_3m).astype(np.int8) + (~wt_bull_15m).astype(np.int8) + (~wt_bull_1h).astype(np.int8) + (~wt_bull_4h).astype(np.int8)
            commit((wt1_15m > 20) & (wt_vel_15m < 0) & (wt1_1h < wt2_1h) & (wt_bear_cnt >= 2), B02_BC156_BOTTOM, 85.0, 1.5)
    # B12
    if getattr(config, 'REENTRY_B12_WT_MOM_ENABLED', True):
        if is_long:
            commit((wt1_3m > wt2_3m) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h) & (wt_vel_3m > 1.0), B12_WT_MOM, 75.0, 1.0)
        else:
            commit((wt1_3m < wt2_3m) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h) & (wt_vel_3m < -1.0), B12_WT_MOM, 75.0, 1.0)
    # B14
    if getattr(config, 'REENTRY_B14_HA_TREND_ENABLED', True):
        if is_long:
            commit((ha_3m == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_3m < 60), B14_HA_TREND, 70.0, 1.0)
        else:
            commit((ha_3m == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_3m > 40), B14_HA_TREND, 70.0, 1.0)
    # B10
    if getattr(config, 'REENTRY_B10_STOCH_REV_ENABLED', True):
        if is_long:
            commit((k_3m_prev <= d_3m) & (k_3m > d_3m) & (k_3m < 25) & (k_15m < 40), B10_STOCH_REV, 72.0, 1.0)
        else:
            commit((k_3m_prev >= d_3m) & (k_3m < d_3m) & (k_3m > 75) & (k_15m > 60), B10_STOCH_REV, 72.0, 1.0)
    # B01 (default OFF)
    if getattr(config, 'REENTRY_B01_WT_2of3_ENABLED', False):
        if is_long:
            wf = (wt1_3m > wt2_3m).astype(np.int8) + (wt1_15m > wt2_15m).astype(np.int8) + (wt1_1h > wt2_1h).astype(np.int8)
        else:
            wf = (wt1_3m < wt2_3m).astype(np.int8) + (wt1_15m < wt2_15m).astype(np.int8) + (wt1_1h < wt2_1h).astype(np.int8)
        commit(wf >= 2, B01_WT_2OF3, 85.0, 1.0)
    # B09 (default OFF)
    if getattr(config, 'REENTRY_B09_SNAPBACK_ENABLED', False):
        if is_long:
            commit((dc_low_15m > 0) & (cp < dc_low_15m * 1.003) & (wt_vel_3m > 0.5) & (k_3m < 30), B09_SNAPBACK, 65.0, 1.0)
        else:
            commit((dc_high_15m > 0) & (cp > dc_high_15m * 0.997) & (wt_vel_3m < -0.5) & (k_3m > 70), B09_SNAPBACK, 65.0, 1.0)

    return {
        'fire': fire,
        'block_id': block_id,
        'conviction': conviction,
        'qty_mult': qty_mult,
    }


# ════════════════════════════════════════════════════════════════════════════════
# Block id → name mapping for diagnostic dumps
# ════════════════════════════════════════════════════════════════════════════════

BLOCK_NAMES = {
    B15_STRONG_TREND: 'B15_STRONG_TREND',
    B04_DC_RETEST: 'B04_DC_RETEST',
    B11_DC_BREAK: 'B11_DC_BREAK',
    B02_BC156_BOTTOM: 'B02_BC156_BOTTOM',
    B12_WT_MOM: 'B12_WT_MOM',
    B14_HA_TREND: 'B14_HA_TREND',
    B10_STOCH_REV: 'B10_STOCH_REV',
    B01_WT_2OF3: 'B01_WT_2of3',
    B09_SNAPBACK: 'B09_SNAPBACK',
}
