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
    # B16: DC MIDRANGE RECLAIM — price in upper half of 1h DC channel + 15m WT momentum
    # Proxy for "price recovered above exit level"; fires when trend resumes after reduction.
    if getattr(config, 'REENTRY_B16_MIDRANGE_ENABLED', True):
        _dc_span_1h = max(dc_high_1h - dc_low_1h, 1e-9) if dc_high_1h > 0 and dc_low_1h > 0 else 0
        _dc_pos_1h = (current_price - dc_low_1h) / _dc_span_1h if _dc_span_1h > 0 else 0.5
        if is_long and _dc_pos_1h > 0.5 and wt1_15m > wt2_15m and wt_vel_15m > 0 and k_15m < 78:
            return ReentrySignal(reason=f'B16_MIDRANGE_LONG_dcpos={_dc_pos_1h:.2f}_wt15={wt1_15m:.0f}_k15={k_15m:.0f}', conviction=68.0, quantity=re_qty_base)
        if not is_long and _dc_pos_1h < 0.5 and wt1_15m < wt2_15m and wt_vel_15m < 0 and k_15m > 22:
            return ReentrySignal(reason=f'B16_MIDRANGE_SHORT_dcpos={_dc_pos_1h:.2f}_wt15={wt1_15m:.0f}_k15={k_15m:.0f}', conviction=68.0, quantity=re_qty_base)
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
B16_MIDRANGE = 16


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
    # B16: DC MIDRANGE RECLAIM — proxy for "price recovered above exit level"
    if getattr(config, 'REENTRY_B16_MIDRANGE_ENABLED', True):
        dc_span_1h = np.maximum(dc_high_1h - dc_low_1h, np.float32(1e-9))
        dc_pos_1h_v = np.where((dc_high_1h > 0) & (dc_low_1h > 0), (cp - dc_low_1h) / dc_span_1h, np.float32(0.5))
        if is_long:
            commit((dc_pos_1h_v > 0.5) & (wt1_15m > wt2_15m) & (wt_vel_15m > 0) & (k_15m < 78), B16_MIDRANGE, 68.0, 1.0)
        else:
            commit((dc_pos_1h_v < 0.5) & (wt1_15m < wt2_15m) & (wt_vel_15m < 0) & (k_15m > 22), B16_MIDRANGE, 68.0, 1.0)

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
    B16_MIDRANGE: 'B16_MIDRANGE',
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
_MOM_EXHAUST_DOWN = -1
_MOM_EXHAUST_UP = 1
# CORRECT per backtest_v8_precompute.py:718-720:
#   mom = 2  → score>0 AND vel>0   → IMPULSE_UP   (full bull momentum, NOT exhausting)
#   mom = 1  → score>0 AND vel<=0  → EXHAUST_UP   (bull-but-decelerating, exit longs)
#   mom = -2 → score<0 AND vel<0   → IMPULSE_DOWN
#   mom = -1 → score<0 AND vel>=0  → EXHAUST_DOWN
# Prior (e1a48d7b...) used 2/-2 which made wt_exhaust fire on IMPULSE bars (33% of all bars
# on tradier NPZs), causing LONG positions to exit on the strongest bull bars and trade-count
# explosion (3,008 trades / 5 syms / 2 sides / 2.13yr = 141 trades/sym-side/yr vs slow-engine
# 6.5 trades/sym/yr reference). Fix verified 2026-05-17.
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


# ════════════════════════════════════════════════════════════════════════════════
# UNIVERSAL_NOLOSS_GATE + OBLIGATORY_HEDGE — exit-side gate parity
# ════════════════════════════════════════════════════════════════════════════════
# Mirrors ez_manage.py:14263-14436 (execute_now reduce-at-loss branch).
#
# Decision flow (live):
#   1. If reduce/close attempt arrives while UNIVERSAL_NOLOSS_GATE is active
#      AND position is in real-loss (gain < COMMISSION_BUFFER_PCT):
#      a) Check bypass list (UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS, is_hedge,
#         LIQUIDATION, STRUCTURAL_RANGE_SHIFT) → if match: ALLOW_REDUCE.
#      b) Else fire OBLIGATORY_HEDGE if enabled + WT cascade fires.
#         - cascade preference (live ez_manage:14387-14401):
#           * HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H (default True 2026-05-12)
#               trigger = 3m AND (15m OR 1h)
#           * HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H (older)
#               trigger = 3m AND 1h
#           * HEDGE_TRIGGER_USE_WT_3M_ALONE
#               trigger = 3m alone
#           * else fallback = 15m OR (3m AND 1h)
#         - Plus the OBLIGATORY_HEDGE_WT_TFS_REQUIRED count gate (OR with cascade).
#      c) If hedge fires successfully → HOLD (block reduce).
#      d) If hedge attempt fails AND HEDGE_FAILED_FALLBACK_CLOSE_ENABLED →
#         CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK (reason gets 'HEDGE_FAILED_FALLBACK_CLOSE'
#         prefix so downstream paths match the bypass list).
#      e) Else (hedge skipped/disabled/wt_not_against/gain_too_small) → HOLD.
#
# In backtest the hedge_attempt_succeeded outcome is supplied by the caller (engine
# decides whether the hedge can be opened — usually True in vec sim).
#
# OUTPUT: (action, reason, hedge_should_fire, hedge_qty)
#   action ∈ {"HOLD", "ALLOW_REDUCE", "CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK"}

# Action constants for callers
NOLOSS_ACTION_HOLD = "HOLD"
NOLOSS_ACTION_ALLOW_REDUCE = "ALLOW_REDUCE"
NOLOSS_ACTION_CLOSE_HEDGE_FAILED = "CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK"


