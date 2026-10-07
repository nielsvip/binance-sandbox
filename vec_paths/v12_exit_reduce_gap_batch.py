"""Strict persisted-array contracts for the v12 WT/DC exit-reduce gap batch.

This module is intentionally disjoint from :mod:`v12_quick_engine`.  It is an
integration-ready, source-exact implementation of the bounded EXIT/REDUCE
tranche recorded in ``V12_EXIT_REDUCE_GAP_BATCH_CONTRACT.csv``.

The causal sources are ``ez_manage.process_position`` (WT 4h velocity,
DC-hopeless, WT exhaustion, WT percentile, E-1 and E-3), and
``ez_positions_quick.process_single_exit`` (WT cross and reduce bands).
Unlike older research adapters, this module never substitutes another
timeframe, zero-fills a missing indicator, or invents a proxy.  A predicate
whose persisted inputs are absent or malformed is all-False and reports the
missing keys in :class:`ExitReduceGapBatchResult`.

Position state remains an explicit caller input because it is not NPZ market
data.  Scalars broadcast; one-dimensional arrays must match the NPZ bar count.
No execution ordering or fill behavior is implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


PATH_REQUIRED_ARRAYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "wt_4h_velocity_exit": (
            "wt_velocity_4h",
            "stoch_k_3m",
            "stoch_k_15m",
        ),
        "dc_hopeless_exit": ("dc_high_4h", "dc_low_4h"),
        "wt_exhaust_exit": (
            "wt_momentum_state_4h",
            "wt_momentum_state_1h",
            "wt_momentum_state_15m",
        ),
        "wt_percentile_exit": (
            "wt_percentile_D",
            "wt_percentile_4h",
            "wt1_15m",
            "wt2_15m",
        ),
        "e1_wt_delta_exit": ("wt_composite_delta",),
        # The persisted/decode contract exposes peak structure as
        # ``wt_structure`` (-1=LH, +1=HH).  LL/HL are separate trough fields
        # and are not read by the authoritative E-3 path.
        "e3_structure": tuple(f"wt_structure_{tf}" for tf in ("15m", "1h", "4h")),
        "wt_cross_exit": tuple(
            key
            for tf in ("1h", "15m", "3m")
            for key in (f"wt1_{tf}", f"wt2_{tf}")
        ),
        # The three reduce knobs select fractions from caller position state;
        # they have no persisted market-array dependency.
        "wt_reduce_bands": (),
    }
)


@dataclass(frozen=True)
class ExitReduceGapBatchResult:
    """Masks and diagnostics produced by :func:`evaluate_gap_batch`."""

    masks: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]
    reduce_fraction: np.ndarray


def _cfg(config: Any, name: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def _infer_n(npz: Mapping[str, Any], bar_count: int | None) -> int:
    if bar_count is not None:
        n = int(bar_count)
        if n < 0:
            raise ValueError("bar_count must be non-negative")
        return n
    for value in npz.values():
        arr = np.asarray(value)
        if arr.ndim == 1:
            return int(arr.shape[0])
    raise ValueError("bar_count is required when NPZ has no one-dimensional arrays")


def _state(value: Any, n: int, name: str, *, dtype: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=dtype)
    if arr.ndim == 0:
        return np.full(n, arr.item(), dtype=dtype)
    if arr.ndim != 1 or len(arr) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return arr


def _load_path(
    npz: Mapping[str, Any], path: str, n: int
) -> tuple[dict[str, np.ndarray] | None, tuple[str, ...]]:
    missing: list[str] = []
    loaded: dict[str, np.ndarray] = {}
    for key in PATH_REQUIRED_ARRAYS[path]:
        if key not in npz:
            missing.append(key)
            continue
        arr = np.asarray(npz[key])
        if arr.ndim != 1 or len(arr) != n:
            missing.append(key)
            continue
        loaded[key] = arr
    return (None, tuple(missing)) if missing else (loaded, ())


def _finite(*arrays: np.ndarray) -> np.ndarray:
    valid = np.ones(len(arrays[0]), dtype=bool)
    for arr in arrays:
        try:
            valid &= np.isfinite(arr.astype(np.float64, copy=False))
        except (TypeError, ValueError):
            return np.zeros(len(arr), dtype=bool)
    return valid


def evaluate_gap_batch(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_seconds: Any,
    position_active: Any = True,
    position_above_min_qty: Any = True,
    entry_price: Any = 0.0,
    is_hedge: Any = False,
    in_grace_period: Any = False,
    hard_exit_pending: Any = False,
    min_profit_pct: Any = 0.0,
    candidate_is_full_close: Any = False,
    bar_count: int | None = None,
) -> ExitReduceGapBatchResult:
    """Evaluate the exact bounded WT/DC exit and WT-reduce predicates.

    The apparently contradictory WT-cross loser selector is preserved exactly:
    ``position.gain > 0.015`` guards the block before the loser/winner choice,
    so ``WT_CROSS_EXIT_APPLIES_TO_LOSERS`` is source-inert.  The contract marks
    that field rejected rather than manufacturing a vector-only effect.

    Returned masks are independent predicates, not additive scores.  The live
    first-match ordering is deliberately left to the integrating simulator.
    ``reduce_fraction`` is NaN outside the three configurable partial-reduce
    bands or for full-close candidates.
    """
    side = str(position_side).upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("position_side must be LONG or SHORT")
    is_long = side == "LONG"
    n = _infer_n(npz, bar_count)
    zeros = lambda: np.zeros(n, dtype=bool)

    gain = _state(gain_pct, n, "gain_pct", dtype=np.float64)
    age_s = _state(position_age_seconds, n, "position_age_seconds", dtype=np.float64)
    active = _state(position_active, n, "position_active", dtype=bool)
    above_min = _state(
        position_above_min_qty, n, "position_above_min_qty", dtype=bool
    )
    entry = _state(entry_price, n, "entry_price", dtype=np.float64)
    hedge = _state(is_hedge, n, "is_hedge", dtype=bool)
    grace = _state(in_grace_period, n, "in_grace_period", dtype=bool)
    prior_exit = _state(hard_exit_pending, n, "hard_exit_pending", dtype=bool)
    min_profit = _state(min_profit_pct, n, "min_profit_pct", dtype=np.float64)
    full_close = _state(
        candidate_is_full_close, n, "candidate_is_full_close", dtype=bool
    )
    position_ok = active & above_min

    masks: dict[str, np.ndarray] = {}
    missing_by_path: dict[str, tuple[str, ...]] = {}

    def arrays(path: str) -> dict[str, np.ndarray] | None:
        loaded, missing = _load_path(npz, path, n)
        if missing:
            missing_by_path[path] = missing
        return loaded

    # ez_manage.py:43191-43264.  The 360-second age comparison is strict.
    a = arrays("wt_4h_velocity_exit")
    m = zeros()
    if bool(_cfg(config, "WT_4H_VEL_EXIT_ENABLED", True)) and a is not None:
        vel = np.asarray(a["wt_velocity_4h"], dtype=np.float64)
        k3 = np.asarray(a["stoch_k_3m"], dtype=np.float64)
        k15 = np.asarray(a["stoch_k_15m"], dtype=np.float64)
        against = vel < float(_cfg(config, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0))
        if not is_long:
            against = vel > float(_cfg(config, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0))
        if bool(_cfg(config, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True)):
            profit_ok = gain >= float(_cfg(config, "COMMISSION_BUFFER_PCT", 0.10))
        else:
            profit_ok = np.ones(n, dtype=bool)
        if bool(_cfg(config, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)):
            if is_long:
                threshold = float(_cfg(config, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0))
                k_ok = (k3 >= threshold) | (k15 >= threshold)
            else:
                threshold = float(_cfg(config, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0))
                k_ok = (k3 <= threshold) | (k15 <= threshold)
        else:
            k_ok = np.ones(n, dtype=bool)
        m = (
            position_ok
            & np.isfinite(gain)
            & np.isfinite(age_s)
            & (age_s > 360.0)
            & against
            & profit_ok
            & k_ok
        )
        m &= _finite(vel, k3, k15)
    masks["wt_4h_velocity_exit"] = m

    # ez_manage.py:43274-43317.
    a = arrays("dc_hopeless_exit")
    m = zeros()
    if bool(_cfg(config, "DC_HOPELESS_EXIT_ENABLED", True)) and a is not None:
        dc_high = np.asarray(a["dc_high_4h"], dtype=np.float64)
        dc_low = np.asarray(a["dc_low_4h"], dtype=np.float64)
        hopeless = (entry > dc_high) if is_long else (entry < dc_low)
        m = (
            position_ok
            & np.isfinite(age_s)
            & _finite(dc_high, dc_low, entry)
            & (dc_high > 0)
            & (dc_low > 0)
            & (entry > 0)
            & (age_s > float(_cfg(config, "DC_HOPELESS_EXIT_MIN_AGE_S", 900)))
            & hopeless
        )
    masks["dc_hopeless_exit"] = m

    # ez_manage.py:43326-43366. Persisted encoding: +1 EXHAUST_UP,
    # -1 EXHAUST_DOWN.  This includes the live min-gain gate omitted by the
    # older position_evaluator vector helper.
    a = arrays("wt_exhaust_exit")
    m = zeros()
    if bool(_cfg(config, "WT_EXHAUST_EXIT_ENABLED", True)) and a is not None:
        m4 = np.asarray(a["wt_momentum_state_4h"], dtype=np.float64)
        m1 = np.asarray(a["wt_momentum_state_1h"], dtype=np.float64)
        m15 = np.asarray(a["wt_momentum_state_15m"], dtype=np.float64)
        code = 1.0 if is_long else -1.0
        exhaust = (m4 == code) & ((m1 == code) | (m15 == code))
        gain_ok = gain >= float(_cfg(config, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", 0.5))
        if bool(_cfg(config, "WT_EXHAUST_EXIT_REQUIRE_GAIN", False)):
            gain_ok &= gain > 0.0
        m = position_ok & np.isfinite(gain) & _finite(m4, m1, m15) & exhaust & gain_ok
    masks["wt_exhaust_exit"] = m

    # ez_manage.py:43373-43413. Exact live semantics include the 15m WT
    # confirmation, which the older vector helper omitted.
    a = arrays("wt_percentile_exit")
    m = zeros()
    if bool(_cfg(config, "WT_PERCENTILE_EXIT_ENABLED", True)) and a is not None:
        pct_d = np.asarray(a["wt_percentile_D"], dtype=np.float64)
        pct_4h = np.asarray(a["wt_percentile_4h"], dtype=np.float64)
        wt1 = np.asarray(a["wt1_15m"], dtype=np.float64)
        wt2 = np.asarray(a["wt2_15m"], dtype=np.float64)
        if is_long:
            extreme = (
                (pct_d > float(_cfg(config, "WT_PERCENTILE_EXIT_OB_D", 75.0)))
                & (pct_4h > float(_cfg(config, "WT_PERCENTILE_EXIT_OB_4H", 55.0)))
                & (wt1 < wt2)
            )
        else:
            extreme = (
                (pct_d < float(_cfg(config, "WT_PERCENTILE_EXIT_OS_D", 10.0)))
                & (pct_4h < float(_cfg(config, "WT_PERCENTILE_EXIT_OS_4H", 25.0)))
                & (wt1 > wt2)
            )
        m = position_ok & _finite(pct_d, pct_4h, wt1, wt2) & extreme
    masks["wt_percentile_exit"] = m

    # ez_manage.py:43417-43443.
    a = arrays("e1_wt_delta_exit")
    m = zeros()
    if bool(_cfg(config, "E_1_WT_EXIT_USE_DELTA_ENABLED", False)) and a is not None:
        delta = np.asarray(a["wt_composite_delta"], dtype=np.float64)
        threshold = float(_cfg(config, "E_1_EXIT_DELTA_THR", 50.0))
        against = delta < -threshold if is_long else delta > threshold
        m = position_ok & _finite(delta) & against
    masks["e1_wt_delta_exit"] = m

    # ez_manage.py:43447-43482. The path reads only ``wt_structure``.  Under
    # the persisted/decode contract this is peak structure (-1=LH, +1=HH), so
    # the source comments mentioning LL/HL are not reachable via this key.
    a = arrays("e3_structure")
    shadow = zeros()
    exit_mask = zeros()
    e3_mode = int(_cfg(config, "E_3_USE_WT_STRUCTURE_EXIT_MODE", 0))
    if e3_mode in {1, 2} and a is not None:
        against_count = np.zeros(n, dtype=np.int8)
        valid = np.ones(n, dtype=bool)
        for tf in ("15m", "1h", "4h"):
            structure = np.asarray(a[f"wt_structure_{tf}"], dtype=np.float64)
            against_count += (
                structure == (-1.0 if is_long else 1.0)
            ).astype(np.int8)
            valid &= _finite(structure)
        qualified = position_ok & valid & (against_count >= 2)
        if e3_mode == 1:
            shadow = qualified
        else:
            exit_mask = qualified
    masks["e3_structure_shadow"] = shadow
    masks["e3_structure_exit"] = exit_mask

    # ez_positions_quick.py:14400-14431. All three persisted TFs are required
    # here even though live treats missing 15m/3m as permissive; the vector
    # contract is explicitly fail-closed on missing data.
    a = arrays("wt_cross_exit")
    m = zeros()
    if bool(_cfg(config, "WT_CROSS_EXIT_ENABLED", True)) and a is not None:
        w1_1h = np.asarray(a["wt1_1h"], dtype=np.float64)
        w2_1h = np.asarray(a["wt2_1h"], dtype=np.float64)
        w1_15 = np.asarray(a["wt1_15m"], dtype=np.float64)
        w2_15 = np.asarray(a["wt2_15m"], dtype=np.float64)
        w1_3 = np.asarray(a["wt1_3m"], dtype=np.float64)
        w2_3 = np.asarray(a["wt2_3m"], dtype=np.float64)
        have_1h = (w1_1h != 0) | (w2_1h != 0)
        have_15 = (w1_15 != 0) | (w2_15 != 0)
        have_3 = (w1_3 != 0) | (w2_3 != 0)
        if is_long:
            flip_1h = w1_1h < w2_1h
            confirm_15 = w1_15 < w2_15
            with_position_3m = have_3 & (w1_3 > w2_3)
        else:
            flip_1h = w1_1h > w2_1h
            confirm_15 = w1_15 > w2_15
            with_position_3m = have_3 & (w1_3 < w2_3)
        if not bool(_cfg(config, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)):
            confirm_15 = np.ones(n, dtype=bool)
        # Since persisted 15m is mandatory, a requested confirmation must have
        # a non-zero live-equivalent observation as well as the direction.
        else:
            confirm_15 &= have_15
        pnl_ok = np.where(
            gain < 0,
            bool(_cfg(config, "WT_CROSS_EXIT_APPLIES_TO_LOSERS", True)),
            bool(_cfg(config, "WT_CROSS_EXIT_APPLIES_TO_WINNERS", True)),
        )
        veto = with_position_3m & (
            age_s / 60.0 < float(_cfg(config, "WT_CROSS_EXIT_3M_VETO_MAX_AGE", 30.0))
        )
        m = (
            position_ok
            & np.isfinite(gain)
            & np.isfinite(age_s)
            & ~prior_exit
            & ~hedge
            & ~grace
            & (gain > 0.015)
            & (age_s / 60.0 >= float(_cfg(config, "WT_CROSS_EXIT_MIN_AGE_MINUTES", 1.0)))
            & pnl_ok
            & have_1h
            & flip_1h
            & confirm_15
            & ~veto
            & _finite(w1_1h, w2_1h, w1_15, w2_15, w1_3, w2_3)
        )
    masks["wt_cross_exit"] = m

    # ez_positions_quick.py:14579-14590. These masks identify exactly where
    # each configurable fraction owns the partial-close quantity.
    finite_reduce_state = np.isfinite(gain) & np.isfinite(min_profit)
    reducible = active & above_min & ~full_close & finite_reduce_state
    low = reducible & (gain < 0.5) & (gain < 1.0) & (gain >= min_profit)
    med = reducible & (gain >= 0.5) & (gain < 1.0) & (gain >= min_profit)
    high = reducible & (gain >= 1.0) & (gain < 3.0)
    masks["wt_reduce_low_band"] = low
    masks["wt_reduce_med_band"] = med
    masks["wt_reduce_high_band"] = high
    reduce_fraction = np.full(n, np.nan, dtype=np.float64)
    reduce_fraction[low] = float(_cfg(config, "WT_REDUCE_FRAC_LOW", 0.15))
    reduce_fraction[med] = float(_cfg(config, "WT_REDUCE_FRAC_MED", 0.25))
    reduce_fraction[high] = float(_cfg(config, "WT_REDUCE_FRAC_HIGH", 0.50))

    return ExitReduceGapBatchResult(
        masks=MappingProxyType(masks),
        missing_arrays=MappingProxyType(missing_by_path),
        reduce_fraction=reduce_fraction,
    )


__all__ = [
    "ExitReduceGapBatchResult",
    "PATH_REQUIRED_ARRAYS",
    "evaluate_gap_batch",
]
