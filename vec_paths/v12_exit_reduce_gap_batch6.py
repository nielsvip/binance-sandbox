"""Source-exact high-fanout lifecycle filters, EXIT/REDUCE batch 6.

This adapter exposes predicates and action payloads only.  It does not install
them into v12, synthesize missing indicators, or reinterpret broker state.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from vec_paths.funding_gate import funding_long_veto, funding_short_veto


@dataclass(frozen=True)
class ExitReduceBatch6Result:
    masks: Mapping[str, np.ndarray]
    values: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class LifecycleActionResult:
    masks: Mapping[str, np.ndarray]


def _cfg(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)


def _state(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    a = np.asarray(value, dtype=dtype)
    if a.ndim == 0:
        return np.full(n, a.item(), dtype=dtype)
    if a.ndim != 1 or len(a) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return a


def evaluate_gap_batch6(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_minutes: Any,
    current_price: Any,
    previous_gain_pct: Any = np.nan,
    position_active: Any = True,
    position_qty: Any = 1.0,
    position_min_qty: Any = 0.001,
    entry_price: Any = 1.0,
    opposite_position_qty: Any = 0.0,
    opposite_position_gain_pct: Any = 0.0,
    is_known_hedge: Any = False,
    ppl_enabled: Any = False,
    ppl_account_allowed: Any = False,
    ppl_skip_standard_exits: Any = False,
    ppl_fired: Any = False,
    ppl_min_gain_pct: Any = 0.5,
    gr_against_score: Any = 0.0,
    gr_proactive_passes: Any = False,
    hedge_min_loss_pct: Any = -0.05,
    orderbook_fresh: Any = True,
    bar_count: int | None = None,
) -> ExitReduceBatch6Result:
    side = str(position_side).upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("position_side must be LONG or SHORT")
    origin_long = side == "LONG"
    hedge_long = not origin_long
    if bar_count is None:
        n = next((len(np.asarray(v)) for v in npz.values() if np.asarray(v).ndim == 1), None)
        if n is None:
            raise ValueError("bar_count is required when no 1-D persisted array exists")
    else:
        n = int(bar_count)
    zeros = lambda: np.zeros(n, dtype=bool)
    ones = lambda: np.ones(n, dtype=bool)
    gain = _state(gain_pct, n, "gain_pct", float)
    age = _state(position_age_minutes, n, "position_age_minutes", float)
    price = _state(current_price, n, "current_price", float)
    prev_gain = _state(previous_gain_pct, n, "previous_gain_pct", float)
    active = _state(position_active, n, "position_active", bool)
    qty = np.abs(_state(position_qty, n, "position_qty", float))
    min_qty = np.abs(_state(position_min_qty, n, "position_min_qty", float))
    entry = _state(entry_price, n, "entry_price", float)
    opp_qty = np.abs(_state(opposite_position_qty, n, "opposite_position_qty", float))
    opp_gain = _state(opposite_position_gain_pct, n, "opposite_position_gain_pct", float)
    known_hedge = _state(is_known_hedge, n, "is_known_hedge", bool)
    ppl_on = _state(ppl_enabled, n, "ppl_enabled", bool)
    ppl_acct = _state(ppl_account_allowed, n, "ppl_account_allowed", bool)
    ppl_skip = _state(ppl_skip_standard_exits, n, "ppl_skip_standard_exits", bool)
    ppl_done = _state(ppl_fired, n, "ppl_fired", bool)
    ppl_min = _state(ppl_min_gain_pct, n, "ppl_min_gain_pct", float)
    gr_score = _state(gr_against_score, n, "gr_against_score", float)
    gr_pass = _state(gr_proactive_passes, n, "gr_proactive_passes", bool)
    min_loss = _state(hedge_min_loss_pct, n, "hedge_min_loss_pct", float)
    ob_fresh = _state(orderbook_fresh, n, "orderbook_fresh", bool)
    masks: dict[str, np.ndarray] = {}
    values: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str], *, report: bool = True) -> dict[str, np.ndarray] | None:
        out: dict[str, np.ndarray] = {}
        bad: list[str] = []
        for key in dict.fromkeys(keys):
            if key not in npz:
                bad.append(key); continue
            a = np.asarray(npz[key])
            if a.ndim != 1 or len(a) != n:
                bad.append(key); continue
            try: out[key] = a.astype(float, copy=False)
            except (TypeError, ValueError): bad.append(key)
        if bad:
            if report: missing[path] = tuple(bad)
            return None
        return out

    def finite(*arrays: np.ndarray) -> np.ndarray:
        out = ones()
        for a in arrays: out &= np.isfinite(a)
        return out

    # NOLOSS_MIN_PROFIT_PCT has both strict and inclusive source consumers.
    noloss = float(_cfg(config, "NOLOSS_MIN_PROFIT_PCT", 0.0))
    masks["noloss_profit_gt_filter"] = active & finite(gain) & (gain > noloss)
    masks["noloss_profit_ge_filter"] = active & finite(gain) & (gain >= noloss)

    # Partial-profit-lock hedge exclusion, reduce decision/payload, and PPL-adjusted gain.
    deep = float(_cfg(config, "OPPOSITE_LOSER_DEEP_LOSS_PCT", -5.0))
    effective_hedge = known_hedge | ((opp_qty > 0.0001) & (opp_gain < deep))
    frac = float(_cfg(config, "PARTIAL_PROFIT_LOCK_FRAC", 0.5))
    reduce_qty = qty * frac
    keep_qty = qty - reduce_qty
    ppl_reduce = active & ppl_on & ppl_acct & ~ppl_skip & ~effective_hedge & ~ppl_done
    ppl_reduce &= finite(gain, ppl_min, qty, min_qty, entry) & (gain >= ppl_min) & (qty > min_qty) & (entry > 0)
    ppl_reduce &= (reduce_qty > min_qty) & (keep_qty > min_qty)
    masks["opposite_deep_loser_hedge_filter"] = effective_hedge
    masks["partial_profit_lock_reduce"] = ppl_reduce
    values["partial_profit_lock_reduce_fraction"] = np.full(n, frac, dtype=float)
    values["partial_profit_lock_reduce_qty"] = np.where(ppl_reduce, reduce_qty, 0.0)
    values["partial_profit_lock_keep_qty"] = np.where(ppl_reduce, keep_qty, qty)
    eff_gain = gain.copy()
    if bool(_cfg(config, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True)) and 0.0 < frac < 1.0:
        eff_gain = np.where(ppl_done, gain / (1.0 - frac), gain)
    values["ppl_effective_gain_pct"] = eff_gain

    # Configurable DC/WT/strict-WT candidate guards from _quick_hedge_rank.
    rank_keys = tuple(
        [f"dc_position_{tf}" for tf in ("15m", "1h", "4h")]
        + [f"dc_{edge}_{tf}" for edge in ("high", "low") for tf in ("1h", "4h")]
        + [f"wt_velocity_{tf}" for tf in ("3m", "1h", "4h")]
        + [f"wt{x}_{tf}" for tf in ("3m", "15m", "1h", "4h", "D") for x in (1, 2)]
    )
    a = load("hedge_rank_config_guards", rank_keys)
    rank_block = ones()  # selected HEDGE_OPEN path fails closed without its native recipe
    aligned_count = np.zeros(n, dtype=np.int8)
    if a is not None:
        rank_block = zeros()
        dc_on = bool(_cfg(config, "HEDGE_DC_RESISTANCE_GATE_ENABLED", True))
        wt_on = bool(_cfg(config, "HEDGE_WT_VEL_GATE_ENABLED", True))
        if dc_on:
            lth = float(_cfg(config, "HEDGE_DC_LONG_REJECT_DCP", 0.85))
            sth = float(_cfg(config, "HEDGE_DC_SHORT_REJECT_DCP", 0.15))
            if hedge_long:
                rank_block |= (a["dc_position_1h"] >= lth) | (a["dc_position_4h"] >= lth) | (a["dc_position_15m"] >= max(lth + .07, .92))
                rank_block |= ((a["dc_high_1h"] > 0) & (price >= a["dc_high_1h"] * .995)) | ((a["dc_high_4h"] > 0) & (price >= a["dc_high_4h"] * .995))
            else:
                rank_block |= (a["dc_position_1h"] <= sth) | (a["dc_position_4h"] <= sth) | (a["dc_position_15m"] <= min(sth - .07, .08))
                rank_block |= ((a["dc_low_1h"] > 0) & (price <= a["dc_low_1h"] * 1.005)) | ((a["dc_low_4h"] > 0) & (price <= a["dc_low_4h"] * 1.005))
        if wt_on:
            if hedge_long:
                rank_block |= ((a["wt_velocity_1h"] <= 0) & (a["wt_velocity_4h"] <= 0)) | (a["wt_velocity_3m"] < -1)
            else:
                rank_block |= ((a["wt_velocity_1h"] >= 0) & (a["wt_velocity_4h"] >= 0)) | (a["wt_velocity_3m"] > 1)
        for tf in ("3m", "15m", "1h", "4h", "D"):
            w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
            aligned_count += (((w1 > w2) if hedge_long else (w1 < w2)) & ((w1 != 0) | (w2 != 0))).astype(np.int8)
        if bool(_cfg(config, "HEDGE_STRICT_WT_ALL_TFS_ENABLED", True)):
            rank_block |= aligned_count < int(_cfg(config, "HEDGE_STRICT_WT_MIN_TFS_AGAINST", 4))
        rank_block |= ~finite(price, *a.values())
    masks["hedge_rank_config_block"] = rank_block
    values["hedge_strict_wt_aligned_count"] = aligned_count.astype(float)

    # Main hedge scan WT trigger, deteriorating-gain gate, GR alternative and newborn grace.
    a = load("hedge_scan_filters", tuple(f"wt{x}_{tf}" for tf in ("3m", "15m", "1h") for x in (1, 2)) + (("dc_low_3m",) if origin_long else ("dc_high_3m",)))
    scan = zeros()
    newborn_block = ones()
    deteriorating_block = ones()
    gr_trigger = zeros()
    if a is not None:
        against = {tf: ((a[f"wt1_{tf}"] < a[f"wt2_{tf}"]) if origin_long else (a[f"wt1_{tf}"] > a[f"wt2_{tf}"])) for tf in ("3m", "15m", "1h")}
        use_or = bool(_cfg(config, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H", True))
        if use_or:
            wt_trigger = against["3m"] & (against["15m"] | against["1h"])
        elif bool(_cfg(config, "HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H", True)):
            wt_trigger = against["3m"] & against["1h"]
        elif bool(_cfg(config, "HEDGE_TRIGGER_USE_WT_3M_ALONE", True)):
            wt_trigger = against["3m"]
        else:
            wt_trigger = against["15m"] | (against["3m"] & against["1h"])
        gr_enabled = bool(_cfg(config, "HEDGE_TRIGGER_GR_SCORE_ENABLED", True))
        gr_trigger = gr_enabled & (gr_score >= float(_cfg(config, "GR_HEDGE_SCORE_FLOOR", 15))) & against["3m"]
        proactive = gr_enabled & gr_pass
        if bool(_cfg(config, "HEDGE_DETERIORATING_GAIN_ENABLED", True)):
            delta = float(_cfg(config, "HEDGE_DETERIORATING_GAIN_DELTA_PP", .1))
            deteriorating_block = ~wt_trigger & (~np.isfinite(prev_gain) | (gain >= prev_gain - delta))
        else:
            deteriorating_block = zeros()
        grace = float(_cfg(config, "HEDGE_NEWBORN_GRACE_MINUTES", 10.0))
        dc_key = "dc_low_3m" if origin_long else "dc_high_3m"
        breach = (a[dc_key] > 0) & ((price < a[dc_key]) if origin_long else (price > a[dc_key]))
        if not bool(_cfg(config, "HEDGE_NEWBORN_DC_BREACH_ALLOWED", True)):
            breach = zeros()
        newborn_block = (grace > 0) & (age < grace) & ~breach
        gain_gate = bool(_cfg(config, "HEDGE_ALL_POSITIONS", False)) | (gain < min_loss) | proactive
        scan = active & finite(gain, age, price, *a.values()) & gain_gate & ~deteriorating_block & ~newborn_block & (wt_trigger | proactive | gr_trigger)
    masks["hedge_gr_trigger"] = gr_trigger
    masks["hedge_deteriorating_block"] = deteriorating_block
    masks["hedge_newborn_grace_block"] = newborn_block
    masks["hedge_scan_filter_admit"] = scan

    # Entry-wrapper hedge application switches: OI, funding and orderbook red-zone.
    oi_keys = ("oi_change_1h_pct", "close_1h_prev")
    a = load("hedge_oi_gate", oi_keys)
    oi_block = zeros()
    if bool(_cfg(config, "OI_CONFIRM_ENABLED", False)) and bool(_cfg(config, "OI_HEDGE_GATE_ENABLED", False)) and a is not None:
        pxchg = (price - a["close_1h_prev"]) / a["close_1h_prev"] * 100.0
        strong = finite(price, *a.values()) & (a["close_1h_prev"] > 0)
        strong &= np.abs(a["oi_change_1h_pct"]) >= float(_cfg(config, "OI_CONFIRM_MIN_CHANGE_PCT", .5))
        strong &= np.abs(pxchg) >= float(_cfg(config, "OI_CONFIRM_MIN_PRICE_PCT", .3))
        px_up, oi_up = pxchg > 0, a["oi_change_1h_pct"] > 0
        oi_block = strong & ((px_up & ~oi_up) | (~px_up & oi_up) if hedge_long else ((~px_up & ~oi_up) | (px_up & oi_up)))
    masks["hedge_oi_entry_block"] = oi_block

    funding_keys = ("funding_rate_3m",) + tuple(f"wt{x}_{tf}" for tf in ("15m", "1h", "4h", "D") for x in (1, 2))
    a = load("hedge_funding_gate", funding_keys)
    funding_block = zeros()
    if bool(_cfg(config, "FUNDING_GATE_ENABLED", True)) and bool(_cfg(config, "FUNDING_HEDGE_GATE_ENABLED", True)) and a is not None:
        funding_block = np.asarray(funding_long_veto(a["funding_rate_3m"], a, config) if hedge_long else funding_short_veto(a["funding_rate_3m"], a, config), dtype=bool)
        funding_block &= finite(*a.values())
    masks["hedge_funding_entry_block"] = funding_block

    ob_keys = ("ob_ask_wall_pct", "ob_ask_wall_size", "ob_bid_wall_pct", "ob_bid_wall_size")
    ob = load("hedge_red_zone_gate_orderbook", ob_keys, report=False)
    rz_block = zeros()
    if bool(_cfg(config, "RED_ZONE_GATE_ENABLED", False)) and bool(_cfg(config, "RED_ZONE_HEDGE_GATE_ENABLED", True)):
        min_dist = float(_cfg(config, "RED_ZONE_MIN_DISTANCE_PCT", .4))
        min_size = float(_cfg(config, "RED_ZONE_MIN_WALL_NOTIONAL_USD", 50000.0))
        ob_present = zeros()
        if ob is not None:
            ob_present = finite(*ob.values())
            if hedge_long:
                rz_block |= ob_present & ob_fresh & (ob["ob_ask_wall_pct"] < min_dist) & (ob["ob_ask_wall_size"] >= min_size)
            else:
                rz_block |= ob_present & ob_fresh & (ob["ob_bid_wall_pct"] < min_dist) & (ob["ob_bid_wall_size"] >= min_size)
        if bool(_cfg(config, "RED_ZONE_GATE_FALLBACK_ENABLED", True)):
            fb_keys = ("stoch_k_15m", "stoch_k_1m", "stoch_k_1m_prev") + (("high_1h", "high_1h_prev", "high_4h", "high_4h_prev") if hedge_long else ("low_1h", "low_1h_prev", "low_4h", "low_4h_prev"))
            fb = load("hedge_red_zone_gate_fallback", fb_keys)
            if fb is not None:
                if hedge_long:
                    fb_block = (fb["stoch_k_15m"] >= float(_cfg(config, "RED_ZONE_FALLBACK_K15_HIGH", 80))) & (fb["stoch_k_1m"] < fb["stoch_k_1m_prev"])
                    fb_block &= (fb["high_1h"] > 0) & (fb["high_1h"] < fb["high_1h_prev"]) & (fb["high_4h"] > 0) & (fb["high_4h"] < fb["high_4h_prev"])
                else:
                    fb_block = (fb["stoch_k_15m"] <= float(_cfg(config, "RED_ZONE_FALLBACK_K15_LOW", 20))) & (fb["stoch_k_1m"] > fb["stoch_k_1m_prev"])
                    fb_block &= (fb["low_1h"] > 0) & (fb["low_1h"] > fb["low_1h_prev"]) & (fb["low_4h"] > 0) & (fb["low_4h"] > fb["low_4h_prev"])
                rz_block |= ~ob_present & finite(*fb.values()) & fb_block
    masks["hedge_red_zone_entry_block"] = rz_block

    masks["hedge_entry_filter_admit"] = active & ~rank_block & ~oi_block & ~funding_block & ~rz_block
    return ExitReduceBatch6Result(MappingProxyType(masks), MappingProxyType(values), MappingProxyType(missing))


def consume_lifecycle_actions(result: ExitReduceBatch6Result) -> LifecycleActionResult:
    m = result.masks
    z = np.zeros_like(m["partial_profit_lock_reduce"])
    return LifecycleActionResult(MappingProxyType({
        "ENTRY": z,
        "REENTRY": z,
        "AUGMENT": z,
        "REDUCE": m["partial_profit_lock_reduce"],
        "EXIT": z,
        "STOP": z,
        "HEDGE_OPEN": m["hedge_scan_filter_admit"] & m["hedge_entry_filter_admit"],
        "EXIT_FILTER_GT": m["noloss_profit_gt_filter"],
        "EXIT_FILTER_GE": m["noloss_profit_ge_filter"],
        "REDUCE_FILTER_GT": m["noloss_profit_gt_filter"],
    }))


__all__ = ["ExitReduceBatch6Result", "LifecycleActionResult", "consume_lifecycle_actions", "evaluate_gap_batch6"]