def _wt_against_scalar(ind: Dict[str, Any], tf: str, is_long: bool) -> bool:
    """LONG: wt1 < wt2 is against (price falling). SHORT: wt1 > wt2 is against (price rising).
    Mirrors ez_manage.py:14373 `_ag = (_w1 < _w2) if _is_long else (_w1 > _w2)`."""
    w1 = _sf(ind.get(f'wt1_{tf}'), 0.0)
    w2 = _sf(ind.get(f'wt2_{tf}'), 0.0)
    return (w1 < w2) if is_long else (w1 > w2)


def _bypass_reason_matches(reason: str, bypass_list) -> Optional[str]:
    """Case-insensitive substring match of `reason` against bypass list."""
    if not reason or not bypass_list:
        return None
    ru = reason.upper()
    for b in bypass_list:
        if b and b.upper() in ru:
            return b
    return None


def evaluate_noloss_gate_core(
    indicators: Dict[str, Any],
    is_long: bool,
    real_gain_pct: float,
    positionAmt: float,
    mark_price: float,
    reason: str,
    config: Any,
    *,
    account_key: str = "",
    is_hedge: bool = False,
    is_reduce: bool = True,
    hedge_already_active: bool = False,
    hedge_attempt_succeeded: bool = True,
) -> Tuple[str, str, bool, Optional[float]]:
    """Scalar UNIVERSAL_NOLOSS_GATE + OBLIGATORY_HEDGE evaluator.

    Returns (action, reason_out, hedge_should_fire, hedge_qty):
        action ∈ {HOLD, ALLOW_REDUCE, CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK}
        reason_out — possibly mutated reason (live appends HEDGE_FAILED_FALLBACK_CLOSE prefix)
        hedge_should_fire — True when OBLIGATORY_HEDGE conditions met (caller actually fires it)
        hedge_qty — abs(positionAmt) * OBLIGATORY_HEDGE_PCT when hedge fires, else None

    Pure function: no I/O, no side effects, no logger calls. Live ez_manage retains the
    logging at its own callsite; this function provides ground-truth decision parity.

    NOTE: This evaluator does NOT implement DC_RECOVERY_EXIT_BYPASS or NOLOSS_MIN_PROFIT
    blocking — those are secondary paths inside the live gate that depend on indicator
    fetching outside the canonical (real_gain, wt_*) decision surface. Callers who need
    them must run their own pre-checks; backtest_v8_engine has historically skipped
    DC_RECOVERY (default OFF) and runs NOLOSS_MIN_PROFIT=0 globally, so omitting them
    matches the production sweep config.
    """
    # Gate not active at all → caller can reduce freely
    ung_active = bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE', True))
    if not ung_active or not is_reduce:
        return (NOLOSS_ACTION_ALLOW_REDUCE, reason, False, None)

    reason_upper = (reason or "").upper()

    # Hedge / liquidation bypass (live ez_manage:14274)
    if is_hedge or 'LIQUIDATION' in reason_upper:
        return (NOLOSS_ACTION_ALLOW_REDUCE, reason, False, None)

    # Technical bypass list (live ez_manage:14277-14283)
    if bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL', True)):
        bypass_list = getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []
        matched = _bypass_reason_matches(reason_upper, bypass_list)
        if matched is not None:
            return (NOLOSS_ACTION_ALLOW_REDUCE, reason, False, None)

    # Structural range shift bypass (live ez_manage:14286, 14333)
    if 'STRUCTURAL_RANGE_SHIFT' in reason_upper:
        return (NOLOSS_ACTION_ALLOW_REDUCE, reason, False, None)

    # COMMISSION_BUFFER_PCT loss check (live ez_manage:14294-14295)
    comm_buf = float(getattr(config, 'COMMISSION_BUFFER_PCT', 0.10))
    if real_gain_pct >= comm_buf:
        # Not in real loss (after commissions) → caller can reduce
        return (NOLOSS_ACTION_ALLOW_REDUCE, reason, False, None)

    # ─── In real loss + no bypass → consider OBLIGATORY_HEDGE ────────────────────
    hedge_outcome = "skip"
    hedge_should_fire = False
    hedge_qty: Optional[float] = None

    if bool(getattr(config, 'OBLIGATORY_HEDGE_ENABLED', True)):
        oh_min_loss = float(getattr(config, 'OBLIGATORY_HEDGE_MIN_LOSS_PCT', -0.25))
        if real_gain_pct <= oh_min_loss and not hedge_already_active:
            # WT cascade — mirrors ez_manage.py:14357-14401 exactly
            oh_use = {
                '1m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1M', False)),
                '3m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_3M', True)),
                '15m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_15M', False)),
                '1h': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1H', True)),
            }
            oh_wt_against = 0
            oh_tfs_enabled = 0
            oh_3m_against = False
            oh_15m_against = False
            oh_1h_against = False
            for tf in ('1m', '3m', '15m', '1h'):
                if not oh_use[tf]:
                    continue
                oh_tfs_enabled += 1
                ag = _wt_against_scalar(indicators, tf, is_long)
                oh_wt_against += int(ag)
                if tf == '3m':
                    oh_3m_against = bool(ag)
                elif tf == '15m':
                    oh_15m_against = bool(ag)
                elif tf == '1h':
                    oh_1h_against = bool(ag)
            # USER 2026-05-12 (ez_manage.py:14378-14383): compute 15m independently
            # even if OBLIGATORY_HEDGE_WT_USE_15M=False, so the (15m OR 1h)
            # cascade works regardless of loop config.
            if not oh_use['15m']:
                oh_15m_against = _wt_against_scalar(indicators, '15m', is_long)

            oh_req = int(getattr(config, 'OBLIGATORY_HEDGE_WT_TFS_REQUIRED', 2))
            req_3m_15m_or_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H', True))
            req_3m_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H', False))
            use_3m_alone = bool(getattr(config, 'HEDGE_TRIGGER_USE_WT_3M_ALONE', True))

            # Cascade precedence: matches ez_manage.py:14390-14401 if/elif chain
            if req_3m_15m_or_1h:
                oh_user_trigger = oh_3m_against and (oh_15m_against or oh_1h_against)
                trigger_label = "3m_AND_(15m_OR_1h)"
            elif req_3m_1h:
                oh_user_trigger = oh_3m_against and oh_1h_against
                trigger_label = "3m_AND_1h"
            elif use_3m_alone:
                oh_user_trigger = oh_3m_against
                trigger_label = "3m_alone"
            else:
                oh_user_trigger = oh_15m_against or (oh_3m_against and oh_1h_against)
                trigger_label = "15m_OR_(3m_AND_1h)"

            # Live ez_manage:14402 — fire on count OR cascade match
            if oh_tfs_enabled > 0 and (oh_wt_against >= oh_req or oh_user_trigger):
                hedge_should_fire = True
                oh_pct = float(getattr(config, 'OBLIGATORY_HEDGE_PCT', 1.0))
                hedge_qty = abs(float(positionAmt)) * oh_pct
                hedge_outcome = "success" if hedge_attempt_succeeded else "failed"
            else:
                hedge_outcome = "wt_not_against"
        else:
            hedge_outcome = "gain_too_small" if real_gain_pct > oh_min_loss else (
                "already_covered" if hedge_already_active else "disabled")
    else:
        hedge_outcome = "disabled"

    # Decision — live ez_manage:14425-14436
    fallback_enabled = bool(getattr(config, 'HEDGE_FAILED_FALLBACK_CLOSE_ENABLED', True))
    if hedge_outcome == "failed" and fallback_enabled:
        # Live mutates reason → HEDGE_FAILED_FALLBACK_CLOSE_g{gain}%_orig:{prev}
        new_reason = f"HEDGE_FAILED_FALLBACK_CLOSE_g{real_gain_pct:.2f}%_orig:{(reason or '')[:60]}"
        return (NOLOSS_ACTION_CLOSE_HEDGE_FAILED, new_reason, hedge_should_fire, hedge_qty)

    # All other outcomes block the reduce — HOLD.
    return (NOLOSS_ACTION_HOLD, reason, hedge_should_fire, hedge_qty)


