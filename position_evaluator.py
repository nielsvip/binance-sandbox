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


# ════════════════════════════════════════════════════════════════════════════════
# Phase 3a: TRADE QTY PIPELINE
# Extracted from ez_positions_quick.execute_trade_wrapper (lines 11589-11782).
# Backtests historically used a flat START_POSITION_SIZE for every trade —
# silently dropping the WT_HTF_DISCOUNT (×0.3 / ×0.5) and HEDGE_SIZE_CAP
# mutations that live applies. Result: sweep-tuned qty multipliers (e.g. B02 1.5×)
# showed zero variance in Sharpe because the qty was never actually weighted.
#
# Live qty pipeline for OPEN/AUGMENT/REENTRY (non-hedge):
#   1. base_qty = block-multiplier × START_POSITION_SIZE / price
#   2. If not RZ entry AND HTF (4h+D) WT alignment < 2: qty × WT_HTF_DISCOUNT
#      - 1/2 aligned: × 0.5
#      - 0/2 aligned: × 0.3
#   3. Floor at MIN_POSITION_SIZE / price
#
# Live qty pipeline for HEDGE_OPEN:
#   1. base_qty = block-multiplier × START_POSITION_SIZE / price
#   2. HEDGE_SIZE_CAP: cap at origin_position_value × HEDGE_MAX_PCT_OF_LOSER / price
# ════════════════════════════════════════════════════════════════════════════════

# Modifier id constants — for diagnostic dumps + v8_quick stats
MOD_NONE = 0
MOD_WT_HTF_DISCOUNT_HALF = 1     # × 0.5 (1/2 HTF aligned)
MOD_WT_HTF_DISCOUNT_HARSH = 2    # × 0.3 (0/2 HTF aligned)
MOD_HEDGE_SIZE_CAP = 3           # capped at origin × pct
MOD_MIN_POS_FLOOR = 4            # raised to MIN_POSITION_SIZE

MODIFIER_NAMES = {
    MOD_NONE: 'NONE',
    MOD_WT_HTF_DISCOUNT_HALF: 'WT_HTF_DISCOUNT_x0.5',
    MOD_WT_HTF_DISCOUNT_HARSH: 'WT_HTF_DISCOUNT_x0.3',
    MOD_HEDGE_SIZE_CAP: 'HEDGE_SIZE_CAP',
    MOD_MIN_POS_FLOOR: 'MIN_POS_FLOOR',
}


def _wt_htf_aligned_count_scalar(i: Dict[str, Any], is_long: bool) -> int:
    """Count of HTF (4h, D) WT timeframes aligned with trade direction."""
    w1_4h = _sf(i.get('wt1_4h'), 0); w2_4h = _sf(i.get('wt2_4h'), 0)
    w1_D = _sf(i.get('wt1_D'), 0); w2_D = _sf(i.get('wt2_D'), 0)
    if is_long:
        return int(w1_4h > w2_4h and w1_4h != 0) + int(w1_D > w2_D and w1_D != 0)
    return int(w1_4h < w2_4h and w1_4h != 0) + int(w1_D < w2_D and w1_D != 0)


def compute_trade_qty_core(
    base_qty: float,
    action: str,
    is_long: bool,
    is_hedge: bool,
    current_price: float,
    indicators: Dict[str, Any],
    config: Any,
    is_rz_entry: bool = False,
    origin_position_value: float = 0.0,
) -> Tuple[float, int]:
    """Apply WT_HTF_DISCOUNT + HEDGE_SIZE_CAP + MIN_POS floor to a proposed qty.
    Returns (final_qty, modifier_id) — modifier_id surfaces the FIRST mutation that
    changed the qty (for stats). Multiple mutations can stack, but the dominant one
    is reported. Use compute_trade_qty_vec for backtest hot-path."""
    if current_price <= 0 or base_qty <= 0:
        return 0.0, MOD_NONE
    qty = base_qty
    modifier = MOD_NONE
    # 1. HEDGE_SIZE_CAP
    if is_hedge and origin_position_value > 0:
        cap_pct = float(getattr(config, 'HEDGE_MAX_PCT_OF_LOSER', 1.0))
        max_qty = (origin_position_value * cap_pct) / current_price
        if qty > max_qty:
            qty = max_qty
            modifier = MOD_HEDGE_SIZE_CAP
    # 2. WT_HTF_DISCOUNT (non-hedge OPEN/AUGMENT/REENTRY paths only, RZ exempt)
    if not is_hedge and not is_rz_entry and getattr(config, 'WT_HTF_DISCOUNT_ENABLED', True):
        htf_aligned = _wt_htf_aligned_count_scalar(indicators, is_long)
        if htf_aligned < 2:
            disc = 0.5 if htf_aligned == 1 else 0.3
            qty = qty * disc
            if modifier == MOD_NONE:
                modifier = MOD_WT_HTF_DISCOUNT_HALF if htf_aligned == 1 else MOD_WT_HTF_DISCOUNT_HARSH
    # 3. MIN_POSITION_SIZE floor
    min_pos_size = float(getattr(config, 'MIN_POSITION_SIZE', 55.0))
    min_qty = min_pos_size / current_price
    if qty < min_qty:
        qty = min_qty
        if modifier == MOD_NONE:
            modifier = MOD_MIN_POS_FLOOR
    return qty, modifier


