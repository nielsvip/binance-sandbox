#!/usr/bin/env python3
"""
ez_reentry.py — Centralized reentry module (2026-04-28).

THE goal: every reentry path in the system goes through this module so we have
ONE source of truth for reentry behavior across:
  • Live ez_manage.py (per-account workers)
  • Live ez_positions_quick.py (general scanner)
  • V8 vectorized backtest (v8_quick_engine.py)
  • V8 real-engine backtest (backtest_v8_engine.py)
  • Standalone daemon (ez_reentry_daemon.py)

Architecture (Phase 1, 2026-04-28):
  • This module is a FACADE that re-exports the canonical scalar evaluators
    living in ez_manage.py (evaluate_reentry, evaluate_reentry_2,
    periodic_evaluate_reentry_loop, _price_level_reentry_monitor*) and
    ez_positions_quick.py (evaluate_reentry_epq, reentry_enforcement_loop_epq,
    evaluate_reentry_2_periodic_loop_epq). Defining functions in only one
    place avoids drift; this module is the import surface other code uses.
  • inline_active(name) is the kill-switch helper. Inline call sites in
    ez_manage / ez_positions_quick wrap their reentry blocks with this so
    user can flip EZ_REENTRY_INLINE_ENABLED=False to defer to the daemon.
  • pick_reentry_evaluator() returns the vectorized version for V8 backtests
    when V8_VECTORIZED_REENTRY=1 (default 1 from 2026-04-28).

Phase 2 (next session, gated): physically move the function bodies out of
ez_manage.py / ez_positions_quick.py into this file and update the import
direction (callers import from ez_reentry instead of declaring locally).
That requires moving ~6 evaluator functions + their internal helpers and
is left out of Phase 1 to avoid a 100-touchpoint same-session refactor.

User invariants (CLAUDE.md): tradeable_keys is sacred, hedging is sacred —
neither is touched by this module.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional


def inline_active(name: str, *, default: bool = True) -> bool:
    """Return True iff the inline reentry block keyed by ``name`` should run.

    Two-level gating: the master ``EZ_REENTRY_INLINE_ENABLED`` overrides every
    granular ``EZ_REENTRY_INLINE_<NAME>_ENABLED`` switch. When the master is
    False, all inline reentry paths are dormant and the daemon (or a future
    Redis-driven path) is the only producer of reentry signals.

    name: short tag matching the granular switch suffix (e.g. ``TIER12_EPQ``,
        ``EVAL_EPQ``, ``EVAL2_DIRECT``, ``LOOP_PERIODIC``, ``LOOP_ENFORCE``,
        ``LOOP_PRICE_MONITOR``, ``LOOP_ENFORCE_EPQ``, ``LOOP_EVAL2_EPQ``).
    """
    try:
        import config as _cfg  # late binding — avoids cycle if config imports us
    except Exception:
        return default
    if not bool(getattr(_cfg, 'EZ_REENTRY_INLINE_ENABLED', True)):
        return False
    return bool(getattr(_cfg, f'EZ_REENTRY_INLINE_{name}_ENABLED', default))


def daemon_enabled() -> bool:
    try:
        import config as _cfg
        return bool(getattr(_cfg, 'EZ_REENTRY_DAEMON_ENABLED', True))
    except Exception:
        return True


def heartbeat(redis_client, role: str = "daemon", ttl: int = 90) -> None:
    """Write a heartbeat into Redis under ``ez_reentry:heartbeat:<role>`` so
    other components can detect a stalled daemon. Best-effort; swallows errors.
    """
    if redis_client is None:
        return
    try:
        redis_client.set(f"ez_reentry:heartbeat:{role}", int(time.time()), ex=ttl)
    except Exception:
        pass


def heartbeat_age(redis_client, role: str = "daemon") -> Optional[float]:
    if redis_client is None:
        return None
    try:
        v = redis_client.get(f"ez_reentry:heartbeat:{role}")
        if v is None:
            return None
        return time.time() - float(v)
    except Exception:
        return None


def get_evaluate_reentry():
    """Return the canonical scalar evaluate_reentry coroutine from ez_manage."""
    import ez_manage  # late import — avoids cycle at module load
    return ez_manage.evaluate_reentry


def get_evaluate_reentry_2():
    import ez_manage
    return ez_manage.evaluate_reentry_2


def get_periodic_evaluate_reentry_loop():
    import ez_manage
    return ez_manage.periodic_evaluate_reentry_loop


def get_price_level_reentry_monitor_loop():
    import ez_manage
    return ez_manage._price_level_reentry_monitor_loop


def get_evaluate_reentry_epq():
    import ez_positions_quick
    return ez_positions_quick.evaluate_reentry_epq


def get_reentry_enforcement_loop_epq():
    import ez_positions_quick
    return ez_positions_quick.reentry_enforcement_loop_epq


def get_evaluate_reentry_2_periodic_loop_epq():
    import ez_positions_quick
    return ez_positions_quick.evaluate_reentry_2_periodic_loop_epq


def get_evaluate_reentry_2_epq():
    import ez_positions_quick
    return ez_positions_quick.evaluate_reentry_2_epq


def pick_reentry_evaluator(stores: Optional[dict] = None, cfg: Optional[Any] = None):
    """Return a callable that evaluates reentry for the V8 backtest path.

    When V8_VECTORIZED_REENTRY env var is "1" (default from 2026-04-28) and the
    caller supplied numpy stores, returns the vectorized evaluator's bound
    method. Otherwise returns the scalar coroutine — same return contract.
    """
    use_vec = os.environ.get("V8_VECTORIZED_REENTRY", "1") == "1"
    if use_vec and stores is not None and cfg is not None:
        try:
            from ez_reentry_vectorized import VectorizedReentryEvaluator
            return VectorizedReentryEvaluator(stores, cfg).evaluate
        except Exception:
            pass
    return get_evaluate_reentry()


async def enforce_price_cross_reentry(trade_manager) -> int:
    """Safety net (2026-04-28, user-mandated): for every position with a recorded
    exit price in trade_manager.reentry_data, fire a partial reentry the FIRST
    time current price crosses past exit (LONG: above, SHORT: below).

    Why: existing reentry paths (evaluate_reentry, evaluate_reentry_2, TIER1/TIER2,
    _price_level_reentry_monitor, reentry_enforcement_loop) all CAN block on
    downstream gates (NO_DOUBLE_OPEN, MIN_GAP, SYMGATE, RALLY_K15M, etc) and
    sometimes the position never reenters even when price has clearly crossed
    the exit. User invariant: "NEVER allow a position to break out above exit
    price without having at least a partial reentry. Effective NOW".

    Idempotent via trade_manager._price_cross_last_fire (in-memory dict). Fires
    once per cross epoch (default 600s min gap between fires per position). Uses
    execute_now per CLAUDE.md "ONLY gate" rule. Returns count of fires.

    Switches: EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED (default True),
    EZ_REENTRY_PRICE_CROSS_PCT (cross threshold, default 0.001 = 0.1%),
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S (per-key dedup, default 600s),
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC (size fraction, default 0.5).
    """
    try:
        import config as _cfg
    except Exception:
        return 0
    if not bool(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED", True)):
        return 0
    cross_pct = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_PCT", 0.001))
    min_gap_s = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_MIN_GAP_S", 600.0))
    partial_frac = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC", 0.5))
    start_size = float(getattr(_cfg, "START_POSITION_SIZE", 18.0))
    if not hasattr(trade_manager, "_price_cross_last_fire"):
        trade_manager._price_cross_last_fire = {}
    src = (
        trade_manager.service.reentry_data
        if getattr(trade_manager, "service", None)
        else getattr(trade_manager, "reentry_data", {})
    )
    if not isinstance(src, dict) or not src:
        return 0
    try:
        from utils import parse_position_key as _ppk
    except Exception:
        return 0
    now = time.time()
    fired = 0
    for pk, rd in list(src.items()):
        if not isinstance(rd, dict):
            continue
        try:
            exit_px = float(rd.get("reentry_level") or rd.get("exit_price") or 0.0)
        except (TypeError, ValueError):
            exit_px = 0.0
        if exit_px <= 0:
            continue
        try:
            account_key, sym, side = _ppk(pk)
        except Exception:
            continue
        is_long = side == "LONG"
        position = trade_manager.positions.get(pk) if hasattr(trade_manager, "positions") else None
        if position is None:
            continue
        try:
            cur_px = float(getattr(position, "mark_price", 0) or 0)
        except (TypeError, ValueError):
            cur_px = 0.0
        if cur_px <= 0:
            continue
        crossed = (
            (is_long and cur_px > exit_px * (1.0 + cross_pct))
            or ((not is_long) and cur_px < exit_px * (1.0 - cross_pct))
        )
        if not crossed:
            continue
        last_fire = float(trade_manager._price_cross_last_fire.get(pk, 0))
        if now - last_fire < min_gap_s:
            continue
        try:
            re_amt = float(rd.get("reentry_amount", 0) or 0)
        except (TypeError, ValueError):
            re_amt = 0.0
        fire_qty = (re_amt if re_amt > 0 else (start_size / max(cur_px, 1e-9))) * partial_frac
        if fire_qty <= 0:
            continue
        try:
            pos_amt = abs(float(getattr(position, "positionAmt", 0) or 0))
        except (TypeError, ValueError):
            pos_amt = 0.0
        try:
            uid = f"PRICE_CROSS_GUARANTEE_{int(now)}"
            reason = (
                f"GUARANTEED_PRICE_CROSS_REENTRY_exit{exit_px:.6f}"
                f"_cur{cur_px:.6f}_partial{partial_frac:.2f}"
            )
            await trade_manager.execute_now(
                position_key=pk,
                account_key=account_key,
                symbol=sym,
                original_positionAmt=pos_amt,
                side=("BUY" if is_long else "SELL"),
                position_side=side,
                quantity=fire_qty,
                old_price=cur_px,
                unique_id=uid,
                reason=reason,
                is_full_close=False,
                action="AUGMENT",
            )
            trade_manager._price_cross_last_fire[pk] = now
            fired += 1
        except Exception as e:
            try:
                trade_manager.logger.error(f"[PRICE_CROSS_GUARANTEE] {pk}: {e}")
            except Exception:
                pass
    return fired


async def price_cross_reentry_safety_loop(trade_manager) -> None:
    """Tight tick loop wrapping enforce_price_cross_reentry. Default 5s interval.
    Owned by ez_manage.py's main(); also callable from any other live process."""
    import asyncio as _aio
    try:
        import config as _cfg
        interval = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_INTERVAL_S", 5.0))
    except Exception:
        interval = 5.0
    try:
        trade_manager.logger.info(
            f"[PRICE_CROSS_GUARANTEE] safety loop started — interval={interval}s"
        )
    except Exception:
        pass
    while True:
        try:
            n = await enforce_price_cross_reentry(trade_manager)
            if n:
                try:
                    trade_manager.logger.warning(
                        f"[PRICE_CROSS_GUARANTEE] fired {n} reentries this tick"
                    )
                except Exception:
                    pass
        except Exception as e:
            try:
                trade_manager.logger.error(f"[PRICE_CROSS_GUARANTEE_ERROR] {e}", exc_info=True)
            except Exception:
                pass
        await _aio.sleep(interval)


__all__ = [
    "inline_active",
    "daemon_enabled",
    "heartbeat",
    "heartbeat_age",
    "get_evaluate_reentry",
    "get_evaluate_reentry_2",
    "get_periodic_evaluate_reentry_loop",
    "get_price_level_reentry_monitor_loop",
    "get_evaluate_reentry_epq",
    "get_reentry_enforcement_loop_epq",
    "get_evaluate_reentry_2_periodic_loop_epq",
    "get_evaluate_reentry_2_epq",
    "pick_reentry_evaluator",
    "enforce_price_cross_reentry",
    "price_cross_reentry_safety_loop",
]