def evaluate_noloss_gate_vec(
    npz: Dict[str, np.ndarray],
    is_long: bool,
    real_gain_pct: np.ndarray,
    positionAmt: np.ndarray,
    mark_price: np.ndarray,
    reason: str,
    config: Any,
    *,
    is_hedge: bool = False,
    is_reduce: bool = True,
    hedge_already_active=None,  # bool array or scalar
    hedge_attempt_succeeded=None,  # bool array or scalar
) -> Dict[str, np.ndarray]:
    """Vectorized UNIVERSAL_NOLOSS_GATE + OBLIGATORY_HEDGE evaluator.

    Inputs are per-bar arrays where indicated; `reason`, `is_hedge`, `is_reduce`,
    `is_long` are scalars (the reduce attempt has a single reason string —
    backtest engines that re-evaluate per bar can supply the same arrays with a
    fresh `reason` per call).

    Returns dict with per-bar arrays:
        action_id        — np.int8 (0=HOLD, 1=ALLOW_REDUCE, 2=CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK)
        hedge_fire       — bool per bar
        hedge_qty        — float32 per bar (0.0 when no hedge)
        oh_wt_against    — int8 count of TFs against (diagnostic)
        oh_user_trigger  — bool per bar (cascade matched)
        comm_loss        — bool per bar (real_gain_pct < commission buffer)
    Plus scalar metadata:
        reason_out       — string (only differs from `reason` if action_id==2 at any bar;
                           live mutates per fire so caller should regenerate per bar if needed)

    NOTE: For action_id==2 (HEDGE_FAILED close), the live code emits a reason string
    containing the gain at that bar. The vec version returns the canonical action label;
    callers that need per-bar reason strings should iterate firing bars and call
    evaluate_noloss_gate_core on those — keeps the hot loop fast.
    """
    # Pick array length from real_gain_pct as the canonical "n"
    real_gain_pct = np.asarray(real_gain_pct, dtype=np.float32)
    n = len(real_gain_pct)

    positionAmt = np.asarray(positionAmt, dtype=np.float32)
    if positionAmt.shape == ():
        positionAmt = np.full(n, float(positionAmt), dtype=np.float32)

    def f(name: str, default: float = 0.0) -> np.ndarray:
        a = npz.get(name)
        if a is None:
            return np.full(n, default, dtype=np.float32)
        a = np.asarray(a, dtype=np.float32)
        if len(a) != n:
            out_a = np.full(n, default, dtype=np.float32)
            m = min(len(a), n)
            out_a[:m] = a[:m]
            a = out_a
        return np.nan_to_num(a, nan=default)

    # Defaults: hedge_already_active=False, hedge_attempt_succeeded=True (both per-bar)
    if hedge_already_active is None:
        haa = np.zeros(n, dtype=bool)
    else:
        haa = np.asarray(hedge_already_active, dtype=bool)
        if haa.shape == ():
            haa = np.full(n, bool(hedge_already_active), dtype=bool)
    if hedge_attempt_succeeded is None:
        hsucc = np.ones(n, dtype=bool)
    else:
        hsucc = np.asarray(hedge_attempt_succeeded, dtype=bool)
        if hsucc.shape == ():
            hsucc = np.full(n, bool(hedge_attempt_succeeded), dtype=bool)

    # ─── Quick exits for scalar conditions ───────────────────────────────────────
    ung_active = bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE', True))
    reason_upper = (reason or "").upper()

    out_action = np.zeros(n, dtype=np.int8)  # 0=HOLD default; we'll overwrite
    out_hedge_fire = np.zeros(n, dtype=bool)
    out_hedge_qty = np.zeros(n, dtype=np.float32)
    out_oh_wt_against = np.zeros(n, dtype=np.int8)
    out_oh_user_trigger = np.zeros(n, dtype=bool)

    if not ung_active or not is_reduce:
        out_action[:] = 1  # ALLOW_REDUCE everywhere
        return {
            'action_id': out_action,
            'hedge_fire': out_hedge_fire,
            'hedge_qty': out_hedge_qty,
            'oh_wt_against': out_oh_wt_against,
            'oh_user_trigger': out_oh_user_trigger,
            'comm_loss': np.zeros(n, dtype=bool),
        }

    if is_hedge or 'LIQUIDATION' in reason_upper:
        out_action[:] = 1
        return {
            'action_id': out_action,
            'hedge_fire': out_hedge_fire,
            'hedge_qty': out_hedge_qty,
            'oh_wt_against': out_oh_wt_against,
            'oh_user_trigger': out_oh_user_trigger,
            'comm_loss': np.zeros(n, dtype=bool),
        }

    if bool(getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL', True)):
        bypass_list = getattr(config, 'UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS', []) or []
        if _bypass_reason_matches(reason_upper, bypass_list) is not None:
            out_action[:] = 1
            return {
                'action_id': out_action,
                'hedge_fire': out_hedge_fire,
                'hedge_qty': out_hedge_qty,
                'oh_wt_against': out_oh_wt_against,
                'oh_user_trigger': out_oh_user_trigger,
                'comm_loss': np.zeros(n, dtype=bool),
            }

    if 'STRUCTURAL_RANGE_SHIFT' in reason_upper:
        out_action[:] = 1
        return {
            'action_id': out_action,
            'hedge_fire': out_hedge_fire,
            'hedge_qty': out_hedge_qty,
            'oh_wt_against': out_oh_wt_against,
            'oh_user_trigger': out_oh_user_trigger,
            'comm_loss': np.zeros(n, dtype=bool),
        }

    # ─── Per-bar evaluation ──────────────────────────────────────────────────────
    comm_buf = float(getattr(config, 'COMMISSION_BUFFER_PCT', 0.10))
    in_loss = real_gain_pct < comm_buf  # below commission buffer = real loss territory
    # Bars NOT in real loss → ALLOW_REDUCE
    out_action[~in_loss] = 1

    # For in_loss bars, evaluate OBLIGATORY_HEDGE cascade
    if not bool(getattr(config, 'OBLIGATORY_HEDGE_ENABLED', True)):
        # OH disabled — all in_loss bars stay HOLD (action=0), already-default
        return {
            'action_id': out_action,
            'hedge_fire': out_hedge_fire,
            'hedge_qty': out_hedge_qty,
            'oh_wt_against': out_oh_wt_against,
            'oh_user_trigger': out_oh_user_trigger,
            'comm_loss': in_loss,
        }

    oh_min_loss = float(getattr(config, 'OBLIGATORY_HEDGE_MIN_LOSS_PCT', -0.25))
    oh_eligible = in_loss & (real_gain_pct <= oh_min_loss) & (~haa)

    # Build WT-against bool arrays
    oh_use = {
        '1m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1M', False)),
        '3m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_3M', True)),
        '15m': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_15M', False)),
        '1h': bool(getattr(config, 'OBLIGATORY_HEDGE_WT_USE_1H', True)),
    }

    def _against_vec(tf: str) -> np.ndarray:
        w1 = f(f'wt1_{tf}', 0.0)
        w2 = f(f'wt2_{tf}', 0.0)
        return (w1 < w2) if is_long else (w1 > w2)

    oh_wt_against = np.zeros(n, dtype=np.int8)
    oh_tfs_enabled = 0
    oh_3m_against = np.zeros(n, dtype=bool)
    oh_15m_against = np.zeros(n, dtype=bool)
    oh_1h_against = np.zeros(n, dtype=bool)
    for tf in ('1m', '3m', '15m', '1h'):
        ag = _against_vec(tf) if oh_use[tf] else None
        if oh_use[tf]:
            oh_tfs_enabled += 1
            oh_wt_against = oh_wt_against + ag.astype(np.int8)
        if tf == '3m':
            oh_3m_against = ag if ag is not None else _against_vec('3m') if oh_use['3m'] else np.zeros(n, dtype=bool)
        elif tf == '15m':
            oh_15m_against = ag if ag is not None else np.zeros(n, dtype=bool)
        elif tf == '1h':
            oh_1h_against = ag if ag is not None else np.zeros(n, dtype=bool)
    # Live: compute 15m independently when OBLIGATORY_HEDGE_WT_USE_15M=False (ez_manage:14380-14383)
    if not oh_use['15m']:
        oh_15m_against = _against_vec('15m')
    # Live: 3m & 1h are read into oh_3m/1h_against ONLY when use flag True; otherwise False.
    # If oh_use['3m']=False, the cascade's '3m alone' / '3m AND (...)' branches see False.
    if not oh_use['3m']:
        oh_3m_against = np.zeros(n, dtype=bool)
    if not oh_use['1h']:
        oh_1h_against = np.zeros(n, dtype=bool)

    oh_req = int(getattr(config, 'OBLIGATORY_HEDGE_WT_TFS_REQUIRED', 2))
    req_3m_15m_or_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H', True))
    req_3m_1h = bool(getattr(config, 'HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H', False))
    use_3m_alone = bool(getattr(config, 'HEDGE_TRIGGER_USE_WT_3M_ALONE', True))

    if req_3m_15m_or_1h:
        oh_user_trigger = oh_3m_against & (oh_15m_against | oh_1h_against)
    elif req_3m_1h:
        oh_user_trigger = oh_3m_against & oh_1h_against
    elif use_3m_alone:
        oh_user_trigger = oh_3m_against
    else:
        oh_user_trigger = oh_15m_against | (oh_3m_against & oh_1h_against)

    # Fire condition: count OR cascade (ez_manage:14402)
    cascade_fire = (oh_wt_against >= oh_req) | oh_user_trigger
    hedge_fire_mask = oh_eligible & (oh_tfs_enabled > 0) & cascade_fire

    out_hedge_fire = hedge_fire_mask
    out_oh_wt_against = oh_wt_against
    out_oh_user_trigger = oh_user_trigger

    oh_pct = float(getattr(config, 'OBLIGATORY_HEDGE_PCT', 1.0))
    out_hedge_qty = np.where(hedge_fire_mask, np.abs(positionAmt) * oh_pct, 0.0).astype(np.float32)

    # Action assignment for in_loss bars:
    # - hedge fires + attempt fails + fallback enabled → CLOSE_AT_LOSS_VIA_HEDGE_FALLBACK (2)
    # - else HOLD (0)
    fallback_enabled = bool(getattr(config, 'HEDGE_FAILED_FALLBACK_CLOSE_ENABLED', True))
    if fallback_enabled:
        close_mask = hedge_fire_mask & (~hsucc)
        out_action[close_mask] = 2

    return {
        'action_id': out_action,
        'hedge_fire': out_hedge_fire,
        'hedge_qty': out_hedge_qty,
        'oh_wt_against': out_oh_wt_against,
        'oh_user_trigger': out_oh_user_trigger,
        'comm_loss': in_loss,
    }


