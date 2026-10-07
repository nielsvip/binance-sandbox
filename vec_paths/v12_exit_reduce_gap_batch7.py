"""Source-exact portfolio EXIT/REDUCE/filter contracts, batch 7.

Rows represent the ratio loop's ordered position/candidate list.  Portfolio
aggregates and cooldown state are explicit inputs.  Candidate HTF fields must
be native persisted arrays; absent data disables the open path and is reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ExitReduceBatch7Result:
    masks: Mapping[str, np.ndarray]
    values: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class LifecycleActionResult:
    masks: Mapping[str, np.ndarray]


def _cfg(c: Any, name: str, default: Any) -> Any:
    return c.get(name, default) if isinstance(c, Mapping) else getattr(c, name, default)


def _arr(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    a = np.asarray(value, dtype=dtype)
    if a.ndim == 0: return np.full(n, a.item(), dtype=dtype)
    if a.ndim != 1 or len(a) != n: raise ValueError(f"{name} must be scalar or shape ({n},)")
    return a


def _take_ordered(mask: np.ndarray, order: np.ndarray, cap: int) -> np.ndarray:
    out = np.zeros(len(mask), dtype=bool)
    if cap <= 0: return out
    idx = np.flatnonzero(mask)
    if len(idx): out[idx[np.argsort(order[idx], kind="stable")[:cap]]] = True
    return out


def evaluate_gap_batch7(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_sides: Any,
    gain_pct: Any,
    position_age_minutes: Any,
    position_qty: Any,
    is_hedge: Any,
    delta_bull_speed: Any,
    delta_bear_speed: Any,
    candidate_score: Any = 0.0,
    candidate_price: Any = 0.0,
    long_value: float = 0.0,
    short_value: float = 0.0,
    pnl_long_avg: float = 0.0,
    pnl_short_avg: float = 0.0,
    pnl_long_count: int = 0,
    pnl_short_count: int = 0,
    breadth_bias: float = 0.5,
    average_sentiment: float = 0.0,
    htf_aligned: bool = False,
    ratio_extreme: bool | None = None,
    k1h_crossed: bool = False,
    elapsed_since_rebalance_seconds: float = np.inf,
    elapsed_since_loser_close_seconds: float = np.inf,
    regime: str = "NORMAL",
    stuck_loser_count: int = 0,
    start_position_size: float = 1.0,
) -> ExitReduceBatch7Result:
    sides = np.asarray(position_sides).astype(str)
    if sides.ndim != 1: raise ValueError("position_sides must be a 1-D ordered list")
    sides = np.char.upper(sides); n = len(sides)
    long = sides == "LONG"; short = sides == "SHORT"
    if not np.all(long | short): raise ValueError("position_sides rows must be LONG or SHORT")
    gain = _arr(gain_pct, n, "gain_pct", float)
    age = _arr(position_age_minutes, n, "position_age_minutes", float)
    qty = np.abs(_arr(position_qty, n, "position_qty", float))
    hedge = _arr(is_hedge, n, "is_hedge", bool)
    bull = np.abs(_arr(delta_bull_speed, n, "delta_bull_speed", float))
    bear = np.abs(_arr(delta_bear_speed, n, "delta_bear_speed", float))
    score = _arr(candidate_score, n, "candidate_score", float)
    cprice = _arr(candidate_price, n, "candidate_price", float)
    active = qty >= .0001
    masks: dict[str, np.ndarray] = {}
    values: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str]) -> dict[str, np.ndarray] | None:
        out = {}; bad = []
        for key in dict.fromkeys(keys):
            a = npz.get(key)
            if a is None or np.asarray(a).ndim != 1 or len(np.asarray(a)) != n:
                bad.append(key); continue
            try: out[key] = np.asarray(a, dtype=float)
            except (TypeError, ValueError): bad.append(key)
        if bad: missing[path] = tuple(bad); return None
        return out

    # Cooldown selector is computed before any ratio action.
    pnl_delta = float(pnl_long_avg) - float(pnl_short_avg)
    pnl_divergent = bool(_cfg(config, "RATIO_PNL_WEIGHT_ENABLED", True)) and abs(pnl_delta) >= float(_cfg(config, "RATIO_PNL_DELTA_THRESHOLD", 3.0)) and pnl_long_count > 0 and pnl_short_count > 0
    if ratio_extreme is None:
        pre_ratio = float(long_value) / max(float(short_value), 1.0)
        extreme = pre_ratio > 3.0 or pre_ratio < .33
    else: extreme = bool(ratio_extreme)
    if pnl_divergent: cooldown = float(_cfg(config, "RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT", 120.0))
    elif extreme: cooldown = float(_cfg(config, "RATIO_REBALANCE_COOLDOWN_EXTREME", 600.0))
    elif htf_aligned: cooldown = float(_cfg(config, "RATIO_REBALANCE_COOLDOWN_CRASH", 300.0))
    else: cooldown = float(_cfg(config, "RATIO_REBALANCE_COOLDOWN_NORMAL", 3600.0))
    if k1h_crossed: cooldown = 0.0
    cycle_ready = float(elapsed_since_rebalance_seconds) >= cooldown
    values["ratio_selected_cooldown_seconds"] = np.full(n, cooldown)
    masks["ratio_cycle_ready"] = np.full(n, cycle_ready)

    # STALL_SUB executes before ratio cooldown and sorts oldest first.
    stall_eligible = active & ~hedge & np.isfinite(gain) & np.isfinite(age) & np.isfinite(bull) & np.isfinite(bear)
    stall_eligible &= age >= float(_cfg(config, "STALL_AGE_MIN_MIN", 180.0))
    stall_eligible &= np.abs(gain) < float(_cfg(config, "STALL_GAIN_ABS_MAX", .5))
    stall_eligible &= np.maximum(bull, bear) <= float(_cfg(config, "STALL_DELTA_SPEED_MAX", 1.0))
    if not bool(_cfg(config, "STALL_SUB_ENABLED", True)): stall_eligible[:] = False
    stall_cap = int(_cfg(config, "STALL_MAX_CLOSES_PER_CYCLE", 2))
    masks["stall_sub_close"] = _take_ordered(stall_eligible, -age, stall_cap)

    # Breadth/sentiment target and PnL blend.
    sent_ratio = (float(average_sentiment) + 100.0) / 200.0
    base_ratio = float(breadth_bias) * .6 + sent_ratio * .4
    amplified = .5 + (base_ratio - .5) * float(_cfg(config, "RATIO_MULTIPLIER", 3.0))
    pnl_min = float(_cfg(config, "RATIO_PNL_TARGET_LONG_MIN", 10.0)); pnl_max = float(_cfg(config, "RATIO_PNL_TARGET_LONG_MAX", 90.0))
    if pnl_divergent:
        pnl_signal = np.clip(.5 + pnl_delta * float(_cfg(config, "RATIO_PNL_ACCELERATION", 2.5)) / 100.0, pnl_min / 100.0, pnl_max / 100.0)
        weight = float(_cfg(config, "RATIO_PNL_WEIGHT", .5))
        target_long = float(np.clip((pnl_signal * weight + amplified * (1.0 - weight)) * 100.0, pnl_min, pnl_max))
    else: target_long = float(np.clip(amplified * 100.0, 25.0, 75.0))
    total = float(long_value) + float(short_value)
    long_pct = float(long_value) / total * 100.0 if total > 0 else 50.0
    short_pct = 100.0 - long_pct
    if str(regime).upper() == "CRASH" and long_pct > 40: target_long = min(target_long, 30.0)
    elif str(regime).upper() == "JUMP" and short_pct > 40: target_long = 100.0 - min(100.0 - target_long, 30.0)
    target_short = 100.0 - target_long
    long_off = long_pct - target_long
    overweight_long = long_off > 0
    skew = abs(long_off)
    values["ratio_target_long_pct"] = np.full(n, target_long)
    values["ratio_target_short_pct"] = np.full(n, target_short)
    values["ratio_skew_pp"] = np.full(n, skew)
    masks["ratio_overweight_side"] = long if overweight_long else short
    dead_zone = 2.0 if str(regime).upper() in {"CRASH", "JUMP"} or stuck_loser_count >= 5 or extreme else 5.0
    rebalance_needed = abs(long_off) >= dead_zone and abs(-long_off) >= dead_zone
    open_side_long = not overweight_long
    against_flow = ((not open_side_long) and float(breadth_bias) > .55) or (open_side_long and float(breadth_bias) < .45)
    action_ready = cycle_ready and rebalance_needed and not against_flow
    masks["ratio_action_ready"] = np.full(n, action_ready)

    # Close-overweight-only branch: eligible winners sorted by weakest |WT15 delta|.
    wt = load("ratio_close_overweight", ("wt1_15m", "wt2_15m"))
    close_ow = np.zeros(n, bool)
    close_only = bool(_cfg(config, "RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY", True))
    if close_only and action_ready and wt is not None:
        conviction = np.abs(wt["wt1_15m"] - wt["wt2_15m"])
        eligible = active & (long if overweight_long else short) & (gain >= float(_cfg(config, "COMMISSION_BUFFER_PCT", .1))) & np.isfinite(conviction)
        close_ow = _take_ordered(eligible, conviction, int(_cfg(config, "RATIO_REBALANCE_MAX_CLOSES", 3)))
    masks["ratio_close_overweight_reduce"] = close_ow

    # Opt-in losing-overweight close: exact sign/skew/cooldown guards, worst loss first.
    close_loser = np.zeros(n, bool)
    if bool(_cfg(config, "RATIO_CLOSE_LOSING_OVERWEIGHT", False)) and action_ready and not close_only:
        min_loss = float(_cfg(config, "RATIO_CLOSE_LOSING_MIN_LOSS_PCT", -5.0))
        min_skew = float(_cfg(config, "RATIO_CLOSE_LOSING_MIN_SKEW_PP", 40.0))
        min_delta = float(_cfg(config, "RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT", 10.0))
        sign_ok = (not overweight_long and pnl_delta > min_delta) or (overweight_long and pnl_delta < -min_delta)
        cd_ok = float(elapsed_since_loser_close_seconds) >= float(_cfg(config, "RATIO_CLOSE_LOSING_COOLDOWN_SECONDS", 900.0))
        if sign_ok and skew >= min_skew and cd_ok:
            eligible = active & (long if overweight_long else short) & (gain <= min_loss)
            close_loser = _take_ordered(eligible, gain, int(_cfg(config, "RATIO_CLOSE_LOSING_MAX_PER_CYCLE", 2)))
    masks["ratio_close_losing_overweight_reduce"] = close_loser

    # Normal underweight OPEN/AUGMENT path, enabled only when close-only is false.
    open_mask = np.zeros(n, bool)
    if action_ready and not close_only:
        threshold = 1.0 if extreme or str(regime).upper() in {"CRASH", "JUMP"} or skew > 20 else 5.0
        eligible = (long if open_side_long else short) & (score >= threshold) & (cprice > 0)
        htf = load("ratio_rebalance_htf_gate", tuple(f"wt{x}_{tf}" for tf in ("D", "4h", "1h") for x in (1, 2)) + ("sma_200_D",))
        if bool(_cfg(config, "RATIO_REBALANCE_APPLY_HTF_GATE", True)):
            if htf is None: eligible[:] = False
            else:
                aligned = []
                for tf in ("D", "4h", "1h"):
                    w1, w2 = htf[f"wt1_{tf}"], htf[f"wt2_{tf}"]
                    aligned.append(((w1 > w2) if open_side_long else (w1 < w2)) & ((w1 != 0) | (w2 != 0)))
                d_ok = aligned[0]
                met = aligned[0].astype(int) + aligned[1].astype(int) + aligned[2].astype(int)
                if bool(_cfg(config, "HTF_GATE_SIGNALS_SMA200D", True)):
                    sma_valid = htf["sma_200_D"] > 0
                    sma_ok = (cprice > htf["sma_200_D"]) if open_side_long else (cprice < htf["sma_200_D"])
                    met += (sma_valid & sma_ok).astype(int)
                gate = met >= int(_cfg(config, "HTF_GATE_MIN_CONFIRMATIONS", 3))
                if bool(_cfg(config, "HTF_GATE_D_MANDATORY", True)): gate &= d_ok
                eligible &= gate
        max_normal = int(_cfg(config, "RATIO_REBALANCE_MAX_OPENS_NORMAL", 8)); max_stuck = int(_cfg(config, "RATIO_REBALANCE_MAX_OPENS_STUCK", 10)); max_extreme = int(_cfg(config, "RATIO_REBALANCE_MAX_OPENS_EXTREME", 12))
        family_cap = max_extreme if extreme else (max_stuck if stuck_loser_count >= 5 else max_normal)
        cap = min(family_cap, max(2, int(skew / 3)))
        open_mask = _take_ordered(eligible, np.arange(n), cap)
    masks["ratio_underweight_open_augment"] = open_mask
    mult = min(float(_cfg(config, "RATIO_REBALANCE_SIZE_MAX_MULT", 10.0)), float(_cfg(config, "RATIO_REBALANCE_SIZE_MULT", 4.0)) + skew * float(_cfg(config, "RATIO_REBALANCE_SIZE_SKEW_BOOST", .05)))
    values["ratio_rebalance_size_multiplier"] = np.full(n, mult)
    values["ratio_rebalance_notional"] = np.where(open_mask, float(start_position_size) * mult, 0.0)
    return ExitReduceBatch7Result(MappingProxyType(masks), MappingProxyType(values), MappingProxyType(missing))


def consume_lifecycle_actions(result: ExitReduceBatch7Result) -> LifecycleActionResult:
    m = result.masks; z = np.zeros_like(m["stall_sub_close"])
    return LifecycleActionResult(MappingProxyType({
        "ENTRY": m["ratio_underweight_open_augment"],
        "REENTRY": z,
        "AUGMENT": m["ratio_underweight_open_augment"],
        "REDUCE": m["ratio_close_overweight_reduce"] | m["ratio_close_losing_overweight_reduce"],
        "EXIT": m["stall_sub_close"],
        "STOP": z,
        "HEDGE_OPEN": z,
    }))


__all__ = ["ExitReduceBatch7Result", "LifecycleActionResult", "consume_lifecycle_actions", "evaluate_gap_batch7"]
