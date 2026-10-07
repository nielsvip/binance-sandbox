"""
vec_paths/partial_profit_lock_v2.py — 3-step Partial Profit Lock state machine.

SOURCE: tradier_manage.py:~5166 (_check_exit_conditions PARTIAL_PROFIT_LOCK block)
         ez_manage.py (PARTIAL_PROFIT_LOCK_ENABLED block — crypto mirror)

LIVE REASON PATTERNS (searchable in /history/):
    Step 1 REDUCE:  PPL_TP_gain<N>_50pct
    Step 2 (state): PPL_TRADIER_STOP_UPGRADED (log only, no trade)
    Step 3 CLOSE:   PPL_SL_CLOSE_upgraded-0.5pct_px<N>_stop<N>
                    PPL_SL_CLOSE_BE+buffer_px<N>_stop<N>

NOTE: As of 2026-05-12, PARTIAL_PROFIT_LOCK_ENABLED=False in both config.py and
config_tradier.py. Live /history/ shows zero PPL_TP_* events. These functions
are wired for future use and validation scaffolding.

PRIOR VEC APPROXIMATION (in vec_engine_v1.py lines 1015-1048):
The old inline block was a close approximation of the 3-step logic. This module
REPLACES it (the inline block is guarded via flag). The only behavioral change
is that Step 1 now records the partial REDUCE correctly as a fraction of qty
(not a scaled gain), and Step 2 fires on the NEXT bar after Step 1 (real engine
does it in the same loop pass or next evaluation cycle).

RETURN TYPES:

check_ppl_step1(store, bar_idx, pos_state, cfg) -> Optional[dict]
    If fires: {"path": "PPL_STEP1", "frac": 0.5, "reason": str, "stop_level": float, "first_exit_price": float}

check_ppl_step2(store, bar_idx, pos_state, cfg) -> Optional[dict]
    If fires (state update only): {"path": "PPL_STEP2_STOP_UPGRADE", "new_stop": float}

check_ppl_step3(store, bar_idx, pos_state, cfg) -> Optional[dict]
    If fires (full close): {"path": "PPL_STEP3_SL_HIT", "reason": str, "bypass_noloss": False}

USAGE in vec_engine_v1.py simulate():

    if cfg.PARTIAL_PROFIT_LOCK_ENABLED:
        for pos in (pos_long, pos_short):
            if not pos.open: continue
            # Step 1 — partial close
            r1 = check_ppl_step1(store, bar_idx, pos, cfg)
            if r1 is not None:
                frac = r1["frac"]
                partial_gain = pos.gain_pct * frac
                returns_by_sym[sym].append(partial_gain)
                all_returns.append(partial_gain)
                running_gain += partial_gain
                pos.qty *= (1.0 - frac)
                pos.ppl_fired = True
                pos.ppl_first_exit_price = price
                pos.ppl_stop_level = r1["stop_level"]
                pos.ppl_stop_upgraded = False
                continue
            # Step 2 — stop upgrade (state only, no close)
            if pos.ppl_fired and not pos.ppl_stop_upgraded:
                r2 = check_ppl_step2(store, bar_idx, pos, cfg)
                if r2 is not None:
                    pos.ppl_stop_level = r2["new_stop"]
                    pos.ppl_stop_upgraded = True
            # Step 3 — stop hit → full close
            if pos.ppl_fired and pos.ppl_stop_level > 0:
                r3 = check_ppl_step3(store, bar_idx, pos, cfg)
                if r3 is not None:
                    pnl = pos.gain_pct
                    returns_by_sym[sym].append(pnl)
                    all_returns.append(pnl)
                    running_gain += pnl
                    pos.open = False
                    pos.last_close_ts = ts_i
                    pos.last_close_price = price
"""
from __future__ import annotations

from typing import Any, Optional


def _ppl_cfg(cfg: Any, base_key: str, mode: str, default: float) -> float:
    """Mode-aware PPL config read.

    For tradier mode, prefer the _TRADIER-suffixed key (config_tradier.py defines
    only PARTIAL_PROFIT_LOCK_*_TRADIER, e.g. GAIN_PCT_TRADIER=1.5, ARM_GAIN_PCT_TRADIER=1.75,
    BE_BUFFER_PCT_TRADIER=0.02, FRAC_TRADIER=0.625). Fall back to the non-suffixed key
    (used by SweepConfig + config.py crypto), then the hardcoded default. Crypto mode
    reads the non-suffixed key directly, preserving prior behavior.
    """
    if str(mode).lower() == "tradier":
        val = getattr(cfg, base_key + "_TRADIER", None)
        if val is not None:
            return float(val)
    val = getattr(cfg, base_key, None)
    if val is not None:
        return float(val)
    return float(default)


