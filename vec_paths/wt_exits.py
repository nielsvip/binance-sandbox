"""vec_paths/wt_exits.py — 2026-05-26.

Vectorize WT_DIV / WT_ACCEL / WT_MOMENTUM / WT_EXHAUST exit families so
`v8_vec_sweep` matches the live ez_manage / ez_positions_quick byte-for-byte.

Live source map
---------------
  WT_DIV_EXIT       — ez_positions_quick.py:3461-3464   (WT_EXHAUST+DIV_EXIT)
                      Fires when wt_momentum_state_1h == EXHAUST_<dir>
                      AND wt_divergence_1h is opposite-bias (BEAR for LONG, BULL for SHORT).
                      Also see MI_DIV_EXIT (line 3527-3530) — wt_divergence_1h/4h votes.
  WT_ACCEL_EXIT     — derived from wt_acceleration_<tf> per backtest_v8_precompute.py:689
                      (acceleration = diff(wt1 - wt2)). Live MI_VELOCITY uses velocity,
                      but the WT_ACCEL_EXIT knob in autonomous-iters/exit_sweep_targeted
                      maps to the acceleration variant: N TFs with accel against position.
  WT_MOMENTUM_EXIT  — wt_momentum_state_<tf> int8 == EXHAUST_<opposite> for >=
                      WT_MOMENTUM_EXIT_THRESHOLD timeframes. Mirrors MI_EXHAUST_EXIT vote
                      (ez_positions_quick.py:3523-3526) without the gain/age coupling.
  WT_EXHAUST_EXIT   — already lives in position_evaluator.evaluate_exit_gates_vec, but
                      the live rule is 4h AND (1h OR 15m). BTC_TECH_EXIT_WT_MIN_TFS lets
                      callers require N (>=2) momentum TFs total. This module emits a
                      configurable BTC_TECH_EXIT_WT_MIN_TFS-aware mask.

Encoding (from backtest_v8_precompute.py)
  wt_momentum_state_<tf>: int8;  1=EXHAUST_UP, -1=EXHAUST_DOWN, 2=IMPULSE_UP, -2=IMPULSE_DOWN
  wt_divergence_<tf>:     int8;  1=BULL, -1=BEAR, 0=none
  wt_acceleration_<tf>:   float32 = diff(wt1-wt2)

All functions return a per-bar bool mask plus a small dict of arrays. Wiring in
v8_vec_sweep is purely a per-bar `if mask[i]` check — same pattern as the
existing wt_4h_vel_full and wt_exhaust gates inside the simulate-loop.

Knobs read (via getattr — vec_sweep's _apply_per_task_overrides honours dynamic attrs):
  WT_DIV_EXIT_ENABLED          (bool, default False)
  WT_DIV_EXIT_TF               (str,  default '1h')
  WT_DIV_EXIT_REQUIRE_EXHAUST  (bool, default True — pair with momentum state)
  WT_DIV_EXIT_MOM_TF           (str,  default '1h')

  WT_ACCEL_EXIT_ENABLED        (bool, default False)
  WT_ACCEL_EXIT_MIN_TFS        (int,  default 2)
  WT_ACCEL_EXIT_TFS            (list, default ['15m','1h','4h'])
  WT_ACCEL_EXIT_LONG_THR       (float, default -0.5  — accel<thr ⇒ against LONG)
  WT_ACCEL_EXIT_SHORT_THR      (float, default 0.5   — accel>thr ⇒ against SHORT)

  WT_MOMENTUM_EXIT_ENABLED     (bool, default False)
  WT_MOMENTUM_EXIT_THRESHOLD   (int,  default 2  — min TFs in EXHAUST_<opposite>)
  WT_MOMENTUM_EXIT_TFS         (list, default ['15m','1h','4h'])

  WT_EXHAUST_EXIT_MIN_TFS      (int,  default 2  — total momentum-state alignment)
  WT_EXHAUST_EXIT_TFS          (list, default ['15m','1h','4h'])
  BTC_TECH_EXIT_WT_MIN_TFS     (int,  default 3  — caller may use the wt_exhaust_btc mask
                                                   for BTC-only branches.)
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


# Matches position_evaluator._MOM_EXHAUST_*
_MOM_EXHAUST_UP = 1
_MOM_EXHAUST_DOWN = -1
_DIV_BULL = 1
_DIV_BEAR = -1


def _get_arr(npz: Dict[str, np.ndarray], key: str, n: int, default: float = 0.0,
             dtype=np.float32) -> np.ndarray:
    a = npz.get(key)
    if a is None:
        return np.full(n, default, dtype=dtype)
    a = np.asarray(a)
    if a.ndim == 0:
        # A scalar placeholder is not a time series.  Preserve the documented
        # neutral fallback rather than letting ``len`` or a later cast fail.
        return np.full(n, default, dtype=dtype)
    if len(a) != n:
        out = np.full(n, default, dtype=dtype)
        m = min(len(a), n)
        out[:m] = a[:m]
        a = out
    try:
        if dtype is np.float32:
            a = np.nan_to_num(a.astype(np.float32), nan=default)
        elif dtype is np.int8:
            a = a.astype(np.int8)
    except (TypeError, ValueError):
        # Optional TF selectors can legitimately name an unavailable series in
        # a frozen NPZ.  Treat a non-numeric fallback as the documented neutral
        # value; never turn a missing indicator into a simulation crash.
        return np.full(n, default, dtype=dtype)
    return a


# ────────────────────────────────────────────────────────────────────────────
# WT_DIV_EXIT
# Live source: ez_positions_quick.py:3461-3464 WT_EXHAUST+DIV_EXIT
# ────────────────────────────────────────────────────────────────────────────
def check_wt_div_exit_vec(npz: Dict[str, np.ndarray], n: int, is_long: bool,
                          config: Any) -> Dict[str, np.ndarray]:
    """Build per-bar mask for the WT divergence exit.

    Mask is True when:
        wt_divergence_<DIV_TF>  is opposite-bias (BEAR for LONG, BULL for SHORT)
        AND (if REQUIRE_EXHAUST) wt_momentum_state_<MOM_TF> == EXHAUST_<position dir>

    Caller layers gain/age/cooldown — same convention as evaluate_exit_gates_vec.
    """
    if not bool(getattr(config, 'WT_DIV_EXIT_ENABLED', False)):
        return {'enabled': False, 'mask': np.zeros(n, dtype=bool)}
    div_tf = str(getattr(config, 'WT_DIV_EXIT_TF', '1h'))
    require_exh = bool(getattr(config, 'WT_DIV_EXIT_REQUIRE_EXHAUST', True))
    mom_tf = str(getattr(config, 'WT_DIV_EXIT_MOM_TF', '1h'))
    div_arr = _get_arr(npz, f'wt_divergence_{div_tf}', n, default=0, dtype=np.int8)
    if is_long:
        div_against = (div_arr == _DIV_BEAR)
    else:
        div_against = (div_arr == _DIV_BULL)
    if require_exh:
        mom_arr = _get_arr(npz, f'wt_momentum_state_{mom_tf}', n, default=0, dtype=np.int8)
        if is_long:
            mom_exhaust = (mom_arr == _MOM_EXHAUST_UP)
        else:
            mom_exhaust = (mom_arr == _MOM_EXHAUST_DOWN)
        mask = div_against & mom_exhaust
    else:
        mask = div_against
    return {'enabled': True, 'mask': mask, 'div_tf': div_tf, 'mom_tf': mom_tf}


# ────────────────────────────────────────────────────────────────────────────
# WT_ACCEL_EXIT
# Live source: derived (autonomous_iters knob); semantics mirror MI_VELOCITY_EXIT
# (ez_positions_quick.py:3531-3542) but on wt_acceleration_<tf>.
# ────────────────────────────────────────────────────────────────────────────
def check_wt_accel_exit_vec(npz: Dict[str, np.ndarray], n: int, is_long: bool,
                            config: Any) -> Dict[str, np.ndarray]:
    """Per-bar mask: True when WT acceleration is against position on >= MIN_TFS
    timeframes in TFS list.

    For LONG: wt_acceleration_<tf> < LONG_THR  (deceleration/reversal)
    For SHORT: wt_acceleration_<tf> > SHORT_THR
    """
    if not bool(getattr(config, 'WT_ACCEL_EXIT_ENABLED', False)):
        return {'enabled': False, 'mask': np.zeros(n, dtype=bool)}
    tfs = list(getattr(config, 'WT_ACCEL_EXIT_TFS', ['15m', '1h', '4h']))
    min_tfs = int(getattr(config, 'WT_ACCEL_EXIT_MIN_TFS', 2))
    long_thr = float(getattr(config, 'WT_ACCEL_EXIT_LONG_THR', -0.5))
    short_thr = float(getattr(config, 'WT_ACCEL_EXIT_SHORT_THR', 0.5))
    against = np.zeros(n, dtype=np.int16)
    for tf in tfs:
        a = _get_arr(npz, f'wt_acceleration_{tf}', n, default=0.0, dtype=np.float32)
        if is_long:
            against += (a < long_thr).astype(np.int16)
        else:
            against += (a > short_thr).astype(np.int16)
    mask = against >= min_tfs
    return {'enabled': True, 'mask': mask, 'against_count': against, 'tfs': tfs}


# ────────────────────────────────────────────────────────────────────────────
# WT_MOMENTUM_EXIT
# Live source: ez_positions_quick.py MI_EXHAUST_EXIT pattern (lines 3523-3526).
# ────────────────────────────────────────────────────────────────────────────
def check_wt_momentum_exit_vec(npz: Dict[str, np.ndarray], n: int, is_long: bool,
                               config: Any) -> Dict[str, np.ndarray]:
    """Per-bar mask: True when wt_momentum_state_<tf> == EXHAUST_<opposite> on
    >= WT_MOMENTUM_EXIT_THRESHOLD timeframes.
    """
    if not bool(getattr(config, 'WT_MOMENTUM_EXIT_ENABLED', False)):
        return {'enabled': False, 'mask': np.zeros(n, dtype=bool)}
    tfs = list(getattr(config, 'WT_MOMENTUM_EXIT_TFS', ['15m', '1h', '4h']))
    threshold = int(getattr(config, 'WT_MOMENTUM_EXIT_THRESHOLD', 2))
    exhaust_target = _MOM_EXHAUST_UP if is_long else _MOM_EXHAUST_DOWN
    against = np.zeros(n, dtype=np.int16)
    for tf in tfs:
        m = _get_arr(npz, f'wt_momentum_state_{tf}', n, default=0, dtype=np.int8)
        against += (m == exhaust_target).astype(np.int16)
    mask = against >= threshold
    return {'enabled': True, 'mask': mask, 'against_count': against, 'tfs': tfs}


# ────────────────────────────────────────────────────────────────────────────
# WT_EXHAUST_EXIT (configurable min-TFs variant)
# Live source: ez_manage.py:41148-41194. Live rule = 4h AND (1h OR 15m).
# Position_evaluator already emits that exact mask. This function adds a
# configurable BTC_TECH_EXIT_WT_MIN_TFS-aware variant for BTC dedicated paths
# and any caller that wants N-TF momentum confluence.
# ────────────────────────────────────────────────────────────────────────────
def check_wt_exhaust_exit_vec(npz: Dict[str, np.ndarray], n: int, is_long: bool,
                              config: Any) -> Dict[str, np.ndarray]:
    """Per-bar mask: True when >= MIN_TFS momentum TFs in EXHAUST_<position dir>.
    Defaults to the live-equivalent 4h AND (1h OR 15m) when MIN_TFS == 0
    (sentinel — fall back to the existing position_evaluator behaviour).
    """
    if not bool(getattr(config, 'WT_EXHAUST_EXIT_ENABLED', True)):
        # Keep the bundle schema stable when disabled. v8_vec_sweep consumes
        # both masks unconditionally during bulk precompute; omitting mask_btc
        # made the legitimate False setting crash instead of becoming inert.
        zero = np.zeros(n, dtype=bool)
        return {
            'enabled': False, 'mask': zero, 'mask_btc': zero,
            'count': np.zeros(n, dtype=np.int16), 'tfs': [],
            'min_tfs': 0, 'btc_min_tfs': 0,
        }
    tfs = list(getattr(config, 'WT_EXHAUST_EXIT_TFS', ['15m', '1h', '4h']))
    min_tfs = int(getattr(config, 'WT_EXHAUST_EXIT_MIN_TFS', 0))
    # BTC variant — caller selects which mask via the returned 'mask_btc'
    btc_min_tfs = int(getattr(config, 'BTC_TECH_EXIT_WT_MIN_TFS', 3))
    target = _MOM_EXHAUST_UP if is_long else _MOM_EXHAUST_DOWN
    m4 = _get_arr(npz, 'wt_momentum_state_4h', n, default=0, dtype=np.int8)
    m1 = _get_arr(npz, 'wt_momentum_state_1h', n, default=0, dtype=np.int8)
    m15 = _get_arr(npz, 'wt_momentum_state_15m', n, default=0, dtype=np.int8)
    # Live-equivalent default mask (4h AND (1h OR 15m))
    mask_live = (m4 == target) & ((m1 == target) | (m15 == target))
    # Configurable N-TF mask
    if min_tfs > 0:
        count = np.zeros(n, dtype=np.int16)
        for tf in tfs:
            mm = _get_arr(npz, f'wt_momentum_state_{tf}', n, default=0, dtype=np.int8)
            count += (mm == target).astype(np.int16)
        mask = count >= min_tfs
    else:
        mask = mask_live
        count = (m4 == target).astype(np.int16) + (m1 == target).astype(np.int16) \
                + (m15 == target).astype(np.int16)
    # BTC-min-TFs mask (always emit for caller convenience)
    mask_btc = count >= btc_min_tfs
    return {'enabled': True, 'mask': mask, 'mask_btc': mask_btc, 'count': count,
            'tfs': tfs, 'min_tfs': min_tfs, 'btc_min_tfs': btc_min_tfs}


# ────────────────────────────────────────────────────────────────────────────
# Bundle entrypoint — single call from v8_vec_sweep precompute block.
# ────────────────────────────────────────────────────────────────────────────
def build_wt_exit_masks(npz: Dict[str, np.ndarray], n: int, is_long: bool,
                        config: Any) -> Dict[str, Dict[str, np.ndarray]]:
    """One-shot precompute of all four WT exit gates."""
    return {
        'div': check_wt_div_exit_vec(npz, n, is_long, config),
        'accel': check_wt_accel_exit_vec(npz, n, is_long, config),
        'momentum': check_wt_momentum_exit_vec(npz, n, is_long, config),
        'exhaust_cfg': check_wt_exhaust_exit_vec(npz, n, is_long, config),
    }


WT_EXITS_KNOBS: Tuple[str, ...] = (
    'WT_DIV_EXIT_ENABLED', 'WT_DIV_EXIT_TF', 'WT_DIV_EXIT_REQUIRE_EXHAUST', 'WT_DIV_EXIT_MOM_TF',
    'WT_ACCEL_EXIT_ENABLED', 'WT_ACCEL_EXIT_MIN_TFS', 'WT_ACCEL_EXIT_TFS',
    'WT_ACCEL_EXIT_LONG_THR', 'WT_ACCEL_EXIT_SHORT_THR',
    'WT_MOMENTUM_EXIT_ENABLED', 'WT_MOMENTUM_EXIT_THRESHOLD', 'WT_MOMENTUM_EXIT_TFS',
    'WT_EXHAUST_EXIT_MIN_TFS', 'WT_EXHAUST_EXIT_TFS',
    'BTC_TECH_EXIT_WT_MIN_TFS',
)