# ════════════════════════════════════════════════════════════════════════════════
# EMERGENCY_BRAKE — re-exports from vec_paths/emergency_brake.py
# Mirrors ez_manage.py:14114-14166 (per-hour rate limiter).
# ════════════════════════════════════════════════════════════════════════════════
try:
    from vec_paths.emergency_brake import (
        evaluate_emergency_brake_core,
        evaluate_emergency_brake_vec,
        BrakeLookup,
        load_decision_events,
        REASON_OK,
        REASON_DISABLED,
        REASON_MAX_ENTRIES,
        REASON_MAX_TRADES,
        REASON_SYMBOL_CHURN,
        CODE_OK,
        CODE_DISABLED,
        CODE_MAX_ENTRIES,
        CODE_MAX_TRADES,
        CODE_SYMBOL_CHURN,
        REASON_BY_CODE,
    )
except ImportError:
    pass


# ════════════════════════════════════════════════════════════════════════════════
# AUGMENT ELIGIBILITY — LOSING_POSITION_HARD_BLOCK + DUP_GUARD + PULLBACK_AUGMENT
# Live scalar site: ez_manage.py:13704-13787 (LOSING_POSITION_HARD_BLOCK)
#                   ez_manage.py:11014-11038 (DUP_GUARD_GAIN_GATE)
#                   ez_manage.py:14062-14080 (HARD_AUGMENT_LOCK)
# User mandate: "NEVER AUGMENTING LOSING POSITIONS OR POSITIONS NOT IN A DECENT GAIN!"
# AUGMENT requires real_gain > 0.5 * MIN_GAIN_TO_BUY_AGGRESSIVELY (default = 1.5%).
# REENTRY (positionAmt==0) and HEDGE bypass these gates.
# AUGMENT on losing position → ALWAYS REFUSED.
# ════════════════════════════════════════════════════════════════════════════════

