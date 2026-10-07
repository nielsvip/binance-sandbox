"""Second strict v12 EXIT/REDUCE/STOP vector contract batch.

The predicates below are direct translations of the cited live/research
sources.  They consume persisted arrays and explicit position/execution state;
they do not substitute timeframes or manufacture effects.  Missing or malformed
persisted inputs fail only the affected path closed and are reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ExitReduceStopBatch2Result:
    masks: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]
    mi_signal_count: np.ndarray
    all_tf_against_count: np.ndarray
    hlr_confirm_count: np.ndarray


def _cfg(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)


def _n(npz: Mapping[str, Any], bar_count: int | None) -> int:
    if bar_count is not None:
        if int(bar_count) < 0:
            raise ValueError("bar_count must be non-negative")
        return int(bar_count)
    for value in npz.values():
        arr = np.asarray(value)
        if arr.ndim == 1:
            return len(arr)
    raise ValueError("bar_count is required when no 1-D persisted array exists")


def _state(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=dtype)
    if arr.ndim == 0:
        return np.full(n, arr.item(), dtype=dtype)
    if arr.ndim != 1 or len(arr) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return arr


def evaluate_gap_batch2(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any,
    position_age_minutes: Any,
    position_active: Any = True,
    position_above_min_qty: Any = True,
    entry_price: Any = 0.0,
    max_gain_pct: Any = 0.0,
    is_hedge: Any = False,
    in_grace_period: Any = False,
    hard_exit_pending: Any = False,
    trend_regime_veto: Any = False,
    winner_protect_veto: Any = False,
    is_trend_account: Any = False,
    seconds_since_all_tf_close: Any = np.inf,
    fresh_gain_pct: Any | None = None,
    hard_exit_reason: Any = "",
    rate_recommendation: Any = "HOLD",
    rate_reason: Any = "",
    rate_score: Any = 0.0,
    stdev_breakout_active: Any = False,
    stdev_breakout_tf: str = "D",
    bar_count: int | None = None,
) -> ExitReduceStopBatch2Result:
    """Evaluate independent source predicates; integration owns precedence/fills."""
    side = str(position_side).upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("position_side must be LONG or SHORT")
    long = side == "LONG"
    n = _n(npz, bar_count)
    z = lambda: np.zeros(n, dtype=bool)
    gain = _state(gain_pct, n, "gain_pct", float)
    age_m = _state(position_age_minutes, n, "position_age_minutes", float)
    active = _state(position_active, n, "position_active", bool)
    above = _state(position_above_min_qty, n, "position_above_min_qty", bool)
    entry = _state(entry_price, n, "entry_price", float)
    max_gain = _state(max_gain_pct, n, "max_gain_pct", float)
    hedge = _state(is_hedge, n, "is_hedge", bool)
    grace = _state(in_grace_period, n, "in_grace_period", bool)
    prior_exit = _state(hard_exit_pending, n, "hard_exit_pending", bool)
    trend_veto = _state(trend_regime_veto, n, "trend_regime_veto", bool)
    winner_veto = _state(winner_protect_veto, n, "winner_protect_veto", bool)
    trend_account = _state(is_trend_account, n, "is_trend_account", bool)
    since_all = _state(seconds_since_all_tf_close, n, "seconds_since_all_tf_close", float)
    fresh_gain = _state(gain if fresh_gain_pct is None else fresh_gain_pct, n, "fresh_gain_pct", float)
    reason = _state(hard_exit_reason, n, "hard_exit_reason", object)
    recommendation = _state(rate_recommendation, n, "rate_recommendation", object)
    rec_reason = _state(rate_reason, n, "rate_reason", object)
    rec_score = _state(rate_score, n, "rate_score", float)
    stdev_active = _state(stdev_breakout_active, n, "stdev_breakout_active", bool)
    pos = active & above

    masks: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str]) -> dict[str, np.ndarray] | None:
        bad: list[str] = []
        out: dict[str, np.ndarray] = {}
        for key in keys:
            if key not in npz:
                bad.append(key)
                continue
            arr = np.asarray(npz[key])
            if arr.ndim != 1 or len(arr) != n:
                bad.append(key)
                continue
            try:
                arr = arr.astype(float, copy=False)
            except (TypeError, ValueError):
                bad.append(key)
                continue
            out[key] = arr
        if bad:
            missing[path] = tuple(bad)
            return None
        return out

    def finite(*values: np.ndarray) -> np.ndarray:
        out = np.ones(n, dtype=bool)
        for value in values:
            out &= np.isfinite(value)
        return out

    def native_previous(values: np.ndarray, timestamps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Previous completed native-TF value for each broadcast row."""
        previous = np.full(n, np.nan, dtype=float)
        valid = np.zeros(n, dtype=bool)
        prior_group_value = np.nan
        last_ts = None
        for idx in range(n):
            ts = timestamps[idx]
            if last_ts is None or ts != last_ts:
                if last_ts is not None:
                    prior_group_value = values[idx - 1]
                last_ts = ts
            if np.isfinite(prior_group_value):
                previous[idx] = prior_group_value
                valid[idx] = True
        return previous, valid

    # ez_manage.py:41501-41570 — exact five-TF adverse vote and cooldown.
    all_keys = tuple(k for tf in ("3m", "15m", "1h", "4h", "D") for k in (f"wt1_{tf}", f"wt2_{tf}"))
    a = load("all_tf_against_close", all_keys)
    all_count = np.zeros(n, dtype=np.int8)
    m = z()
    if bool(_cfg(config, "ALL_TF_AGAINST_CLOSE_ENABLED", True)) and a is not None:
        valid = np.ones(n, dtype=bool)
        for tf in ("3m", "15m", "1h", "4h", "D"):
            w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
            all_count += ((w1 < w2) if long else (w1 > w2)).astype(np.int8)
            valid &= finite(w1, w2)
        m = pos & valid & (all_count >= int(_cfg(config, "ALL_TF_AGAINST_CLOSE_MIN_TFS", 4)))
        m &= since_all >= float(_cfg(config, "ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC", 30.0))
    masks["all_tf_against_close"] = m

    # ez_manage.py:40286-40327. Missing optional confirmations fail closed here.
    htf_tfs = ["1h"]
    if bool(_cfg(config, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)):
        htf_tfs.append("15m")
    if bool(_cfg(config, "HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", False)):
        htf_tfs.append("4h")
    if bool(_cfg(config, "HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M", True)):
        htf_tfs.append("3m")
    if bool(_cfg(config, "HTF_AGAINST_FORCE_CLOSE_CONFIRM_D", True)):
        htf_tfs.append("D")
    htf_keys = tuple(k for tf in htf_tfs for k in (f"wt1_{tf}", f"wt2_{tf}"))
    a = load("htf_against_force_close", htf_keys)
    m = z()
    if bool(_cfg(config, "HTF_AGAINST_FORCE_CLOSE_ENABLED", True)) and a is not None:
        w11, w21 = a["wt1_1h"], a["wt2_1h"]
        have1 = (np.abs(w11) > 1e-9) | (np.abs(w21) > 1e-9)
        m = pos & have1 & ((w11 < w21) if long else (w11 > w21)) & finite(*a.values())
        confirmations = (
            ("15m", "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True, False),
            ("4h", "HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", False, False),
            ("3m", "HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M", True, True),
            ("D", "HTF_AGAINST_FORCE_CLOSE_CONFIRM_D", True, True),
        )
        for tf, field, default, allow_missing_live in confirmations:
            if bool(_cfg(config, field, default)):
                w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
                directional = (w1 < w2) if long else (w1 > w2)
                have = (np.abs(w1) > 1e-9) | (np.abs(w2) > 1e-9)
                m &= directional | (~have if allow_missing_live else False)
    masks["htf_against_force_close"] = m

    # ez_positions_quick.py:14301-14317.
    a = load("trend_reversal_exit", ("htf_trend_score",))
    m = z()
    if a is not None:
        score = a["htf_trend_score"]
        flip = float(_cfg(config, "TREND_EXIT_SCORE_FLIP", 0))
        against = score <= flip if long else score >= -flip
        m = pos & ~prior_exit & ~hedge & ~winner_veto & trend_account & finite(score, gain)
        m &= gain >= float(_cfg(config, "TREND_MIN_GAIN_EXIT", 0.1))
        m &= against
    masks["trend_reversal_exit"] = m

    # ez_positions_quick.py:14322-14340.
    veto_keys = tuple(k for tf in ("1h", "4h", "D") for k in (f"wt1_{tf}", f"wt2_{tf}"))
    a = load("htf_exit_veto", veto_keys)
    veto = z()
    if bool(_cfg(config, "HTF_EXIT_VETO_ENABLED", True)) and a is not None:
        count = np.zeros(n, dtype=np.int8)
        for tf in ("1h", "4h", "D"):
            w1, w2 = a[f"wt1_{tf}"], a[f"wt2_{tf}"]
            count += ((w1 > w2) if long else (w1 < w2)).astype(np.int8)
        veto = pos & ~hedge & finite(*a.values(), gain)
        veto &= np.abs(gain) <= float(_cfg(config, "HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0))
        veto &= count >= int(_cfg(config, "HTF_EXIT_VETO_MIN_ALIGNED", 2))
    masks["htf_exit_veto"] = veto

    # ez_positions_quick.py:14340-14368. This reproduces the unusual hard-floor
    # behavior exactly: when enabled, insufficient peak blocks the exit.
    m = z()
    if bool(_cfg(config, "BREAKEVEN_GAIN_EROSION_ENABLED", False)):
        req_profit = bool(_cfg(config, "BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT", True))
        min_gain = float(_cfg(config, "BREAKEVEN_GAIN_EROSION_MIN_GAIN", 50.0))
        upper = max(min_gain + 0.5, 0.02)
        window = ((gain >= min_gain) & (gain < upper)) if req_profit else (gain < 0.02)
        hbf = bool(_cfg(config, "HARD_BREAKEVEN_FLOOR_ENABLED", True))
        peak_min = float(_cfg(config, "HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5))
        hbf_override = hbf & (max_gain >= peak_min)
        allowed = (~veto | hbf_override) & ((not hbf) | (max_gain >= peak_min))
        m = pos & ~prior_exit & ~hedge & ~grace & ~trend_veto & finite(gain, age_m, max_gain)
        m &= age_m >= float(_cfg(config, "BREAKEVEN_GRACE_MINUTES", 15.0))
        m &= window & allowed
    masks["breakeven_gain_erosion_exit"] = m

    # ez_positions_quick.py:14369-14393 — configurable DC vs DC4 persisted basis.
    mode = str(_cfg(config, "BREAKEVEN_DC_FIELD_MODE", "DC4")).upper()
    dc_keys = ("close_3m", "dc_low_3m", "dc_high_3m") if mode == "DC" else ("close_3m", "dc_low4_3m", "dc_high4_3m")
    a = load("breakeven_dc_structural_exit", dc_keys)
    m = z()
    if bool(_cfg(config, "BREAKEVEN_DC_LOW4_ENABLED", True)) and a is not None:
        price = a["close_3m"]
        low_key, high_key = dc_keys[1], dc_keys[2]
        boundary = a[low_key] if long else a[high_key]
        breach = (price < boundary) if long else (price > boundary)
        m = pos & ~prior_exit & ~grace & ~veto & finite(price, boundary)
        m &= (price > 0) & (boundary > 0) & breach
    masks["breakeven_dc_structural_exit"] = m

    # ez_positions_quick.py:14449-14467.
    m = z()
    if bool(_cfg(config, "PEAK_GIVEBACK_PROTECTION_ENABLED", True)):
        qualified = max_gain >= float(_cfg(config, "PEAK_GIVEBACK_MIN_PEAK_PCT", 0.5))
        hard_zero = bool(_cfg(config, "PEAK_GIVEBACK_HARD_ZERO_ENABLED", True)) & (gain < 0.08)
        drop = bool(_cfg(config, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False)) & (
            gain < max_gain - float(_cfg(config, "PEAK_GIVEBACK_DROP_PCT", 1.0))
        )
        m = pos & ~prior_exit & ~hedge & ~grace & finite(gain, max_gain) & qualified & (hard_zero | drop)
    masks["peak_giveback_exit"] = m

    # ez_positions_quick.py:14518-14536 — fresh-price stale exit decision.
    reason_s = np.asarray([str(x) for x in reason], dtype=object)
    technical_tokens = ("WT_CROSS_EXIT", "WT_CROSS_BULLISH", "WT_CROSS_BEARISH", "STDEV_BREAKOUT", "STRUCTURAL_RANGE_SHIFT", "EMERGENCY_DC1H_BREACH", "PARABOLIC_EXIT")
    breakeven_tokens = ("BREAKEVEN", "DC_LOW4_3M", "DC_HIGH4_3M", "PEAK_GIVEBACK")
    is_technical = np.array([any(t in s for t in technical_tokens) for s in reason_s])
    is_breakeven = np.array([any(t in s for t in breakeven_tokens) for s in reason_s])
    allow_near = bool(_cfg(config, "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED", False)) & (max_gain >= 0.5) & (fresh_gain > -0.5)
    technical_survives = bool(_cfg(config, "LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK", True)) & is_technical
    has_reason = reason_s != ""
    stale_abort = has_reason & (fresh_gain < -0.01) & ~hedge & ~allow_near & ~is_breakeven & ~technical_survives
    masks["stale_price_allow_near_breakeven"] = has_reason & allow_near & (fresh_gain < -0.01)
    masks["stale_price_abort"] = stale_abort

    # AdvancedSignalRater.rate: simple TP, MACD exit, and MI vote.
    masks["simple_tp_exit"] = pos & bool(_cfg(config, "SIMPLE_TP_EXIT_ENABLED", False)) & finite(gain) & (gain >= float(_cfg(config, "SIMPLE_TP_PCT", 0.5)))

    macd_tf = str(_cfg(config, "MACD_EXIT_TF", "15m"))
    macd_key = f"macd_crossunder_{macd_tf}" if long else f"macd_crossover_{macd_tf}"
    a = load("macd_exit", (macd_key,))
    m = z()
    if bool(_cfg(config, "MACD_EXIT_ENABLED", False)) and a is not None:
        m = pos & finite(gain, a[macd_key]) & (gain > float(_cfg(config, "MACD_EXIT_MIN_GAIN", 0.3))) & (a[macd_key] != 0)
    masks["macd_exit"] = m

    mi_keys_list: list[str] = []
    if bool(_cfg(config, "MI_STRUCT_EXIT_ENABLED", True)):
        prefix = "wt_peak_structure" if long else "wt_trough_structure"
        mi_keys_list.extend((f"{prefix}_1h", f"{prefix}_4h"))
    if bool(_cfg(config, "MI_EXHAUST_EXIT_ENABLED", True)):
        mi_keys_list.extend(("wt_momentum_state_1h", "wt_momentum_state_4h"))
    if bool(_cfg(config, "MI_DIV_EXIT_ENABLED", True)):
        mi_keys_list.extend(("wt_divergence_1h", "wt_divergence_4h"))
    if bool(_cfg(config, "MI_VELOCITY_EXIT_ENABLED", True)):
        mi_keys_list.extend(("wt_velocity_3m", "wt_velocity_1h", "wt_velocity_4h"))
    if bool(_cfg(config, "MI_WAVE_EXIT_ENABLED", True)):
        mi_keys_list.append("wt_wave_phase_1h")
    mi_keys = tuple(mi_keys_list)
    a = load("mi_exit", mi_keys)
    mi_count = np.zeros(n, dtype=np.int8)
    m = z()
    if bool(_cfg(config, "MI_EXIT_ENABLED", False)) and a is not None:
        if bool(_cfg(config, "MI_STRUCT_EXIT_ENABLED", True)):
            struct_code = -1 if long else 1
            struct_prefix = "wt_peak_structure" if long else "wt_trough_structure"
            mi_count += (a[f"{struct_prefix}_1h"] == struct_code).astype(np.int8)
            mi_count += (a[f"{struct_prefix}_4h"] == struct_code).astype(np.int8)
        if bool(_cfg(config, "MI_EXHAUST_EXIT_ENABLED", True)):
            code = 1 if long else -1
            mi_count += (a["wt_momentum_state_1h"] == code).astype(np.int8)
            mi_count += (a["wt_momentum_state_4h"] == code).astype(np.int8)
        if bool(_cfg(config, "MI_DIV_EXIT_ENABLED", True)):
            code = -1 if long else 1
            mi_count += (a["wt_divergence_1h"] == code).astype(np.int8)
            mi_count += (a["wt_divergence_4h"] == code).astype(np.int8)
        if bool(_cfg(config, "MI_VELOCITY_EXIT_ENABLED", True)):
            if long:
                vc = (a["wt_velocity_3m"] < -1).astype(int) + (a["wt_velocity_1h"] < -1).astype(int) + (a["wt_velocity_4h"] < -0.5).astype(int)
            else:
                vc = (a["wt_velocity_3m"] > 1).astype(int) + (a["wt_velocity_1h"] > 1).astype(int) + (a["wt_velocity_4h"] > 0.5).astype(int)
            mi_count += (vc >= 2).astype(np.int8)
        if bool(_cfg(config, "MI_WAVE_EXIT_ENABLED", True)):
            mi_count += (a["wt_wave_phase_1h"] == -1).astype(np.int8)
        m = pos & finite(gain, *a.values()) & (gain >= float(_cfg(config, "MI_MIN_GAIN_EXIT", 0.1)))
        m &= mi_count >= int(_cfg(config, "MI_TF_AGREE_MIN", 3))
    masks["mi_exit"] = m

    # ez_positions_quick.py:14655-14669. Prior bars are causal shifts.
    a = load("rule_b_3m_exit", ("high_3m", "low_3m"))
    m = z()
    if bool(_cfg(config, "RULE_B_3M_EXIT_ENABLED", True)) and a is not None:
        hi, lo = a["high_3m"], a["low_3m"]
        hip = np.roll(hi, 1); lop = np.roll(lo, 1)
        valid = finite(hi, lo, hip, lop) & (hi > 0) & (lo > 0) & (hip > 0) & (lop > 0)
        valid[0] = False
        no_existing = np.isin(np.char.upper(recommendation.astype(str)), ("HOLD", "WAIT", "BOYCOTT"))
        turn = ((lo < lop) & (hi < hip)) if long else ((hi > hip) & (lo > lop))
        m = pos & valid & no_existing & turn
    masks["rule_b_3m_exit"] = m

    # ez_positions_quick.py:14670-14681 — suppress unnamed composite stochastic reductions.
    rec_u = np.char.upper(recommendation.astype(str))
    text_u = np.char.upper(
        np.char.add(np.char.add(rec_reason.astype(str), "|"), recommendation.astype(str))
    )
    stoch_family = np.isin(rec_u, ("WEAK_REDUCE", "NO_PROFIT", "STRONG_REDUCE", "SCALP_REDUCE", "NOW_REDUCE", "REDUCE"))
    named_tokens = ("RULE_B_3M", "MTF_ATR_TRAIL", "R1_DC", "R2_WT", "WT_4H_VEL", "WT_CROSS", "WT_PERCENTILE", "WT_EXHAUST", "WT_DIV", "WT_ACCEL", "WT_MOMENTUM_EXIT", "GR_EXIT", "DC_HOPELESS", "HTF_AGAINST", "ALL_TF_AGAINST", "FAST_CUT_LOSS", "WRONG_SIDE", "PARTIAL", "PPL", "BREAK_EVEN", "HEDGE_FAILED", "PANIC")
    named = np.array([any(token in s for token in named_tokens) for s in text_u])
    hold = np.isin(rec_u, ("HOLD", "WAIT", "BOYCOTT"))
    rec_close = np.array(
        [any(token in s for token in ("CLOSE", "REDUCE", "PROFIT", "EXIT", "DECAY")) for s in rec_u]
    ) | (rec_score < -4)
    hard_present = reason_s != ""
    proposed = ~hold & (hard_present | (rec_close & ~grace))
    suppress = bool(_cfg(config, "QUICK_REDUCE_TECHNICAL_ONLY", True)) & proposed & ~hard_present & stoch_family & ~named
    masks["quick_reduce_allowed"] = proposed & ~suppress
    masks["quick_reduce_suppressed"] = suppress

    # Stateful STDEV breakout failure and stateless rejection path.
    tf = str(stdev_breakout_tf)
    pct_key = f"bb_pct_b_{tf}"
    stdev_wt_enabled = bool(_cfg(config, "STDEV_BREAKOUT_EXIT_WT_ENABLED", True))
    stdev_keys = (pct_key, "wt1_1h", "wt2_1h") if stdev_wt_enabled else (pct_key,)
    a = load("stdev_breakout_exit", stdev_keys)
    m = z()
    if bool(_cfg(config, "STDEV_BREAKOUT_ENABLED", False)) and a is not None:
        pct = a[pct_key]
        threshold = float(_cfg(config, "STDEV_BREAKOUT_EXIT_PCTB_FAIL", 0.75))
        fail = pct < threshold if long else pct > 1.0 - threshold
        if stdev_wt_enabled:
            w1, w2 = a["wt1_1h"], a["wt2_1h"]
            wt_turn = ((w1 < w2) & (w1 > 60)) if long else ((w1 > w2) & (w1 < -60))
            valid = finite(pct, w1, w2)
        else:
            wt_turn = z()
            valid = finite(pct)
        m = pos & stdev_active & valid & (fail | wt_turn)
    masks["stdev_breakout_exit"] = m

    reject_tf = str(_cfg(config, "STDEV_REJECT_EXIT_TF", "D"))
    reject_key = f"bb_pct_b_{reject_tf}"
    reject_ts_key = f"timestamp_{reject_tf}"
    a = load("stdev_reject_exit", (reject_key, reject_ts_key, "wt_velocity_1h"))
    m = z()
    if bool(_cfg(config, "STDEV_REJECT_EXIT_ENABLED", False)) and a is not None:
        pct, vel = a[reject_key], a["wt_velocity_1h"]
        prev, prev_valid = native_previous(pct, a[reject_ts_key])
        zone = float(_cfg(config, "STDEV_REJECT_EXIT_ZONE", 0.8))
        ret = float(_cfg(config, "STDEV_REJECT_EXIT_RETURN", 0.65))
        turn = ((prev >= zone) & (pct < ret) & (vel < 0)) if long else ((prev <= 1-zone) & (pct > 1-ret) & (vel > 0))
        m = pos & prev_valid & finite(pct, prev, vel) & turn
    masks["stdev_reject_exit"] = m

    # AdvancedSignalRater HLR top path. All W/D inputs are native persisted
    # requirements; absence is honest N/A rather than a lower-TF proxy.
    hlr_keys = (
        "wt_velocity_1h", "wt_velocity_4h", "wt_velocity_D", "wt_velocity_W",
        "wt_momentum_state_4h", "wt_momentum_state_D", "wt_momentum_state_W",
        "wt_divergence_4h", "wt_divergence_D", "wt_peak_structure_4h", "wt_peak_structure_D",
    )
    a = load("hlr_top_exit", hlr_keys)
    hlr_count = np.zeros(n, dtype=np.int8)
    m = z()
    if bool(_cfg(config, "HLR_TOP_EXIT_ENABLED", True)) and a is not None:
        sign = 1 if long else -1
        c1 = a["wt_velocity_1h"] < float(_cfg(config, "HLR_TOP_VEL_1H_THRESH", -1.0)) if long else a["wt_velocity_1h"] > abs(float(_cfg(config, "HLR_TOP_VEL_1H_THRESH", -1.0)))
        c4 = ((a["wt_velocity_4h"] < float(_cfg(config, "HLR_TOP_VEL_4H_THRESH", 0.0))) if long else (a["wt_velocity_4h"] > 0)) | (a["wt_momentum_state_4h"] == sign) | (a["wt_divergence_4h"] == (-1 if long else 1)) | (a["wt_peak_structure_4h"] == (-1 if long else 1))
        cd = ((a["wt_velocity_D"] < float(_cfg(config, "HLR_TOP_VEL_D_THRESH", 0.0))) if long else (a["wt_velocity_D"] > 0)) | (a["wt_momentum_state_D"] == sign) | (a["wt_divergence_D"] == (-1 if long else 1))
        cw = ((a["wt_velocity_W"] < 0) if long else (a["wt_velocity_W"] > 0)) | (a["wt_momentum_state_W"] == sign)
        hlr_count = c1.astype(np.int8) + c4.astype(np.int8) + cd.astype(np.int8) + cw.astype(np.int8)
        m = pos & finite(gain, *a.values()) & (gain >= float(_cfg(config, "NOLOSS_MIN_PROFIT_PCT", 0.5))) & (gain >= float(_cfg(config, "HLR_TOP_MIN_GAIN_PCT", 1.5)))
        m &= hlr_count >= int(_cfg(config, "HLR_TOP_MIN_TFS", 2))
        m &= c4 | cd | cw
    masks["hlr_top_exit"] = m

    return ExitReduceStopBatch2Result(
        masks=MappingProxyType(masks),
        missing_arrays=MappingProxyType(missing),
        mi_signal_count=mi_count,
        all_tf_against_count=all_count,
        hlr_confirm_count=hlr_count,
    )


__all__ = ["ExitReduceStopBatch2Result", "evaluate_gap_batch2"]
