"""Tracked VEC_EXACT dispatch (USER 2026-10-13: 45s monitor guillotine vs 120s maker rule).

Root cause it fixes: monitor_entries wraps process_position in asyncio.timeout(45),
but maker-chase execution needs up to 120s + market fallback by ground rule. Every
slow close was cancelled mid-flight (CANCELLED_DISPATCH), retried next sweep, swept
again — 0% close success, positions strand, noref/div climb unboundedly.

Design: execution runs as a per-position_key single-flight background task with its
own timeout. The awaiting sweep may be cancelled (45s budget) — the execution
survives via shield and shepherds the broker order to fill/market-fallback. A later
sweep seeing live in-flight execution skips re-dispatch (no dup orders). Entries
older than timeout+60s are treated as dead (fail-open: re-dispatch allowed, the
wedged task leaks until recycle exactly as today — never strand a key).

Only this module + 3 call sites in _vec_exact_process_position. No other paths.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

INFLIGHT = {}
STALE_GRACE_S = 60.0


def _live(key):
    ent = INFLIGHT.get(key)
    if not ent:
        return None
    task, born, timeout = ent
    if task.done():
        INFLIGHT.pop(key, None)
        return None
    if time.time() - born > timeout + STALE_GRACE_S:
        logger.warning(f"[VEC_EXACT] {key} STALE_INFLIGHT age>{timeout + STALE_GRACE_S:.0f}s — wedged task abandoned, re-dispatch allowed")
        INFLIGHT.pop(key, None)
        return None
    return ent


async def dispatch_tracked(position_key, label, coro_fn, timeout):
    """Run coro_fn() tracked; survives outer (sweep) cancellation. Returns result.

    Raises CancelledError (after logging CANCELLED_DISPATCH) when the awaiting
    sweep is cancelled — the execution itself continues detached.
    """
    if _live(position_key):
        logger.warning(f"[VEC_EXACT] {position_key} {label} SKIPPED_VX_INFLIGHT (execution already running, no dup dispatch)")
        return "SKIPPED_VX_INFLIGHT"

    async def _run():
        try:
            return await asyncio.wait_for(coro_fn(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[VEC_EXACT] {position_key} {label} BLOCKED_DISPATCH_TIMEOUT after {timeout:.0f}s (tracked execution timed out)")
            return "BLOCKED_DISPATCH_TIMEOUT"

    born = time.time()
    inner = asyncio.ensure_future(_run())

    def _done(t):
        INFLIGHT.pop(position_key, None)
        try:
            res = t.result()
            logger.info(f"[VEC_EXACT] {position_key} {label} TRACKED_DONE -> {str(res)[:120]}")
        except asyncio.CancelledError:
            logger.warning(f"[VEC_EXACT] {position_key} {label} TRACKED_CANCELLED (inner died)")
        except Exception as e:
            logger.error(f"[VEC_EXACT] {position_key} {label} TRACKED_ERROR: {e}")

    inner.add_done_callback(_done)
    INFLIGHT[position_key] = (inner, born, timeout)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        logger.warning(f"[VEC_EXACT] {position_key} {label} CANCELLED_DISPATCH (sweep superseded mid-execution; TRACKED execution survives detached; dup re-dispatch suppressed)")
        raise
