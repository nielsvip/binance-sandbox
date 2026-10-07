"""
vec_paths/stale_mark_price.py — STALE_MARK_PRICE_BLOCK live / backtest parity.

LIVE SOURCE: ez_manage.py:13575-13633 (added 2026-05-05 after 1000LUNCUSDT loss
where a 60-min-stale Redis mark made a -45% loser look like a +37% winner and
fired AUGMENTs/REDUCEs against the real direction).

LIVE BEHAVIOR (scalar, real-time):
    age = now() - position.mark_price_last_updated
    if age > EXECUTE_NOW_MAX_MARK_AGE_S (default 120.0s):
        try ONE Redis refresh via _get_mark_price_from_redis()
        if still stale: return "BLOCKED_STALE_MARK_PRICE_age{age}s"
    CLOSE actions / INTERVENTION / MANUAL / AGENT reasons bypass the gate.

BACKTEST SEMANTICS — POLICY (Option A, default):
    The backtest engine reconstructs mark_price from indicator NPZ + bar close_ts
    inside the simulation loop. There is no Redis, no WebSocket, no clock drift.
    "mark_price_last_updated" in backtest IS the bar's close_ts; "current_ts"
    is the same bar's close_ts (or close_ts + tick_offset for intra-bar checks).
    => age is ~0 by construction. The gate NEVER fires in pure backtest.
    => This matches the live ez_manage scalar's hard skip when V8_SWEEP_MODE=1.

BACKTEST SEMANTICS — POLICY (Option B, opt-in via env var):
    V8_BACKTEST_SIMULATE_STALE_MARK=1 injects synthetic per-bar staleness drawn
    from a uniform distribution [0, EXECUTE_NOW_MAX_MARK_AGE_S * 3]. Useful
    for verifying the gate works end-to-end inside the engine, NOT for parity
    sweeps. Default OFF.

CONFIG KNOBS MIRRORED:
    EXECUTE_NOW_MAX_MARK_AGE_S  : float  default 120.0  (live max age, seconds)
    STALE_MARK_REDIS_REFRESH_ENABLED : bool default True (try ONE refresh in live)
                                       NEW knob — proposed default True. If a
                                       project does not define it, getattr falls
                                       back to True for live parity.

OUTPUT (scalar + vec):
    Tuple[blocked: bool, reason: str, age_seconds: float]
    reason ∈ {"OK", "STALE_MARK_PRICE_BLOCK", "MARK_PRICE_REFRESHED_OK"}.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional, Tuple

import numpy as np


# ────────────────────────────────────────────────────────────────────────────────
# SCALAR — ground truth, byte-equivalent to live ez_manage.py:13601-13631
# ────────────────────────────────────────────────────────────────────────────────


def evaluate_stale_mark_block_core(
    mark_price: float,
    mark_price_last_updated_ts: Optional[float],
    current_ts: float,
    cfg: Any,
    *,
    symbol: Optional[str] = None,
    redis_fetch_callable: Optional[Callable[[str], Tuple[Optional[float], Optional[float]]]] = None,
    is_close_action: bool = False,
    is_sweep_mode: bool = False,
) -> Tuple[bool, str, float]:
    """Scalar STALE_MARK_PRICE_BLOCK evaluator.

    Args:
        mark_price:                 Current cached mark price (float, $).
        mark_price_last_updated_ts: Unix epoch seconds of last WS/Redis update,
                                    or None if never stamped (treat as infinitely stale).
        current_ts:                 Unix epoch seconds — "now" in live, bar close in backtest.
        cfg:                        Config object (must expose EXECUTE_NOW_MAX_MARK_AGE_S).
        symbol:                     Optional symbol for redis refresh; required if
                                    redis_fetch_callable is provided.
        redis_fetch_callable:       Optional callable(symbol) -> (price, ts) for ONE
                                    live refresh attempt. None in backtest.
        is_close_action:            CLOSE / INTERVENTION / MANUAL / AGENT bypass.
                                    Engine-side decisions can pass True to short-circuit.
        is_sweep_mode:              When True (backtest sweep), gate never fires —
                                    matches V8_SWEEP_MODE bypass in live ez_manage.

    Returns:
        (blocked, reason, age_seconds).
        blocked=False means the action is allowed.
        reason="STALE_MARK_PRICE_BLOCK" means blocked=True.
        reason="MARK_PRICE_REFRESHED_OK" means a redis refresh resolved the staleness
            (blocked=False, age_seconds is the refreshed age).
        reason="OK" means age was already within tolerance (blocked=False).
    """
    # Bypass paths — mirror live ez_manage.py:13593-13600
    if is_close_action or is_sweep_mode:
        return False, "OK", 0.0

    max_age = float(getattr(cfg, "EXECUTE_NOW_MAX_MARK_AGE_S", 120.0))

    # Unknown stamp → treat as infinitely stale (live default 999999.0).
    if mark_price_last_updated_ts is None:
        age = 999999.0
    else:
        try:
            age = float(current_ts) - float(mark_price_last_updated_ts)
        except (TypeError, ValueError):
            age = 999999.0
        if age < 0.0:
            age = 0.0

    if age <= max_age:
        return False, "OK", age

    # Try ONE Redis refresh — matches live behavior at ez_manage.py:13619-13628.
    refresh_enabled = bool(getattr(cfg, "STALE_MARK_REDIS_REFRESH_ENABLED", True))
    if refresh_enabled and redis_fetch_callable is not None and symbol:
        try:
            ref_price, ref_ts = redis_fetch_callable(symbol)
            if ref_price is not None and float(ref_price) > 0.0 and ref_ts is not None:
                age2 = float(current_ts) - float(ref_ts)
                if age2 < 0.0:
                    age2 = 0.0
                if age2 <= max_age:
                    return False, "MARK_PRICE_REFRESHED_OK", age2
                age = age2  # still stale, fall through with refreshed age
        except Exception:
            pass  # fail-open behavior matches live (warning logged, then continues)

    return True, "STALE_MARK_PRICE_BLOCK", age


# ────────────────────────────────────────────────────────────────────────────────
# VECTORIZED — per-bar age computation + decision array
# ────────────────────────────────────────────────────────────────────────────────


def evaluate_stale_mark_block_vec(
    current_ts_arr: np.ndarray,
    mark_price_ts_arr: Optional[np.ndarray],
    cfg: Any,
    *,
    is_close_action_arr: Optional[np.ndarray] = None,
    is_sweep_mode: bool = True,
    inject_synthetic_staleness: Optional[bool] = None,
    rng: Optional[np.random.Generator] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized STALE_MARK_PRICE_BLOCK evaluator.

    Args:
        current_ts_arr:        shape (N,) unix epoch seconds per bar (bar close_ts).
        mark_price_ts_arr:     shape (N,) unix epoch seconds of last mark update per bar,
                               or None (treat as never stamped → infinite staleness if
                               the gate is forced on).
        cfg:                   Config (EXECUTE_NOW_MAX_MARK_AGE_S, optional
                               STALE_MARK_REDIS_REFRESH_ENABLED).
        is_close_action_arr:   shape (N,) bool. True bars bypass the gate (CLOSE etc.).
                               None ⇒ all False.
        is_sweep_mode:         True ⇒ gate disabled (default, matches V8_SWEEP_MODE).
                               When True, the function returns all-OK arrays unless
                               inject_synthetic_staleness is True.
        inject_synthetic_staleness:
                               Override env var V8_BACKTEST_SIMULATE_STALE_MARK.
                               When True, draws ages ~ U(0, 3*max_age) per bar so the
                               gate fires on ~2/3 of bars — used to smoke-test that
                               downstream engine code respects the block.
        rng:                   numpy Generator for synthetic injection. Default seeded.

    Returns:
        (blocked_arr, reason_arr, age_arr)
            blocked_arr: shape (N,) bool — True = block this bar
            reason_arr:  shape (N,) object — one of {"OK", "STALE_MARK_PRICE_BLOCK"}
            age_arr:     shape (N,) float — age in seconds per bar
    """
    n = int(len(current_ts_arr))
    max_age = float(getattr(cfg, "EXECUTE_NOW_MAX_MARK_AGE_S", 120.0))

    # Env-var fallback for synthetic staleness (Option B).
    if inject_synthetic_staleness is None:
        inject_synthetic_staleness = (
            os.environ.get("V8_BACKTEST_SIMULATE_STALE_MARK", "0") == "1"
        )

    # ── Compute ages ──────────────────────────────────────────────────────────
    if inject_synthetic_staleness:
        # Option B — synthetic distribution for gate-smoke-tests.
        if rng is None:
            rng = np.random.default_rng(42)
        age_arr = rng.uniform(0.0, max_age * 3.0, size=n).astype(np.float64)
    elif mark_price_ts_arr is None:
        # No stamps provided. In sweep mode this is the structural backtest case
        # (mark = bar close, stamp = bar close → age = 0).
        if is_sweep_mode:
            age_arr = np.zeros(n, dtype=np.float64)
        else:
            age_arr = np.full(n, 999999.0, dtype=np.float64)
    else:
        cur = np.asarray(current_ts_arr, dtype=np.float64)
        mts = np.asarray(mark_price_ts_arr, dtype=np.float64)
        age_arr = cur - mts
        # Guard against negative (clock skew / out-of-order bars) and NaN stamps.
        age_arr = np.where(np.isnan(age_arr), 999999.0, age_arr)
        age_arr = np.clip(age_arr, 0.0, None)

    # ── Decision ──────────────────────────────────────────────────────────────
    # Sweep-mode hard bypass unless synthetic injection is on.
    if is_sweep_mode and not inject_synthetic_staleness:
        blocked_arr = np.zeros(n, dtype=bool)
    else:
        blocked_arr = age_arr > max_age

    # Bypass close/intervention actions per-bar.
    if is_close_action_arr is not None:
        close_mask = np.asarray(is_close_action_arr, dtype=bool)
        if close_mask.shape == blocked_arr.shape:
            blocked_arr = blocked_arr & ~close_mask

    reason_arr = np.where(blocked_arr, "STALE_MARK_PRICE_BLOCK", "OK").astype(object)
    return blocked_arr, reason_arr, age_arr


