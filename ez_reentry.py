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

import json
import os
import time
from pathlib import Path
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


def _get_redis_client(trade_manager):
    try:
        rm = getattr(trade_manager, "redis_manager", None)
        if rm is None:
            return None
        client = getattr(rm, "client", None)
        if client is not None:
            return client
        return rm
    except Exception:
        return None


def _load_market_data(trade_manager) -> dict:
    """Read Redis ``latest_market_data`` once per tick — it's the canonical live
    price source, a single JSON-encoded string of {symbol: {current_price, ...}}.
    Per-tick result is cached on the trade_manager via _mark_price_dict_cache."""
    client = _get_redis_client(trade_manager)
    if client is None:
        return {}
    try:
        raw = client.get("latest_market_data")
        if not raw:
            return {}
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="ignore")
        return json.loads(raw) or {}
    except Exception:
        return {}


def _lookup_mark_price(trade_manager, position_key: str, symbol: str, mkt: Optional[dict] = None) -> float:
    cur_px = 0.0
    try:
        position = trade_manager.positions.get(position_key) if hasattr(trade_manager, "positions") else None
        if position is not None:
            cur_px = float(getattr(position, "mark_price", 0) or 0)
    except Exception:
        cur_px = 0.0
    if cur_px > 0:
        return cur_px
    # latest_market_data — primary live source, dict {sym: {current_price, ...}}
    if mkt is not None:
        try:
            entry = mkt.get(symbol) or {}
            v = float(entry.get("current_price") or entry.get("mark_price") or entry.get("close_3m") or 0)
            if v > 0:
                return v
        except Exception:
            pass
    # tradier path: tradier_prices_latest is a JSON-string for stocks
    client = _get_redis_client(trade_manager)
    if client is None:
        return 0.0
    try:
        raw = client.hget("mark_prices", symbol)
        if raw:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="ignore")
            obj = json.loads(raw)
            v = float(obj.get("price") or 0)
            if v > 0:
                return v
    except Exception:
        pass
    return 0.0


def _allowed_accounts_for(trade_manager) -> list:
    raw = getattr(trade_manager, "_allowed_accounts", None)
    accs = list(raw) if raw else []
    if not accs:
        single = getattr(trade_manager, "account_key", None)
        if single:
            accs = [single]
    return [a for a in accs if isinstance(a, str)]


def is_reentry_eligible(trade_manager, position_key: str, account_key: str, symbol: str, cfg=None) -> tuple:
    """Pre-flight check: would execute_now refuse this REENTRY/AUGMENT call?

    Mirrors the two top-of-execute_now hard guards so a caller can skip BEFORE
    paying the cost of indicator fetch + execute_now traversal:
      • NON_TRADEABLE_HARD_BLOCK  — `position_key not in trade_manager.tradeable_keys`
      • LOSING_POSITION_HARD_BLOCK — existing positionAmt>0 AND effective_gain<MIN_GAIN

    Returns ``(eligible: bool, reason: str)``. Caller skips when ``eligible`` is
    False. Failure modes fail-open (return True) so a misconfigured trade_manager
    cannot starve all reentry paths.

    Call at the TOP of every path that fires a REENTRY toward execute_now —
    safety loop (`enforce_price_cross_reentry`), `reentry_enforcement_loop`,
    `evaluate_reentry`, `evaluate_reentry_2`, TIER1/TIER2 inline blocks, etc.
    User mandate 2026-04-28: "find from where the call came and ADD THE FILTER
    there instead of just dumbly turning it off ... so we stop wasting compute
    on dead end streets".
    """
    if cfg is None:
        try:
            import config as cfg
        except Exception:
            return True, "NO_CONFIG"
    try:
        tk = getattr(trade_manager, "tradeable_keys", None)
        if tk is not None and len(tk) > 0 and position_key not in tk:
            return False, "NON_TRADEABLE"
    except Exception:
        pass
    try:
        pos = None
        if hasattr(trade_manager, "positions_by_account"):
            pos = (trade_manager.positions_by_account.get(account_key, {}) or {}).get(position_key)
        if pos is None and hasattr(trade_manager, "positions"):
            pos = trade_manager.positions.get(position_key)
        if pos is not None:
            amt = abs(float(getattr(pos, "positionAmt", 0) or 0))
            if amt > 0:
                raw_gain = float(getattr(pos, "gain", 0) or 0)
                eff_gain = effective_gain_pct(position_key, raw_gain, trade_manager, cfg)
                min_gain = float(getattr(cfg, "MIN_GAIN", 3.0))
                if eff_gain < min_gain:
                    return False, f"GAIN_LT_MIN_raw{raw_gain:.2f}_eff{eff_gain:.2f}_min{min_gain:.2f}"
    except Exception:
        pass
    return True, "OK"


