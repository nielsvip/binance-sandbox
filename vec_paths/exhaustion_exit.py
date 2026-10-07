"""vec_paths/exhaustion_exit.py — Phase D 2026-05-19.

USER MANDATE: "retest bb/dc 3m rejection / basis cross IN COMBINATION WITH
k_15m value / bb/dc 15m/1h/4h. NO MORE failures like the previous you need
to get SMART about exits as the hedge system was a LIE."

Exit fires when a SMART JOINT CONDITION is met:

  (REJECTION_LTF OR BASIS_CROSS_LTF) AND K_15M_EXTREME [AND HTF_REJECT_NEAR]

Where:
  REJECTION_LTF (LONG) — price was > bb_upper_3m or dc_high_3m at any prior bar
                          of the position AND has now crossed back BELOW.
  BASIS_CROSS_LTF (LONG) — bb_basis_3m or dc_basis_3m crossed DOWNWARD
                            (this bar's price < basis AND prior bar's price >= basis).
  K_15M_EXTREME (LONG) — stoch_k_15m >= EXH_K_15M_LONG_THRESHOLD (default 80).
  HTF_REJECT_NEAR (LONG) — optional gate. Price within EXH_HTF_NEAR_PCT (default 0.5%)
                            of bb_upper_<HTF> or dc_high_<HTF>.

SHORT mirrors with bb_lower / dc_low / k_15m <= 20 / lower HTF level.

KNOBS:
    EXH_EXIT_ENABLED
    EXH_LTF_FIELD          # 'bb_upper' or 'dc_high' (auto-flips for SHORT)
    EXH_USE_BASIS_CROSS    # bool — also fire on basis cross
    EXH_BASIS_FIELD        # 'bb_basis' or 'dc_basis'
    EXH_K_15M_LONG_THRESHOLD  # >= this on stoch_k_15m to fire LONG exit (default 80)
    EXH_K_15M_SHORT_THRESHOLD # <= this on stoch_k_15m to fire SHORT exit (default 20)
    EXH_HTF_GATE_ENABLED   # require HTF rejection-near too
    EXH_HTF_TF             # '15m' / '1h' / '4h'
    EXH_HTF_FIELD          # 'bb_upper' or 'dc_high'
    EXH_HTF_NEAR_PCT       # |price - HTF_level| / price < this (default 0.5%)
"""
from __future__ import annotations
import numpy as np

EXHAUSTION_EXIT_KNOBS = (
    "EXH_EXIT_ENABLED",
    "EXH_LTF_FIELD",
    "EXH_USE_BASIS_CROSS",
    "EXH_BASIS_FIELD",
    "EXH_K_15M_LONG_THRESHOLD",
    "EXH_K_15M_SHORT_THRESHOLD",
    "EXH_HTF_GATE_ENABLED",
    "EXH_HTF_TF",
    "EXH_HTF_FIELD",
    "EXH_HTF_NEAR_PCT",
)


def _get_arr(npz, key: str, n: int) -> np.ndarray:
    arr = npz.get(key)
    if arr is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(arr, nan=0.0).astype(np.float32)