def compute_trade_qty_vec(
    npz: Dict[str, np.ndarray],
    base_qty_arr: np.ndarray,
    is_long: bool,
    config: Any,
    is_hedge: bool = False,
    is_rz_entry_arr: Optional[np.ndarray] = None,
    origin_value_arr: Optional[np.ndarray] = None,
) -> Dict[str, np.ndarray]:
    """Vectorized qty pipeline. base_qty_arr is per-bar proposed qty (already
    multiplied by block multiplier and divided by price by the caller).

    Returns dict with:
        qty (float32):       final per-bar qty after all mutations
        modifier (int8):     per-bar dominant modifier id
    """
    n = len(base_qty_arr)
    qty = np.asarray(base_qty_arr, dtype=np.float32).copy()
    modifier = np.full(n, MOD_NONE, dtype=np.int8)
    close = npz.get('close')
    if close is None:
        # Try LTF close
        for tf in ('3m', '5m'):
            close = npz.get(f'close_{tf}')
            if close is not None:
                break
    cp = np.asarray(close, dtype=np.float32) if close is not None else np.ones(n, dtype=np.float32)
    if len(cp) != n:
        cp = cp[:n] if len(cp) > n else np.pad(cp, (0, n - len(cp)), constant_values=cp[-1] if len(cp) else 1.0)
    cp = np.where(cp > 0, cp, 1.0)

    # 1. HEDGE_SIZE_CAP
    if is_hedge and origin_value_arr is not None:
        cap_pct = float(getattr(config, 'HEDGE_MAX_PCT_OF_LOSER', 1.0))
        max_qty = (origin_value_arr * cap_pct) / cp
        cap_hit = (origin_value_arr > 0) & (qty > max_qty)
        qty = np.where(cap_hit, max_qty, qty)
        modifier = np.where(cap_hit, np.int8(MOD_HEDGE_SIZE_CAP), modifier)

    # 2. WT_HTF_DISCOUNT
    if not is_hedge and getattr(config, 'WT_HTF_DISCOUNT_ENABLED', True):
        w1_4h = np.nan_to_num(np.asarray(npz.get('wt1_4h', np.zeros(n)), dtype=np.float32), nan=0.0)
        w2_4h = np.nan_to_num(np.asarray(npz.get('wt2_4h', np.zeros(n)), dtype=np.float32), nan=0.0)
        w1_D = np.nan_to_num(np.asarray(npz.get('wt1_D', np.zeros(n)), dtype=np.float32), nan=0.0)
        w2_D = np.nan_to_num(np.asarray(npz.get('wt2_D', np.zeros(n)), dtype=np.float32), nan=0.0)
        if is_long:
            al_4h = (w1_4h > w2_4h) & (w1_4h != 0)
            al_D = (w1_D > w2_D) & (w1_D != 0)
        else:
            al_4h = (w1_4h < w2_4h) & (w1_4h != 0)
            al_D = (w1_D < w2_D) & (w1_D != 0)
        htf_aligned = al_4h.astype(np.int8) + al_D.astype(np.int8)
        rz_mask = is_rz_entry_arr if is_rz_entry_arr is not None else np.zeros(n, dtype=bool)
        eligible = ~rz_mask
        half_discount = eligible & (htf_aligned == 1)
        harsh_discount = eligible & (htf_aligned == 0)
        qty = np.where(half_discount, qty * 0.5, qty)
        qty = np.where(harsh_discount, qty * 0.3, qty)
        # Set modifier where it was previously NONE
        new_mod_half = half_discount & (modifier == MOD_NONE)
        new_mod_harsh = harsh_discount & (modifier == MOD_NONE)
        modifier = np.where(new_mod_half, np.int8(MOD_WT_HTF_DISCOUNT_HALF), modifier)
        modifier = np.where(new_mod_harsh, np.int8(MOD_WT_HTF_DISCOUNT_HARSH), modifier)

    # 3. MIN_POSITION_SIZE floor
    min_pos_size = float(getattr(config, 'MIN_POSITION_SIZE', 55.0))
    min_qty_arr = min_pos_size / cp
    floor_hit = qty < min_qty_arr
    qty = np.where(floor_hit, min_qty_arr, qty)
    new_mod_floor = floor_hit & (modifier == MOD_NONE)
    modifier = np.where(new_mod_floor, np.int8(MOD_MIN_POS_FLOOR), modifier)

    return {'qty': qty, 'modifier': modifier}