def effective_gain_pct(position_key: str, raw_gain: float, trade_manager=None, cfg=None) -> float:
    """User mandate (2026-04-28): after a PARTIAL_PROFIT_LOCK 50% close, the
    REMAINING position has effectively doubled its gain percentage on the
    half-sized cost basis. So gain<MIN_GAIN augment-block checks should compare
    against the AMPLIFIED gain (raw_gain / (1 - PPL_FRAC)) when PPL has fired
    for this position_key. With FRAC=0.5 → 2× multiplier.

    Returns ``raw_gain`` unchanged if PPL has not fired or feature is disabled.
    Switch: ``EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED`` (default True).

    Used at every gain<MIN_GAIN block site in ez_manage / ez_positions_quick /
    tradier_manage so a profit-locked position becomes augment-eligible at
    half the gain it would otherwise need.
    """
    try:
        if cfg is None:
            import config as cfg
    except Exception:
        return raw_gain
    if not bool(getattr(cfg, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True)):
        return raw_gain
    if trade_manager is None:
        return raw_gain
    try:
        st = getattr(trade_manager, "partial_profit_lock_state", None)
        if not isinstance(st, dict):
            return raw_gain
        entry = st.get(position_key) or {}
        if not entry.get("fired"):
            return raw_gain
        frac = float(getattr(cfg, "PARTIAL_PROFIT_LOCK_FRAC", 0.5))
        if not (0.0 < frac < 1.0):
            return raw_gain
        return raw_gain / (1.0 - frac)
    except Exception:
        return raw_gain