def build_exh_arrays(npz: dict, n: int, is_long: bool, config) -> dict:
    """Precompute all arrays needed by the hot-loop check."""
    if not bool(getattr(config, "EXH_EXIT_ENABLED", False)):
        return {"enabled": False}
    ltf_field = str(getattr(config, "EXH_LTF_FIELD", "bb_upper"))
    use_basis = bool(getattr(config, "EXH_USE_BASIS_CROSS", True))
    basis_field = str(getattr(config, "EXH_BASIS_FIELD", "dc_basis"))
    htf_gate = bool(getattr(config, "EXH_HTF_GATE_ENABLED", False))
    htf_tf = str(getattr(config, "EXH_HTF_TF", "1h"))
    htf_field = str(getattr(config, "EXH_HTF_FIELD", "bb_upper"))
    # LTF rejection level on 3m
    if is_long:
        ltf_level_field = "bb_upper_3m" if ltf_field == "bb_upper" else "dc_high_3m"
        basis_level_field = "bb_basis_3m" if basis_field == "bb_basis" else "dc_basis_3m"
        htf_level_field = f"bb_upper_{htf_tf}" if htf_field == "bb_upper" else f"dc_high_{htf_tf}"
    else:
        ltf_level_field = "bb_lower_3m" if ltf_field == "bb_upper" else "dc_low_3m"
        basis_level_field = "bb_basis_3m" if basis_field == "bb_basis" else "dc_basis_3m"
        htf_level_field = f"bb_lower_{htf_tf}" if htf_field == "bb_upper" else f"dc_low_{htf_tf}"
    ltf_level = _get_arr(npz, ltf_level_field, n)
    # bb_basis_3m fallback: derive from upper+lower if missing
    if basis_field == "bb_basis" and basis_level_field not in npz:
        u = _get_arr(npz, "bb_upper_3m", n)
        l = _get_arr(npz, "bb_lower_3m", n)
        basis_level = np.where((u > 0) & (l > 0), (u + l) / 2.0, 0.0).astype(np.float32)
    else:
        basis_level = _get_arr(npz, basis_level_field, n)
    htf_level = _get_arr(npz, htf_level_field, n) if htf_gate else np.zeros(n, dtype=np.float32)
    # k_15m
    k_15m = _get_arr(npz, "k_15m", n)
    if (k_15m == 0).all():  # try stoch_k_15m fallback
        k_15m = _get_arr(npz, "stoch_k_15m", n)
    return {
        "enabled": True,
        "ltf_level": ltf_level,
        "basis_level": basis_level,
        "htf_level": htf_level,
        "k_15m": k_15m,
        "use_basis": use_basis,
        "htf_gate": htf_gate,
    }


def check_exh_exit_at_bar(
    arrays: dict, i: int, mark: float, prev_mark: float,
    is_long: bool, ever_outside_ltf: bool, config,
) -> tuple[bool, str, bool]:
    """Returns (fire, reason, new_ever_outside_ltf).

    REJECTION: price was > ltf_level at any prior bar AND now < ltf_level.
    BASIS_CROSS: prev_mark >= basis AND now < basis (for LONG).
    K_15M: stoch_k_15m >= threshold for LONG / <= threshold for SHORT.
    HTF gate (optional): |mark - htf_level| / mark < near_pct.
    """
    if not arrays.get("enabled"):
        return False, "", ever_outside_ltf
    ltf_level = float(arrays["ltf_level"][i])
    basis_level = float(arrays["basis_level"][i])
    htf_level = float(arrays["htf_level"][i])
    k_15m = float(arrays["k_15m"][i])
    use_basis = arrays["use_basis"]
    htf_gate = arrays["htf_gate"]
    new_ever = ever_outside_ltf
    if ltf_level > 0:
        if is_long:
            if mark > ltf_level:
                new_ever = True
        else:
            if mark < ltf_level:
                new_ever = True
    # K_15M extreme gate
    if is_long:
        k_threshold = float(getattr(config, "EXH_K_15M_LONG_THRESHOLD", 80.0))
        k_extreme = (k_15m >= k_threshold)
    else:
        k_threshold = float(getattr(config, "EXH_K_15M_SHORT_THRESHOLD", 20.0))
        k_extreme = (k_15m <= k_threshold)
    if not k_extreme:
        return False, "", new_ever
    # REJECTION_LTF: re-crossed back inside
    rej = False
    if new_ever and ltf_level > 0:
        if is_long and mark < ltf_level:
            rej = True
        if (not is_long) and mark > ltf_level:
            rej = True
    # BASIS_CROSS: prev_mark on one side, now other
    basis_cross = False
    if use_basis and basis_level > 0 and prev_mark > 0:
        if is_long and prev_mark >= basis_level and mark < basis_level:
            basis_cross = True
        if (not is_long) and prev_mark <= basis_level and mark > basis_level:
            basis_cross = True
    if not (rej or basis_cross):
        return False, "", new_ever
    # HTF gate (optional)
    if htf_gate and htf_level > 0:
        near_pct = float(getattr(config, "EXH_HTF_NEAR_PCT", 0.5))
        diff_pct = abs(mark - htf_level) / mark * 100.0 if mark > 0 else 999.0
        if diff_pct > near_pct:
            return False, "", new_ever
    # Build reason
    parts = []
    if rej: parts.append("REJ")
    if basis_cross: parts.append("BCX")
    parts.append(f"k{int(k_15m)}")
    if htf_gate: parts.append(f"htf{str(getattr(config, 'EXH_HTF_TF', '1h'))}")
    reason = f"EXH_{'_'.join(parts)}"
    return True, reason, new_ever
