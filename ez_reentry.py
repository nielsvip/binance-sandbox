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
]
