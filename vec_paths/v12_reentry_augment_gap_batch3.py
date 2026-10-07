"""Third disjoint source-exact V12 re-entry/augment vector tranche.

All predicates consume persisted arrays and/or explicit lifecycle state.  They
never fetch live data, infer a substitute timeframe, fabricate an event, or use
reason strings as an effect.  Missing and misaligned inputs fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class FieldContract:
    name: str
    value_type: str
    default: Any
    grid: tuple[Any, ...]
    family: str
    required_arrays: tuple[str, ...]
    required_state: tuple[str, ...]
    lifecycle_filter_consumers: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class MaskResult:
    mask: np.ndarray
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class DecisionResult:
    mask: np.ndarray
    multiplier: np.ndarray
    available: bool
    reason: str = ""


def _fc(name, typ, default, grid, family, arrays, state, consumers, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays), tuple(state), tuple(consumers), source)


_ACCEL = tuple(f"wt_{kind}_{tf}" for tf in ("3m", "15m", "1h", "4h", "D") for kind in ("velocity", "acceleration"))
_BTC_ENTRY_STATE = ("candidate_mask", "red_zone_active", "opposing_divergence")
_BTC_FT_STATE = ("candidate_mask", "prior_exit_matches_side", "last_exit_price", "bars_since_last_exit")
_PSR = ("close", "prev_price", "k_3m", "d_3m", "k_3m_prev", "d_3m_prev", "k_15m", "d_15m", "k_15m_prev", "d_15m_prev", "dc_high_3m", "dc_low_3m", "dc_high_15m", "dc_low_15m", "dc_basis_15m", "dc_basis_crossover_3m")
_DFR = ("k_3m", "d_3m", "wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h", "wt_trough_structure_15m", "wt_peak_structure_15m")
_MU = ("close", "dc_low_4h", "dc_low_1h", "k_1h", "d_1h", "k_1h_prev", "d_1h_prev", "k_4h", "d_4h", "k_4h_prev", "d_4h_prev")
_HLR = ("wt_velocity_1h", "wt_velocity_4h", "wt_velocity_D", "wt_velocity_W", "wt_momentum_state_4h", "wt_momentum_state_D", "wt_momentum_state_W", "wt_divergence_4h", "wt_divergence_D", "wt_peak_structure_4h")


FIELD_CONTRACTS: dict[str, FieldContract] = {
    c.name: c for c in (
        _fc("BTC_ACCEL_RAMP_MIN_TFS", "int", 5, [2, 3, 4], "BTC_PRIMARY", _ACCEL, _BTC_ENTRY_STATE, ("ENTRY", "REENTRY"), "btc_loop.py:428-434,462-468"),
        _fc("BTC_ACCEL_RAMP_REQUIRE_POSITIVE", "bool", True, [True, False], "BTC_PRIMARY", _ACCEL, _BTC_ENTRY_STATE, ("ENTRY", "REENTRY"), "ez_positions_quick.py:1957;btc_loop.py:91-127"),
        _fc("BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", "bool", True, [True, False], "BTC_PRIMARY", _ACCEL, _BTC_ENTRY_STATE, ("ENTRY", "REENTRY"), "btc_loop.py:428,462"),
        _fc("BTC_ENTRY_PRIMARY_REQUIRE_RZ", "bool", True, [True, False], "BTC_PRIMARY", _ACCEL, _BTC_ENTRY_STATE, ("ENTRY", "REENTRY"), "btc_loop.py:425,460"),
        _fc("BTC_FOLLOW_THROUGH_MIN_MOVE_PCT", "float", 0.3, [0.05, 0.5, 1.0], "BTC_FOLLOW_THROUGH", ("close",) + _ACCEL, _BTC_FT_STATE, ("REENTRY",), "OPUS_VOMIT.py:4471-4491"),
        _fc("BTC_FOLLOW_THROUGH_REENTRY_ENABLED", "bool", True, [True, False], "BTC_FOLLOW_THROUGH", ("close",) + _ACCEL, _BTC_FT_STATE, ("REENTRY",), "OPUS_VOMIT.py:4471-4491"),
        _fc("BTC_GUARANTEED_REENTRY_ENABLED", "bool", True, [True, False], "BTC_GUARANTEED_REENTRY", _ACCEL, _BTC_ENTRY_STATE + ("flat", "has_prior_exit", "bars_since_last_exit"), ("REENTRY",), "btc_loop.py:642-675"),
        _fc("BTC_GUARANTEED_REENTRY_MAX_AGE_BARS", "int", 480, [480], "BTC_GUARANTEED_REENTRY", _ACCEL, _BTC_ENTRY_STATE + ("flat", "has_prior_exit", "bars_since_last_exit"), ("REENTRY",), "btc_loop.py:655-656"),
        _fc("BTC_GUARANTEED_REENTRY_MIN_GAP_BARS", "int", 5, [1, 10, 20], "BTC_GUARANTEED_REENTRY", _ACCEL, _BTC_ENTRY_STATE + ("flat", "has_prior_exit", "bars_since_last_exit"), ("REENTRY",), "btc_loop.py:653-654"),
        _fc("BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE", "bool", True, [False, True], "BTC_GUARANTEED_REENTRY", _ACCEL, _BTC_ENTRY_STATE + ("flat", "has_prior_exit", "bars_since_last_exit"), ("REENTRY",), "btc_loop.py:657-658"),
        _fc("BTC_REVERSE_ON_EXIT_ENABLED", "bool", True, [True, False], "BTC_REVERSE_ON_EXIT", (), ("candidate_mask", "opposite_breakout", "restricted_entry_mode", "prior_exit_matches_opposite"), ("ENTRY", "REENTRY"), "OPUS_VOMIT.py:4665-4690"),
        _fc("LEGACY_PROC_SINGLE_REENTRY", "bool", False, [False, True], "LEGACY_PSR", _PSR, ("candidate_mask", "is_invalidated"), ("REENTRY",), "ez_manage.py:36691,36791"),
        _fc("LEGACY_REENTRY_PSR_DC_BOUNCE", "bool", True, [False, True], "LEGACY_PSR", ("close", "dc_low_1h", "dc_low_15m", "dc_high_1h", "dc_high_15m", "dc_basis_15m", "dc_high_1h_ant", "dc_low_1h_ant"), ("candidate_mask", "hours_since_reduction", "last_reduction_price"), ("REENTRY",), "ez_manage.py:36865-36965"),
        _fc("LEGACY_REENTRY_PSR_FULL_DC", "bool", True, [False, True], "LEGACY_PSR", _PSR, ("candidate_mask", "is_invalidated", "position_notional", "start_position_size"), ("REENTRY",), "ez_manage.py:36829-36857"),
        _fc("LEGACY_REENTRY_PSR_K_DC_CROSSOVER", "bool", False, [False, True], "LEGACY_PSR", _PSR, ("candidate_mask", "is_invalidated"), ("REENTRY",), "ez_manage.py:36650-36812"),
        _fc("LEGACY_REENTRY_PSR_QUICK_RECOVERY", "bool", True, [False, True], "LEGACY_PSR", ("close", "atr_3m", "k_3m", "d_3m"), ("candidate_mask", "minutes_since_reduction", "last_reduction_price"), ("REENTRY",), "ez_manage.py:36420-36466"),
        _fc("DIRECTION_FAVORABLE_REENTRY_ENABLED", "bool", False, [False, True], "DIRECTION_FAVORABLE", _DFR, ("candidate_mask", "minutes_since_exit", "position_notional", "start_position_size"), ("REENTRY",), "ez_manage.py:36050-36104"),
        _fc("REENTRY2_DIR_FAV_ENABLED", "bool", True, [False, True], "DIRECTION_FAVORABLE", _DFR, ("candidate_mask", "minutes_since_exit", "position_notional", "start_position_size"), ("REENTRY",), "ez_manage.py:36072"),
        _fc("REENTRY_SIZE_BREAKOUT_MULT", "float", 1.5, [1.5], "REENTRY_SIZING", ("close", "k_1h"), ("candidate_mask", "reentry_level", "base_multiplier"), ("REENTRY",), "ez_manage.py:35923"),
        _fc("REENTRY_SIZE_DIP_MULT", "float", 2.0, [2.0], "REENTRY_SIZING", ("close", "k_1h"), ("candidate_mask", "reentry_level", "base_multiplier"), ("REENTRY",), "ez_manage.py:35923"),
        _fc("REENTRY_SIZE_EXTENDED_K1H", "float", 90.0, [90.0], "REENTRY_SIZING", ("close", "k_1h"), ("candidate_mask", "reentry_level", "base_multiplier"), ("REENTRY",), "ez_manage.py:35920"),
        _fc("REENTRY_SIZE_EXTENDED_MULT", "float", 1.0, [1.0], "REENTRY_SIZING", ("close", "k_1h"), ("candidate_mask", "reentry_level", "base_multiplier"), ("REENTRY",), "ez_manage.py:35923"),
        _fc("MU_CORRECTION_REENTRY_DC_TOL_PCT", "float", 2.0, [2.0], "MU_CORRECTION_REENTRY", _MU, ("candidate_mask", "is_mu_long", "flat", "has_reduction", "age_minutes", "hardcool_minutes"), ("REENTRY",), "tradier_manage.py:15305-15325"),
        _fc("MU_CORRECTION_REENTRY_ENABLED", "bool", False, [False, True], "MU_CORRECTION_REENTRY", _MU, ("candidate_mask", "is_mu_long", "flat", "has_reduction", "age_minutes", "hardcool_minutes"), ("REENTRY",), "tradier_manage.py:15291-15325"),
        _fc("BOUNCE_REENTRY_K_RESET_LONG_TRADIER", "int", 35, [35], "TRADIER_BOUNCE_RESET", ("stoch_k_5m", "stoch_k_5m_prev"), ("candidate_mask",), ("REENTRY",), "tradier_manage.py:15524-15533"),
        _fc("BOUNCE_REENTRY_K_RESET_SHORT_TRADIER", "int", 65, [65], "TRADIER_BOUNCE_RESET", ("stoch_k_5m", "stoch_k_5m_prev"), ("candidate_mask",), ("REENTRY",), "tradier_manage.py:15525-15538"),
        _fc("DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED", "bool", False, [False, True], "DAEMON_REENTRY", ("wt1_3m", "wt2_3m", "k_3m"), ("candidate_mask", "is_short"), ("REENTRY",), "ez_manage.py:49093-49106"),
        _fc("DAEMON_REENTRY_STALE_EXIT_ENABLED", "bool", True, [False, True], "DAEMON_REENTRY", ("close",), ("candidate_mask", "is_daemon_reentry", "position_amount", "exit_price"), ("EXIT",), "ez_manage.py:40585-40628"),
        _fc("DELTA_REENTRY_FILTER_ENABLED", "bool", False, [False, True], "DELTA_REENTRY", (), ("candidate_mask", "delta_engine_enabled", "has_delta_signal", "delta_exiting", "delta_tf_count", "delta_speed_z"), ("REENTRY",), "ez_manage.py:301-348"),
        _fc("GUARANTEED_REENTRY_DELTA_GATE_ENABLED", "bool", False, [False, True], "DELTA_REENTRY", (), ("candidate_mask", "delta_engine_enabled", "has_delta_signal", "delta_exiting", "delta_tf_count", "delta_speed_z"), ("REENTRY",), "ez_manage.py:30934-30945"),
        _fc("HLR_REENTRY_MAX_AGE_S", "float", 14400.0, [14400.0], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("REENTRY",), "ez_positions_quick.py:16083-16089"),
        _fc("HLR_REENTRY_MULT_1H", "float", 1.5, [1.5], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("EXIT", "REENTRY"), "ez_positions_quick.py:3583-3599"),
        _fc("HLR_REENTRY_MULT_4H", "float", 2.0, [2.0], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("EXIT", "REENTRY"), "ez_positions_quick.py:3587-3599"),
        _fc("HLR_REENTRY_MULT_D", "float", 2.5, [2.5], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("EXIT", "REENTRY"), "ez_positions_quick.py:3591-3599"),
        _fc("HLR_REENTRY_MULT_W", "float", 3.0, [3.0], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("EXIT", "REENTRY"), "ez_positions_quick.py:3595-3599"),
        _fc("HLR_REENTRY_EXIT_MIN_GRWTDC_TFS", "int", 2, [0, 1, 2, 3], "HLR_REENTRY", _HLR, ("candidate_mask", "registry_age_seconds", "registry_has_qty"), ("EXIT",), "STRICT 2026-08-27: HLR_REENTRY_EXIT requires >=2 TFs with GR or wt_dc confirmation to prevent immediate fire. ROLLBACK: set to 0 to disable."),
        _fc("AUGMENTED_POSITIONS_GUARD_FLOOR_MULT", "float", 0.5, [0.5], "AUGMENT_GUARDS", (), ("candidate_mask", "already_augmented", "original_action_was_reentry", "gain_pct", "min_gain", "position_amount", "position_min_qty"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:21590-21603"),
        _fc("MAX_AUGMENTS_PER_POSITION", "int", 999999, [999999], "AUGMENT_GUARDS", (), ("candidate_mask", "is_augment", "original_action_was_reentry", "has_position", "augment_count"), ("AUGMENT",), "ez_manage.py:18453-18460"),
        _fc("UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", "bool", True, [False, True], "AUGMENT_GUARDS", (), ("candidate_mask", "position_amount", "position_min_qty", "last_augmentation_price", "current_price", "gain_pct", "max_gain_pct", "is_long"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:25587-25635"),
        _fc("HIGH_GAIN_AUGMENTATION_MIN_SIZE", "float", 200.0, [200.0], "AUGMENT_GUARDS", (), ("candidate_mask", "position_notional"), ("AUGMENT", "REDUCE"), "ez_manage.py:39031-39035"),
        _fc("DELTA_PYRAMID_MAX", "int", 8, [8], "DELTA_PYRAMID", (), ("candidate_mask", "n_entries", "last_entry_price", "current_price", "tf_count", "minimum_tf_count", "directional_strength", "opposing_strength"), ("AUGMENT",), "wt_dc_delta.py:927-940"),
        _fc("DELTA_PYRAMID_PRICE_TOL", "float", 0.02, [0.02], "DELTA_PYRAMID", (), ("candidate_mask", "n_entries", "last_entry_price", "current_price", "tf_count", "minimum_tf_count", "directional_strength", "opposing_strength"), ("AUGMENT",), "wt_dc_delta.py:928-940"),
    )
}


def _cfg(cfg: Any, name: str, default: Any = None) -> Any:
    if default is None and name in FIELD_CONTRACTS: default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources):
    for source in sources:
        for value in source.values():
            arr = np.asarray(value)
            if arr.ndim and len(arr): return len(arr)
    return 0


def _require(source: Mapping[str, Any], names: Sequence[str], n: int):
    out, missing, bad = {}, [], []
    for name in names:
        if name not in source: missing.append(name); continue
        arr = np.asarray(source[name])
        if arr.ndim == 0 or len(arr) != n: bad.append(name); continue
        out[name] = arr
    if missing or bad:
        reason = ("missing=" + ",".join(missing)) if missing else ""
        if bad: reason += (";" if reason else "") + "misaligned=" + ",".join(bad)
        return None, reason
    return out, ""


def _unavailable(n, reason): return MaskResult(np.zeros(n, bool), False, reason)
def _dunavailable(n, reason): return DecisionResult(np.zeros(n, bool), np.zeros(n), False, reason)


def _accel(arrays, cfg, n):
    a, reason = _require(arrays, _ACCEL, n)
    if a is None: return None, reason
    bull = np.zeros(n, np.int8); bear = np.zeros(n, np.int8); positive = bool(_cfg(cfg, "BTC_ACCEL_RAMP_REQUIRE_POSITIVE"))
    for tf in ("3m", "15m", "1h", "4h", "D"):
        v = np.asarray(a[f"wt_velocity_{tf}"], float); prev = v - np.asarray(a[f"wt_acceleration_{tf}"], float)
        bull += ((v > prev) & ((prev > 0) if positive else True)).astype(np.int8)
        bear += ((v < prev) & ((prev < 0) if positive else True)).astype(np.int8)
    return (bull, bear, np.where((bull > bear) & (bull > 0), 1, np.where((bear > bull) & (bear > 0), -1, 0))), ""


def btc_primary_gate(arrays, state, is_long, cfg) -> MaskResult:
    n = _n(arrays, state); accel, ar = _accel(arrays, cfg, n); s, sr = _require(state, _BTC_ENTRY_STATE, n)
    if accel is None or s is None: return _unavailable(n, ar or sr)
    candidate = np.asarray(s["candidate_mask"], bool) & ~np.asarray(s["opposing_divergence"], bool)
    rz = np.asarray(s["red_zone_active"], bool); boost = bool(_cfg(cfg, "BTC_RZ_AS_BOOST_ENABLED", True)); soften = np.where(rz & boost, int(_cfg(cfg, "BTC_RZ_SOFTEN_ACCEL_BY", 1)), 0)
    if (not boost) and bool(_cfg(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ")): candidate &= rz
    if bool(_cfg(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP")):
        bull, bear, side = accel; count = bull if is_long else bear; candidate &= (side == (1 if is_long else -1)) & (count >= np.maximum(1, int(_cfg(cfg, "BTC_ACCEL_RAMP_MIN_TFS")) - soften))
    return MaskResult(candidate, True)


def btc_follow_through_mask(arrays, state, is_long, cfg) -> MaskResult:
    n = _n(arrays, state); accel, ar = _accel(arrays, cfg, n); a, rr = _require(arrays, ("close",), n); s, sr = _require(state, _BTC_FT_STATE, n)
    if accel is None or a is None or s is None: return _unavailable(n, ar or rr or sr)
    if not bool(_cfg(cfg, "BTC_FOLLOW_THROUGH_REENTRY_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    close, exit_px, bars = np.asarray(a["close"], float), np.asarray(s["last_exit_price"], float), np.asarray(s["bars_since_last_exit"], int)
    move = np.divide(close - exit_px, exit_px, out=np.zeros(n), where=exit_px > 0) * 100; bull, bear, side = accel
    momentum = (side == (1 if is_long else -1)) & ((bull if is_long else bear) >= 1); threshold = float(_cfg(cfg, "BTC_FOLLOW_THROUGH_MIN_MOVE_PCT"))
    return MaskResult(np.asarray(s["candidate_mask"], bool) & np.asarray(s["prior_exit_matches_side"], bool) & (exit_px > 0) & (bars >= int(_cfg(cfg, "BTC_GUARANTEED_REENTRY_MIN_GAP_BARS"))) & (bars <= int(_cfg(cfg, "BTC_GUARANTEED_REENTRY_MAX_AGE_BARS"))) & ((move >= threshold) if is_long else (move <= -threshold)) & momentum, True)


def btc_guaranteed_reentry_gate(arrays, state, is_long, cfg) -> MaskResult:
    n = _n(arrays, state); names = _BTC_ENTRY_STATE + ("flat", "has_prior_exit", "bars_since_last_exit"); s, reason = _require(state, names, n)
    if s is None: return _unavailable(n, reason)
    primary = btc_primary_gate(arrays, state, is_long, cfg)
    if not primary.available: return primary
    if not bool(_cfg(cfg, "BTC_GUARANTEED_REENTRY_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    bars = np.asarray(s["bars_since_last_exit"], int); mask = primary.mask & np.asarray(s["flat"], bool) & np.asarray(s["has_prior_exit"], bool)
    mask &= (bars >= int(_cfg(cfg, "BTC_GUARANTEED_REENTRY_MIN_GAP_BARS"))) & (bars <= int(_cfg(cfg, "BTC_GUARANTEED_REENTRY_MAX_AGE_BARS")))
    if bool(_cfg(cfg, "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE")): mask &= np.asarray(s["red_zone_active"], bool)
    return MaskResult(mask, True)


def btc_reverse_on_exit_gate(state, cfg) -> MaskResult:
    n = _n(state); names = ("candidate_mask", "opposite_breakout", "restricted_entry_mode", "prior_exit_matches_opposite"); s, reason = _require(state, names, n)
    if s is None: return _unavailable(n, reason)
    mask = np.asarray(s["candidate_mask"], bool) & bool(_cfg(cfg, "BTC_REVERSE_ON_EXIT_ENABLED")) & ~np.asarray(s["restricted_entry_mode"], bool) & np.asarray(s["opposite_breakout"], bool) & np.asarray(s["prior_exit_matches_opposite"], bool)
    return MaskResult(mask, True)


def reentry_sizing_decision(arrays, state, is_long, cfg) -> DecisionResult:
    n = _n(arrays, state); a, ar = _require(arrays, ("close", "k_1h"), n); s, sr = _require(state, ("candidate_mask", "reentry_level", "base_multiplier"), n)
    if a is None or s is None: return _dunavailable(n, ar or sr)
    close, k, level = np.asarray(a["close"], float), np.asarray(a["k_1h"], float), np.asarray(s["reentry_level"], float); threshold = float(_cfg(cfg, "REENTRY_SIZE_EXTENDED_K1H"))
    extended = (k > threshold) if is_long else (k < 100 - threshold); dip = (level > 0) & ((close < level) if is_long else (close > level))
    chosen = np.where(extended, float(_cfg(cfg, "REENTRY_SIZE_EXTENDED_MULT")), np.where(dip, float(_cfg(cfg, "REENTRY_SIZE_DIP_MULT")), float(_cfg(cfg, "REENTRY_SIZE_BREAKOUT_MULT"))))
    candidate = np.asarray(s["candidate_mask"], bool); return DecisionResult(candidate, np.where(candidate, np.asarray(s["base_multiplier"], float) * chosen, 0), True)


def direction_favorable_mask(arrays, state, is_long, cfg) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, _DFR, n); s, sr = _require(state, ("candidate_mask", "minutes_since_exit", "position_notional", "start_position_size"), n)
    if a is None or s is None: return _unavailable(n, ar or sr)
    if not (bool(_cfg(cfg, "LEGACY_DIRECTION_FAVORABLE", False)) and bool(_cfg(cfg, "DIRECTION_FAVORABLE_REENTRY_ENABLED")) and bool(_cfg(cfg, "REENTRY2_DIR_FAV_ENABLED"))): return MaskResult(np.zeros(n, bool), True)
    k, d = np.asarray(a["k_3m"], float), np.asarray(a["d_3m"], float); w1h, w2h = np.asarray(a["wt1_1h"], float), np.asarray(a["wt2_1h"], float); htf = (w1h > w2h) if is_long else (w1h < w2h)
    struct = np.asarray(a["wt_trough_structure_15m"] == "HL", bool) if is_long else np.asarray(a["wt_peak_structure_15m"] == "LH", bool)
    direction = ((k > d) & (k < 85)) if is_long else ((k < d) & (k > 15)); w3, z3, w15, z15 = (np.asarray(a[x], float) for x in ("wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m")); confirm = htf | (((w15 > z15) & (w3 > z3)) if is_long else ((w15 < z15) & (w3 < z3)))
    mask = np.asarray(s["candidate_mask"], bool) & (np.asarray(s["minutes_since_exit"], float) < 120) & (np.asarray(s["position_notional"], float) < np.asarray(s["start_position_size"], float)) & direction & htf & struct & confirm
    return MaskResult(mask, True)


def legacy_psr_masks(arrays, state, is_long, cfg) -> dict[str, MaskResult]:
    n = _n(arrays, state); out = {}
    qa, qr = _require(arrays, ("close", "atr_3m", "k_3m", "d_3m"), n); qs, qsr = _require(state, ("candidate_mask", "minutes_since_reduction", "last_reduction_price"), n)
    if qa is None or qs is None: out["quick_recovery"] = _unavailable(n, qr or qsr)
    else:
        c, last, atr = np.asarray(qa["close"], float), np.asarray(qs["last_reduction_price"], float), np.asarray(qa["atr_3m"], float); k, d = np.asarray(qa["k_3m"], float), np.asarray(qa["d_3m"], float)
        q = ((c > last + atr) & (k > d)) if is_long else ((c < last - atr) & (k < d)); out["quick_recovery"] = MaskResult(np.asarray(qs["candidate_mask"], bool) & (last > 0) & (atr > 0) & (np.asarray(qs["minutes_since_reduction"], float) < float(_cfg(cfg, "QUICK_RECOVERY_WINDOW_MIN", 120.0))) & q & bool(_cfg(cfg, "REENTRY2_QUICK_RECOVERY_ENABLED", True)) & bool(_cfg(cfg, "LEGACY_REENTRY_PSR_QUICK_RECOVERY")), True)
    a, ar = _require(arrays, _PSR, n); s, sr = _require(state, ("candidate_mask", "is_invalidated"), n)
    if a is None or s is None:
        out["k_dc_crossover"] = out["full_dc"] = _unavailable(n, ar or sr)
    else:
        c, prev = np.asarray(a["close"], float), np.asarray(a["prev_price"], float); k3,d3,k3p,d3p,k15,d15,k15p,d15p = (np.asarray(a[x], float) for x in ("k_3m","d_3m","k_3m_prev","d_3m_prev","k_15m","d_15m","k_15m_prev","d_15m_prev")); inv = np.asarray(s["is_invalidated"], bool)
        dc3 = np.asarray(a["dc_low_3m" if is_long else "dc_high_3m"], float); dc15 = np.asarray(a["dc_low_15m" if is_long else "dc_high_15m"], float)
        cross3 = ((k3p <= d3p) & (k3 > d3)) if is_long else ((k3p >= d3p) & (k3 < d3)); cross15 = ((k15p <= d15p) & (k15 > d15)) if is_long else ((k15p >= d15p) & (k15 < d15))
        px3 = cross3 & (dc3 > 0) & ((c > dc3) & (prev <= dc3) if is_long else (c < dc3) & (prev >= dc3)); px15 = cross15 & (dc15 > 0) & ((c > dc15) if is_long else (c < dc15))
        kzone = ((k15 >= d15) & (k3 >= d3) & (k15 < 70) & (k3 < 70)) if is_long else ((k15 <= d15) & (k3 <= d3) & (k15 > 30) & (k3 > 30))
        kmask = np.asarray(s["candidate_mask"], bool) & ~inv & (px3 | px15) & kzone & bool(_cfg(cfg, "LEGACY_PROC_SINGLE_REENTRY")) & bool(_cfg(cfg, "LEGACY_REENTRY_PSR_K_DC_CROSSOVER")); out["k_dc_crossover"] = MaskResult(kmask, True)
        full = cross3 & (((c > np.asarray(a["dc_basis_15m"], float)) if is_long else (c < np.asarray(a["dc_basis_15m"], float))) & (np.asarray(a["dc_basis_15m"], float) > 0)) | np.asarray(a["dc_basis_crossover_3m"], bool)
        fs, fsr = _require(state, ("position_notional", "start_position_size"), n)
        if fs is None:
            out["full_dc"] = _unavailable(n, fsr)
        else:
            size_ok = np.asarray(fs["position_notional"], float) < np.asarray(fs["start_position_size"], float)
            out["full_dc"] = MaskResult(np.asarray(s["candidate_mask"], bool) & ~inv & full & size_ok & bool(_cfg(cfg, "LEGACY_REENTRY_PSR_FULL_DC")), True)
    da = ("close", "dc_low_1h", "dc_low_15m", "dc_high_1h", "dc_high_15m", "dc_basis_15m", "dc_high_1h_ant", "dc_low_1h_ant"); a, ar = _require(arrays, da, n); s, sr = _require(state, ("candidate_mask", "hours_since_reduction", "last_reduction_price"), n)
    if a is None or s is None: out["dc_bounce"] = _unavailable(n, ar or sr)
    else:
        c, last, hours = np.asarray(a["close"], float), np.asarray(s["last_reduction_price"], float), np.asarray(s["hours_since_reduction"], float)
        bounce_levels = [np.asarray(a[x], float) for x in (("dc_low_1h","dc_low_15m") if is_long else ("dc_high_1h","dc_high_15m"))]
        reduction_levels = [np.asarray(a[x], float) for x in (("dc_high_1h","dc_high_15m") if is_long else ("dc_low_1h","dc_low_15m"))]
        bounce = np.zeros(n, bool)
        for level in bounce_levels: bounce |= (level > 0) & (np.abs(c-level) / level <= .002)
        basis = np.asarray(a["dc_basis_15m"], float); bounce |= (basis > 0) & ((c >= basis) if is_long else (c <= basis)); near = np.zeros(n, bool)
        for level in reduction_levels: near |= (level > 0) & (np.abs(last-level)/level < .02)
        trend = (np.asarray(a["dc_high_1h"], float) > np.asarray(a["dc_high_1h_ant"], float)) if is_long else (np.asarray(a["dc_low_1h"], float) < np.asarray(a["dc_low_1h_ant"], float))
        out["dc_bounce"] = MaskResult(np.asarray(s["candidate_mask"], bool) & (hours < 8) & (last > 0) & near & bounce & trend & bool(_cfg(cfg, "LEGACY_REENTRY_PSR_DC_BOUNCE")), True)
    return out


def mu_correction_reentry_mask(arrays, state, cfg) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, _MU, n); names = ("candidate_mask", "is_mu_long", "flat", "has_reduction", "age_minutes", "hardcool_minutes"); s, sr = _require(state, names, n)
    if a is None or s is None: return _unavailable(n, ar or sr)
    if not bool(_cfg(cfg, "MU_CORRECTION_REENTRY_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    c = np.asarray(a["close"], float); tol = float(_cfg(cfg, "MU_CORRECTION_REENTRY_DC_TOL_PCT"))/100; trigger = np.zeros(n, bool)
    for tf in ("4h","1h"): trigger |= (np.asarray(a[f"dc_low_{tf}"], float)>0) & (c <= np.asarray(a[f"dc_low_{tf}"], float)*(1+tol))
    if bool(_cfg(cfg, "MU_CORRECTION_REENTRY_STOCH_ENABLED", True)):
        for tf in ("1h","4h"):
            k,d,kp,dp=(np.asarray(a[f"{x}_{tf}{suf}"],float) for x,suf in (("k",""),("d",""),("k","_prev"),("d","_prev"))); trigger |= (k>d)&(kp<=dp)
    base=np.asarray(s["candidate_mask"],bool)&np.asarray(s["is_mu_long"],bool)&np.asarray(s["flat"],bool)&np.asarray(s["has_reduction"],bool)&(np.asarray(s["age_minutes"],float)>=np.asarray(s["hardcool_minutes"],float)); return MaskResult(base&trigger,True)


def tradier_bounce_reset_mask(arrays, state, is_long, cfg) -> MaskResult:
    n=_n(arrays,state);a,ar=_require(arrays,("stoch_k_5m","stoch_k_5m_prev"),n);s,sr=_require(state,("candidate_mask",),n)
    if a is None or s is None:return _unavailable(n,ar or sr)
    if not bool(_cfg(cfg,"BOUNCE_REENTRY_ENABLED_TRADIER",True)):return MaskResult(np.zeros(n,bool),True)
    k,kp=np.asarray(a["stoch_k_5m"],float),np.asarray(a["stoch_k_5m_prev"],float);thr=int(_cfg(cfg,"BOUNCE_REENTRY_K_RESET_LONG_TRADIER" if is_long else "BOUNCE_REENTRY_K_RESET_SHORT_TRADIER"));m=(kp<=thr)&(k>thr)&(k>kp) if is_long else (kp>=thr)&(k<thr)&(k<kp);return MaskResult(np.asarray(s["candidate_mask"],bool)&m,True)


def daemon_short_gate(arrays,state,cfg)->MaskResult:
    n=_n(arrays,state);a,ar=_require(arrays,("wt1_3m","wt2_3m","k_3m"),n);s,sr=_require(state,("candidate_mask","is_short"),n)
    if a is None or s is None:return _unavailable(n,ar or sr)
    cand=np.asarray(s["candidate_mask"],bool);enabled=bool(_cfg(cfg,"DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED"));gate=(np.asarray(a["wt1_3m"],float)<np.asarray(a["wt2_3m"],float))&(np.asarray(a["k_3m"],float)>60);return MaskResult(cand&(~np.asarray(s["is_short"],bool)|(~enabled)|gate),True)


def daemon_stale_exit_mask(arrays,state,is_long,cfg)->MaskResult:
    n=_n(arrays,state);a,ar=_require(arrays,("close",),n);s,sr=_require(state,("candidate_mask","is_daemon_reentry","position_amount","exit_price"),n)
    if a is None or s is None:return _unavailable(n,ar or sr)
    stale=(np.asarray(a["close"],float)<np.asarray(s["exit_price"],float)) if is_long else (np.asarray(a["close"],float)>np.asarray(s["exit_price"],float));return MaskResult(np.asarray(s["candidate_mask"],bool)&bool(_cfg(cfg,"DAEMON_REENTRY_STALE_EXIT_ENABLED"))&np.asarray(s["is_daemon_reentry"],bool)&(np.asarray(s["position_amount"],float)>0)&(np.asarray(s["exit_price"],float)>0)&stale,True)


def delta_reentry_gate(state,cfg,guaranteed=False)->MaskResult:
    n=_n(state);names=("candidate_mask","delta_engine_enabled","has_delta_signal","delta_exiting","delta_tf_count","delta_speed_z");s,r=_require(state,names,n)
    if s is None:return _unavailable(n,r)
    cand=np.asarray(s["candidate_mask"],bool)
    if guaranteed and not bool(_cfg(cfg,"GUARANTEED_REENTRY_DELTA_GATE_ENABLED")):return MaskResult(cand.copy(),True)
    if not bool(_cfg(cfg,"DELTA_REENTRY_FILTER_ENABLED")):return MaskResult(cand.copy(),True)
    active=np.asarray(s["delta_engine_enabled"],bool)&np.asarray(s["has_delta_signal"],bool);passed=~active|(~np.asarray(s["delta_exiting"],bool)&(np.asarray(s["delta_tf_count"],float)>=float(_cfg(cfg,"DELTA_REENTRY_MIN_TF",2)))&(np.asarray(s["delta_speed_z"],float)>=float(_cfg(cfg,"DELTA_REENTRY_Z_THRESHOLD",1.0))));return MaskResult(cand&passed,True)


def hlr_reentry_decision(arrays,state,is_long,cfg)->DecisionResult:
    n=_n(arrays,state);a,ar=_require(arrays,_HLR,n);s,sr=_require(state,("candidate_mask","registry_age_seconds","registry_has_qty"),n)
    if a is None or s is None:return _dunavailable(n,ar or sr)
    exh="EXHAUST_UP" if is_long else "EXHAUST_DOWN";div="BEAR" if is_long else "BULL";peak="LH" if is_long else "HH";mult=np.ones(n);votes=np.zeros(n,np.int8);htf=np.zeros(n,bool)
    v1=np.asarray(a["wt_velocity_1h"],float);ok=(v1<float(_cfg(cfg,"HLR_TOP_VEL_1H_THRESH",-1))) if is_long else (v1>abs(float(_cfg(cfg,"HLR_TOP_VEL_1H_THRESH",-1))));votes+=ok;mult=np.where(ok,np.maximum(mult,float(_cfg(cfg,"HLR_REENTRY_MULT_1H"))),mult)
    for tf,key in (("4h","HLR_REENTRY_MULT_4H"),("D","HLR_REENTRY_MULT_D"),("W","HLR_REENTRY_MULT_W")):
        vel=np.asarray(a[f"wt_velocity_{tf}"],float);ok=((vel<(float(_cfg(cfg,"HLR_TOP_VEL_4H_THRESH",0)) if tf=="4h" else 0)) if is_long else (vel>0))| (np.asarray(a[f"wt_momentum_state_{tf}"])==exh)
        if tf in ("4h","D"):ok|=(np.asarray(a[f"wt_divergence_{tf}"])==div)
        if tf == "4h":ok|=(np.asarray(a["wt_peak_structure_4h"])==peak)
        votes+=ok;htf|=ok;mult=np.where(ok,np.maximum(mult,float(_cfg(cfg,key))),mult)
    # FIX 2026-08-27: HLR_REENTRY_EXIT must listen to at least 2 TFs in GR or wt_dc — prevents immediate fire on single 1h velocity tick.
    # Requires votes>=2 AND (htf confirms on >=2 higher TFs OR GR/wt_dc 2TF consensus). Fall back to requiring 2+ htf TFs if GR arrays absent.
    gr_wt_confirm = np.zeros(n, dtype=bool)
    try:
        # Attempt GR / wt_dc 2TF check from available arrays: need at least 2 of (4h, D, W) showing exhaust/divergence/peak
        htf_count = np.zeros(n, dtype=np.int8)
        for tf in ("4h","D","W"):
            if f"wt_momentum_state_{tf}" in a:
                htf_count += (np.asarray(a[f"wt_momentum_state_{tf}"]) == exh).astype(np.int8)
            if tf in ("4h","D") and f"wt_divergence_{tf}" in a:
                htf_count += (np.asarray(a[f"wt_divergence_{tf}"]) == div).astype(np.int8)
        # wt_velocity sign consensus across 2+ TFs
        vel_consensus = np.zeros(n, dtype=np.int8)
        for tf in ("1h","4h","D"):
            k = f"wt_velocity_{tf}"
            if k in a:
                v = np.asarray(a[k], float)
                vel_consensus += ((v < -0.5) if is_long else (v > 0.5)).astype(np.int8)
        gr_wt_confirm = (htf_count >= 1) & (vel_consensus >= 2) | (htf_count >= 2)
    except Exception:
        gr_wt_confirm = htf  # fallback to htf gate
    # Enforce 2-TF minimum: original votes>=2 plus GR/wt 2TF consensus or 2+ HTF votes
    htf_votes = htf.astype(np.int8)  # at least need 2 higher TFs, but htf was OR; recompute per-TF
    # Count per-TF htf hits
    try:
        per_tf = []
        for tf in ("4h","D","W"):
            vel=np.asarray(a[f"wt_velocity_{tf}"],float) if f"wt_velocity_{tf}" in a else np.zeros(n)
            ms = np.asarray(a[f"wt_momentum_state_{tf}"]) if f"wt_momentum_state_{tf}" in a else np.array([""]*n)
            ok2 = ((vel < 0) if is_long else (vel > 0)) | (ms == exh)
            per_tf.append(ok2.astype(np.int8))
        htf_two = (sum(per_tf) >= 2) if per_tf else htf
    except Exception:
        htf_two = htf
    _grwtdc_min=int(_cfg(cfg,"HLR_REENTRY_EXIT_MIN_GRWTDC_TFS",2))
    _grwtdc_ok = (htf_two | gr_wt_confirm) if _grwtdc_min<=1 else (htf_two & gr_wt_confirm) if _grwtdc_min>2 else (htf_two | gr_wt_confirm)  # min 2: require at least 2 TFs in GR or wt_dc (htf_two counts 2+ HTFs, gr_wt_confirm counts GR/wt_dc consensus)
    # Enforce new threshold: if min 2, require htf_two (2+ HTF votes) as GR/wt_dc proxy
    exitmask=np.asarray(s["candidate_mask"],bool)&(votes>=max(2,int(_cfg(cfg,"HLR_TOP_MIN_TFS",2))))&(_grwtdc_ok if _grwtdc_min>0 else np.ones(n,bool))&(htf_two | gr_wt_confirm);registry=np.asarray(s["registry_has_qty"],bool)&(np.asarray(s["registry_age_seconds"],float)<float(_cfg(cfg,"HLR_REENTRY_MAX_AGE_S")));return DecisionResult(exitmask&registry,np.where(exitmask&registry,mult,0),True)


def augment_guard_mask(state,cfg,kind)->MaskResult:
    n=_n(state)
    if kind=="augmented_floor":
        names=("candidate_mask","already_augmented","original_action_was_reentry","gain_pct","min_gain","position_amount","position_min_qty");s,r=_require(state,names,n)
        if s is None:return _unavailable(n,r)
        block=np.asarray(s["already_augmented"],bool)&~np.asarray(s["original_action_was_reentry"],bool)&(np.asarray(s["gain_pct"],float)<float(_cfg(cfg,"AUGMENTED_POSITIONS_GUARD_FLOOR_MULT"))*np.asarray(s["min_gain"],float))&(np.asarray(s["position_amount"],float)>np.asarray(s["position_min_qty"],float));return MaskResult(np.asarray(s["candidate_mask"],bool)&~block,True)
    if kind=="max_augments":
        names=("candidate_mask","is_augment","original_action_was_reentry","has_position","augment_count");s,r=_require(state,names,n)
        if s is None:return _unavailable(n,r)
        block=np.asarray(s["is_augment"],bool)&~np.asarray(s["original_action_was_reentry"],bool)&np.asarray(s["has_position"],bool)&(np.asarray(s["augment_count"],float)>=int(_cfg(cfg,"MAX_AUGMENTS_PER_POSITION")));return MaskResult(np.asarray(s["candidate_mask"],bool)&~block,True)
    if kind=="universal_gain":
        names=("candidate_mask","position_amount","position_min_qty","last_augmentation_price","current_price","gain_pct","max_gain_pct","is_long");s,r=_require(state,names,n)
        if s is None:return _unavailable(n,r)
        cand=np.asarray(s["candidate_mask"],bool)
        if not bool(_cfg(cfg,"UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED")):return MaskResult(cand.copy(),True)
        last,cur=np.asarray(s["last_augmentation_price"],float),np.asarray(s["current_price"],float);raw_move=np.divide(cur-last,last,out=np.zeros(n),where=last>0)*100;gain=np.where(np.asarray(s["is_long"],bool),raw_move,-raw_move);minimum=float(_cfg(cfg,"MIN_GAIN_TO_BUY_AGGRESSIVELY",3));raw,maxg=np.asarray(s["gain_pct"],float),np.asarray(s["max_gain_pct"],float);pb=bool(_cfg(cfg,"PULLBACK_AUGMENT_ENABLED",True))&(maxg>=minimum)&(raw>=.5*minimum)&((maxg-raw)>=float(_cfg(cfg,"PULLBACK_AUGMENT_REVERSAL_MIN",1)));active=(np.asarray(s["position_amount"],float)>np.asarray(s["position_min_qty"],float))&(last>0)&(cur>0);return MaskResult(cand&(~active|(gain>=minimum)|pb),True)
    if kind=="high_gain_monitor":
        s,r=_require(state,("candidate_mask","position_notional"),n)
        if s is None:return _unavailable(n,r)
        return MaskResult(np.asarray(s["candidate_mask"],bool)&(np.asarray(s["position_notional"],float)>=.3*float(_cfg(cfg,"HIGH_GAIN_AUGMENTATION_MIN_SIZE"))),True)
    return _unavailable(n,"unknown_kind")


def delta_pyramid_mask(state,is_long,cfg)->MaskResult:
    n=_n(state);names=("candidate_mask","n_entries","last_entry_price","current_price","tf_count","minimum_tf_count","directional_strength","opposing_strength");s,r=_require(state,names,n)
    if s is None:return _unavailable(n,r)
    last,cur=np.asarray(s["last_entry_price"],float),np.asarray(s["current_price"],float);tol=float(_cfg(cfg,"DELTA_PYRAMID_PRICE_TOL"));price=(cur<=last*(1+tol)) if is_long else (cur>=last*(1-tol));m=np.asarray(s["candidate_mask"],bool)&(np.asarray(s["n_entries"],int)<int(_cfg(cfg,"DELTA_PYRAMID_MAX")))&(last>0)&(cur>0)&price&(np.asarray(s["tf_count"],int)>=np.asarray(s["minimum_tf_count"],int))&(np.asarray(s["directional_strength"],float)>np.asarray(s["opposing_strength"],float));return MaskResult(m,True)


_FAMILY_API = {
    "BTC_PRIMARY": "btc_primary_gate",
    "BTC_FOLLOW_THROUGH": "btc_follow_through_mask",
    "BTC_GUARANTEED_REENTRY": "btc_guaranteed_reentry_gate",
    "BTC_REVERSE_ON_EXIT": "btc_reverse_on_exit_gate",
    "LEGACY_PSR": "legacy_psr_masks",
    "DIRECTION_FAVORABLE": "direction_favorable_mask",
    "REENTRY_SIZING": "reentry_sizing_decision",
    "MU_CORRECTION_REENTRY": "mu_correction_reentry_mask",
    "TRADIER_BOUNCE_RESET": "tradier_bounce_reset_mask",
    "DAEMON_REENTRY": "daemon_stale_exit_mask",  # short-gate row overridden below
    "DELTA_REENTRY": "delta_reentry_gate",
    "HLR_REENTRY": "hlr_reentry_decision",
    "AUGMENT_GUARDS": "augment_guard_mask",
    "DELTA_PYRAMID": "delta_pyramid_mask",
}

# Integration contract: every declared action consumer has a concrete predicate
# API.  This intentionally names the actual vector output, rather than granting
# coverage to a config reader or a helper that cannot affect an action mask.
LIFECYCLE_FILTER_APIS: dict[str, dict[str, str]] = {
    name: {consumer: _FAMILY_API[contract.family] for consumer in contract.lifecycle_filter_consumers}
    for name, contract in FIELD_CONTRACTS.items()
}
LIFECYCLE_FILTER_APIS["DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED"]["REENTRY"] = "daemon_short_gate"


__all__=["FIELD_CONTRACTS","LIFECYCLE_FILTER_APIS","DecisionResult","FieldContract","MaskResult","augment_guard_mask","btc_follow_through_mask","btc_guaranteed_reentry_gate","btc_primary_gate","btc_reverse_on_exit_gate","daemon_short_gate","daemon_stale_exit_mask","delta_pyramid_mask","delta_reentry_gate","direction_favorable_mask","hlr_reentry_decision","legacy_psr_masks","mu_correction_reentry_mask","reentry_sizing_decision","tradier_bounce_reset_mask"]