# ════════════════════════════════════════════════════════════════════════════════
# Phase 4: PROCESS_POSITION EXIT GATES
# Extracted from ez_manage.process_position lines 20500-20651. The 5 most-impactful
# technical exit gates that v8_quick's compute_exit_signals does NOT replicate:
#   • WT_4H_VEL_EXIT      — vel_4h against direction + age + profit + k_extreme
#   • DC_HOPELESS_EXIT    — entry_price outside dc_4h channel + age > 900s
#   • WT_EXHAUST_EXIT     — wt_momentum_state EXHAUST on 4h + (1h or 15m)
#   • WT_PERCENTILE_EXIT  — wt_percentile D + 4h both at extreme
#   • E_1_WT_DELTA_EXIT   — wt_composite_delta crosses threshold against position
#   • E_3_STRUCTURE_EXIT  — wt_structure HH/HL/LH/LL on ≥2 HTFs
#
# Live and backtest historically had different exit logic — sweep tuning on these
# gates produced different live behavior than backtest predicted.
# ════════════════════════════════════════════════════════════════════════════════

# Exit reason ids (for vec dumps + diagnostics)
EXIT_NONE = 0
EXIT_WT_4H_VEL = 1
EXIT_DC_HOPELESS = 2
EXIT_WT_EXHAUST = 3
EXIT_WT_PERCENTILE = 4
EXIT_E1_WT_DELTA = 5
EXIT_E3_STRUCTURE = 6

EXIT_NAMES = {
    EXIT_NONE: 'NONE',
    EXIT_WT_4H_VEL: 'WT_4H_VEL_EXIT',
    EXIT_DC_HOPELESS: 'DC_HOPELESS_EXIT',
    EXIT_WT_EXHAUST: 'WT_EXHAUST_EXIT',
    EXIT_WT_PERCENTILE: 'WT_PERCENTILE_EXIT',
    EXIT_E1_WT_DELTA: 'E_1_WT_DELTA_EXIT',
    EXIT_E3_STRUCTURE: 'E_3_STRUCTURE_EXIT',
}

# wt_momentum_state encoding (matches backtest_v8_harness._INT_DECODE)
_MOM_EXHAUST_DOWN = -2
_MOM_EXHAUST_UP = 2
# wt_structure encoding: -1=LH, 0=NEUTRAL, 1=HH