def _ts_to_epoch(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _collect_reentry_candidates(trade_manager, base_path: Path) -> list:
    """Build [(position_key, is_long, exit_px, exit_amt, exit_ts, src)] from disk
    JSON files PLUS in-memory reentry_data. Sorted by exit_ts desc so the
    freshest crosses fire first when fire-cap is hit. Disk wins on duplicates."""
    by_key: dict = {}
    for acc in _allowed_accounts_for(trade_manager):
        acc_dir = base_path / acc
        if not acc_dir.exists():
            continue
        for side_name, is_long in (("long", True), ("short", False)):
            f = acc_dir / f"{side_name}_reentry.json"
            if not f.exists():
                continue
            try:
                with open(f, "r") as fp:
                    data = json.load(fp)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for pk, rd in data.items():
                if not isinstance(rd, dict):
                    continue
                try:
                    exit_px = float(rd.get("reentry_level") or rd.get("exit_price") or 0.0)
                    exit_amt = float(rd.get("reentry_amount", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if exit_px <= 0:
                    continue
                exit_ts = _ts_to_epoch(rd.get("timestamp") or rd.get("exit_timestamp"))
                by_key[pk] = (is_long, exit_px, exit_amt, exit_ts, f"DISK_{side_name.upper()}")
    src_mem = (
        trade_manager.service.reentry_data
        if getattr(trade_manager, "service", None)
        else getattr(trade_manager, "reentry_data", {})
    )
    if isinstance(src_mem, dict):
        for pk, rd in src_mem.items():
            if pk in by_key or not isinstance(rd, dict):
                continue
            try:
                exit_px = float(rd.get("reentry_level") or rd.get("exit_price") or 0.0)
                exit_amt = float(rd.get("reentry_amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            if exit_px <= 0:
                continue
            exit_ts = _ts_to_epoch(rd.get("timestamp") or rd.get("exit_timestamp"))
            is_long = pk.endswith("_LONG")
            by_key[pk] = (is_long, exit_px, exit_amt, exit_ts, "MEM")
    items = [(pk,) + v for pk, v in by_key.items()]
    items.sort(key=lambda r: r[4], reverse=True)
    return items


async def enforce_price_cross_reentry(trade_manager) -> int:
    """Safety net (2026-04-28, user-mandated): for every position with a recorded
    exit price (disk JSON file OR in-memory reentry_data), fire a partial reentry
    when current mark_price crosses past exit (LONG: above, SHORT: below).

    Why disk + Redis instead of just in-memory: in-memory state can be empty
    immediately after restart, on a different worker, or after a service hot-swap.
    Disk JSON files (`<account>/long_reentry.json` / `short_reentry.json`) are the
    canonical persistence layer written by execute_trade_action on every reduce/
    close. Mark price comes from Redis `mark_price:<SYMBOL>` (JSON-encoded with
    `price` field) when the position object is stale.

    Existing paths (evaluate_reentry, evaluate_reentry_2, TIER1/TIER2,
    _price_level_reentry_monitor, reentry_enforcement_loop) all gate on
    NO_DOUBLE_OPEN/MIN_GAP/SYMGATE/RALLY_K15M and miss real crosses. This loop
    has NO pre-gates — only cross + dedup + execute_now (CLAUDE.md "ONLY gate").

    Action='REENTRY' so execute_now's REENTRY-bypass paths (lines 10641-10674,
    13007-13008) treat the call as a guaranteed rebuild instead of a normal
    AUGMENT (which can re-block on entry gates).

    Switches: EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED (default True),
    EZ_REENTRY_PRICE_CROSS_PCT (cross threshold, default 0.0 = strict cross),
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S (per-key dedup, default 60s),
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC (size fraction, default 0.5).
    """
    try:
        import config as _cfg
    except Exception:
        return 0
    if not bool(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED", True)):
        return 0
    cross_pct = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_PCT", 0.0))
    min_gap_s = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_MIN_GAP_S", 60.0))
    partial_frac = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC", 0.5))
    start_size = float(getattr(_cfg, "START_POSITION_SIZE", 18.0))
    min_pos_size = float(getattr(_cfg, "MIN_POSITION_SIZE", 55.0))
    max_age_h = float(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS", 48.0))
    max_fires = int(getattr(_cfg, "EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK", 20))
    base_path = Path(getattr(_cfg, "BASE_PATH", "/Users/niels/Documents/binance"))
    if not hasattr(trade_manager, "_price_cross_last_fire"):
        trade_manager._price_cross_last_fire = {}
    candidates = _collect_reentry_candidates(trade_manager, base_path)
    if not candidates:
        return 0
    try:
        from utils import parse_position_key as _ppk
    except Exception:
        return 0
    allowed = set(_allowed_accounts_for(trade_manager))
    now = time.time()
    age_cutoff = now - (max_age_h * 3600.0)
    mkt = _load_market_data(trade_manager)
    fired = 0
    for pk, is_long, exit_px, exit_amt, exit_ts, src_tag in candidates:
        if fired >= max_fires:
            break
        if exit_ts > 0 and exit_ts < age_cutoff:
            continue
        try:
            account_key, sym, side = _ppk(pk)
        except Exception:
            continue
        if allowed and account_key not in allowed:
            continue
        # Pre-flight gate (user 2026-04-28): skip if execute_now would refuse this
        # call — saves the indicator fetch, mark-price lookup, dedup write, and
        # execute_now traversal. Mirror of LOSING_POSITION_HARD_BLOCK + NON_TRADEABLE
        # at top of execute_now (ez_manage:13084 / ez_positions_quick:11380).
        eligible, gate_reason = is_reentry_eligible(trade_manager, pk, account_key, sym, _cfg)
        if not eligible:
            continue
        cur_px = _lookup_mark_price(trade_manager, pk, sym, mkt)
        if cur_px <= 0:
            continue
        if cross_pct > 0:
            crossed = (
                (is_long and cur_px > exit_px * (1.0 + cross_pct))
                or ((not is_long) and cur_px < exit_px * (1.0 - cross_pct))
            )
        else:
            crossed = (is_long and cur_px > exit_px) or ((not is_long) and cur_px < exit_px)
        if not crossed:
            continue
        last_fire = float(trade_manager._price_cross_last_fire.get(pk, 0))
        if now - last_fire < min_gap_s:
            continue
        fire_qty = (exit_amt if exit_amt > 0 else (start_size / max(cur_px, 1e-9))) * partial_frac
        if fire_qty <= 0:
            continue
        position = trade_manager.positions.get(pk) if hasattr(trade_manager, "positions") else None
        try:
            pos_amt = abs(float(getattr(position, "positionAmt", 0) or 0)) if position is not None else 0.0
        except Exception:
            pos_amt = 0.0
        # Only fire when position is FLAT-OR-NEAR-FLAT. Partial-close reentries on
        # still-substantial positions get blocked downstream by LOSING_POSITION_HARD_BLOCK
        # when gain<MIN_GAIN (user absolute rule, ez_manage:12990 / ez_positions_quick:11380).
        # Mirrors TIER1/TIER2 inline gate at ez_positions_quick:14251 (`pos_amt <= _pos_min_qty_entry`).
        flat_threshold_qty = min_pos_size / max(cur_px, 1e-9)
        if pos_amt > flat_threshold_qty:
            continue
        try:
            uid = f"PRICE_CROSS_GUARANTEE_{int(now)}"
            reason = (
                f"GUARANTEED_PRICE_CROSS_REENTRY_{src_tag}_exit{exit_px:.6f}"
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
                action="REENTRY",
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
    "effective_gain_pct",
    "is_reentry_eligible",
]
