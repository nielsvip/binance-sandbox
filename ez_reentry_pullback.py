"""
ez_reentry_pullback.py — Profit-reduce pullback reentry (2026-05-06).

Fixes the three root causes that leave a LONG permanently un-reentered after a
profit-taking REDUCE at a pump top followed by a deep price correction:

  Fix A — Directional: FAVORABLE_MOVE in the live loop is LONG=price-UP only.
           After a profit-taking reduce, price going DOWN is a better re-buy.
           We check: current_price <= reduce_price * (1 - PULLBACK_DROP_PCT).

  Fix B — HTF gate: GUARANTEED_REENTRY_STRICT_CONFIRMATION needs _htf_count>=1
           but after a pump+crash all HTF WT (1h/4h/D) are bearish → count=0.
           We require only WT3m + WT15m crossing up from oversold. No HTF needed
           for a profit-taking reduce (profit was already banked in the first leg).

  Fix C — MIN_GAIN bypass: LOSING_POSITION_HARD_BLOCK fires when the remaining
           position's gain drifts below MIN_GAIN (3%) after the pullback.
           This is NOT adding to a loser — it's reclaiming a position we already
           banked profit on. We lower MIN_GAIN to REENTRY_PULLBACK_MIN_GAIN_OVERRIDE
           (default 0.0) for the duration of execute_now, then restore.

Config switches (all via getattr — add to config.py before live deployment):
  REENTRY_PROFIT_PULLBACK_ENABLED     False   master switch
  REENTRY_PULLBACK_DROP_PCT           3.0     price must drop >= X% from reduce level
  REENTRY_PULLBACK_WT3M_OS_THRESH    -10.0   wt1_3m_prev threshold for oversold (LONG)
  REENTRY_PULLBACK_REQUIRE_15M        True    also require WT15m crossed in direction
  REENTRY_PULLBACK_REQUIRE_VEL        True    require wt_velocity_3m > 0 (LONG)
  REENTRY_PULLBACK_MIN_GAIN_OVERRIDE  0.0     MIN_GAIN floor used during this reentry
  REENTRY_PULLBACK_DEDUP_S           300.0   per-key cooldown in seconds
"""

from __future__ import annotations
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_PROFIT_REDUCE_MARKERS = (
    'PPL_', 'PARTIAL_PROFIT', 'STRONG_REDUCE_K', 'PEAK_GIVEBACK',
    'PROFIT_LOCK', 'MAKER_PROFIT_EXIT', 'REDUCED_PPL',
    'REDUCED_PARTIAL_PROFIT', 'REDUCED_STRONG_REDUCE', 'REDUCED_PEAK_GIVEBACK',
    'REDUCED_MAKER_PROFIT', 'GOLDEN_RULE', 'GR_TIGHT',
)

_pullback_last_fire: dict = {}


def _is_profit_reduce(reason: str) -> bool:
    r = reason.upper()
    return any(m in r for m in _PROFIT_REDUCE_MARKERS)


def _patch_min_gain(value: float) -> list:
    """Patch MIN_GAIN on config module + ez_manage.config + ez_positions_quick.config.
    Returns list of (obj, attr, old_val) so caller can restore."""
    import config as _cm
    patches = []
    for obj in [_cm]:
        if hasattr(obj, 'MIN_GAIN'):
            patches.append((obj, 'MIN_GAIN', getattr(obj, 'MIN_GAIN')))
            obj.MIN_GAIN = value
    for _inst in list(getattr(_cm.Config, '_INSTANCES', [])):
        try:
            patches.append((_inst, 'MIN_GAIN', getattr(_inst, 'MIN_GAIN', value)))
            _inst.MIN_GAIN = value
        except Exception:
            pass
    for mod_name in ('ez_manage', 'ez_positions_quick'):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            cfg = getattr(mod, 'config', None)
            if cfg is not None and hasattr(cfg, 'MIN_GAIN'):
                patches.append((cfg, 'MIN_GAIN', cfg.MIN_GAIN))
                cfg.MIN_GAIN = value
        except Exception:
            pass
    return patches


def _restore_patches(patches: list) -> None:
    for obj, attr, old_val in patches:
        try:
            setattr(obj, attr, old_val)
        except Exception:
            pass


