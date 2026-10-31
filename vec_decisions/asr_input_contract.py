"""Strict NPZ and causal-state binding contract for ordered ASR array stages."""

from collections.abc import Mapping
from typing import Any

import numpy as np

from vec_decisions.asr_input_adapter import paired_npz_view


class ASRInputUnavailable(ValueError):
    """Raised when a requested ordered score stage lacks a real input array."""


STAGE_INPUTS = {
    "dc_structure": {
        "npz": tuple(f"dc_{edge}_{tf}{suffix}" for tf in ("3m", "15m", "1h", "4h") for edge in ("low", "high") for suffix in ("", "_ant")),
        "state": (),
    },
    "directional": {
        "npz": ("k_1h", "d_1h", "dc_basis_15m", "dc_basis_1h", "dc_basis_4h", "sma_200_1m", "sma_200_15m", "sma_200_1h", "ha_15m", "ha_1h", "wt_score_15m", "wt_score_1h"),
        "state": ("market_price",),
    },
    "wt_intelligence": {
        "npz": ("wt_divergence_1h", "wt_divergence_4h", "wt_momentum_state_1h", "wt_momentum_state_4h", "wt_trough_structure_1h", "wt_peak_structure_1h", "wt_trough_structure_4h", "wt_peak_structure_4h", "wt_cross_1h", "wt_cross_rising_1h", "wt_percentile_1h", "wt_percentile_4h", "wt_extreme_1h", "wt_trough_structure_D", "wt_peak_structure_D"),
        "state": (),
    },
    "volume_setup": {
        "npz": ("relative_volume_1h", "zconviction_augment_long", "zconviction_augment_short", "dc_high_1h", "dc_low_1h", "k_1m", "k_1m_prev", "k_3m", "d_3m"),
        "state": ("market_price", "trend_ok", "pa_confirm", "pr_confirm"),
    },
    "stoch_entry": {
        "npz": ("wt_bullish_3m", "wt_score_3m", "wt_velocity_3m", "wt_cross_bull_3m", "wt_momentum_state_1h", "k_15m", "k_15m_prev", "k_3m", "d_3m"),
        "state": (),
    },
    "mts_gate_bonus_mi": {
        "npz": ("wt_trough_structure_1h", "wt_trough_structure_4h", "wt_peak_structure_1h", "wt_peak_structure_4h", "wt_momentum_state_1h", "wt_momentum_state_4h", "wt_divergence_1h"),
        "state": ("mts_bottom", "mts_entry_quality"),
    },
    "history": {
        "npz": (),
        "state": ("history_present", "win_rate", "total_trades", "total_realized_pnl"),
    },
    "portfolio_ratio": {
        "npz": (),
        "state": ("portfolio_ratio_present", "portfolio_ratio"),
    },
    "rank_sentiment": {
        "npz": ("0is_top_sentiment", "0is_bottom_sentiment", "0ranking_points", "0market_sentiment_local", "0market_sentiment_global", "0sentiment_classification"),
        "state": (),
    },
}


EXTERNAL_STATE_DEPENDENCIES = {
    "freshness_and_eligibility": ("tick_timestamp", "allowed", "tradeable_key"),
    "position_lifecycle": ("is_exit", "gain", "prev_gain", "last_exit_timestamp", "last_reduction_price", "last_reduction_time", "last_augmentation_time", "opened_at", "entry_price"),
    "tracker_history": ("history_present", "win_rate", "total_trades", "total_realized_pnl"),
    "account_portfolio": ("portfolio_ratio_present", "portfolio_ratio"),
    "sentiment_registry": ("raw_score_by_symbol", "final_score_norm_by_symbol", "universe", "local_sentiment", "global_sentiment", "top_flags", "bottom_flags", "ranking_points"),
    "delta_tracker": ("delta_signal", "active_timeframes", "reentry_markers"),
    "event_cache": ("latest_cross_events", "previous_cross_events"),
}


def _cfg_value(cfg: Any, name: str, default: Any) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _as_column(value: Any, name: str, n: int) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim == 0:
        raise ASRInputUnavailable(f"{name}: scalar supplied; expected explicit ({n},) causal array")
    array = array.reshape(-1)
    if array.size != n:
        raise ASRInputUnavailable(f"{name}: length {array.size}; expected {n}")
    return array


def bind_asr_stage_arrays(npz: Any, cfg: Any, causal_state: Mapping[str, Any], n: int, is_long: bool, stages: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Bind exact stage inputs without filling absent NPZ or causal-state values.

    Per-symbol config is supplied by the caller. `market_price`, tracker/portfolio
    arrays and other state must be causal arrays with exactly n rows.
    """
    if n <= 0:
        raise ASRInputUnavailable(f"invalid bar count {n}")
    if not isinstance(causal_state, Mapping):
        raise ASRInputUnavailable("causal_state must be a mapping of explicit arrays")
    use_ltf = bool(_cfg_value(cfg, "USE_1M_3M_SIGNALS_ENABLED", False))
    selected = paired_npz_view(npz, use_ltf)
    requested = tuple(STAGE_INPUTS) if stages is None else tuple(stages)
    unknown = sorted(set(requested) - set(STAGE_INPUTS))
    if unknown:
        raise ASRInputUnavailable(f"unknown ASR stages: {','.join(unknown)}")
    bound = {"n": n, "is_ltf_enabled": use_ltf, "is_long": bool(is_long), "cfg": cfg, "npz": selected, "stages": {}}
    failures = []
    for stage in requested:
        spec = STAGE_INPUTS[stage]
        stage_values = {}
        missing_npz = [key for key in spec["npz"] if key not in selected]
        missing_state = [key for key in spec["state"] if key not in causal_state]
        if stage == "volume_setup":
            side_key = "zconviction_augment_long" if is_long else "zconviction_augment_short"
            if side_key in selected:
                stage_values["z_conviction"] = _as_column(selected[side_key], side_key, n)
            else:
                missing_npz.append(side_key)
            missing_npz = [key for key in missing_npz if not key.startswith("zconviction_augment_")]
        for key in spec["npz"]:
            if key in selected:
                stage_values[key] = _as_column(selected[key], key, n)
        for key in spec["state"]:
            if key in causal_state:
                stage_values[key] = _as_column(causal_state[key], key, n)
        if missing_npz or missing_state:
            failures.append(f"{stage}: missing NPZ[{','.join(sorted(set(missing_npz)))}] STATE[{','.join(missing_state)}]")
        else:
            bound["stages"][stage] = stage_values
    if failures:
        raise ASRInputUnavailable("; ".join(failures))
    return bound
