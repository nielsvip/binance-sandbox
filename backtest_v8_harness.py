"""V8 Harness — IndicatorStore for reading NPZ files bar-by-bar.

Decodes integer-encoded string fields back to the string values
the live trading code expects (wt_cross, wt_divergence, etc.).
Reconstructs wt_cross from wt_cross_bull/bear binary flags when
the encoded wt_cross array is broken (all zeros).
"""
import numpy as np
from datetime import datetime
from typing import Dict, Any


class _IndicatorDict(dict):
    """dict that returns 0.0 for missing keys instead of None.
    Prevents 'float > NoneType' TypeErrors when live code accesses new fields
    not in the NPZ. Behaves like a normal dict otherwise."""
    def __missing__(self, key):
        return 0.0
    def get(self, key, default=0.0):
        val = dict.get(self, key, default)
        return val if val is not None else default


# Integer encodings used by backtest_v8_precompute.py
# Each tuple: (field_prefix, mapping {int -> string})
_INT_DECODE = {
    "wt_cross":          {-1: "BEAR", 0: "NONE", 1: "BULL"},
    "wt_signal":         {-1: "BEAR", 0: "NEUTRAL", 1: "BULL"},
    "wt_divergence":     {-1: "BEAR", 0: "", 1: "BULL"},
    "wt_momentum_state": {-2: "EXHAUST_DOWN", -1: "IMPULSE_DOWN", 0: "NEUTRAL", 1: "IMPULSE_UP", 2: "EXHAUST_UP"},
    "wt_peak_structure": {-1: "LH", 0: "NEUTRAL", 1: "HH"},
    "wt_trough_structure": {-1: "LL", 0: "NEUTRAL", 1: "HL"},
    "wt_structure":      {-1: "LH", 0: "NEUTRAL", 1: "HH"},
    "wt_wave_phase":     {-1: "CONTRACTING", 0: "NEUTRAL", 1: "EXPANDING"},
    "wt_composite_bias": {-1: "SHORT", 0: "NEUTRAL", 1: "LONG"},
    "ha":                {-1: "red", 0: "neutral", 1: "green"},
}

# Timeframes present in tradier (5m) and crypto (3m) precomputes
_ALL_TFS = ["3m", "5m", "15m", "1h", "4h", "D"]


def _decode_int_to_str(arr: np.ndarray, mapping: dict, default: str = "") -> np.ndarray:
    """Convert integer-encoded numpy array to object array of strings."""
    result = np.empty(len(arr), dtype=object)
    result[:] = default
    for int_val, str_val in mapping.items():
        result[arr == int_val] = str_val
    return result


def _rebuild_wt_cross(bull_arr: np.ndarray, bear_arr: np.ndarray) -> np.ndarray:
    """Reconstruct wt_cross string array from per-bar bull/bear cross events.

    The precompute's wt_cross encoded field is sometimes all zeros due to a
    dtype/reference bug. Rebuild it from the correct bull/bear binary flags:
    - 1 on the cross bar = "BULL" / "BEAR"
    - 0 otherwise → forward-fill last known state (None = "NONE" before any cross)
    """
    n = len(bull_arr)
    result = np.empty(n, dtype=object)
    last_state = "NONE"
    for i in range(n):
        if int(bull_arr[i]) == 1:
            last_state = "BULL"
        elif int(bear_arr[i]) == 1:
            last_state = "BEAR"
        result[i] = last_state
    return result


def _rebuild_bars_ago(bull_arr: np.ndarray, bear_arr: np.ndarray) -> np.ndarray:
    """Recompute wt_cross_bars_ago from binary cross event arrays.

    The precompute's bars_ago counter is broken (always returns 1-2).
    Recompute: count bars since last bull-or-bear cross event.
    """
    n = len(bull_arr)
    result = np.full(n, 999, dtype=np.int32)
    last_cross = -999
    for i in range(n):
        if int(bull_arr[i]) == 1 or int(bear_arr[i]) == 1:
            last_cross = i
        result[i] = i - last_cross if last_cross >= 0 else 999
    return result


