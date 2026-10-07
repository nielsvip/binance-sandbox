"""Source-exact v12 EXIT/REDUCE/STOP contracts, batch 5.

The module is deliberately disjoint from ``v12_quick_engine``.  Persisted
indicator inputs are never substituted across timeframes.  Position/portfolio
state that live reads from objects is an explicit caller input.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from ordinary_ladder_contract import Completed4hE02, e02_exit_signal
from tradier_entry_contract import dc_4h_boundary_breached


@dataclass(frozen=True)
class ExitReduceBatch5Result:
    masks: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]
    frozen_dc_level: np.ndarray
    frozen_bb_level: np.ndarray


@dataclass(frozen=True)
class LifecycleActionResult:
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


def evaluate_gap_batch5(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_minutes: Any,
    current_price: Any = np.nan,
    position_active: Any = True,
    position_above_min_qty: Any = True,
    last_augmentation_age_seconds: Any = np.inf,
    has_last_augmentation: Any = False,
    current_qty: Any = 1.0,
    start_position_qty: Any = 0.1,
    stop_signal: Any = False,
    manage_reduce: Any = True,
    recently_reduced: Any = False,
    strict_no_loss_account: Any = False,
    hedge_account: Any = False,
    has_covering_hedge: Any = False,
    unhedged_age_minutes: Any = 0.0,
    is_hedge: Any = False,
    original_gain_pct: Any = 0.0,
    original_max_loss_since_hedge_pct: Any = 0.0,
    original_position_gone: Any = False,
    hedge_max_gain_pct: Any = 0.0,
    trend_account: Any = False,
    delta_exit_signal: Any = False,
    initial_frozen_dc_level: float = 0.0,
    initial_frozen_bb_level: float = 0.0,
    bar_count: int | None = None,
) -> ExitReduceBatch5Result:
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
    price = _state(current_price, n, "current_price", float)
    active = _state(position_active, n, "position_active", bool)
    above = _state(position_above_min_qty, n, "position_above_min_qty", bool)
    aug_age = _state(last_augmentation_age_seconds, n, "last_augmentation_age_seconds", float)
    has_aug = _state(has_last_augmentation, n, "has_last_augmentation", bool)
    qty = _state(current_qty, n, "current_qty", float)
    start_qty = _state(start_position_qty, n, "start_position_qty", float)
    stop = _state(stop_signal, n, "stop_signal", bool)
    manage = _state(manage_reduce, n, "manage_reduce", bool)
    recent = _state(recently_reduced, n, "recently_reduced", bool)
    strict = _state(strict_no_loss_account, n, "strict_no_loss_account", bool)
    hedge_acct = _state(hedge_account, n, "hedge_account", bool)
    covered = _state(has_covering_hedge, n, "has_covering_hedge", bool)
    unhedged_age = _state(unhedged_age_minutes, n, "unhedged_age_minutes", float)
    hedge = _state(is_hedge, n, "is_hedge", bool)
    orig_gain = _state(original_gain_pct, n, "original_gain_pct", float)
    orig_worst = _state(original_max_loss_since_hedge_pct, n, "original_max_loss_since_hedge_pct", float)
    orig_gone = _state(original_position_gone, n, "original_position_gone", bool)
    hedge_peak = _state(hedge_max_gain_pct, n, "hedge_max_gain_pct", float)
    trend = _state(trend_account, n, "trend_account", bool)
    delta_exit = _state(delta_exit_signal, n, "delta_exit_signal", bool)
    pos = active & above
    masks: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str]) -> dict[str, np.ndarray] | None:
        out: dict[str, np.ndarray] = {}
        bad: list[str] = []
        for key in dict.fromkeys(keys):
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
        for array in arrays:
            valid &= np.isfinite(array)
        return valid

    # Shared live/vector WT-4h velocity predicate (position_evaluator.py:637-662).
    a = load("wt_4h_velocity_exit", ("wt_velocity_4h", "stoch_k_3m", "stoch_k_15m"))
    wt4 = zeros()
    if bool(_cfg(config, "WT_4H_VEL_EXIT_ENABLED", True)) and a is not None:
        threshold = float(_cfg(config, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0) if long else _cfg(config, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0))
        adverse = a["wt_velocity_4h"] < threshold if long else a["wt_velocity_4h"] > threshold
        req_profit = bool(_cfg(config, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True))
        profit_ok = gain >= float(_cfg(config, "COMMISSION_BUFFER_PCT", 0.10)) if req_profit else np.ones(n, bool)
        if bool(_cfg(config, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)):
            hi = float(_cfg(config, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0))
            lo = float(_cfg(config, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0))
            k_ok = ((a["stoch_k_3m"] >= hi) | (a["stoch_k_15m"] >= hi)) if long else ((a["stoch_k_3m"] <= lo) | (a["stoch_k_15m"] <= lo))
        else:
            k_ok = np.ones(n, bool)
        wt4 = pos & finite(*a.values(), gain, age) & adverse & (age * 60.0 > 360.0) & profit_ok & k_ok
    masks["wt_4h_velocity_exit"] = wt4

    # Crypto loss-protection helper, exposed as a cross-reduce/stop blocking mask.
    loss_threshold = float(_cfg(config, "NEW_POSITION_MAX_LOSS_THRESHOLD", -0.7))
    protection = pos & finite(gain) & (gain >= loss_threshold)
    if bool(_cfg(config, "ENABLE_LOSS_PROTECTION", True)):
        protection &= gain <= 0.12
    else:
        protection &= has_aug & finite(aug_age) & (aug_age >= 0.0)
        protection &= aug_age < float(_cfg(config, "NEW_POSITION_MIN_AGE_SECONDS", 180.0))
    masks["loss_protection_filter"] = protection

    # RIDICULOUS_HOLD_GUARD (ez_manage.py:41746-41831).
    ridiculous = zeros()
    if bool(_cfg(config, "RIDICULOUS_HOLD_GUARD_ENABLED", True)):
        loss_hit = gain <= float(_cfg(config, "RIDICULOUS_LOSS_PCT", -15.0))
        if bool(_cfg(config, "RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG", True)):
            hold_gain = gain >= 0.0
        else:
            hold_gain = gain < 0.0
        hold_hit = hold_gain & (age > float(_cfg(config, "RIDICULOUS_HOLD_HOURS", 48.0)) * 60.0)
        ridiculous = pos & finite(gain, age) & (loss_hit | hold_hit)
    masks["ridiculous_hold_guard_exit"] = ridiculous

    # Stateful frozen Donchian activation stop (ez_manage.py:40628-40702).
    dc_tf = str(_cfg(config, "FROZEN_ACTIVATION_TF", "4h") or "4h")
    dc_key = f"dc_low_{dc_tf}" if long else f"dc_high_{dc_tf}"
    a = load("frozen_dc_stop", (dc_key,))
    frozen_dc = np.zeros(n, dtype=float)
    frozen_value = float(initial_frozen_dc_level)
    frozen_stop = zeros()
    if bool(_cfg(config, "FROZEN_ACTIVATION_STOP_ENABLED", True)) and a is not None:
        floor = float(_cfg(config, "FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO", -10.0))
        for i in range(n):
            if not active[i]:
                frozen_value = 0.0
                continue
            if frozen_value <= 0 and np.isfinite(a[dc_key][i]) and a[dc_key][i] > 0:
                frozen_value = float(a[dc_key][i])
            frozen_dc[i] = frozen_value
            breach = frozen_value > 0 and gain[i] < 0 and ((price[i] < frozen_value) if long else (price[i] > frozen_value))
            frozen_stop[i] = bool(pos[i] and np.isfinite(gain[i]) and np.isfinite(price[i]) and (gain[i] <= floor or breach))
    masks["frozen_dc_stop"] = frozen_stop

    # Stateful frozen Bollinger stop (ez_manage.py:40710-40744).
    bb_tf = str(_cfg(config, "BB_FROZEN_STOP_TF", "1h") or "1h")
    bb_field = str(_cfg(config, "BB_FROZEN_STOP_FIELD", "lower") or "lower")
    if bb_field in {"lower", "upper"}:
        bb_field = "lower" if long else "upper"
    bb_key = f"bb_{bb_field}_{bb_tf}"
    a = load("bb_frozen_stop", (bb_key,))
    frozen_bb = np.zeros(n, dtype=float)
    frozen_value = float(initial_frozen_bb_level)
    bb_stop = zeros()
    if bool(_cfg(config, "BB_FROZEN_STOP_ENABLED", False)) and a is not None:
        for i in range(n):
            if not active[i]:
                frozen_value = 0.0
                continue
            if frozen_value <= 0 and np.isfinite(a[bb_key][i]) and a[bb_key][i] > 0:
                frozen_value = float(a[bb_key][i])
            frozen_bb[i] = frozen_value
            bb_stop[i] = bool(pos[i] and gain[i] < 0 and frozen_value > 0 and np.isfinite(price[i]) and ((price[i] < frozen_value) if long else (price[i] > frozen_value)))
    masks["bb_frozen_stop"] = bb_stop

    # Tradier hard 4h Donchian emergency channel (legacy setting name retained).
    a = load("emergency_dc4h_exit", ("dc_low_4h" if long else "dc_high_4h",))
    emergency = zeros()
    if bool(_cfg(config, "EXIT_EMERGENCY_DC1H_ENABLED", False)) and a is not None:
        indicators = {key: 0.0 for key in ("dc_low_4h", "dc_high_4h")}
        for i in range(n):
            indicators["dc_low_4h" if long else "dc_high_4h"] = a["dc_low_4h" if long else "dc_high_4h"][i]
            emergency[i] = pos[i] and dc_4h_boundary_breached(price[i], side, indicators, require_level=False)[0]
    masks["emergency_dc4h_exit"] = emergency

    # Completed-parent E02, consuming every causal 4h token once.
    e02_keys = ("e02_source_close_ts_4h", "e02_availability_ts", "e02_completed_close_4h", "e02_prior_low_4h_n30", "e02_prior_high_4h_n30")
    a = load("lr_band_e02_exit", e02_keys)
    e02 = zeros()
    seen: set[tuple[str, int]] = set()
    if bool(_cfg(config, "LR_BAND_E02_EXIT_ENABLED", False)) and a is not None:
        for i in range(n):
            if not all(np.isfinite(a[key][i]) for key in e02_keys):
                continue
            event = Completed4hE02(int(a[e02_keys[0]][i]), int(a[e02_keys[1]][i]), a[e02_keys[2]][i], a[e02_keys[3]][i], a[e02_keys[4]][i])
            e02[i] = bool(pos[i] and e02_exit_signal(event, side=side, seen=seen) is not None)
    masks["lr_band_e02_exit"] = e02

    # Stateful selected-TF lower-high/lower-low structure transition.
    struct_tf = str(_cfg(config, "EXIT_STRUCT_TF", "None") or "None")
    structural = zeros()
    if struct_tf != "None":
        keys = (f"open_{struct_tf}", f"high_{struct_tf}_prev", f"low_{struct_tf}_prev")
        a = load("selected_tf_structure_exit", keys)
        if a is not None:
            last_open = last_hi = last_lo = None
            for i in range(n):
                op, hi, lo = (a[key][i] for key in keys)
                if not active[i] or not np.isfinite([op, hi, lo]).all() or min(op, hi, lo) <= 0:
                    if not active[i]: last_open = last_hi = last_lo = None
                    continue
                if last_open is None:
                    last_open, last_hi, last_lo = op, hi, lo
                elif abs(op - last_open) > 1e-8:
                    structural[i] = (hi < last_hi and lo < last_lo) if long else (hi > last_hi and lo > last_lo)
                    last_open, last_hi, last_lo = op, hi, lo
    masks["selected_tf_structure_exit"] = structural & pos

    # Hedge scalp close rules A-D (ez_positions_quick.py:5846-5907).
    wt1m = npz.get("wt_bullish_1m")
    wt1m_ok = wt1m is not None and np.asarray(wt1m).ndim == 1 and len(np.asarray(wt1m)) == n
    if not wt1m_ok:
        missing["hedge_scalp_close_rule_d"] = ("wt_bullish_1m",)
        wt1m_arr = np.zeros(n, dtype=bool)
        wt1m_valid = np.zeros(n, dtype=bool)
    else:
        raw = np.asarray(wt1m)
        wt1m_valid = np.asarray([value is not None and not (isinstance(value, float) and np.isnan(value)) for value in raw])
        wt1m_arr = raw.astype(bool)
    recovery = orig_gain - orig_worst
    rule_a = (gain >= 0.2) & (recovery >= 0.0)
    rule_b = (recovery >= 0.3) & (orig_gain < 0.0)
    max_age = float(_cfg(config, "HEDGE_SCALP_MAX_AGE_MIN", 5.0) or 5.0)
    c_floor = 2.0 * float(_cfg(config, "COMMISSION_BUFFER_PCT", 0.10))
    c_ok = (orig_gain + gain >= c_floor) if bool(_cfg(config, "HEDGE_SCALP_C_REQUIRE_COMBINED_NONNEG", True)) else np.ones(n, bool)
    rule_c = (gain < -0.3) & (age > max_age) & c_ok
    rule_d = wt1m_valid & ((~wt1m_arr) if long else wt1m_arr)
    scalp_close = zeros()
    if bool(_cfg(config, "HEDGE_CLOSE_SCALP_MODE", False)):
        scalp_close = pos & hedge & finite(gain, age, orig_gain, orig_worst) & (rule_a | rule_b | rule_c | rule_d)
    masks["hedge_scalp_close"] = scalp_close

    # Hedge max-age, decay, and trend-account expiry paths.
    commission = float(_cfg(config, "COMMISSION_BUFFER_PCT", 0.10))
    hedge_max_hours = float(_cfg(config, "HEDGE_MAX_AGE_HOURS", 0.0))
    req_hedge_profit = bool(_cfg(config, "HEDGE_MAX_AGE_KILL_REQUIRE_PROFIT", True))
    masks["hedge_max_age_exit"] = pos & hedge & finite(age, gain) & (hedge_max_hours > 0) & (age >= hedge_max_hours * 60.0) & ((gain >= commission) if req_hedge_profit else True)
    masks["hedge_decay_nuke_exit"] = pos & hedge & bool(_cfg(config, "HEDGE_DECAY_NUKE_ENABLED", False)) & finite(gain, hedge_peak) & (hedge_peak > 1.0) & (gain > 0.0) & (gain <= 0.5)
    trend_seconds = float(_cfg(config, "TREND_HEDGE_MAX_SEC", 0.0))
    masks["trend_hedge_expiry_exit"] = pos & hedge & trend & finite(age) & (trend_seconds > 0) & (age * 60.0 > trend_seconds)

    # 15m origin-favouring hedge close with the optional 3m/origin-loss veto.
    a = load("hedge_bandaid_off_exit", ("wt1_15m", "wt2_15m", "wt1_3m", "wt2_3m"))
    bandaid = zeros()
    if bool(_cfg(config, "HEDGE_BANDAID_OFF_ENABLED", False)) and a is not None:
        favors_origin = (a["wt1_15m"] > a["wt2_15m"]) if long else (a["wt1_15m"] < a["wt2_15m"])
        with_hedge = (a["wt1_3m"] < a["wt2_3m"]) if long else (a["wt1_3m"] > a["wt2_3m"])
        has_3m = (a["wt1_3m"] != 0) | (a["wt2_3m"] != 0)
        origin_in_loss = orig_gain < float(_cfg(config, "BANDAID_OFF_LOSER_RECOVER_PCT", -0.25))
        veto = with_hedge & origin_in_loss & has_3m if bool(_cfg(config, "HEDGE_BANDAID_OFF_REQUIRE_WT_3M_FLIP", True)) else zeros()
        bandaid = pos & hedge & finite(*a.values(), orig_gain) & favors_origin & ~veto
    masks["hedge_bandaid_off_exit"] = bandaid

    # Hedge cleanup R6: switch changes required WT vote 3/3 -> 2/3; fee gate remains absolute.
    a = load("hedge_cleanup_r6_exit", tuple(f"wt{x}_{tf}" for tf in ("3m", "15m", "1h") for x in (1, 2)))
    cleanup = zeros()
    if a is not None:
        wrong = {tf: ((a[f"wt1_{tf}"] < a[f"wt2_{tf}"]) if long else (a[f"wt1_{tf}"] > a[f"wt2_{tf}"])) for tf in ("3m", "15m", "1h")}
        wt_fire = (wrong["3m"] & wrong["1h"]) if bool(_cfg(config, "HEDGE_EXIT_BYPASS_NOLOSS", True)) else (wrong["3m"] & wrong["15m"] & wrong["1h"])
        main_recovered = orig_gone | (orig_gain >= commission)
        cleanup = pos & hedge & finite(*a.values(), gain, orig_gain) & (wt_fire | main_recovered) & (gain >= commission)
    masks["hedge_cleanup_r6_exit"] = cleanup

    # STOP_FUNCTIONS_KILL branch including hedge-account escape.
    a = load("stop_functions_kill_reduce", ("stoch_k_3m", "stoch_d_3m"))
    kill = zeros()
    fast_kill = bool(_cfg(config, "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED", False)) & (gain < -5.0) & (qty > 5.0 * start_qty)
    technical = zeros() if a is None else (age >= 5.0) & ((a["stoch_k_3m"] < a["stoch_d_3m"]) if long else (a["stoch_k_3m"] > a["stoch_d_3m"]))
    eligible = pos & stop & manage & ~recent & ~strict & (qty > 0.5 * start_qty) & (fast_kill | technical)
    hedge_block = hedge_acct & (gain < 0.17)
    escape = bool(_cfg(config, "LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED", False)) & ~covered & (gain < -15.0) & (unhedged_age > 30.0)
    kill = eligible & (~hedge_block | escape)
    masks["stop_functions_kill_reduce"] = kill

    # Delta exit itself is supplied by the causal delta engine; this setting only arms reentry.
    masks["delta_exit_mandatory_reentry_arm"] = pos & delta_exit & bool(_cfg(config, "DELTA_EXIT_MANDATORY_REENTRY_ENABLED", False))

    return ExitReduceBatch5Result(
        masks=MappingProxyType(masks),
        missing_arrays=MappingProxyType(missing),
        frozen_dc_level=frozen_dc,
        frozen_bb_level=frozen_bb,
    )


def consume_lifecycle_actions(result: ExitReduceBatch5Result) -> LifecycleActionResult:
    m = result.masks
    source_exits = (
        m["wt_4h_velocity_exit"] | m["ridiculous_hold_guard_exit"]
        | m["frozen_dc_stop"] | m["bb_frozen_stop"] | m["emergency_dc4h_exit"]
        | m["lr_band_e02_exit"] | m["selected_tf_structure_exit"]
        | m["hedge_scalp_close"] | m["hedge_max_age_exit"]
        | m["hedge_decay_nuke_exit"] | m["trend_hedge_expiry_exit"]
        | m["hedge_bandaid_off_exit"] | m["hedge_cleanup_r6_exit"]
    )
    zeros = np.zeros_like(source_exits)
    return LifecycleActionResult(masks=MappingProxyType({
        "ENTRY": zeros,
        "REENTRY": zeros,
        "AUGMENT": zeros,
        "REDUCE": m["stop_functions_kill_reduce"] & ~m["loss_protection_filter"],
        "EXIT": source_exits,
        "STOP": source_exits | (m["stop_functions_kill_reduce"] & ~m["loss_protection_filter"]),
        "MANDATORY_REENTRY_ARM": m["lr_band_e02_exit"] | m["emergency_dc4h_exit"] | m["delta_exit_mandatory_reentry_arm"],
    }))


__all__ = ["ExitReduceBatch5Result", "LifecycleActionResult", "consume_lifecycle_actions", "evaluate_gap_batch5"]