def check_ppl_step1(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    cfg: Any,
    mode: str = "crypto",
) -> Optional[dict]:
    """Step 1: Close FRAC (50%) at GAIN_PCT threshold. Set BE stop.

    Mirrors tradier_manage.py:5191 (not _ppl_fired_t block).
    Fires when:
        pos.ppl_fired == False
        AND gain >= PARTIAL_PROFIT_LOCK_GAIN_PCT
        AND pos.qty > min_qty (simplified: qty > 1e-6)

    Returns dict with frac/stop_level/reason/first_exit_price, or None.
    """
    if pos_state.ppl_fired:
        return None
    if not pos_state.open:
        return None

    gain = pos_state.gain_pct
    threshold = _ppl_cfg(cfg, "PARTIAL_PROFIT_LOCK_GAIN_PCT", mode, 0.5)
    if gain < threshold:
        return None

    frac = _ppl_cfg(cfg, "PARTIAL_PROFIT_LOCK_FRAC", mode, 0.5)
    be_buf = _ppl_cfg(cfg, "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT", mode, 0.02) / 100.0
    price = store.price(bar_idx)
    if price <= 0:
        return None

    entry = pos_state.entry_price
    if entry <= 0:
        return None

    if pos_state.side == "LONG":
        stop_level = entry * (1.0 + be_buf)
    else:
        stop_level = entry * (1.0 - be_buf)

    reason = f"PPL_TP_gain{gain:.2f}_{int(frac*100)}pct"
    return {
        "path": "PPL_STEP1",
        "reason": reason,
        "frac": frac,
        "stop_level": stop_level,
        "first_exit_price": price,
    }


def check_ppl_step2(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    cfg: Any,
    mode: str = "crypto",
) -> Optional[dict]:
    """Step 2: Upgrade stop to first_exit_price when gain >= ARM_GAIN_PCT.

    Mirrors tradier_manage.py:5208 (_ppl_fired_t and not _ppl_stop_upgraded_t block).
    This is a STATE UPDATE ONLY — no trade fires. Caller sets pos.ppl_stop_upgraded=True
    and pos.ppl_stop_level = new_stop.

    Returns {"path": "PPL_STEP2_STOP_UPGRADE", "new_stop": float} or None.
    """
    if not pos_state.ppl_fired:
        return None
    if pos_state.ppl_stop_upgraded:
        return None
    if not pos_state.open:
        return None

    gain = pos_state.gain_pct
    arm_pct = _ppl_cfg(cfg, "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT", mode, 0.75)
    if gain < arm_pct:
        return None
    if pos_state.ppl_first_exit_price <= 0:
        return None

    return {
        "path": "PPL_STEP2_STOP_UPGRADE",
        "new_stop": pos_state.ppl_first_exit_price,
    }


def check_ppl_step3(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    cfg: Any,
    mode: str = "crypto",
) -> Optional[dict]:
    """Step 3: Close remainder when price hits stop_level.

    Mirrors tradier_manage.py:5211 (_ppl_fired_t and _ppl_stop_level_t > 0 block).
    Fires when price crosses stop_level (LONG: price <= stop, SHORT: price >= stop).

    Returns {"path": "PPL_STEP3_SL_HIT", "reason": str, "bypass_noloss": False} or None.
    Note: bypass_noloss=False because the stop should always be at or above entry after Step 1.
    """
    if not pos_state.ppl_fired:
        return None
    if not pos_state.open:
        return None
    stop_level = pos_state.ppl_stop_level
    if stop_level <= 0:
        return None

    price = store.price(bar_idx)
    if price <= 0:
        return None

    is_long = (pos_state.side == "LONG")
    sl_hit = (is_long and price <= stop_level) or ((not is_long) and price >= stop_level)
    if not sl_hit:
        return None

    sl_label = "upgraded-0.5pct" if pos_state.ppl_stop_upgraded else "BE+buffer"
    reason = f"PPL_SL_CLOSE_{sl_label}_px{price:.4f}_stop{stop_level:.4f}"
    return {
        "path": "PPL_STEP3_SL_HIT",
        "reason": reason,
        "bypass_noloss": False,
    }
