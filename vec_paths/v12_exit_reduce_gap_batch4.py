"""Source-exact v12 EXIT/REDUCE/STOP contracts, batch 4.

All indicator decisions consume native persisted arrays.  Stateful trails run
sequentially over completed-source timestamps.  Missing selected-timeframe data
disables only its owning path and is reported; no timeframe substitution or
fabricated outcome is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from protective_trail_contract import ProtectiveTrailParams, protective_trail_step


@dataclass(frozen=True)
class ExitReduceBatch4Result:
    masks: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]
    tradier_wt_against_count: np.ndarray
    mu_htf_hit_count: np.ndarray
    mu_roll_count: np.ndarray
    mu_ltf_fall_count: np.ndarray
    dyn_trail_level: np.ndarray
    dyn_trail_armed: np.ndarray


@dataclass(frozen=True)
class LifecycleActionResult:
    masks: Mapping[str, np.ndarray]


def _cfg(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)


def _cfg_alias(config: Any, overlay: str, base: str, default: Any) -> Any:
    sentinel = object()
    value = _cfg(config, overlay, sentinel)
    return _cfg(config, base, default) if value is sentinel else value


def _state(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=dtype)
    if arr.ndim == 0:
        return np.full(n, arr.item(), dtype=dtype)
    if arr.ndim != 1 or len(arr) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return arr


def evaluate_gap_batch4(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_minutes: Any,
    entry_price: Any,
    max_gain_pct: Any,
    position_active: Any = True,
    position_above_min_qty: Any = True,
    symbol: str = "",
    is_scalp_v3: Any = False,
    orderbook_ask_wall_pct: Any = np.nan,
    orderbook_bid_wall_pct: Any = np.nan,
    dyn_initial_level: float = 0.0,
    dyn_initial_armed: bool = False,
    bar_count: int | None = None,
) -> ExitReduceBatch4Result:
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
    entry = _state(entry_price, n, "entry_price", float)
    peak = _state(max_gain_pct, n, "max_gain_pct", float)
    active = _state(position_active, n, "position_active", bool)
    above = _state(position_above_min_qty, n, "position_above_min_qty", bool)
    scalp = _state(is_scalp_v3, n, "is_scalp_v3", bool)
    ask_wall = _state(orderbook_ask_wall_pct, n, "orderbook_ask_wall_pct", float)
    bid_wall = _state(orderbook_bid_wall_pct, n, "orderbook_bid_wall_pct", float)
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
        for arr in arrays:
            valid &= np.isfinite(arr)
        return valid

    def against(w1: np.ndarray, w2: np.ndarray) -> np.ndarray:
        return (w1 < w2) if long else (w1 > w2)

    # Shared Bottom-A completed-parent state machine (protective_trail_contract.py).
    arm_tf = str(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME", "4h"))
    trail_tf = str(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME", "5m"))
    bottom_params = ProtectiveTrailParams(
        arm_timeframe=arm_tf,
        trail_timeframe=trail_tf,
        mode=str(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_MODE", "STDEV")).upper(),
        break_buffer_atr=float(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR", 0.5)),
        distance_mult=float(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT", 1.0)),
        lookback=int(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK", 6)),
    )
    bottom_keys = tuple(
        key
        for tf in dict.fromkeys((arm_tf, trail_tf))
        for key in (f"timestamp_{tf}", f"close_{tf}", f"high_{tf}", f"low_{tf}", f"atr_{tf}")
    )
    a = load("bottom_a_protective_trail", bottom_keys)
    bottom = zeros()
    if bool(_cfg(config, "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED", False)) and a is not None:
        try:
            bottom_params.validate()
            trail_state: dict[str, Any] = {}
            for i in range(n):
                values: dict[str, float] = {}
                for tf in dict.fromkeys((arm_tf, trail_tf)):
                    values[f"timestamp_{tf}"] = float(a[f"timestamp_{tf}"][i])
                    for field in ("close", "high", "low", "atr"):
                        values[f"{field}_{tf}"] = float(a[f"{field}_{tf}"][i])
                values["_tick_ts"] = max(values[f"timestamp_{arm_tf}"], values[f"timestamp_{trail_tf}"])
                signal = protective_trail_step(
                    trail_state,
                    values,
                    side=side,
                    active=bool(pos[i]),
                    params=bottom_params,
                )
                bottom[i] = signal is not None
        except (TypeError, ValueError):
            missing["bottom_a_protective_trail"] = ("INVALID_RECIPE",)
            bottom[:] = False
    masks["bottom_a_protective_trail_exit"] = bottom

    # Tradier direct selected-TF WT crossing exit (tradier_manage.py:12647-12682).
    direct_tf = str(_cfg(config, "MTF_WT_CROSS_EXIT_TF", "15m") or "15m")
    direct_keys = (
        f"wt1_{direct_tf}", f"wt2_{direct_tf}",
        f"wt1_{direct_tf}_prev", f"wt2_{direct_tf}_prev",
    )
    a = load("mtf_wt_cross_direct", direct_keys)
    direct = zeros()
    if bool(_cfg(config, "MTF_WT_CROSS_EXIT_DIRECT_ENABLED", False)) and a is not None:
        w1, w2 = a[f"wt1_{direct_tf}"], a[f"wt2_{direct_tf}"]
        p1, p2 = a[f"wt1_{direct_tf}_prev"], a[f"wt2_{direct_tf}_prev"]
        direct = pos & finite(w1, w2, p1, p2)
        direct &= ((w1 < w2) & (p1 >= p2)) if long else ((w1 > w2) & (p1 <= p2))
    masks["mtf_wt_cross_direct_exit"] = direct

    # Configured Tradier WT adverse-vote exit (tradier_manage.py:12355-12376).
    wt_tfs_raw = str(_cfg_alias(config, "TRADIER_WT_EXIT_TFS_TRADIER", "WT_EXIT_TFS_TRADIER", "5m+15m+1h+4h+D"))
    wt_tfs = tuple(tf.strip() for tf in wt_tfs_raw.replace("+", ",").split(",") if tf.strip())
    a = load("tradier_wt_vote_exit", tuple(k for tf in wt_tfs for k in (f"wt1_{tf}", f"wt2_{tf}")))
    wt_count = np.zeros(n, dtype=np.int8)
    wt_vote = zeros()
    if a is not None and wt_tfs:
        valid = np.ones(n, dtype=bool)
        for tf in wt_tfs:
            w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
            wt_count += against(w1, w2).astype(np.int8)
            valid &= finite(w1, w2)
        needed = int(_cfg_alias(config, "TRADIER_WT_EXIT_MIN_TFS_TRADIER", "WT_EXIT_MIN_TFS_TRADIER", 4))
        wt_vote = pos & valid & (needed > 0) & (wt_count >= needed)
    masks["tradier_wt_vote_exit"] = wt_vote

    # Structural newborn Donchian stop.  The later emergency branch in live is
    # unreachable because its final condition still requires age<=newborn_age.
    newborn_field = str(_cfg(config, "NEWBORN_DC_STOP_FIELD", "dc_low4_5m") or "dc_low4_5m")
    if not long:
        newborn_field = newborn_field.replace("low", "high")
    a = load("newborn_dc_stop", ("close_5m", newborn_field))
    newborn = zeros()
    if bool(_cfg(config, "NEWBORN_DC_STOP_ENABLED", True)) and a is not None:
        price, level = a["close_5m"], a[newborn_field]
        breach = (price <= level) if long else (price >= level)
        newborn = pos & finite(price, level, age) & (level > 0) & breach
        newborn &= age <= float(_cfg(config, "NEWBORN_DC_STOP_MAX_AGE_MIN", 20.0))
    masks["newborn_dc_stop"] = newborn

    # Time-based breakeven protection (tradier_manage.py:13980-14011).
    a = load(
        "breakeven_after_bars",
        ("close_5m", "wt1_15m", "wt2_15m", "wt1_15m_prev", "wt2_15m_prev"),
    )
    breakeven = zeros()
    if bool(_cfg(config, "BREAKEVEN_EXIT_AFTER_BARS_ENABLED", False)) and a is not None:
        price = a["close_5m"]
        calc_gain = gain.copy()
        valid_entry = entry > 0
        calc_gain[valid_entry] = (
            ((price[valid_entry] - entry[valid_entry]) / entry[valid_entry] * 100.0)
            if long else
            ((entry[valid_entry] - price[valid_entry]) / entry[valid_entry] * 100.0)
        )
        buffer = abs(float(_cfg(config, "BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT", 0.05) or 0.05))
        tf = str(_cfg(config, "BREAKEVEN_EXIT_AFTER_BARS_TF", "15m") or "15m")
        minutes = {"1m": 1.0, "5m": 5.0, "15m": 15.0, "1h": 60.0, "4h": 240.0, "D": 1440.0}.get(tf, 15.0)
        bars = max(1, int(_cfg(config, "BREAKEVEN_EXIT_AFTER_BARS", 8) or 8))
        w1, w2 = a["wt1_15m"], a["wt2_15m"]
        p1, p2 = a["wt1_15m_prev"], a["wt2_15m_prev"]
        structure = ((w1 > p1) | (w2 > p2)) if long else ((w1 < p1) | (w2 < p2))
        if not bool(_cfg(config, "BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE", True)):
            structure = np.ones(n, dtype=bool)
        breakeven = pos & finite(price, entry, peak, age, calc_gain, w1, w2, p1, p2)
        breakeven &= (age >= bars * minutes) & (peak > buffer)
        breakeven &= (calc_gain >= -buffer) & (calc_gain <= buffer) & structure
    masks["breakeven_after_bars_exit"] = breakeven

    # MU_LONG-only peak rollover (tradier_manage.py:12723-12791).
    htf_tfs = tuple(tf.strip() for tf in str(_cfg(config, "MU_CORRECTION_HTF_TFS", "1h+4h") or "1h+4h").replace(",", "+").split("+") if tf.strip() in {"1h", "4h"}) or ("1h", "4h")
    ltf_tfs = tuple(tf.strip() for tf in str(_cfg(config, "MU_CORRECTION_LTF_FALL_TFS", "5m+15m") or "5m+15m").replace(",", "+").split("+") if tf.strip())
    mu_keys = tuple(
        [k for tf in htf_tfs for k in (f"stoch_k_{tf}", f"rsi_{tf}", f"high_{tf}", f"high_{tf}_prev", f"close_{tf}", f"close_{tf}_prev")]
        + [k for tf in ltf_tfs for k in (f"stoch_k_{tf}", f"stoch_k_{tf}_prev", f"wt1_{tf}", f"wt2_{tf}")]
    )
    a = load("mu_correction_exit", mu_keys)
    mu_htf = np.zeros(n, dtype=np.int8)
    mu_roll = np.zeros(n, dtype=np.int8)
    mu_ltf = np.zeros(n, dtype=np.int8)
    mu = zeros()
    allowed_symbols = {s.strip().upper() for s in str(_cfg(config, "MU_CORRECTION_SYMBOLS", "MU") or "MU").replace(",", "+").split("+") if s.strip()}
    if bool(_cfg(config, "MU_CORRECTION_EXIT_ENABLED", False)) and long and str(symbol).upper() in allowed_symbols and a is not None:
        valid = np.ones(n, dtype=bool)
        k_min = float(_cfg(config, "MU_CORRECTION_HTF_K_MIN", 80.0) or 80.0)
        rsi_min = float(_cfg(config, "MU_CORRECTION_HTF_RSI_MIN", 60.0) or 60.0)
        high_required = bool(_cfg(config, "MU_CORRECTION_REQUIRE_HIGH_REVERSAL", True))
        close_required = bool(_cfg(config, "MU_CORRECTION_REQUIRE_CLOSE_REVERSAL", True))
        for tf in htf_tfs:
            k, rsi = a[f"stoch_k_{tf}"], a[f"rsi_{tf}"]
            high, high_prev = a[f"high_{tf}"], a[f"high_{tf}_prev"]
            close, close_prev = a[f"close_{tf}"], a[f"close_{tf}_prev"]
            mu_htf += ((k >= k_min) | (rsi >= rsi_min)).astype(np.int8)
            high_rev = np.ones(n, bool) if not high_required else ((high > 0) & (high_prev > 0) & (high >= high_prev))
            close_rev = np.ones(n, bool) if not close_required else ((close > 0) & (close_prev > 0) & (close <= close_prev))
            mu_roll += (high_rev & close_rev).astype(np.int8)
            valid &= finite(k, rsi, high, high_prev, close, close_prev)
        for tf in ltf_tfs:
            k, kp, w1, w2 = (a[f"stoch_k_{tf}"], a[f"stoch_k_{tf}_prev"], a[f"wt1_{tf}"], a[f"wt2_{tf}"])
            mu_ltf += ((k < kp) | (((w1 != 0) | (w2 != 0)) & (w1 < w2))).astype(np.int8)
            valid &= finite(k, kp, w1, w2)
        htf_need = max(1, int(_cfg(config, "MU_CORRECTION_HTF_MIN_TFS", 1) or 1))
        ltf_need = max(1, int(_cfg(config, "MU_CORRECTION_LTF_FALL_MIN_TFS", 2) or 2))
        mu = pos & valid & finite(gain) & (gain >= float(_cfg(config, "MU_CORRECTION_MIN_GAIN_PCT", 0.0) or 0.0))
        mu &= (mu_htf >= htf_need) & (mu_roll >= htf_need) & (mu_ltf >= ltf_need)
    masks["mu_correction_exit"] = mu

    # Crypto SCALP_V3 protective technical exits (ez_positions_quick.py:18260-18435).
    scalp_keys = (
        "high_1m", "high_1m_prev", "low_1m", "low_1m_prev",
        "high_3m", "high_3m_prev", "low_3m", "low_3m_prev",
        "stoch_k_1m", "stoch_k_1m_prev", "stoch_k_3m", "stoch_k_3m_prev",
        "stoch_k_15m", "wt1_3m", "wt2_3m",
    )
    a = load("scalp_v3_protective_exit", scalp_keys)
    scalp_exit = zeros()
    scalp_kob = zeros()
    if bool(_cfg(config, "SCALP_V3_PROTECTIVE_EXIT_ENABLED", True)) and a is not None:
        h1, hp1, l1, lp1 = a["high_1m"], a["high_1m_prev"], a["low_1m"], a["low_1m_prev"]
        h3, hp3, l3, lp3 = a["high_3m"], a["high_3m_prev"], a["low_3m"], a["low_3m_prev"]
        k1, kp1 = a["stoch_k_1m"], a["stoch_k_1m_prev"]
        k3, kp3, k15 = a["stoch_k_3m"], a["stoch_k_3m_prev"], a["stoch_k_15m"]
        w1, w2 = a["wt1_3m"], a["wt2_3m"]
        drop = float(_cfg(config, "SCALP_V3_PROTECTIVE_K_DROP_MIN", 5.0))
        if long:
            structure = ((hp1 > 0) & (h1 < hp1)) | ((lp1 > 0) & (l1 < lp1)) | ((hp3 > 0) & (h3 < hp3)) | ((lp3 > 0) & (l3 < lp3))
            momentum = (k1 < kp1 - drop) | (k3 < kp3 - drop) | (((w1 != 0) | (w2 != 0)) & (w1 < w2))
            ordinary_wall = np.isfinite(ask_wall) & (ask_wall < float(_cfg(config, "SCALP_V3_OB_WALL_TOO_CLOSE_PCT", 0.5) or 0.5)) & (gain > 0)
        else:
            structure = ((hp1 > 0) & (h1 > hp1)) | ((lp1 > 0) & (l1 > lp1)) | ((hp3 > 0) & (h3 > hp3)) | ((lp3 > 0) & (l3 > lp3))
            momentum = (k1 > kp1 + drop) | (k3 > kp3 + drop) | (((w1 != 0) | (w2 != 0)) & (w1 > w2))
            ordinary_wall = np.isfinite(bid_wall) & (bid_wall < float(_cfg(config, "SCALP_V3_OB_WALL_TOO_CLOSE_PCT", 0.5) or 0.5)) & (gain > 0)
        if bool(_cfg(config, "SCALP_V3_K_OB_EXIT_ENABLED", True)):
            wall_limit = float(_cfg(config, "SCALP_V3_K_OB_EXIT_WALL_PCT", 0.5))
            if long:
                extreme = (k3 >= float(_cfg(config, "SCALP_V3_K_OB_EXIT_K3M_HI", 80.0))) | (k15 >= float(_cfg(config, "SCALP_V3_K_OB_EXIT_K15M_HI", 80.0)))
                scalp_kob = extreme & np.isfinite(ask_wall) & (ask_wall < wall_limit)
            else:
                extreme = (k3 <= float(_cfg(config, "SCALP_V3_K_OB_EXIT_K3M_LO", 20.0))) | (k15 <= float(_cfg(config, "SCALP_V3_K_OB_EXIT_K15M_LO", 20.0)))
                scalp_kob = extreme & np.isfinite(bid_wall) & (bid_wall < wall_limit)
        valid = finite(*a.values(), gain)
        scalp_kob &= pos & scalp & valid
        scalp_exit = pos & scalp & valid & (structure | momentum | ordinary_wall | scalp_kob)
    masks["scalp_v3_k_ob_exit"] = scalp_kob
    masks["scalp_v3_protective_exit"] = scalp_exit

    # Stateful monotone Donchian trail (tradier_manage.py:5491-5534).
    dyn_tf = str(_cfg(config, "DYN_STRUCT_TRAIL_TF", "4h") or "4h")
    dyn_key = f"dc_{'low' if long else 'high'}_{dyn_tf}"
    a = load("dyn_struct_trail", ("close_5m", dyn_key))
    dyn_exit = zeros()
    dyn_levels = np.zeros(n, dtype=float)
    dyn_armed_out = np.zeros(n, dtype=bool)
    level = float(dyn_initial_level)
    armed = bool(dyn_initial_armed) or float(_cfg(config, "DYN_STRUCT_TRAIL_MIN_GAIN_PCT", 0.0)) <= 0
    if bool(_cfg(config, "DYN_STRUCT_TRAIL_ENABLED", False)) and a is not None:
        price, dc = a["close_5m"], a[dyn_key]
        min_gain = float(_cfg(config, "DYN_STRUCT_TRAIL_MIN_GAIN_PCT", 0.0))
        for i in range(n):
            if not pos[i] or not np.isfinite(price[i]) or not np.isfinite(dc[i]) or not np.isfinite(gain[i]):
                dyn_levels[i], dyn_armed_out[i] = level, armed
                continue
            if gain[i] >= min_gain:
                armed = True
            if dc[i] > 0:
                if level <= 0:
                    level = float(dc[i])
                elif long and dc[i] > level:
                    level = float(dc[i])
                elif not long and dc[i] < level:
                    level = float(dc[i])
            dyn_exit[i] = armed and level > 0 and ((price[i] <= level) if long else (price[i] >= level))
            dyn_levels[i], dyn_armed_out[i] = level, armed
    masks["dyn_struct_trail_exit"] = dyn_exit

    return ExitReduceBatch4Result(
        masks=MappingProxyType(masks),
        missing_arrays=MappingProxyType(missing),
        tradier_wt_against_count=wt_count,
        mu_htf_hit_count=mu_htf,
        mu_roll_count=mu_roll,
        mu_ltf_fall_count=mu_ltf,
        dyn_trail_level=dyn_levels,
        dyn_trail_armed=dyn_armed_out,
    )


def consume_lifecycle_actions(
    result: ExitReduceBatch4Result,
    candidates: Mapping[str, Any],
) -> LifecycleActionResult:
    """Expose concrete v12 lifecycle consumers for primary integration."""
    n = len(next(iter(result.masks.values()))) if result.masks else 0

    def candidate(name: str) -> np.ndarray:
        if name not in candidates:
            raise KeyError(f"missing lifecycle candidate {name!r}")
        return _state(candidates[name], n, f"candidates[{name!r}]", bool)

    entry, reentry, augment = (candidate(x) for x in ("ENTRY", "REENTRY", "AUGMENT"))
    reduce, exit_, stop = (candidate(x) for x in ("REDUCE", "EXIT", "STOP"))
    m = result.masks
    sourced_exit = (
        m["bottom_a_protective_trail_exit"] | m["mtf_wt_cross_direct_exit"]
        | m["tradier_wt_vote_exit"] | m["newborn_dc_stop"]
        | m["breakeven_after_bars_exit"] | m["mu_correction_exit"]
        | m["scalp_v3_protective_exit"] | m["dyn_struct_trail_exit"]
    )
    return LifecycleActionResult(masks=MappingProxyType({
        "ENTRY": entry,
        "REENTRY": reentry,
        "AUGMENT": augment,
        "REDUCE": reduce,
        "EXIT": exit_ | sourced_exit,
        "STOP": stop | sourced_exit,
        "MANDATORY_REENTRY_ARM": m["bottom_a_protective_trail_exit"] | m["mu_correction_exit"],
    }))


__all__ = [
    "ExitReduceBatch4Result",
    "LifecycleActionResult",
    "consume_lifecycle_actions",
    "evaluate_gap_batch4",
]