# sub_gate_fired enum (string literals match REPORT contract)
SUB_GATE_NONE_ALLOWED = "NONE_ALLOWED"
SUB_GATE_HARD_AUGMENT_LOCK = "HARD_AUGMENT_LOCK"
SUB_GATE_DUP_GUARD_GAIN_GATE = "DUP_GUARD_GAIN_GATE"
SUB_GATE_MIN_GAIN_FLOOR = "MIN_GAIN_FLOOR"
SUB_GATE_ENTRY_PRICE_GATE = "ENTRY_PRICE_GATE"
SUB_GATE_PULLBACK_AUGMENT_LEVEL = "PULLBACK_AUGMENT_LEVEL"
SUB_GATE_AUGMENT_LEVEL = "AUGMENT_LEVEL"

# enum mapping for vec output (sub_gate as int8 for compactness)
_SUB_GATE_TO_INT = {
    SUB_GATE_NONE_ALLOWED: 0,
    SUB_GATE_HARD_AUGMENT_LOCK: 1,
    SUB_GATE_DUP_GUARD_GAIN_GATE: 2,
    SUB_GATE_MIN_GAIN_FLOOR: 3,
    SUB_GATE_ENTRY_PRICE_GATE: 4,
    SUB_GATE_PULLBACK_AUGMENT_LEVEL: 5,
    SUB_GATE_AUGMENT_LEVEL: 6,
}
_INT_TO_SUB_GATE = {v: k for k, v in _SUB_GATE_TO_INT.items()}

# Default constants (mirror ez_manage.py module-level)
_DEFAULT_AUGMENT_LOCK_MIN_SECONDS = 900  # ez_manage.py:4238