async def evaluate_profit_pullback_reentry(trade_manager, cfg=None) -> int:
    """
    Called once per bar by backtest_v8_engine after evaluate_reentry_2_epq.
    Scans reentry_data for profit-taking reduces and fires a REENTRY when
    Fixes A + B + C all pass. Returns number of successful fires.
    """
    if cfg is None:
        try:
            import config as cfg
        except Exception:
            return 0

    if not getattr(cfg, 'REENTRY_PROFIT_PULLBACK_ENABLED', False):
        return 0

    _re_src = getattr(trade_manager, 'service', None)
    _re_dict = getattr(_re_src, 'reentry_data', None) if _re_src else None
    if _re_dict is None:
        _re_dict = getattr(trade_manager, 'reentry_data', {})
    if not _re_dict:
        return 0

    drop_pct_thr = float(getattr(cfg, 'REENTRY_PULLBACK_DROP_PCT', 3.0)) / 100.0
    os_thresh = float(getattr(cfg, 'REENTRY_PULLBACK_WT3M_OS_THRESH', -10.0))
    require_15m = bool(getattr(cfg, 'REENTRY_PULLBACK_REQUIRE_15M', True))
    require_vel = bool(getattr(cfg, 'REENTRY_PULLBACK_REQUIRE_VEL', True))
    min_gain_override = float(getattr(cfg, 'REENTRY_PULLBACK_MIN_GAIN_OVERRIDE', 0.0))
    dedup_s = float(getattr(cfg, 'REENTRY_PULLBACK_DEDUP_S', 300.0))

    fires = 0
    now_ts = time.time()

    for pk, rd in list(_re_dict.items()):
        if not isinstance(rd, dict):
            continue
        if rd.get('status') == 'filled':
            continue

        exit_reason = str(rd.get('reason', ''))
        if not _is_profit_reduce(exit_reason):
            continue

        if now_ts - _pullback_last_fire.get(pk, 0) < dedup_s:
            continue

        reduce_price = float(rd.get('reentry_level') or rd.get('exit_price') or 0)
        if reduce_price <= 0:
            continue

        try:
            from utils import parse_position_key
            account_key, symbol, pos_side = parse_position_key(pk)
        except Exception:
            parts = pk.split(':')
            if len(parts) < 2:
                continue
            account_key = parts[0]
            sym_side = parts[1] if len(parts) > 1 else ''
            pos_side = 'LONG' if sym_side.endswith('_LONG') else 'SHORT'
            symbol = sym_side[:-5] if pos_side == 'LONG' else sym_side[:-6]

        if not symbol:
            continue

        is_long = pos_side == 'LONG'

        # Fetch current indicators (same path as evaluate_reentry_2_epq)
        try:
            from ez_manage import ii as _ii
            indicators = await _ii(trade_manager, symbol)
        except Exception:
            indicators = {}
        if not indicators:
            continue

        sf = lambda k, d=0.0: float(indicators.get(k) or d)
        current_price = sf('current_price')
        if current_price <= 0:
            continue

        # Fix A: correct favorable direction for profit-reduce.
        # LONG profit-reduce → favorable = price dropped from peak reduce level.
        if is_long:
            pulled_back = current_price <= reduce_price * (1.0 - drop_pct_thr)
        else:
            pulled_back = current_price >= reduce_price * (1.0 + drop_pct_thr)
        if not pulled_back:
            continue

        # Fix B: HTF-lite — WT3m from oversold + WT15m same direction. No HTF.
        wt1_3m = sf('wt1_3m'); wt2_3m = sf('wt2_3m')
        wt1_15m = sf('wt1_15m'); wt2_15m = sf('wt2_15m')
        wt1_3m_prev = sf('wt1_3m_prev', wt1_3m)
        wt_vel_3m = sf('wt_velocity_3m')

        if is_long:
            was_oversold = wt1_3m_prev < os_thresh
            crossed_3m = wt1_3m > wt2_3m
            crossed_15m = wt1_15m > wt2_15m
            vel_ok = wt_vel_3m > 0
        else:
            was_oversold = wt1_3m_prev > abs(os_thresh)
            crossed_3m = wt1_3m < wt2_3m
            crossed_15m = wt1_15m < wt2_15m
            vel_ok = wt_vel_3m < 0

        signal_ok = was_oversold and crossed_3m
        if require_15m:
            signal_ok = signal_ok and crossed_15m
        if require_vel:
            signal_ok = signal_ok and vel_ok
        if not signal_ok:
            continue

        side = 'BUY' if is_long else 'SELL'
        re_qty_amt = float(rd.get('reentry_amount') or 0)
        if re_qty_amt <= 0:
            re_qty_amt = float(getattr(cfg, 'START_POSITION_SIZE', 50.0)) / max(current_price, 1e-9)
        else:
            re_qty_amt = min(re_qty_amt, float(getattr(cfg, 'MAX_POSITION_SIZE', 500.0)) / max(current_price, 1e-9))

        drop_actual_pct = abs(reduce_price / current_price - 1) * 100 if is_long else abs(current_price / reduce_price - 1) * 100
        reason = (
            f"PULLBACK_REENTRY_{'LONG' if is_long else 'SHORT'}"
            f"_drop{drop_actual_pct:.1f}pct"
            f"_wt3m{wt1_3m:.1f}_prev{wt1_3m_prev:.1f}"
            f"_wt15m{wt1_15m:.1f}_vel{wt_vel_3m:.1f}"
        )

        # Fix C: temporarily lower MIN_GAIN so LOSING_POSITION_HARD_BLOCK passes.
        patches = _patch_min_gain(min_gain_override)
        try:
            result = await trade_manager.execute_now(
                pk, account_key, symbol,
                0, side, pos_side,
                re_qty_amt, current_price,
                f"PPB_{int(now_ts)}", reason, False, 'REENTRY',
            )
        except Exception as _exec_e:
            logger.error(f"[PROFIT_PULLBACK_REENTRY] {pk}: execute_now err={_exec_e}")
            result = f"EXCEPTION_{_exec_e}"
        finally:
            _restore_patches(patches)

        if result == 'SUCCESS':
            _pullback_last_fire[pk] = now_ts
            fires += 1
            logger.warning(
                f"[PROFIT_PULLBACK_REENTRY] {pk}: FIRED "
                f"reduce_px={reduce_price:.4f} now={current_price:.4f} "
                f"drop={drop_actual_pct:.1f}% "
                f"wt3m={wt1_3m:.1f}(prev={wt1_3m_prev:.1f}) wt15m={wt1_15m:.1f} vel={wt_vel_3m:.1f}"
            )
        else:
            logger.debug(f"[PROFIT_PULLBACK_REENTRY] {pk}: blocked — {result}")

    return fires
