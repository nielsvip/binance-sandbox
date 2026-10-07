"""Strict v12 protective EXIT/REDUCE/STOP vector contracts, batch 3.

This module covers position-age safety stops and no-loss/hedge decisions.  All
market predicates use native persisted arrays.  Execution outcomes (hedge
availability, hedge attempt success, active-hedge state) remain explicit caller
state.  Missing enabled-path arrays fail that path closed; no timeframe proxy or
fabricated decision is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ExitReduceBatch3Result:
    masks: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]
    obligatory_wt_against_count: np.ndarray
    underwater_htf_against_count: np.ndarray
    wrong_side_wt_count: np.ndarray
    wrong_side_stoch_count: np.ndarray


@dataclass(frozen=True)
class LifecycleActionResult:
    """Concrete integration surface for the six v12 lifecycle actions."""

    masks: Mapping[str, np.ndarray]


def _cfg(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)


def _state(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=dtype)
    if arr.ndim == 0:
        return np.full(n, arr.item(), dtype=dtype)
    if arr.ndim != 1 or len(arr) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return arr


def evaluate_gap_batch3(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_minutes: Any,
    position_active: Any = True,
    position_above_min_qty: Any = True,
    is_hedge: Any = False,
    hard_exit_reason: Any = "",
    is_reduce: Any = True,
    hedge_already_active: Any = False,
    hedge_engine_available: Any = True,
    hedge_attempt_succeeded: Any = True,
    seconds_since_underwater_action: Any = np.inf,
    seconds_since_wt15_action: Any = np.inf,
    symbol_is_usdc: Any = False,
    dd_bounce_qty_above_min: Any = False,
    dd_last_augment_price: Any = 0.0,
    bar_count: int | None = None,
) -> ExitReduceBatch3Result:
    side = str(position_side).upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("position_side must be LONG or SHORT")
    long = side == "LONG"
    if bar_count is None:
        n = next((len(np.asarray(v)) for v in npz.values() if np.asarray(v).ndim == 1), None)
        if n is None:
            raise ValueError("bar_count is required when no 1-D persisted array exists")
    else:
        n = int(bar_count)
        if n < 0:
            raise ValueError("bar_count must be non-negative")
    zeros = lambda: np.zeros(n, dtype=bool)
    gain = _state(gain_pct, n, "gain_pct", float)
    age = _state(position_age_minutes, n, "position_age_minutes", float)
    active = _state(position_active, n, "position_active", bool)
    above = _state(position_above_min_qty, n, "position_above_min_qty", bool)
    hedge = _state(is_hedge, n, "is_hedge", bool)
    reason = _state(hard_exit_reason, n, "hard_exit_reason", object)
    reduce = _state(is_reduce, n, "is_reduce", bool)
    hedge_active = _state(hedge_already_active, n, "hedge_already_active", bool)
    engine = _state(hedge_engine_available, n, "hedge_engine_available", bool)
    hedge_success = _state(hedge_attempt_succeeded, n, "hedge_attempt_succeeded", bool)
    uw_since = _state(seconds_since_underwater_action, n, "seconds_since_underwater_action", float)
    wt15_since = _state(seconds_since_wt15_action, n, "seconds_since_wt15_action", float)
    is_usdc = _state(symbol_is_usdc, n, "symbol_is_usdc", bool)
    dd_qty = _state(dd_bounce_qty_above_min, n, "dd_bounce_qty_above_min", bool)
    dd_price = _state(dd_last_augment_price, n, "dd_last_augment_price", float)
    pos = active & above
    masks: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str]) -> dict[str, np.ndarray] | None:
        out: dict[str, np.ndarray] = {}
        bad: list[str] = []
        for key in keys:
            if key not in npz:
                bad.append(key)
                continue
            arr = np.asarray(npz[key])
            if arr.ndim != 1 or len(arr) != n:
                bad.append(key)
                continue
            try:
                out[key] = arr.astype(float, copy=False)
            except (TypeError, ValueError):
                bad.append(key)
        if bad:
            missing[path] = tuple(bad)
            return None
        return out

    def finite(*arrays: np.ndarray) -> np.ndarray:
        valid = np.ones(n, dtype=bool)
        for arr in arrays:
            valid &= np.isfinite(arr)
        return valid

    def against(w1: np.ndarray, w2: np.ndarray) -> np.ndarray:
        return (w1 < w2) if long else (w1 > w2)

    # ez_manage.py:40217-40258.
    vel_tf = str(_cfg(config, "NEWBORN_LOSS_KILL_VEL_TF", "") or "3m")
    vel_required = bool(_cfg(config, "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", True))
    a = load("newborn_loss_kill", (f"wt_velocity_{vel_tf}",) if vel_required else ())
    m = zeros()
    if bool(_cfg(config, "NEWBORN_LOSS_KILL_ENABLED", False)) and a is not None:
        vel_ok = np.ones(n, dtype=bool)
        valid = finite(gain, age)
        if vel_required:
            vel = a[f"wt_velocity_{vel_tf}"]
            vel_ok = (vel < 0) if long else (vel > 0)
            valid &= finite(vel)
        m = pos & ~hedge & valid & (age >= 0)
        m &= age <= float(_cfg(config, "NEWBORN_LOSS_KILL_WINDOW_MIN", 30.0))
        m &= gain <= float(_cfg(config, "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT", 0.0))
        m &= vel_ok
    masks["newborn_loss_kill"] = m

    # Shared exact no-loss/obligatory-hedge decision surface
    # (position_evaluator.py:900-1040; live ez_manage execution gate).
    reason_u = np.array([str(value).upper() for value in reason], dtype=object)
    bypass_reasons = tuple(_cfg(config, "UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS", ()))
    bypass = np.array(
        [any(str(token).upper() in text for token in bypass_reasons) for text in reason_u]
    )
    technical_bypass = bool(_cfg(config, "UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL", True)) & bypass
    unconditional_bypass = hedge | np.array(["LIQUIDATION" in text or "STRUCTURAL_RANGE_SHIFT" in text for text in reason_u])
    real_loss = gain < float(_cfg(config, "COMMISSION_BUFFER_PCT", 0.10))

    oh_use = {
        "1m": bool(_cfg(config, "OBLIGATORY_HEDGE_WT_USE_1M", False)),
        "3m": bool(_cfg(config, "OBLIGATORY_HEDGE_WT_USE_3M", True)),
        "15m": bool(_cfg(config, "OBLIGATORY_HEDGE_WT_USE_15M", False)),
        "1h": bool(_cfg(config, "OBLIGATORY_HEDGE_WT_USE_1H", True)),
    }
    oh_keys: list[str] = []
    for tf, enabled in oh_use.items():
        if enabled or tf == "15m":
            oh_keys.extend((f"wt1_{tf}", f"wt2_{tf}"))
    a = load("obligatory_hedge", oh_keys)
    oh_count = np.zeros(n, dtype=np.int8)
    user_trigger = zeros()
    hedge_fire = zeros()
    gate_enabled = bool(_cfg(config, "UNIVERSAL_NOLOSS_GATE", False))
    if gate_enabled and bool(_cfg(config, "OBLIGATORY_HEDGE_ENABLED", False)) and a is not None:
        ag: dict[str, np.ndarray] = {tf: zeros() for tf in oh_use}
        valid = np.ones(n, dtype=bool)
        for tf, enabled in oh_use.items():
            if enabled:
                w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
                ag[tf] = against(w1, w2)
                oh_count += ag[tf].astype(np.int8)
                valid &= finite(w1, w2)
        if not oh_use["15m"]:
            w1, w2 = a["wt1_15m"], a["wt2_15m"]
            ag["15m"] = against(w1, w2)
            valid &= finite(w1, w2)
        if bool(_cfg(config, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True)):
            user_trigger = ag["3m"] & (ag["15m"] | ag["1h"])
        elif bool(_cfg(config, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", False)):
            user_trigger = ag["3m"] & ag["1h"]
        elif bool(_cfg(config, "HEDGE_TRIGGER_USE_WT_3M_ALONE", False)):
            user_trigger = ag["3m"]
        else:
            user_trigger = ag["15m"] | (ag["3m"] & ag["1h"])
        eligible = pos & reduce & real_loss & ~unconditional_bypass & ~technical_bypass
        eligible &= gain <= float(_cfg(config, "OBLIGATORY_HEDGE_MIN_LOSS_PCT", -0.25))
        eligible &= ~hedge_active & valid
        required = int(_cfg(config, "OBLIGATORY_HEDGE_WT_TFS_REQUIRED", 0))
        count_ok = oh_count >= required
        hedge_fire = eligible & (sum(oh_use.values()) > 0) & (count_ok | user_trigger)
    allow_reduce = pos & reduce & (
        ~gate_enabled | ~real_loss | unconditional_bypass | technical_bypass
    )
    fallback_close = hedge_fire & ~hedge_success & bool(_cfg(config, "HEDGE_FAILED_FALLBACK_CLOSE_ENABLED", True))
    masks["noloss_allow_reduce"] = allow_reduce
    masks["obligatory_hedge_fire"] = hedge_fire
    masks["obligatory_hedge_failed_fallback_close"] = fallback_close
    masks["noloss_hold"] = pos & reduce & gate_enabled & ~allow_reduce & ~fallback_close

    # ez_manage.py:46131-46164.
    price = load("dd_bounce_stop", ("close_3m",))
    m = zeros()
    if bool(_cfg(config, "DD_BOUNCE_ENABLED", False)) and bool(_cfg(config, "DD_BOUNCE_DD_STOP_ENABLED", True)) and price is not None:
        px = price["close_3m"]
        breach = (px < dd_price) if long else (px > dd_price)
        m = pos & dd_qty & finite(px, dd_price) & (dd_price > 0) & breach
    masks["dd_bounce_stop"] = m

    # ez_manage.py:42080-42220.
    uw_keys = tuple(k for tf in ("15m", "1h", "4h", "D") for k in (f"wt1_{tf}", f"wt2_{tf}"))
    a = load("underwater_hedge_or_close", uw_keys)
    uw_count = np.zeros(n, dtype=np.int8)
    adverse15 = zeros()
    uw_eligible = zeros()
    if bool(_cfg(config, "UNDERWATER_HEDGE_OR_CLOSE_ENABLED", True)) and a is not None:
        valid = np.ones(n, dtype=bool)
        for tf in ("15m", "1h", "4h", "D"):
            w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
            have = (w1 != 0) | (w2 != 0)
            ag = against(w1, w2) & have
            uw_count += ag.astype(np.int8)
            valid &= finite(w1, w2)
            if tf == "15m":
                adverse15 = ag
        uw_eligible = pos & valid & finite(gain, uw_since) & (gain < 0) & adverse15
        uw_eligible &= uw_since >= float(_cfg(config, "UNDERWATER_HEDGE_OR_CLOSE_COOLDOWN_SEC", 60.0))
    uw_below_hedge_threshold = gain < float(
        _cfg(config, "MANDATORY_HEDGE_GAIN_THRESHOLD_PCT", -0.5)
    )
    uw_hedge_attempt = uw_eligible & ~hedge_active & uw_below_hedge_threshold
    masks["underwater_hedge_fire"] = uw_hedge_attempt & engine
    usdc = (
        is_usdc
        & bool(_cfg(config, "MICRO_SCALP_USDC_MAKER_ENABLED", False))
        & bool(_cfg(config, "UNDERWATER_HOC_USDC_MAKER_BYPASS", True))
    )
    force_close = uw_eligible & (
        (uw_hedge_attempt & (~engine | ~hedge_success))
        | (hedge_active & (usdc | (uw_count >= int(_cfg(config, "UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED", 2)))))
    )
    masks["underwater_force_close"] = force_close

    # ez_manage.py:43791-43865 — configurable WT + stochastic adverse-vote kill.
    ws_wt_required = int(_cfg(config, "WRONG_SIDE_WT_TFS_REQUIRED", 4))
    ws_k_required = int(_cfg(config, "WRONG_SIDE_K_TFS_REQUIRED", 0))
    ws_keys = (
        tuple(
            k
            for tf in ("3m", "15m", "1h", "4h", "D")
            for k in (f"wt1_{tf}", f"wt2_{tf}")
        )
        if ws_wt_required > 0
        else ()
    ) + (
        tuple(
            k
            for tf in ("3m", "15m", "1h")
            for k in (f"stoch_k_{tf}", f"stoch_d_{tf}")
        )
        if ws_k_required > 0
        else ()
    )
    a = load("wrong_side_abs_kill", ws_keys)
    ws_wt = np.zeros(n, dtype=np.int8)
    ws_k = np.zeros(n, dtype=np.int8)
    m = zeros()
    if bool(_cfg(config, "WRONG_SIDE_ABS_KILL_ENABLED", True)) and a is not None:
        valid = np.ones(n, dtype=bool)
        if ws_wt_required > 0:
            for tf in ("3m", "15m", "1h", "4h", "D"):
                w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
                have = (w1 != 0) | (w2 != 0)
                ws_wt += (against(w1, w2) & have).astype(np.int8)
                valid &= finite(w1, w2)
        if ws_k_required > 0:
            for tf in ("3m", "15m", "1h"):
                k, d = a[f"stoch_k_{tf}"], a[f"stoch_d_{tf}"]
                ws_k += (((k < d) if long else (k > d))).astype(np.int8)
                valid &= finite(k, d)
        m = pos & ~hedge & valid & finite(age)
        m &= age >= float(_cfg(config, "WRONG_SIDE_MIN_AGE_MIN", 30.0))
        m &= (ws_wt >= ws_wt_required) & (ws_k >= ws_k_required)
    masks["wrong_side_abs_kill"] = m

    # ez_manage.py:41594-41655.
    wt15_keys = ["wt1_15m", "wt2_15m"]
    if bool(_cfg(config, "PARABOLIC_PROTECTION_ENABLED", True)):
        wt15_keys.extend(("rsi_4h", "rsi_1h", "bb_pct_b_4h"))
    a = load("wt15m_against_force_hedge", wt15_keys)
    wt15 = zeros()
    parabolic = zeros()
    if bool(_cfg(config, "WT15M_AGAINST_FORCE_HEDGE_ENABLED", False)) and a is not None:
        w1, w2 = a["wt1_15m"], a["wt2_15m"]
        have = (w1 != 0) | (w2 != 0)
        wt15 = pos & have & against(w1, w2) & finite(w1, w2, wt15_since)
        wt15 &= wt15_since >= float(_cfg(config, "WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC", 30.0))
        if bool(_cfg(config, "PARABOLIC_PROTECTION_ENABLED", True)):
            r4, r1, bb4 = a["rsi_4h"], a["rsi_1h"], a["bb_pct_b_4h"]
            up = (
                (r4 >= float(_cfg(config, "PARABOLIC_RSI_4H_MIN", 70.0)))
                & (r1 >= float(_cfg(config, "PARABOLIC_RSI_1H_MIN", 65.0)))
                & (bb4 >= float(_cfg(config, "PARABOLIC_BB_PCT_B_4H_MIN", 0.90)))
            )
            down = (
                (r4 <= float(_cfg(config, "PARABOLIC_RSI_4H_MAX", 30.0)))
                & (r1 <= float(_cfg(config, "PARABOLIC_RSI_1H_MAX", 35.0)))
                & (bb4 <= float(_cfg(config, "PARABOLIC_BB_PCT_B_4H_MAX", 0.10)))
            )
            parabolic = finite(r4, r1, bb4) & (up if long else down)
    masks["wt15m_force_hedge"] = wt15 & ~hedge_active & engine
    # The live branch only force-closes an already-covered primary.  A missing
    # hedge engine or a hedge exception is logged but does not enter this close
    # branch (ez_manage.py:41624-41720).
    masks["wt15m_force_close"] = wt15 & hedge_active & ~parabolic

    return ExitReduceBatch3Result(
        masks=MappingProxyType(masks),
        missing_arrays=MappingProxyType(missing),
        obligatory_wt_against_count=oh_count,
        underwater_htf_against_count=uw_count,
        wrong_side_wt_count=ws_wt,
        wrong_side_stoch_count=ws_k,
    )


def consume_lifecycle_actions(
    result: ExitReduceBatch3Result,
    candidates: Mapping[str, Any],
) -> LifecycleActionResult:
    """Apply batch-3 decisions at the v12 lifecycle action boundary.

    ``ENTRY``, ``REENTRY``, and ``AUGMENT`` are explicit pass-throughs: none of
    this tranche's source fields filters those admissions.  ``REDUCE`` and an
    ordinary ``EXIT`` consume the shared no-loss decision.  Safety close and
    hedge paths are independent source actions and are exposed separately so a
    primary simulator cannot silently reduce them to hookup counters.
    """

    n = len(next(iter(result.masks.values()))) if result.masks else 0

    def candidate(action: str) -> np.ndarray:
        if action not in candidates:
            raise KeyError(f"missing lifecycle candidate {action!r}")
        return _state(candidates[action], n, f"candidates[{action!r}]", bool)

    entry = candidate("ENTRY")
    reentry = candidate("REENTRY")
    augment = candidate("AUGMENT")
    reduce = candidate("REDUCE")
    exit_ = candidate("EXIT")
    stop = candidate("STOP")
    m = result.masks
    forced_close = (
        m["newborn_loss_kill"]
        | m["obligatory_hedge_failed_fallback_close"]
        | m["underwater_force_close"]
        | m["wrong_side_abs_kill"]
        | m["wt15m_force_close"]
    )
    action_masks = {
        "ENTRY": entry,
        "REENTRY": reentry,
        "AUGMENT": augment,
        "REDUCE": (reduce & m["noloss_allow_reduce"]) | m["dd_bounce_stop"],
        "EXIT": (exit_ & m["noloss_allow_reduce"]) | forced_close,
        "STOP": (stop & m["noloss_allow_reduce"]) | forced_close,
        "HEDGE": (
            m["obligatory_hedge_fire"]
            | m["underwater_hedge_fire"]
            | m["wt15m_force_hedge"]
        ),
        "HOLD": m["noloss_hold"],
    }
    return LifecycleActionResult(masks=MappingProxyType(action_masks))


__all__ = [
    "ExitReduceBatch3Result",
    "LifecycleActionResult",
    "consume_lifecycle_actions",
    "evaluate_gap_batch3",
]