def evaluate_augment_eligibility_core(
    action: str,
    position_amt: float,
    real_gain: float,
    entry_price: float,
    mark_price: float,
    proposed_qty: float,
    config: Any,
    last_augmentation_time: Optional[float] = None,
    augmented_count: float = 0.0,
    initial_quantity: float = 0.0,
    max_gain: float = 0.0,
    now_ts: Optional[float] = None,
    is_hedge: bool = False,
    is_long: bool = True,
    ppl_fired: bool = False,
    min_qty: float = 0.0001,
    reason: str = "",
) -> Tuple[bool, str, Optional[float], str]:
    """Scalar augment-eligibility gate. Byte-equivalent to ez_manage.execute_now
    LOSING_POSITION_HARD_BLOCK + DUP_GUARD_GAIN_GATE + HARD_AUGMENT_LOCK +
    AUGMENT_LEVEL + ENTRY_PRICE_GATE + PULLBACK_AUGMENT paths.

    Returns (allowed, reason_str, augment_qty_or_None, sub_gate_fired).

    User contract:
      • REENTRY/OPEN with position_amt==0 → bypass (positionAmt==0 means flat)
      • is_hedge=True → bypass (opposite-side open, not augment)
      • AUGMENT on losing position (gain<0) → always REFUSED
      • AUGMENT requires real_gain > 0.5 * MIN_GAIN_TO_BUY_AGGRESSIVELY
      • PULLBACK_AUGMENT_ENABLED → allows augment if peak hit MIN_GAIN/required
        AND gain ≥ 0.5*MIN_GAIN AND (max_gain-gain) ≥ PULLBACK_AUGMENT_REVERSAL_MIN
    """
    act_up = (action or '').upper()
    # ─── Action classification (mirrors ez_manage _lpb_is_increase + is_augment) ───
    is_increase = (
        ('OPEN' in act_up or 'AUGMENT' in act_up or 'REENTRY' in act_up
         or 'ENTRY' in act_up or 'HEDGE_OPEN' in act_up or 'HEDGE_AUGMENT' in act_up
         or 'REVERSE' in act_up)
        and 'CLOSE' not in act_up and 'REDUCE' not in act_up and 'KILL' not in act_up
    )
    if not is_increase:
        return True, "NOT_AN_INCREASE", proposed_qty, SUB_GATE_NONE_ALLOWED
    # ─── Bypass 1: is_hedge=True opens opposite-side key, not augmenting ───
    if is_hedge:
        return True, "HEDGE_BYPASS", proposed_qty, SUB_GATE_NONE_ALLOWED
    # ─── Bypass 2: REENTRY / OPEN on flat position (positionAmt==0) ───
    is_aug_only = 'AUGMENT' in act_up and 'REENTRY' not in act_up
    if abs(position_amt) <= min_qty:
        return True, "FLAT_POS_REENTRY_OR_OPEN", proposed_qty, SUB_GATE_NONE_ALLOWED
    # ─── Bypass 3: WT_3M_FORCE_OPEN reason (per 2026-05-10 mandate) ───
    reason_up = (reason or '').upper()
    wt3m_bypass = (
        'WT_3M_FORCE_OPEN' in reason_up
        and bool(getattr(config, 'WT_3M_FORCE_OPEN_BYPASS_GATES', True))
    )
    # ─── PPL doubled gain (mirrors ez_reentry.effective_gain_pct) ───
    eff_gain = real_gain
    if ppl_fired and bool(getattr(config, 'EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED', True)):
        frac = float(getattr(config, 'PARTIAL_PROFIT_LOCK_FRAC', 0.5))
        if 0.0 < frac < 1.0:
            eff_gain = real_gain / (1.0 - frac)
    # ─── Config knobs (snapshot once for vectorization parity) ───
    min_gain = float(getattr(config, 'MIN_GAIN', getattr(config, 'MIN_GAIN_TO_BUY_AGGRESSIVELY', 3.0)))
    dup_use_gain = bool(getattr(config, 'DUP_GUARD_USE_GAIN_GATE', True))
    dup_mult = float(getattr(config, 'DUP_GUARD_GAIN_MULTIPLIER', 0.5))
    pullback_enabled = bool(getattr(config, 'PULLBACK_AUGMENT_ENABLED', True))
    pullback_rev_min = float(getattr(config, 'PULLBACK_AUGMENT_REVERSAL_MIN', 1.0))
    aug_lock_secs = float(getattr(config, 'HARD_AUGMENT_LOCK_SECONDS', _DEFAULT_AUGMENT_LOCK_MIN_SECONDS))
    pb_floor = 0.5 * min_gain
    dup_thr = min_gain * dup_mult
    # ─── 1) DUP_GUARD_GAIN_GATE — ez_manage.py:11019 (runs first in live) ───
    if is_aug_only and dup_use_gain and not wt3m_bypass:
        if eff_gain <= dup_thr:
            return False, f"BLOCKED_DUP_GUARD_GAIN_{eff_gain:.2f}pct_lt_{dup_thr:.2f}pct", None, SUB_GATE_DUP_GUARD_GAIN_GATE
    # ─── 2) HARD_AUGMENT_LOCK (ez_manage.py:14062) ───
    if last_augmentation_time is not None and aug_lock_secs > 0 and not wt3m_bypass:
        now = now_ts if now_ts is not None else 0.0
        since_aug = now - float(last_augmentation_time)
        gain_ok = eff_gain >= min_gain
        if since_aug < aug_lock_secs and not gain_ok and is_aug_only:
            return False, f"BLOCKED_HARD_AUGMENT_LOCK_{since_aug:.0f}s", None, SUB_GATE_HARD_AUGMENT_LOCK
    # ─── 3) LOSING_POSITION_HARD_BLOCK — ground truth (ez_manage.py:13740) ───
    if eff_gain < min_gain:
        # PULLBACK_AUGMENT carve-out: real winner pulled back
        pb_ok = (
            is_aug_only
            and pullback_enabled
            and max_gain >= min_gain
            and eff_gain >= pb_floor
            and (max_gain - eff_gain) >= pullback_rev_min
        )
        if pb_ok:
            # Fall through to AUGMENT_LEVEL and ENTRY_PRICE_GATE
            pass
        elif not wt3m_bypass:
            return False, f"BLOCKED_LOSING_POSITION_GAIN{real_gain:.2f}_EFF{eff_gain:.2f}_LT_MIN{min_gain:.2f}", None, SUB_GATE_MIN_GAIN_FLOOR
    # ─── 4) AUGMENT_LEVEL gate — N-th augment requires N × MIN_GAIN ───
    if is_aug_only and initial_quantity > 0:
        aug_n = max(1, round(abs(position_amt) / initial_quantity))
        req = aug_n * min_gain
        if eff_gain < req:
            # PULLBACK_AUGMENT_LEVEL carve-out
            pb_level_ok = (
                pullback_enabled
                and max_gain >= req
                and eff_gain >= pb_floor
                and (max_gain - eff_gain) >= pullback_rev_min
            )
            if pb_level_ok:
                # Allowed via PULLBACK_AUGMENT_LEVEL; fall through to ENTRY_PRICE_GATE
                sub_gate_pulled = SUB_GATE_PULLBACK_AUGMENT_LEVEL
            elif not wt3m_bypass:
                return False, f"BLOCKED_AUGMENT_LEVEL{aug_n}_need{req:.1f}_got{eff_gain:.2f}", None, SUB_GATE_AUGMENT_LEVEL
    # ─── 5) ENTRY_PRICE_GATE — weighted-entry must keep post-augment gain ≥ 0 ───
    if is_aug_only and entry_price > 0 and proposed_qty > 0 and mark_price > 0:
        amt = abs(position_amt)
        new_entry = (amt * entry_price + proposed_qty * mark_price) / (amt + proposed_qty)
        if is_long:
            post_gain = (mark_price - new_entry) / new_entry * 100.0
        else:
            post_gain = (new_entry - mark_price) / new_entry * 100.0
        if post_gain < 0.0 and not wt3m_bypass:
            return False, f"BLOCKED_ENTRY_PRICE_GATE_post_gain{post_gain:.2f}", None, SUB_GATE_ENTRY_PRICE_GATE
    # ─── All gates passed ───
    return True, "AUGMENT_ALLOWED", proposed_qty, SUB_GATE_NONE_ALLOWED