# ────────────────────────────────────────────────────────────────────────────────
# BACKTEST INTEGRATION HELPER
# ────────────────────────────────────────────────────────────────────────────────


def backtest_should_block(
    bar_close_ts: float,
    cfg: Any,
    *,
    action: str = "AUGMENT",
    reason: str = "",
) -> Tuple[bool, str, float]:
    """Convenience wrapper for backtest_v8_engine.process_position().

    Backtest semantic (Option A): the mark_price is ALWAYS up to date because
    it equals the close of the current simulated bar. age = 0.0.

    Returns (False, "OK", 0.0) by default; honors V8_BACKTEST_SIMULATE_STALE_MARK
    for opt-in synthetic gate-smoke-testing.

    Engine integration site (suggested) — backtest_v8_engine.py process_position:
        from vec_paths.stale_mark_price import backtest_should_block
        _stale_blocked, _stale_reason, _stale_age = backtest_should_block(
            bar_close_ts=bar.close_ts,
            cfg=config,
            action=candidate_action,
            reason=candidate_reason,
        )
        if _stale_blocked:
            continue  # skip this candidate; parity with live ez_manage
    """
    upper_act = (action or "").upper()
    upper_reason = (reason or "").upper()
    is_close_action = (
        "CLOSE" in upper_act
        or "INTERVENTION" in upper_reason
        or "MANUAL" in upper_reason
        or "AGENT" in upper_reason
    )
    # Mirror the live ez_manage V8_SWEEP_MODE / V8_OVERRIDE_FILE bypass — in backtest
    # we set is_sweep_mode=True by default.
    is_sweep_mode = (
        os.environ.get("V8_SWEEP_MODE", "0") == "1"
        or os.environ.get("V8_OVERRIDE_FILE", "") != ""
        or os.environ.get("V8_BACKTEST_SIMULATE_STALE_MARK", "0") != "1"
    )
    return evaluate_stale_mark_block_core(
        mark_price=0.0,
        mark_price_last_updated_ts=float(bar_close_ts),
        current_ts=float(bar_close_ts),
        cfg=cfg,
        symbol=None,
        redis_fetch_callable=None,
        is_close_action=is_close_action,
        is_sweep_mode=is_sweep_mode,
    )
