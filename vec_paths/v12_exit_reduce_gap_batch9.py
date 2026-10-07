"""Source-exact session, position-age, and selected-TF exit filters, batch 9.

This module is intentionally disjoint from :mod:`v12_quick_engine`.  Wall-clock,
account, position, and portfolio facts used by live are explicit caller state.
Selected timeframe indicators are never substituted with another timeframe.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from mtf_exit_timing import position_open_is_eligible
from tradier_exit_indicator_contract_c5 import completed_parent_value_pair


@dataclass(frozen=True)
class ExitReduceBatch9Result:
    masks: Mapping[str, np.ndarray]
    values: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class LifecycleActionResult:
    masks: Mapping[str, np.ndarray]


def _cfg(config: Any, name: str, default: Any) -> Any:
    return config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)


def _state(value: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    if array.ndim == 0:
        return np.full(n, array.item(), dtype=dtype)
    if array.ndim != 1 or len(array) != n:
        raise ValueError(f"caller state {name!r} must be scalar or shape ({n},)")
    return array


def evaluate_gap_batch9(
    npz: Mapping[str, Any],
    config: Any,
    *,
    position_side: str,
    gain_pct: Any = 0.0,
    position_active: Any = True,
    weekday_et: Any = 0,
    minutes_since_open_et: Any = np.inf,
    current_et_minutes: Any = 0,
    regular_trading_session: Any = True,
    opening_buffer_exit_bypass: Any = False,
    daytrade_preclose_selected: Any = False,
    position_open_ts: Any = 0.0,
    mtf_startup_ts: Any = 0.0,
    elapsed_since_last_reduction_minutes: Any = np.inf,
    mandatory_reentry: Any = False,
    ordinary_parity_action: Any = False,
    strict_no_loss_account: Any = False,
    account_key: str = "",
    dc_recovery_exit_candidate: Any = False,
    bar_count: int | None = None,
) -> ExitReduceBatch9Result:
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
    active = _state(position_active, n, "position_active", bool)
    gain = _state(gain_pct, n, "gain_pct", float)
    weekday = _state(weekday_et, n, "weekday_et", int)
    minutes_open = _state(minutes_since_open_et, n, "minutes_since_open_et", float)
    current_minutes = _state(current_et_minutes, n, "current_et_minutes", float)
    regular = _state(regular_trading_session, n, "regular_trading_session", bool)
    early_bypass = _state(opening_buffer_exit_bypass, n, "opening_buffer_exit_bypass", bool)
    preclose_selected = _state(daytrade_preclose_selected, n, "daytrade_preclose_selected", bool)
    opened_ts = _state(position_open_ts, n, "position_open_ts", float)
    startup_ts = _state(mtf_startup_ts, n, "mtf_startup_ts", float)
    since_reduction = _state(elapsed_since_last_reduction_minutes, n, "elapsed_since_last_reduction_minutes", float)
    mandatory = _state(mandatory_reentry, n, "mandatory_reentry", bool)
    ordinary = _state(ordinary_parity_action, n, "ordinary_parity_action", bool)
    strict = _state(strict_no_loss_account, n, "strict_no_loss_account", bool)
    dc_candidate = _state(dc_recovery_exit_candidate, n, "dc_recovery_exit_candidate", bool)
    masks: dict[str, np.ndarray] = {}
    values: dict[str, np.ndarray] = {}
    missing: dict[str, tuple[str, ...]] = {}

    def load(path: str, keys: Sequence[str]) -> dict[str, np.ndarray] | None:
        out: dict[str, np.ndarray] = {}
        bad: list[str] = []
        for key in dict.fromkeys(keys):
            raw = npz.get(key)
            if raw is None:
                bad.append(key)
                continue
            array = np.asarray(raw)
            if array.ndim != 1 or len(array) != n:
                bad.append(key)
                continue
            try:
                out[key] = array.astype(float, copy=False)
            except (TypeError, ValueError):
                bad.append(key)
        if bad:
            missing[path] = tuple(bad)
            return None
        return out

    # Tradier's first-N-minutes weekday gate.  The stop path's E02 exception is
    # explicit rather than silently incorporated into the generic buffer mask.
    opening_minutes = float(_cfg(config, "OPENING_BUFFER_NO_CLOSE_MINUTES", 30.0))
    opening = (
        (opening_minutes > 0.0)
        & (weekday >= 0)
        & (weekday < 5)
        & np.isfinite(minutes_open)
        & (minutes_open >= 0.0)
        & (minutes_open < opening_minutes)
    )
    masks["opening_buffer_block"] = opening
    masks["opening_buffer_exit_block"] = opening & ~early_bypass

    # StockDaytradeWing._is_pre_close plus an explicit portfolio-selected row.
    # The two MARKET_CLOSE fields own the timing window, not portfolio ranking.
    close_minutes = int(_cfg(config, "MARKET_CLOSE_HOUR", 16)) * 60 + int(_cfg(config, "MARKET_CLOSE_MINUTE", 0))
    remaining = close_minutes - current_minutes
    preclose_window = regular & (remaining > 0.0) & (remaining <= float(_cfg(config, "DC_DAYTRADE_PRE_CLOSE_MINUTES", 120.0)))
    masks["daytrade_preclose_window"] = preclose_window
    masks["daytrade_preclose_exit"] = active & preclose_window & preclose_selected
    values["minutes_to_market_close"] = remaining.astype(float, copy=False)

    # LONG/SHORT_STRUCT_EXIT_TF is a side-specific fallback only when the common
    # EXIT_STRUCT_TF is exactly disabled, matching tradier_manage.py:5638-5668.
    common_tf = str(_cfg(config, "EXIT_STRUCT_TF", "None") or "None")
    side_name = "LONG_STRUCT_EXIT_TF" if long else "SHORT_STRUCT_EXIT_TF"
    side_tf = str(_cfg(config, side_name, "None") or "None")
    selected_tf = side_tf if common_tf == "None" else "None"
    structural = zeros()
    if selected_tf != "None":
        keys = (f"open_{selected_tf}", f"high_{selected_tf}_prev", f"low_{selected_tf}_prev")
        arrays = load("side_structural_exit", keys)
        if arrays is not None:
            last_open = last_high = last_low = None
            for i in range(n):
                op, high, low = (arrays[key][i] for key in keys)
                if not active[i]:
                    last_open = last_high = last_low = None
                    continue
                if not np.isfinite([op, high, low]).all() or min(op, high, low) <= 0:
                    continue
                if last_open is None:
                    last_open, last_high, last_low = op, high, low
                elif abs(op - last_open) > 1e-8:
                    structural[i] = (high < last_high and low < last_low) if long else (high > last_high and low > last_low)
                    last_open, last_high, last_low = op, high, low
    masks["side_structural_exit"] = structural & active

    # MTF compound positions use the configured absolute cutoff; non-positive
    # config values resolve to manager startup.  Invalid/missing opens fail closed.
    configured_cutoff = float(_cfg(config, "MTF_EXIT_MIN_OPEN_TS", 0.0))
    cutoff = np.full(n, configured_cutoff, dtype=float) if configured_cutoff > 0 else startup_ts.copy()
    eligible = zeros()
    if bool(_cfg(config, "MTF_EXIT_USE_COMPOUND", False)):
        for i in range(n):
            eligible[i] = active[i] and position_open_is_eligible(opened_ts[i], cutoff[i])
    masks["mtf_compound_position_eligible"] = eligible
    values["effective_mtf_min_open_ts"] = cutoff

    # Strict-stock no-loss bypass: all five native Tradier WT timeframes are
    # required.  Missing arrays do not become live's numeric zero defaults.
    wt_keys = tuple(f"wt{which}_{tf}" for tf in ("5m", "15m", "1h", "4h", "D") for which in (1, 2))
    arrays = load("noloss_bypass_wt_5of5", wt_keys)
    adverse_count = np.zeros(n, dtype=int)
    bypass = zeros()
    noloss_threshold = float(_cfg(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))
    noloss_applies = active & strict & (noloss_threshold > 0.0) & np.isfinite(gain) & (gain < noloss_threshold)
    if bool(_cfg(config, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False)) and arrays is not None:
        valid = np.ones(n, dtype=bool)
        for tf in ("5m", "15m", "1h", "4h", "D"):
            w1, w2 = arrays[f"wt1_{tf}"], arrays[f"wt2_{tf}"]
            valid &= np.isfinite(w1) & np.isfinite(w2)
            adverse_count += (w1 < w2) if long else (w1 > w2)
        bypass = noloss_applies & valid & (adverse_count >= int(_cfg(config, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS", 5)))
    masks["noloss_bypass_wt_5of5"] = bypass
    masks["noloss_exit_filter_block"] = noloss_applies & ~bypass
    values["noloss_adverse_wt_tf_count"] = adverse_count

    # C5 completed-parent STDEV failed-breakout rejection.  Prefer an explicit
    # persisted predecessor; otherwise require a native completed-parent token.
    brz_tf = str(_cfg(config, "STDEV_BB_RZ_EXIT_TF", "D") or "D")
    current_key, previous_key = f"bb_pct_b_{brz_tf}", f"bb_pct_b_{brz_tf}_prev"
    token_key = f"_completed_source_ts_{brz_tf}" if f"_completed_source_ts_{brz_tf}" in npz else f"timestamp_{brz_tf}"
    brz = zeros()
    current_raw = npz.get(current_key)
    current_ok = current_raw is not None and np.asarray(current_raw).ndim == 1 and len(np.asarray(current_raw)) == n
    velocity_raw = npz.get("wt_velocity_1h")
    velocity_ok = velocity_raw is not None and np.asarray(velocity_raw).ndim == 1 and len(np.asarray(velocity_raw)) == n
    previous_raw = npz.get(previous_key)
    previous_ok = previous_raw is not None and np.asarray(previous_raw).ndim == 1 and len(np.asarray(previous_raw)) == n
    token_raw = npz.get(token_key)
    token_ok = token_raw is not None and np.asarray(token_raw).ndim == 1 and len(np.asarray(token_raw)) == n
    if not current_ok or not velocity_ok or (not previous_ok and not token_ok):
        bad = []
        if not current_ok: bad.append(current_key)
        if not velocity_ok: bad.append("wt_velocity_1h")
        if not previous_ok and not token_ok: bad.append(f"{previous_key}|_completed_source_ts_{brz_tf}|timestamp_{brz_tf}")
        missing["stdev_bb_rz_exit"] = tuple(bad)
    elif bool(_cfg(config, "STDEV_BB_RZ_EXIT_ENABLED", False)):
        current = np.asarray(current_raw, dtype=float)
        velocity = np.asarray(velocity_raw, dtype=float)
        previous = np.asarray(previous_raw, dtype=float) if previous_ok else None
        tokens = np.asarray(token_raw) if token_ok else None
        parent_state: dict[str, Any] = {}
        for i in range(n):
            if previous is not None:
                now_pctb, prev_pctb = current[i], previous[i]
            else:
                now_pctb, prev_pctb = completed_parent_value_pair(
                    {current_key: current[i], token_key: tokens[i]},
                    timeframe=brz_tf,
                    state=parent_state,
                    state_key=f"{side}:STDEV_BB_RZ:{brz_tf}",
                )
            if not active[i] or not np.isfinite([now_pctb, prev_pctb, velocity[i]]).all():
                continue
            brz[i] = (prev_pctb >= 1.0 and now_pctb < 1.0 and velocity[i] < 0.0) if long else (prev_pctb <= 0.0 and now_pctb > 0.0 and velocity[i] > 0.0)
    masks["stdev_bb_rz_exit"] = brz

    # Queue boundary cooldown is nevertheless a causal open/reentry/hedge filter.
    cooldown = float(_cfg(config, "TRADIER_POST_CLOSE_COOLDOWN_MIN", 15.0))
    masks["post_close_entry_block"] = (cooldown > 0.0) & np.isfinite(since_reduction) & (since_reduction < cooldown) & ~mandatory & ~ordinary

    # Account exclusion controls the existing DC recovery exit candidate only.
    disabled_accounts = _cfg(config, "DC_RECOVERY_EXIT_DISABLED_ACCOUNTS", ["inf"]) or []
    account_disabled = str(account_key) in {str(value) for value in disabled_accounts}
    masks["dc_recovery_exit_filter_block"] = dc_candidate & account_disabled
    masks["dc_recovery_exit"] = dc_candidate & ~account_disabled

    return ExitReduceBatch9Result(
        masks=MappingProxyType(masks),
        values=MappingProxyType(values),
        missing_arrays=MappingProxyType(missing),
    )


def consume_lifecycle_actions(result: ExitReduceBatch9Result) -> LifecycleActionResult:
    m = result.masks
    zeros = np.zeros_like(m["opening_buffer_block"])
    exits = m["daytrade_preclose_exit"] | m["side_structural_exit"] | m["stdev_bb_rz_exit"] | m["dc_recovery_exit"]
    common_buffer = m["opening_buffer_block"]
    return LifecycleActionResult(masks=MappingProxyType({
        "ENTRY": zeros,
        "REENTRY": zeros,
        "AUGMENT": zeros,
        "REDUCE": zeros,
        "EXIT": exits,
        "STOP": exits,
        "ENTRY_FILTER_BLOCK": common_buffer | m["post_close_entry_block"],
        "REENTRY_FILTER_BLOCK": common_buffer | m["post_close_entry_block"],
        "AUGMENT_FILTER_BLOCK": common_buffer,
        "EXIT_FILTER_BLOCK": m["opening_buffer_exit_block"] | m["noloss_exit_filter_block"] | m["dc_recovery_exit_filter_block"],
        "REDUCE_FILTER_BLOCK": m["opening_buffer_exit_block"] | m["noloss_exit_filter_block"],
        "HEDGE_OPEN_FILTER_BLOCK": m["post_close_entry_block"],
        "MTF_COMPOUND_EXIT_FILTER_ALLOW": m["mtf_compound_position_eligible"],
        "DAYTRADE_PRE_CLOSE_WINDOW": m["daytrade_preclose_window"],
    }))


__all__ = ["ExitReduceBatch9Result", "LifecycleActionResult", "consume_lifecycle_actions", "evaluate_gap_batch9"]