def evaluate_augment_eligibility_vec(
    action: str,
    position_amt_arr: np.ndarray,
    real_gain_arr: np.ndarray,
    entry_price_arr: np.ndarray,
    mark_price_arr: np.ndarray,
    proposed_qty_arr: np.ndarray,
    config: Any,
    last_augmentation_time_arr: Optional[np.ndarray] = None,
    initial_quantity_arr: Optional[np.ndarray] = None,
    max_gain_arr: Optional[np.ndarray] = None,
    now_ts_arr: Optional[np.ndarray] = None,
    is_hedge_arr: Optional[np.ndarray] = None,
    is_long: bool = True,
    ppl_fired_arr: Optional[np.ndarray] = None,
    min_qty: float = 0.0001,
    reason_arr: Optional[np.ndarray] = None,
) -> Dict[str, np.ndarray]:
    """Vectorized augment-eligibility gate — identical decisions to _core per bar.

    Returns dict:
        allowed (bool[n]):      True → entry proceeds
        augment_qty (f32[n]):   per-bar qty (proposed_qty when allowed, else 0)
        sub_gate (int8[n]):     enum of which gate fired (see SUB_GATE_* constants
                                mapped to ints via _SUB_GATE_TO_INT)
    """
    n = len(real_gain_arr)
    pa = np.asarray(position_amt_arr, dtype=np.float64)
    rg = np.asarray(real_gain_arr, dtype=np.float64)
    ep = np.asarray(entry_price_arr, dtype=np.float64)
    mp = np.asarray(mark_price_arr, dtype=np.float64)
    pq = np.asarray(proposed_qty_arr, dtype=np.float64)
    out_allowed = np.ones(n, dtype=bool)
    out_qty = pq.astype(np.float32).copy()
    out_sub = np.full(n, _SUB_GATE_TO_INT[SUB_GATE_NONE_ALLOWED], dtype=np.int8)
    # ─── Action classification ───
    act_up = (action or '').upper()
    is_increase = (
        ('OPEN' in act_up or 'AUGMENT' in act_up or 'REENTRY' in act_up
         or 'ENTRY' in act_up or 'HEDGE_OPEN' in act_up or 'HEDGE_AUGMENT' in act_up
         or 'REVERSE' in act_up)
        and 'CLOSE' not in act_up and 'REDUCE' not in act_up and 'KILL' not in act_up
    )
    if not is_increase:
        return {'allowed': out_allowed, 'augment_qty': out_qty, 'sub_gate': out_sub}
    is_aug_only = 'AUGMENT' in act_up and 'REENTRY' not in act_up
    # ─── Bypass masks ───
    hedge_mask = (np.asarray(is_hedge_arr, dtype=bool) if is_hedge_arr is not None else np.zeros(n, dtype=bool))
    flat_mask = (np.abs(pa) <= min_qty)
    # WT_3M_FORCE_OPEN reason bypass (per-bar)
    if reason_arr is not None and bool(getattr(config, 'WT_3M_FORCE_OPEN_BYPASS_GATES', True)):
        wt3m_bypass = np.array([('WT_3M_FORCE_OPEN' in (str(r) or '').upper()) for r in reason_arr], dtype=bool)
    else:
        wt3m_bypass = np.zeros(n, dtype=bool)
    bypass_mask = hedge_mask | flat_mask
    eligible = ~bypass_mask
    # ─── PPL doubled gain ───
    eff_gain = rg.copy()
    if ppl_fired_arr is not None and bool(getattr(config, 'EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED', True)):
        frac = float(getattr(config, 'PARTIAL_PROFIT_LOCK_FRAC', 0.5))
        if 0.0 < frac < 1.0:
            ppl_mask = np.asarray(ppl_fired_arr, dtype=bool)
            eff_gain = np.where(ppl_mask, rg / (1.0 - frac), rg)
    # ─── Config snapshot ───
    min_gain = float(getattr(config, 'MIN_GAIN', getattr(config, 'MIN_GAIN_TO_BUY_AGGRESSIVELY', 3.0)))
    dup_use_gain = bool(getattr(config, 'DUP_GUARD_USE_GAIN_GATE', True))
    dup_mult = float(getattr(config, 'DUP_GUARD_GAIN_MULTIPLIER', 0.5))
    pullback_enabled = bool(getattr(config, 'PULLBACK_AUGMENT_ENABLED', True))
    pullback_rev_min = float(getattr(config, 'PULLBACK_AUGMENT_REVERSAL_MIN', 1.0))
    aug_lock_secs = float(getattr(config, 'HARD_AUGMENT_LOCK_SECONDS', _DEFAULT_AUGMENT_LOCK_MIN_SECONDS))
    pb_floor = 0.5 * min_gain
    dup_thr = min_gain * dup_mult
    # ─── 1) DUP_GUARD_GAIN_GATE (runs first in live, ez_manage.py:11019) ───
    if is_aug_only and dup_use_gain:
        dup_fire = (eff_gain <= dup_thr) & eligible & (~wt3m_bypass)
        out_allowed = np.where(dup_fire, False, out_allowed)
        out_sub = np.where(dup_fire, np.int8(_SUB_GATE_TO_INT[SUB_GATE_DUP_GUARD_GAIN_GATE]), out_sub)
        out_qty = np.where(dup_fire, np.float32(0.0), out_qty)
        eligible = eligible & (~dup_fire)
    # ─── 2) HARD_AUGMENT_LOCK ───
    if last_augmentation_time_arr is not None and now_ts_arr is not None and aug_lock_secs > 0 and is_aug_only:
        la = np.asarray(last_augmentation_time_arr, dtype=np.float64)
        nt = np.asarray(now_ts_arr, dtype=np.float64)
        since_aug = nt - la
        gain_ok = eff_gain >= min_gain
        hard_lock_mask = (since_aug < aug_lock_secs) & (~gain_ok) & eligible & (~wt3m_bypass) & (la > 0)
        out_allowed = np.where(hard_lock_mask, False, out_allowed)
        out_sub = np.where(hard_lock_mask, np.int8(_SUB_GATE_TO_INT[SUB_GATE_HARD_AUGMENT_LOCK]), out_sub)
        out_qty = np.where(hard_lock_mask, np.float32(0.0), out_qty)
        eligible = eligible & (~hard_lock_mask)
    # ─── 3) LOSING_POSITION_HARD_BLOCK ───
    losing_mask_raw = eff_gain < min_gain
    if max_gain_arr is not None:
        mg = np.asarray(max_gain_arr, dtype=np.float64)
        pb_ok = (
            is_aug_only
            & pullback_enabled
            & (mg >= min_gain)
            & (eff_gain >= pb_floor)
            & ((mg - eff_gain) >= pullback_rev_min)
        )
    else:
        pb_ok = np.zeros(n, dtype=bool)
    min_floor_fire = losing_mask_raw & (~pb_ok) & eligible & (~wt3m_bypass)
    out_allowed = np.where(min_floor_fire, False, out_allowed)
    out_sub = np.where(min_floor_fire, np.int8(_SUB_GATE_TO_INT[SUB_GATE_MIN_GAIN_FLOOR]), out_sub)
    out_qty = np.where(min_floor_fire, np.float32(0.0), out_qty)
    eligible = eligible & (~min_floor_fire)
    # ─── 4) AUGMENT_LEVEL ───
    if is_aug_only and initial_quantity_arr is not None:
        iq = np.asarray(initial_quantity_arr, dtype=np.float64)
        safe_iq = np.where(iq > 0, iq, 1.0)
        aug_n = np.maximum(1.0, np.round(np.abs(pa) / safe_iq))
        aug_n = np.where(iq > 0, aug_n, 1.0)
        req = aug_n * min_gain
        under_req = (eff_gain < req) & (iq > 0)
        if max_gain_arr is not None:
            mg = np.asarray(max_gain_arr, dtype=np.float64)
            pb_level_ok = (
                pullback_enabled
                & (mg >= req)
                & (eff_gain >= pb_floor)
                & ((mg - eff_gain) >= pullback_rev_min)
            )
        else:
            pb_level_ok = np.zeros(n, dtype=bool)
        level_fire = under_req & (~pb_level_ok) & eligible & (~wt3m_bypass)
        out_allowed = np.where(level_fire, False, out_allowed)
        out_sub = np.where(level_fire, np.int8(_SUB_GATE_TO_INT[SUB_GATE_AUGMENT_LEVEL]), out_sub)
        out_qty = np.where(level_fire, np.float32(0.0), out_qty)
        eligible = eligible & (~level_fire)
    # ─── 5) ENTRY_PRICE_GATE ───
    if is_aug_only:
        amt = np.abs(pa)
        denom = amt + pq
        safe_denom = np.where(denom > 0, denom, 1.0)
        new_entry = (amt * ep + pq * mp) / safe_denom
        safe_new_entry = np.where(new_entry > 0, new_entry, 1.0)
        if is_long:
            post_gain = (mp - safe_new_entry) / safe_new_entry * 100.0
        else:
            post_gain = (safe_new_entry - mp) / safe_new_entry * 100.0
        ep_fire = (
            (ep > 0) & (pq > 0) & (mp > 0) & (post_gain < 0.0)
            & eligible & (~wt3m_bypass)
        )
        out_allowed = np.where(ep_fire, False, out_allowed)
        out_sub = np.where(ep_fire, np.int8(_SUB_GATE_TO_INT[SUB_GATE_ENTRY_PRICE_GATE]), out_sub)
        out_qty = np.where(ep_fire, np.float32(0.0), out_qty)
    return {'allowed': out_allowed, 'augment_qty': out_qty, 'sub_gate': out_sub}