def evaluate_exit_gates_core(
    indicators: Dict[str, Any],
    is_long: bool,
    config: Any,
    *,
    entry_price: float = 0.0,
    gain_pct: float = 0.0,
    pos_age_s: float = 0.0,
) -> Tuple[int, str]:
    """Pure scalar exit-gate evaluator. Mirrors ez_manage.process_position
    exit-gate sequence (WT_4H_VEL → DC_HOPELESS → WT_EXHAUST → WT_PERCENTILE →
    E_1 delta → E_3 structure). Returns (exit_id, reason_string) — first match
    wins, matching live precedence.

    The position-state args (entry_price, gain_pct, pos_age_s) are pure inputs;
    caller is responsible for pulling them from the live position object or
    the backtest trade state."""
    current_price = _sf(indicators.get('current_price'), 0.0)
    if current_price == 0.0:
        # Fall back to LTF close if caller did not pass one
        for tf in ('3m', '5m'):
            cp = _sf(indicators.get(f'close_{tf}'), 0.0)
            if cp > 0:
                current_price = cp
                break
    # WT_4H_VEL_EXIT
    if getattr(config, 'WT_4H_VEL_EXIT_ENABLED', True):
        vel_4h = _sf(indicators.get('wt_velocity_4h'), 0)
        long_min = float(getattr(config, 'WT_4H_VEL_EXIT_LONG_VEL_MIN', -2.0))
        short_min = float(getattr(config, 'WT_4H_VEL_EXIT_SHORT_VEL_MIN', 2.0))
        against = (is_long and vel_4h < long_min) or (not is_long and vel_4h > short_min)
        req_profit = bool(getattr(config, 'WT_4H_VEL_EXIT_REQUIRE_PROFIT', True))
        req_kx = bool(getattr(config, 'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME', True))
        kx_hi = float(getattr(config, 'WT_4H_VEL_EXIT_K_EXTREME_HIGH', 80.0))
        kx_lo = float(getattr(config, 'WT_4H_VEL_EXIT_K_EXTREME_LOW', 20.0))
        comm_buf = float(getattr(config, 'COMMISSION_BUFFER_PCT', 0.10))
        profit_ok = (gain_pct >= comm_buf) if req_profit else True
        if req_kx:
            k_3m = _sf(indicators.get('stoch_k_3m'), 50)
            k_15m = _sf(indicators.get('stoch_k_15m'), 50)
            kx_ok = ((k_3m >= kx_hi) or (k_15m >= kx_hi)) if is_long else ((k_3m <= kx_lo) or (k_15m <= kx_lo))
        else:
            kx_ok = True
        if against and pos_age_s > 360 and profit_ok and kx_ok:
            return EXIT_WT_4H_VEL, f"WT_4H_VEL_vel={vel_4h:.1f}_g={gain_pct:.2f}%"
    # DC_HOPELESS_EXIT
    if getattr(config, 'DC_HOPELESS_EXIT_ENABLED', True) and entry_price > 0:
        dc_h_4h = _sf(indicators.get('dc_high_4h'), 0)
        dc_l_4h = _sf(indicators.get('dc_low_4h'), 0)
        min_age = float(getattr(config, 'DC_HOPELESS_EXIT_MIN_AGE_S', 900))
        if dc_h_4h > 0 and dc_l_4h > 0 and pos_age_s > min_age:
            hopeless = (is_long and entry_price > dc_h_4h) or (not is_long and entry_price < dc_l_4h)
            if hopeless:
                return EXIT_DC_HOPELESS, f"DC_HOPELESS_entry={entry_price:.4f}_dc=[{dc_l_4h:.4f},{dc_h_4h:.4f}]"
    # WT_EXHAUST_EXIT
    if getattr(config, 'WT_EXHAUST_EXIT_ENABLED', True):
        m4 = int(_sf(indicators.get('wt_momentum_state_4h'), 0))
        m1 = int(_sf(indicators.get('wt_momentum_state_1h'), 0))
        m15 = int(_sf(indicators.get('wt_momentum_state_15m'), 0))
        exhaust = (is_long and m4 == _MOM_EXHAUST_UP and (m1 == _MOM_EXHAUST_UP or m15 == _MOM_EXHAUST_UP)) or \
                  (not is_long and m4 == _MOM_EXHAUST_DOWN and (m1 == _MOM_EXHAUST_DOWN or m15 == _MOM_EXHAUST_DOWN))
        req_gain = bool(getattr(config, 'WT_EXHAUST_EXIT_REQUIRE_GAIN', False))
        if exhaust and (not req_gain or gain_pct > 0):
            return EXIT_WT_EXHAUST, f"WT_EXHAUST_4h={m4}_1h={m1}_15m={m15}_g={gain_pct:.2f}%"
    # WT_PERCENTILE_EXIT
    if getattr(config, 'WT_PERCENTILE_EXIT_ENABLED', True):
        pct_D = _sf(indicators.get('wt_percentile_D'), 50)
        pct_4h = _sf(indicators.get('wt_percentile_4h'), 50)
        ob_D = float(getattr(config, 'WT_PERCENTILE_EXIT_OB_D', 90))
        ob_4h = float(getattr(config, 'WT_PERCENTILE_EXIT_OB_4H', 75))
        os_D = float(getattr(config, 'WT_PERCENTILE_EXIT_OS_D', 10))
        os_4h = float(getattr(config, 'WT_PERCENTILE_EXIT_OS_4H', 25))
        fire = (is_long and pct_D > ob_D and pct_4h > ob_4h) or (not is_long and pct_D < os_D and pct_4h < os_4h)
        if fire:
            return EXIT_WT_PERCENTILE, f"WT_PERCENTILE_pctD={pct_D:.0f}_4h={pct_4h:.0f}"
    # E_1 WT_DELTA_EXIT (default OFF)
    if bool(getattr(config, 'E_1_WT_EXIT_USE_DELTA_ENABLED', False)):
        delta = _sf(indicators.get('wt_composite_delta'), 0)
        thr = float(getattr(config, 'E_1_EXIT_DELTA_THR', 50.0))
        fire = (is_long and delta < -thr) or (not is_long and delta > thr)
        if fire:
            return EXIT_E1_WT_DELTA, f"E_1_DELTA_delta={delta:+.0f}_thr={thr:.0f}"
    # E_3 STRUCTURE_EXIT (default OFF — mode 0)
    e3_mode = int(getattr(config, 'E_3_USE_WT_STRUCTURE_EXIT_MODE', 0))
    if e3_mode == 2:  # mode 1 = shadow (no exit), mode 2 = live exit
        against = 0
        for tf in ('15m', '1h', '4h'):
            s = int(_sf(indicators.get(f'wt_structure_{tf}'), 0))
            # encoding: -1=LH (against LONG), 1=HH (against SHORT). Live also checked
            # 'LL'/'HL' but those map to wt_trough_structure not wt_structure → dead in live too.
            if (is_long and s == -1) or (not is_long and s == 1):
                against += 1
        if against >= 2:
            return EXIT_E3_STRUCTURE, f"E_3_STRUCTURE_against={against}TF"
    return EXIT_NONE, ""


