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
WIRE_LIVE = ("NEW", "PARTIALLY_FILLED", "PENDING", "OPEN", "WORKING")


async def fire_exit_market(trade_manager, position_key, symbol, side, position_side):
    """USER 2026-10-13: an exit that times out becomes MARKET — never canceled (suicide).

    Last-resort flattener: cancel lingering same-side open orders WITH
    confirmation, re-read live amt AFTER the cancels (closes the fill race as
    tightly as possible without the sanction ledger), then MARKET exactly that.
    Returns a result string, never raises.
    """
    try:
        acct = str(position_key).split(":", 1)[0] if ":" in str(position_key) else None
        pos = None
        try:
            pos = (trade_manager.positions or {}).get(position_key)
        except Exception:
            pass
        amt = abs(float(getattr(pos, "positionAmt", 0.0) or 0.0)) if pos is not None else 0.0
        if amt <= 0:
            return "TIMEOUT_ALREADY_FLAT"
        try:
            client = trade_manager.get_client(acct)
        except Exception as e:
            logger.error(f"[VEC_EXACT] {position_key} TIMEOUT_MARKET_NO_CLIENT ({e})")
            return "TIMEOUT_MARKET_NO_CLIENT"
        if client is None:
            return "TIMEOUT_MARKET_NO_CLIENT"
        try:
            step = float((trade_manager.get_symbol_config(symbol) or {}).get("step_size", 0) or 0)
        except Exception:
            step = 0.0
        try:
            oo = await asyncio.wait_for(asyncio.to_thread(client.futures_get_open_orders, symbol=symbol), timeout=15.0)
        except Exception:
            oo = []
        for o in oo or []:
            try:
                if str((o or {}).get("side", "")).upper() != str(side).upper():
                    continue
                if str((o or {}).get("status", "")).upper() not in WIRE_LIVE:
                    continue
                await asyncio.wait_for(asyncio.to_thread(client.futures_cancel_order, symbol=symbol, orderId=(o or {}).get("orderId")), timeout=15.0)
            except Exception:
                pass
        try:
            pos2 = (trade_manager.positions or {}).get(position_key)
            amt2 = abs(float(getattr(pos2, "positionAmt", 0.0) or 0.0)) if pos2 is not None else 0.0
        except Exception:
            amt2 = amt
        qty = amt2
        if step > 0:
            import math
            qty = math.floor(amt2 / step) * step
        if qty <= 0:
            return "TIMEOUT_DUST_AFTER_CANCEL"
        try:
            # 2026-10-14 twin drift: this fallback never wired once (logs 10-11..14: 0 TIMEOUT_EXIT_MARKET; 26 refused
            # ORDER_DEDUPE_BLOCK:NOT_VIA_EXECUTE_NOW, 160 BROKER_UNREACHABLE, 17 15s timeouts) -> timed-out vec exits stayed open. It runs as
            # an execute_now leg of the owed vec CLOSE: same token the execute_now decorator stamps (order_dedupe_guard.execute_now_context;
            # to_thread copies the context). Every other wire check (broker read, dedupe, file reflection) still applies.
            import contextlib
            try:
                import order_dedupe_guard as _odg
                _ctx = _odg.execute_now_context("binance", position_key=position_key, account_key=acct, symbol=symbol, position_side=str(position_side).upper(), action="CLOSE", reason="VEC_TIMEOUT_EXIT_MARKET |VEC_EXACT")
            except Exception:
                _ctx = contextlib.nullcontext()
            with _ctx:
                resp = await asyncio.wait_for(asyncio.to_thread(client.futures_create_order, symbol=symbol, side=str(side).upper(), positionSide=str(position_side).upper(), quantity=str(qty), type="MARKET"), timeout=15.0)
        except Exception as e:
            logger.error(f"[VEC_EXACT] {position_key} TIMEOUT_MARKET_FAIL qty={qty}: {e}")
            return "TIMEOUT_MARKET_FAIL"
        logger.critical(f"[VEC_EXACT] {position_key} TIMEOUT_EXIT_MARKET {side} {qty} wired (resp={str(resp)[:80]})")
        return f"TIMEOUT_EXIT_MARKET {qty}"
    except Exception as e:
        logger.error(f"[VEC_EXACT] {position_key} TIMEOUT_MARKET_ERR: {e}")
        return "TIMEOUT_MARKET_ERR"


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


async def dispatch_tracked(position_key, label, coro_fn, timeout, on_timeout=None):
    """Run coro_fn() tracked; survives outer (sweep) cancellation. Returns result.

    Raises CancelledError (after logging CANCELLED_DISPATCH) when the awaiting
    sweep is cancelled — the execution itself continues detached. On inner
    timeout, runs on_timeout() first (exit->MARKET conversion) when provided.
    """
    if _live(position_key):
        logger.warning(f"[VEC_EXACT] {position_key} {label} SKIPPED_VX_INFLIGHT (execution already running, no dup dispatch)")
        return "SKIPPED_VX_INFLIGHT"

    async def _run():
        try:
            return await asyncio.wait_for(coro_fn(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[VEC_EXACT] {position_key} {label} BLOCKED_DISPATCH_TIMEOUT after {timeout:.0f}s (tracked execution timed out)")
            if on_timeout is not None:
                try:
                    cb = await on_timeout()
                except Exception as e:
                    cb = f"ON_TIMEOUT_ERR {e}"
                return f"BLOCKED_DISPATCH_TIMEOUT->{cb}"
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