class IndicatorStore:
    """Reads NPZ file and serves indicator snapshots per bar index.
    Reconstructs and decodes all integer-encoded string fields so the
    live trading code sees the same string values as production Redis."""

    def __init__(self, path: str, start_idx: int = 0):
        data = np.load(path, allow_pickle=True, mmap_mode='r')
        n_ts_full = len(data.get("timestamps", []))
        if start_idx > 0 and n_ts_full > start_idx:
            self.arrays = {}
            for k in data.files:
                arr = data[k]
                if arr.ndim >= 1 and arr.shape[0] == n_ts_full:
                    self.arrays[k] = np.array(arr[start_idx:])
                else:
                    self.arrays[k] = np.array(arr)
        else:
            self.arrays = {k: np.array(data[k]) for k in data.files}
        data.close()
        self.timestamps = self.arrays.get("timestamps", np.array([]))
        self.n_bars = len(self.timestamps)
        self.ts_to_idx = {int(t): i for i, t in enumerate(self.timestamps)}
        self.has_5m = any(k.endswith("_5m") for k in self.arrays)
        self.has_3m = any(k.endswith("_3m") for k in self.arrays)

        # ── Step 1: forward-fill numeric HTF arrays ────────────────────────
        _ffill_suffixes = ("_1h", "_4h", "_D")
        _skip_prefixes = ("open_", "high_", "low_", "close_", "volume_", "timestamps")
        for key in list(self.arrays.keys()):
            if not any(key.endswith(s) for s in _ffill_suffixes):
                continue
            if any(key.startswith(s) for s in _skip_prefixes):
                continue
            arr = self.arrays[key]
            if arr.dtype.kind not in ('f', 'i'):
                continue
            farr = arr.astype(np.float64)
            mask = (farr == 0) | np.isnan(farr)
            if mask.all() or not mask.any():
                continue
            last_val = 0.0
            for i in range(len(farr)):
                if not mask[i]:
                    last_val = farr[i]
                elif last_val != 0.0:
                    farr[i] = last_val
            self.arrays[key] = farr.astype(arr.dtype)

        # ── Step 2: rebuild wt_cross + bars_ago from binary flags ──────────
        # The encoded wt_cross array is sometimes all zeros due to a precompute bug.
        # Track which TFs were rebuilt so step 3 can skip integer decode for those.
        _wt_cross_rebuilt: set = set()
        # Only rebuild from bull/bear flags when:
        #   (a) the bull/bear event arrays have actual crossover events (not all zeros), AND
        #   (b) the existing wt_cross integer array is all zeros (broken)
        # This prevents clobbering valid integer-encoded wt_cross data with an
        # all-NONE rebuild when the bull/bear event arrays happen to be all zeros.
        for tf in _ALL_TFS:
            bull_key = f"wt_cross_bull_{tf}"
            bear_key = f"wt_cross_bear_{tf}"
            cross_key = f"wt_cross_{tf}"
            if bull_key not in self.arrays or bear_key not in self.arrays:
                continue
            bull_arr = self.arrays[bull_key]
            bear_arr = self.arrays[bear_key]
            bull_has_events = bool(np.any(bull_arr != 0))
            bear_has_events = bool(np.any(bear_arr != 0))
            existing_cross = self.arrays.get(cross_key)
            cross_is_broken = (existing_cross is None or
                               (hasattr(existing_cross, 'dtype') and
                                existing_cross.dtype.kind in ('i', 'u') and
                                not np.any(existing_cross != 0)))
            if (bull_has_events or bear_has_events) and cross_is_broken:
                # Bull/bear flags are valid and existing cross is broken — rebuild
                self.arrays[cross_key] = _rebuild_wt_cross(bull_arr, bear_arr)
                self.arrays[f"wt_cross_bars_ago_{tf}"] = _rebuild_bars_ago(bull_arr, bear_arr)
                _wt_cross_rebuilt.add(tf)
            elif cross_is_broken and not (bull_has_events or bear_has_events):
                # Both broken — rebuild produces all-NONE (no cross events to fill)
                self.arrays[cross_key] = _rebuild_wt_cross(bull_arr, bear_arr)
                self.arrays[f"wt_cross_bars_ago_{tf}"] = _rebuild_bars_ago(bull_arr, bear_arr)
                _wt_cross_rebuilt.add(tf)
            # else: existing cross integer data is valid — decode in step 3

        # ── Step 3: decode remaining integer string fields ─────────────────
        # Skip wt_cross only for TFs rebuilt as strings in step 2; decode the rest.
        for tf in _ALL_TFS:
            for prefix, mapping in _INT_DECODE.items():
                # Skip wt_cross for this TF if already rebuilt as string objects
                if prefix == "wt_cross" and tf in _wt_cross_rebuilt:
                    continue
                key = f"{prefix}_{tf}"
                if key not in self.arrays:
                    continue
                arr = self.arrays[key]
                if arr.dtype.kind not in ('i', 'u'):
                    continue  # already decoded or float
                self.arrays[key] = _decode_int_to_str(arr, mapping)

        # ── Step 3b: recompute wt_composite fields from per-TF data ─────────
        # The precompute's compute_wt_intelligence has a bug where composite
        # fields end up all zeros. Recompute from per-TF wt_bullish + wt_score.
        tfs_present = [tf for tf in _ALL_TFS if f"wt_score_{tf}" in self.arrays or f"wt_bullish_{tf}" in self.arrays]
        if tfs_present:
            n = self.n_bars
            tf_weights = {"3m": 1, "5m": 1, "15m": 2, "1h": 3, "4h": 4, "D": 5}
            bull_count = np.zeros(n, dtype=np.int8)
            bear_count = np.zeros(n, dtype=np.int8)
            comp_long = np.zeros(n, dtype=np.float64)
            comp_short = np.zeros(n, dtype=np.float64)
            for tf in tfs_present:
                w = tf_weights.get(tf, 1)
                bull_key = f"wt_bullish_{tf}"
                score_key = f"wt_score_{tf}"
                if bull_key in self.arrays:
                    b = self.arrays[bull_key]
                    if b.dtype.kind in ('f', 'i', 'u') and len(b) == n:
                        bull_arr = (np.asarray(b, dtype=np.float64) > 0).astype(np.int8)
                        bull_count = np.clip(bull_count.astype(np.int16) + bull_arr, 0, 127).astype(np.int8)
                        bear_arr = (np.asarray(b, dtype=np.float64) <= 0).astype(np.int8)
                        bear_count = np.clip(bear_count.astype(np.int16) + bear_arr, 0, 127).astype(np.int8)
                if score_key in self.arrays:
                    s = self.arrays[score_key]
                    if len(s) == n:
                        s = np.asarray(s, dtype=np.float64)
                        np.nan_to_num(s, copy=False)
                        comp_long += np.maximum(0.0, s) * w
                        comp_short += np.maximum(0.0, -s) * w
            self.arrays["wt_bull_alignment"] = bull_count
            self.arrays["wt_bear_alignment"] = bear_count
            self.arrays["wt_composite_long"] = comp_long.astype(np.float32)
            self.arrays["wt_composite_short"] = comp_short.astype(np.float32)
            self.arrays["wt_composite_delta"] = (comp_long - comp_short).astype(np.float32)
            self.arrays["wt_composite_bias"] = np.where(comp_long > comp_short, "LONG", np.where(comp_short > comp_long, "SHORT", "NEUTRAL"))
        # ── Step 4b: fallback broken 5m DC/stoch to 15m ──────────────────
        # The 5m klines in the tradier NPZ only cover the last ~8% of bars.
        # dc_low_5m/dc_high_5m/stoch_k_5m are constant or NaN for early bars.
        # Detect: if unique dc_low_5m values < 2% of total bars → broken → use 15m.
        n = self.n_bars
        _close_arr = self.arrays.get("close", np.array([]))
        _close_f = _close_arr.astype(np.float64) if _close_arr.dtype.kind in ('f', 'i', 'u') else np.array([])
        for _dc_field, _fb_field in [
            ("dc_low_5m", "dc_low_15m"), ("dc_high_5m", "dc_high_15m"),
            ("dc_basis_5m", "dc_basis_15m"), ("dc_position_5m", "dc_position_15m"),
            ("dc_width_5m", "dc_width_15m"),
            ("low_5m", "low_15m"), ("high_5m", "high_15m"),
            ("low_5m_prev", "low_15m_prev"), ("high_5m_prev", "high_15m_prev"),
            ("open_5m", "close"),
        ]:
            if _dc_field not in self.arrays or _fb_field not in self.arrays:
                continue
            arr = self.arrays[_dc_field]
            if arr.dtype.kind not in ('f', 'i', 'u'):
                continue
            _broken = False
            if _dc_field in ("dc_low_5m", "dc_high_5m") and len(_close_f) == len(arr):
                _dc_f = arr.astype(np.float64)
                if _dc_field == "dc_low_5m":
                    _frac_bad = np.mean(_close_f < _dc_f * 0.95)
                    _broken = (_frac_bad > 0.30)
                elif _dc_field == "dc_high_5m":
                    _frac_bad = np.mean(_close_f > _dc_f * 1.05)
                    _broken = (_frac_bad > 0.30)
            else:
                _n_unique = len(np.unique(arr[~np.isnan(arr.astype(np.float64))]))
                _broken = (_n_unique < max(10, n // 50))
            if _broken:
                self.arrays[_dc_field] = self.arrays[_fb_field].copy()
                for _sfx in ("_ant", "_prev"):
                    if f"{_dc_field[:-2]}{_sfx}" in self.arrays and f"{_fb_field[:-2]}{_sfx}" in self.arrays:
                        self.arrays[f"{_dc_field[:-2]}{_sfx}"] = self.arrays[f"{_fb_field[:-2]}{_sfx}"].copy()
        for _stk, _std, _fb_k, _fb_d in [
            ("stoch_k_5m", "stoch_d_5m", "stoch_k_15m", "stoch_d_15m"),
        ]:
            if _stk not in self.arrays or _fb_k not in self.arrays:
                continue
            arr = self.arrays[_stk]
            if arr.dtype.kind not in ('f',):
                continue
            valid_pct = np.sum(~np.isnan(arr)) / max(1, len(arr))
            if valid_pct < 0.2:
                self.arrays[_stk] = self.arrays[_fb_k].copy()
                if _std in self.arrays and _fb_d in self.arrays:
                    self.arrays[_std] = self.arrays[_fb_d].copy()
        # ── Step 4c: compute stoch _prev arrays ─────────────────────────
        # NPZ precompute stores k_{tf}_prev / d_{tf}_prev as proper HTF-period
        # previous values (computed before HTF→base mapping). Prefer those over
        # bar-shifted versions which are identical within an HTF period and make
        # "falling stoch" conditions dead.
        for _tf in ("5m", "15m", "1h", "4h", "D"):
            _k_key = f"stoch_k_{_tf}"
            _p_key = f"stoch_k_{_tf}_prev"
            _npz_k_prev = f"k_{_tf}_prev"
            if _p_key not in self.arrays:
                if _npz_k_prev in self.arrays:
                    self.arrays[_p_key] = self.arrays[_npz_k_prev]
                elif _k_key in self.arrays:
                    arr = self.arrays[_k_key]
                    if arr.dtype.kind == 'f':
                        prev = np.empty_like(arr)
                        prev[0] = arr[0]
                        prev[1:] = arr[:-1]
                        self.arrays[_p_key] = prev
            _d_key = f"stoch_d_{_tf}"
            _dp_key = f"stoch_d_{_tf}_prev"
            _npz_d_prev = f"d_{_tf}_prev"
            if _dp_key not in self.arrays:
                if _npz_d_prev in self.arrays:
                    self.arrays[_dp_key] = self.arrays[_npz_d_prev]
                elif _d_key in self.arrays:
                    arr = self.arrays[_d_key]
                    if arr.dtype.kind == 'f':
                        prev = np.empty_like(arr)
                        prev[0] = arr[0]
                        prev[1:] = arr[:-1]
                        self.arrays[_dp_key] = prev
        # stoch_k_1m is aliased from 5m/3m — compute its prev too
        if "stoch_k_1m" in self.arrays and "stoch_k_1m_prev" not in self.arrays:
            arr = self.arrays["stoch_k_1m"]
            if arr.dtype.kind == 'f':
                prev = np.empty_like(arr)
                prev[0] = arr[0]
                prev[1:] = arr[:-1]
                self.arrays["stoch_k_1m_prev"] = prev
                self.arrays["k_1m_prev"] = prev
        # ── Step 4: forward-fill HTF string fields ────────────────────────
        # String arrays are skipped in Step 1; do them separately here.
        _str_htf_suffixes = ("_1h", "_4h", "_D")
        for key, arr in list(self.arrays.items()):
            if arr.dtype != object:
                continue
            if not any(key.endswith(s) for s in _str_htf_suffixes):
                continue
            if any(key.startswith(s) for s in _skip_prefixes):
                continue
            last_val = ""
            for i in range(len(arr)):
                v = arr[i]
                if v and v != "NONE" and v != "NEUTRAL":
                    last_val = v
                elif last_val and (not v or v in ("NONE", "NEUTRAL", 0, 0.0)):
                    arr[i] = last_val
            self.arrays[key] = arr

    def get(self, key: str, idx: int, default=0.0):
        arr = self.arrays.get(key)
        if arr is None:
            return default
        if getattr(arr, "ndim", 1) == 0:
            return default
        if idx < 0 or idx >= len(arr):
            return default
        val = arr[idx]
        if arr.dtype == object:
            return val if val is not None else default
        if isinstance(val, (np.floating, float)) and np.isnan(val):
            return default
        return val

    def price(self, idx: int) -> float:
        return float(self.get("close", idx, 0.0))

    def build_indicator_dict(self, idx: int) -> Dict[str, Any]:
        """Build indicator dict for bar idx — same format as live Redis/bridge data."""
        result = _IndicatorDict()
        for key in self.arrays:
            if key == "timestamps":
                continue
            v = self.get(key, idx)
            # Convert numpy scalars to Python natives for safe comparisons
            if isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.floating,)):
                v = float(v)
            elif isinstance(v, (np.bool_,)):
                v = bool(v)
            result[key] = v
        p = self.price(idx)
        result["current_price"] = p
        result["mark_price"] = p
        result["prev_price"] = float(self.get("close", max(0, idx - 1)))
        ts = int(self.timestamps[idx]) if idx < self.n_bars else 0
        result["_tick_ts"] = float(ts)
        result["ts"] = float(ts)
        ts_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        result["timestamp"] = ts_str
        for tf in _ALL_TFS:
            result[f"timestamp_{tf}"] = ts_str
            result[f"age_{tf}"] = 0.0
        # Map 5m↔3m aliases
        stf = "5m" if self.has_5m else "3m"
        otf = "3m" if self.has_5m else "5m"
        for key in list(result.keys()):
            if key.endswith(f"_{stf}"):
                alias = key.replace(f"_{stf}", f"_{otf}")
                if alias not in result:
                    result[alias] = result[key]
        # 1m stoch aliases
        src_tf = "3m" if "stoch_k_3m" in result else ("5m" if "stoch_k_5m" in result else None)
        if src_tf and "stoch_k_1m" not in result:
            result["stoch_k_1m"] = result.get(f"stoch_k_{src_tf}", 50)
            result["stoch_d_1m"] = result.get(f"stoch_d_{src_tf}", 50)
            result["k_1m_prev"] = result.get(f"stoch_k_{src_tf}_prev", 50)
            result["d_1m_prev"] = result.get(f"stoch_d_{src_tf}_prev", 50)
        # WT composite defaults (numeric)
        if not result.get("wt_composite_long"):
            result["wt_composite_long"] = 0.0
        if not result.get("wt_composite_short"):
            result["wt_composite_short"] = 0.0
        if not isinstance(result.get("wt_bull_alignment"), int):
            result["wt_bull_alignment"] = 0
        if not isinstance(result.get("wt_bear_alignment"), int):
            result["wt_bear_alignment"] = 0
        # === FULL LIVE-COMPATIBLE ALIASES ===
        # NPZ uses relative_volume_{tf}, tradier_manage expects rel_vol_{tf}
        for _rvol_tf in ["5m", "15m", "1h", "4h", "D"]:
            _long_key = f"relative_volume_{_rvol_tf}"
            _short_key = f"rel_vol_{_rvol_tf}"
            if _long_key in result and _short_key not in result:
                result[_short_key] = result[_long_key]
        # Live Redis provides short-form k_3m/d_3m aliases that ez_manage expects
        # FIX 2026-04-14: compute stoch prev from PREVIOUS bar (idx-1), not current bar.
        # Without this, stoch_k_{tf}_prev == stoch_k_{tf} → SRS exit conditions impossible.
        _prev_idx = max(0, idx - 1)
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            sk = f"stoch_k_{tf}"; sd = f"stoch_d_{tf}"
            if sk in result:
                _kp_from_arr = float(self.get(sk, _prev_idx, result[sk]))
                _dp_from_arr = float(self.get(sd, _prev_idx, result.get(sd, 50)))
                _kp = result.get(f"stoch_k_{tf}_prev", result.get(f"k_{tf}_prev", _kp_from_arr))
                _dp = result.get(f"stoch_d_{tf}_prev", result.get(f"d_{tf}_prev", _dp_from_arr))
                result[f"k_{tf}_prev"] = _kp
                result[f"d_{tf}_prev"] = _dp
                if f"stoch_k_{tf}_prev" not in result:
                    result[f"stoch_k_{tf}_prev"] = _kp
                if f"stoch_d_{tf}_prev" not in result:
                    result[f"stoch_d_{tf}_prev"] = _dp
        # WT 1m aliases from 3m (live has 1m WT, NPZ only has 3m)
        for f in ["wt1", "wt2", "wt_score", "wt_velocity", "wt_bullish",
                   "wt_cross_bull", "wt_cross_bear", "wt_cross", "wt_cross_value",
                   "wt_cross_prev_value", "wt_cross_rising", "wt_cross_bars_ago"]:
            src = f"{f}_3m" if f"{f}_3m" in result else (f"{f}_5m" if f"{f}_5m" in result else None)
            if src and f"{f}_1m" not in result:
                result[f"{f}_1m"] = result[src]
            # _prev aliases
            if src and f"{f}_1m_prev" not in result and f"{src}_prev" in result:
                result[f"{f}_1m_prev"] = result[f"{src}_prev"]
        # WT cross STRING synthesis: NPZ has wt_cross_bull_3m=1/0, live has wt_cross_3m="BULL"/"BEAR"
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            if result.get(f"wt_cross_bull_{tf}"): result[f"wt_cross_{tf}"] = "BULL"
            elif result.get(f"wt_cross_bear_{tf}"): result[f"wt_cross_{tf}"] = "BEAR"
        # WT signal STRING synthesis: NPZ has wt_signal_3m as int (-1/0/1), live has "BULL"/"BEAR"/"NEUTRAL"
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            sig = result.get(f"wt_signal_{tf}")
            if isinstance(sig, (int, float)):
                result[f"wt_signal_{tf}"] = "BULL" if sig > 0 else ("BEAR" if sig < 0 else "NEUTRAL")
        # HA STRING conversion: NPZ has ha_3m=1/-1/0, live has "green"/"red"/"neutral"
        _ha_map = {1: "green", -1: "red", 0: "neutral", 1.0: "green", -1.0: "red", 0.0: "neutral"}
        for tf in ["1m", "3m", "5m", "15m", "1h", "4h", "D"]:
            ha_key = f"ha_{tf}"
            if ha_key in result and isinstance(result[ha_key], (int, float)):
                result[ha_key] = _ha_map.get(result[ha_key], "neutral")
            ha_prev = f"ha_{tf}_prev"
            if ha_prev in result and isinstance(result[ha_prev], (int, float)):
                result[ha_prev] = _ha_map.get(result[ha_prev], "neutral")
        # t_up / tco / tcu boolean conversion (NPZ has int8, live expects bool)
        for prefix in ["t_up", "tco", "tcu"]:
            for tf in ["3m", "5m", "15m", "1h"]:
                k = f"{prefix}_{tf}"
                if k in result and isinstance(result[k], (int, float)):
                    result[k] = bool(result[k])
        # DC crossover/crossunder boolean conversion
        for prefix in ["dc_basis_crossover", "dc_basis_crossunder", "dc_high_crossover",
                       "dc_high_crossunder", "dc_low_crossover", "dc_low_crossunder",
                       "sma_crossover", "sma_crossunder", "stoch_crossover", "stoch_crossunder",
                       "macd_crossover", "macd_crossunder"]:
            for tf in ["3m", "5m", "15m", "1h", "4h", "D"]:
                k = f"{prefix}_{tf}"
                if k in result and isinstance(result[k], (int, float)):
                    result[k] = bool(result[k])
        return result