def evaluate_exit_gates_vec(
    npz: Dict[str, np.ndarray],
    is_long: bool,
    config: Any,
    ltf: str = '3m',
) -> Dict[str, np.ndarray]:
    """Vectorized indicator-only exit gates. Returns per-bar masks for gates that
    don't require trade state (entry_price, gain, age). The trade loop should call
    `evaluate_exit_gates_core` with full state for the WT_4H_VEL + DC_HOPELESS
    gates that need profit/age info, OR layer profit/age gates manually.

    Returns dict with bool arrays:
        wt_4h_vel_raw       — vel_4h against direction (no profit/age/k filter)
        wt_4h_vel_full      — adds k_extreme filter (profit + age still caller's job)
        wt_exhaust          — fully gated (indicator-only)
        wt_percentile       — fully gated (indicator-only)
        e1_wt_delta         — fully gated (indicator-only)
        e3_structure        — fully gated (indicator-only)
        dc_h_4h, dc_l_4h    — passthrough for caller's entry_price comparison
    """
    close = npz.get('close')
    if close is None:
        close = npz.get(f'close_{ltf}')
    n = len(close)

    def f(name: str, default: float = 0.0) -> np.ndarray:
        a = npz.get(name)
        if a is None:
            return np.full(n, default, dtype=np.float32)
        a = np.asarray(a)
        if a.dtype == np.int8:
            a = a.astype(np.int8)
            if len(a) != n:
                pad = np.zeros(n, dtype=np.int8)
                m = min(len(a), n)
                pad[:m] = a[:m]
                a = pad
            return a
        a = a.astype(np.float32)
        if len(a) != n:
            out = np.full(n, default, dtype=np.float32)
            m = min(len(a), n)
            out[:m] = a[:m]
            a = out
        return np.nan_to_num(a, nan=default)

    out = {}
    # WT_4H_VEL — raw (against direction) and full (adds k_extreme)
    if getattr(config, 'WT_4H_VEL_EXIT_ENABLED', True):
        vel_4h = f('wt_velocity_4h')
        long_min = float(getattr(config, 'WT_4H_VEL_EXIT_LONG_VEL_MIN', -2.0))
        short_min = float(getattr(config, 'WT_4H_VEL_EXIT_SHORT_VEL_MIN', 2.0))
        out['wt_4h_vel_raw'] = (vel_4h < long_min) if is_long else (vel_4h > short_min)
        if bool(getattr(config, 'WT_4H_VEL_EXIT_REQUIRE_K_EXTREME', True)):
            kx_hi = float(getattr(config, 'WT_4H_VEL_EXIT_K_EXTREME_HIGH', 80.0))
            kx_lo = float(getattr(config, 'WT_4H_VEL_EXIT_K_EXTREME_LOW', 20.0))
            k_3m = f(f'stoch_k_{ltf}', 50.0); k_15m = f('stoch_k_15m', 50.0)
            kx = ((k_3m >= kx_hi) | (k_15m >= kx_hi)) if is_long else ((k_3m <= kx_lo) | (k_15m <= kx_lo))
            out['wt_4h_vel_full'] = out['wt_4h_vel_raw'] & kx
        else:
            out['wt_4h_vel_full'] = out['wt_4h_vel_raw']
    else:
        out['wt_4h_vel_raw'] = np.zeros(n, dtype=bool)
        out['wt_4h_vel_full'] = np.zeros(n, dtype=bool)

    # DC_HOPELESS — passthrough for caller's entry_price check
    out['dc_h_4h'] = f('dc_high_4h')
    out['dc_l_4h'] = f('dc_low_4h')

    # WT_EXHAUST_EXIT — fully gated
    if getattr(config, 'WT_EXHAUST_EXIT_ENABLED', True):
        m4 = f('wt_momentum_state_4h', 0)
        m1 = f('wt_momentum_state_1h', 0)
        m15 = f('wt_momentum_state_15m', 0)
        if is_long:
            out['wt_exhaust'] = (m4 == _MOM_EXHAUST_UP) & ((m1 == _MOM_EXHAUST_UP) | (m15 == _MOM_EXHAUST_UP))
        else:
            out['wt_exhaust'] = (m4 == _MOM_EXHAUST_DOWN) & ((m1 == _MOM_EXHAUST_DOWN) | (m15 == _MOM_EXHAUST_DOWN))
    else:
        out['wt_exhaust'] = np.zeros(n, dtype=bool)

    # WT_PERCENTILE_EXIT
    if getattr(config, 'WT_PERCENTILE_EXIT_ENABLED', True):
        pct_D = f('wt_percentile_D', 50)
        pct_4h = f('wt_percentile_4h', 50)
        if is_long:
            ob_D = float(getattr(config, 'WT_PERCENTILE_EXIT_OB_D', 90))
            ob_4h = float(getattr(config, 'WT_PERCENTILE_EXIT_OB_4H', 75))
            out['wt_percentile'] = (pct_D > ob_D) & (pct_4h > ob_4h)
        else:
            os_D = float(getattr(config, 'WT_PERCENTILE_EXIT_OS_D', 10))
            os_4h = float(getattr(config, 'WT_PERCENTILE_EXIT_OS_4H', 25))
            out['wt_percentile'] = (pct_D < os_D) & (pct_4h < os_4h)
    else:
        out['wt_percentile'] = np.zeros(n, dtype=bool)

    # E_1 WT_DELTA_EXIT
    if bool(getattr(config, 'E_1_WT_EXIT_USE_DELTA_ENABLED', False)):
        delta = f('wt_composite_delta')
        thr = float(getattr(config, 'E_1_EXIT_DELTA_THR', 50.0))
        out['e1_wt_delta'] = (delta < -thr) if is_long else (delta > thr)
    else:
        out['e1_wt_delta'] = np.zeros(n, dtype=bool)

    # E_3 STRUCTURE_EXIT (mode 2 = live)
    e3_mode = int(getattr(config, 'E_3_USE_WT_STRUCTURE_EXIT_MODE', 0))
    if e3_mode == 2:
        against = np.zeros(n, dtype=np.int8)
        for tf in ('15m', '1h', '4h'):
            s = f(f'wt_structure_{tf}', 0)
            against = against + (((s == -1) if is_long else (s == 1))).astype(np.int8)
        out['e3_structure'] = against >= 2
    else:
        out['e3_structure'] = np.zeros(n, dtype=bool)

    return out
