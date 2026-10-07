"""vec_paths/mtf_armed_entries.py — Phase E 2026-05-19 USER MANDATE.

PROVEN MTF PROTOCOL (user's instruction, captured across 4 messages):

1) ARMED STATE — persistent per (TF in {1h,4h,D,W}, side in {LONG,SHORT}, bandtype in {dc,bb,wt})
   - LONG armed on TF/dc:  close_TF > prev_dc_high_TF
   - LONG armed on TF/bb:  close_TF > prev_bb_upper_TF
   - LONG armed on TF/wt:  wt1_TF crosses above wt2_TF
   - Stays armed in memory UNTIL OPPOSITE-side breakout on SAME TF voids it (NOT time-based).
   - SHORT mirrors with low/lower/crossunder.

2) ENTRY TRIGGERS — all SMALL (~25% of normal):
   (A) While ARMED on any TF: 1h-WT or 15m-WT crossover (wt1>wt2) AND price WITHIN bb
       (bb_lower_15m < price < bb_upper_15m for the WT TF used).
   (B) 1h-direct: close_1h > prev_dc_high_1h OR close_1h > prev_bb_upper_1h → SMALL immediately.
   (C) 15m-direct (test): same on 15m → SMALL.

3) HH-VALIDATED BOUNCE (per (sym, TF, side)):
   - "Bounce" on TF = bar where wt1_TF prints a local low (turn up after down): wt1[i] > wt1[i-1] AND wt1[i-1] < wt1[i-2].
   - Bounce only VALID if bounce_price > prior_valid_bounce_price (LONG) / < (SHORT).
   - State: prior_bounce_price tracked per TF.

4) BIG-ADD CANDIDATES (3-way test):
   (i)  wt_3m bounce HH-validated + higher-TF wt confirms (wt1_HTF>wt2_HTF) + price > prior_value → BIG ADD.
   (ii) 15m bounce HH-validated + same confirms → BIG ADD.
   (iii) 1h bounce HH-validated + same confirms → BIG ADD.

5) SLOWDOWN IMMEDIATE SELL (after SMALL, before BIG):
   - gain stalls <+0.1% for N bars (default 5), OR
   - red bar on 3m (close < open), OR
   - wt1_3m flips against (was > wt2_3m, now < wt2_3m), OR
   - k_3m drops from peak (peak >= 70, current < peak - 10).

6) GR INTEGRATION — these signals also become contributors to GR's multi-confirm score.

KNOBS:
   MTF_ARMED_ENTRY_ENABLED              # master
   MTF_ARMED_HTF_LIST                   # comma-sep: '1h,4h,D,W' (which TFs to arm on)
   MTF_ARMED_BANDTYPES                  # comma-sep: 'dc,bb,wt' (which to track)
   MTF_TRIGGER_WT_IN_BB_ENABLED         # rule (A)
   MTF_TRIGGER_WT_TF                    # '15m' or '1h'
   MTF_TRIGGER_1H_DIRECT_ENABLED        # rule (B)
   MTF_TRIGGER_1H_DIRECT_BANDTYPE       # 'dc' or 'bb'
   MTF_TRIGGER_15M_DIRECT_ENABLED       # rule (C)
   MTF_TRIGGER_15M_DIRECT_BANDTYPE      # 'dc' or 'bb'
   MTF_REQUIRE_ARMED_ANY                # require >=1 armed state to enter (default True)
   MTF_BIG_ADD_TF                       # '3m' / '15m' / '1h' — which bounce triggers BIG add
   MTF_REQUIRE_HH_BOUNCE                # validate HH/LL on bounce price (default True)
   MTF_BIG_ADD_SIZE_MULT                # multiplier on small (default 4.0 = 4x SMALL = 1x normal)
   MTF_SMALL_SIZE_FRAC                  # fraction of normal for SMALL (default 0.25)
   MTF_SLOWDOWN_STALL_BARS              # N bars (default 5)
   MTF_SLOWDOWN_STALL_PCT               # gain below this for N bars triggers (default 0.1)
   MTF_SLOWDOWN_REQUIRE_BIG_ADD         # don't fire slowdown if BIG ADD already happened (default True)
"""
from __future__ import annotations
import numpy as np

MTF_ARMED_ENTRY_KNOBS = (
    "MTF_ARMED_ENTRY_ENABLED",
    "MTF_ARMED_HTF_LIST",
    "MTF_ARMED_BANDTYPES",
    "MTF_TRIGGER_WT_IN_BB_ENABLED",
    "MTF_TRIGGER_WT_TF",
    "MTF_TRIGGER_1H_DIRECT_ENABLED",
    "MTF_TRIGGER_1H_DIRECT_BANDTYPE",
    "MTF_TRIGGER_15M_DIRECT_ENABLED",
    "MTF_TRIGGER_15M_DIRECT_BANDTYPE",
    "MTF_REQUIRE_ARMED_ANY",
    "MTF_BIG_ADD_TF",
    "MTF_REQUIRE_HH_BOUNCE",
    "MTF_BIG_ADD_SIZE_MULT",
    "MTF_SMALL_SIZE_FRAC",
    "MTF_SLOWDOWN_STALL_BARS",
    "MTF_SLOWDOWN_STALL_PCT",
    "MTF_SLOWDOWN_REQUIRE_BIG_ADD",
    "MTF_SLOWDOWN_MASTER_ENABLED",
    "MTF_SLOWDOWN_REQUIRE_PEAK_PCT",
    "MTF_SLOWDOWN_MIN_AGE_S",
    "MTF_SLOWDOWN_DISABLE_RED3M",
    "MTF_SLOWDOWN_DISABLE_WTFLIP",
    "MTF_SLOWDOWN_DISABLE_KDROP",
    "MTF_SLOWDOWN_DISABLE_STALL",
    "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED",
    "MTF_ENTRY_REQUIRE_GR_FILTER",
    # Phase I 2026-05-19 compound exit
    "MTF_EXIT_USE_COMPOUND",
    "MTF_ATR_TRAIL_ENABLED",
    "MTF_ATR_TRAIL_MULT",
    "MTF_ATR_TRAIL_TF",
    "MTF_GR_EXIT_GATE_ENABLED",
    "MTF_GR_EXIT_MIN_TFS",
    "MTF_GR_EXIT_MIN_IND",
    "MTF_WT_CROSS_EXIT_ENABLED",
    "MTF_WT_CROSS_EXIT_TF",
    "MTF_DC_REJECT_EXIT_ENABLED",
    "MTF_DC_REJECT_EXIT_TF",
    "MTF_BB_REJECT_EXIT_ENABLED",
    "MTF_BB_REJECT_EXIT_TF",
    "MTF_BB_REJECT_EXIT_LOOKBACK",
    "MTF_REENTRY_COOLDOWN_BARS_HARD",
)


def _build_bb_reject_mask(npz, tf, n, is_long, lookback):
    """Inline BB tag-fail builder (was imported from candle_pattern_stops)."""
    h = _arr_get(npz, f"high_{tf}", n)
    l = _arr_get(npz, f"low_{tf}", n)
    bbu = _arr_get(npz, f"bb_upper_{tf}", n)
    bbl = _arr_get(npz, f"bb_lower_{tf}", n)
    if is_long:
        tag = (h > 0) & (bbu > 0) & (h >= bbu - 1e-9)
        fail_now = (h > 0) & (bbu > 0) & (h < bbu)
    else:
        tag = (l > 0) & (bbl > 0) & (l <= bbl + 1e-9)
        fail_now = (l > 0) & (bbl > 0) & (l > bbl)
    recent_tag = np.zeros(n, dtype=bool)
    if lookback <= 0:
        return recent_tag
    for i in range(n):
        lo = max(0, i - lookback)
        hi = i
        if hi > lo:
            recent_tag[i] = tag[lo:hi].any()
    return recent_tag & fail_now


def build_compound_exit_arrays(npz: dict, n: int, is_long: bool, mode: str, config) -> dict:
    """Precompute all compound-exit support arrays.

    Returns dict with keys:
      atr: float[n]              — ATR_TF values per bar (for trail calc)
      wt_cross_15m: bool[n]      — wt1_15m crosses against side
      wt_cross_1h: bool[n]
      dc_reject_high: float[n]   — dc_high_TF level (for above/below tracking)
      dc_reject_low: float[n]    — dc_low_TF level
      bb_reject_mask: bool[n]    — BB tag-fail mask (precomputed)
      gr_exit_mask: bool[n]      — GR exit gate pass
    """
    out = {}
    if not bool(getattr(config, "MTF_EXIT_USE_COMPOUND", False)):
        return out
    atr_tf = str(getattr(config, "MTF_ATR_TRAIL_TF", "15m"))
    # NPZ stores `atr_{tf}` (e.g. atr_15m), not `atr_14_{tf}`. Phase I 2026-05-19
    # bug fix: read canonical key first, fall back to legacy name if absent so
    # this is safe against any future precompute that re-introduces atr_14_*.
    _atr_canon = npz.get(f"atr_{atr_tf}")
    _atr_legacy = npz.get(f"atr_14_{atr_tf}")
    if _atr_canon is not None:
        out["atr"] = _arr_get(npz, f"atr_{atr_tf}", n)
    elif _atr_legacy is not None:
        out["atr"] = _arr_get(npz, f"atr_14_{atr_tf}", n)
    else:
        out["atr"] = np.zeros(n, dtype=np.float32)
    # WT cross arrays (per-bar boolean — wt1 crosses below wt2 for LONG)
    for tf in ("15m", "1h"):
        w1 = _arr_get(npz, f"wt1_{tf}", n)
        w2 = _arr_get(npz, f"wt2_{tf}", n)
        w1_p = _shift_prev(w1)
        w2_p = _shift_prev(w2)
        if is_long:
            cross = (w1 < w2) & (w1_p >= w2_p)
        else:
            cross = (w1 > w2) & (w1_p <= w2_p)
        out[f"wt_cross_{tf}"] = cross
    # DC reject levels (for ever_outside_channel tracking - same as Phase C)
    dc_reject_tf = str(getattr(config, "MTF_DC_REJECT_EXIT_TF", "15m"))
    out["dc_reject_high"] = _arr_get(npz, f"dc_high_{dc_reject_tf}", n) if is_long else np.zeros(n, dtype=np.float32)
    out["dc_reject_low"] = _arr_get(npz, f"dc_low_{dc_reject_tf}", n) if not is_long else np.zeros(n, dtype=np.float32)
    # BB reject mask (build using existing primitive)
    bb_reject_tf = str(getattr(config, "MTF_BB_REJECT_EXIT_TF", "15m"))
    bb_lookback = int(getattr(config, "MTF_BB_REJECT_EXIT_LOOKBACK", 5))
    out["bb_reject_mask"] = _build_bb_reject_mask(npz, bb_reject_tf, n, is_long, bb_lookback)
    return out


def check_mtf_compound_exit(
    arrays: dict, i: int, mark: float, gain: float, entry_price: float,
    is_long: bool, prev_trail: float, ever_outside_dc: bool, config,
) -> tuple[bool, str, float, bool]:
    """Returns (fire, reason, new_trail, new_ever_outside_dc).

    5 triggers, ANY fires close:
      1. ATR trail hit (HARD) — uses prev_trail (one-way ratchet from entry).
      2. GR HTF exit (soft, requires WT cross too) — gated externally via gr_exit_mask[i].
      3. WT cross 15m/1h (soft, pairs with GR exit).
      4. DC reject — price was > dc_high_TF (LONG) and now back below.
      5. BB reject — bb_reject_mask[i] True.
    GR+WT BOTH required for soft compound. DC/BB fire alone.
    """
    if not arrays:
        return False, "", prev_trail, ever_outside_dc
    new_trail = prev_trail
    new_ever = ever_outside_dc
    # 1. ATR trail
    if bool(getattr(config, "MTF_ATR_TRAIL_ENABLED", True)):
        atr_v = float(arrays["atr"][i]) if "atr" in arrays else 0.0
        if atr_v > 0 and entry_price > 0:
            mult = float(getattr(config, "MTF_ATR_TRAIL_MULT", 2.0))
            if is_long:
                candidate = entry_price - mult * atr_v
                # Trail ratchets UP only as price rises (use mark-based trail too)
                mark_trail = mark - mult * atr_v
                candidate = max(candidate, mark_trail)
                new_trail = max(prev_trail, candidate) if prev_trail > 0 else candidate
                if new_trail > 0 and mark < new_trail:
                    return True, f"MTF_ATR_TRAIL_{getattr(config,'MTF_ATR_TRAIL_TF','15m')}_x{mult}_lvl{new_trail:.4f}", new_trail, new_ever
            else:
                candidate = entry_price + mult * atr_v
                mark_trail = mark + mult * atr_v
                candidate = min(candidate, mark_trail)
                new_trail = min(prev_trail, candidate) if prev_trail > 0 else candidate
                if new_trail > 0 and mark > new_trail:
                    return True, f"MTF_ATR_TRAIL_{getattr(config,'MTF_ATR_TRAIL_TF','15m')}_x{mult}_lvl{new_trail:.4f}", new_trail, new_ever
    # 4. DC reject (track ever_outside; fire on re-cross back)
    if bool(getattr(config, "MTF_DC_REJECT_EXIT_ENABLED", True)):
        if is_long:
            band = float(arrays.get("dc_reject_high", np.zeros(1))[i]) if "dc_reject_high" in arrays else 0.0
            if band > 0:
                if mark > band:
                    new_ever = True
                elif new_ever and mark < band:
                    return True, f"MTF_DC_REJECT_{getattr(config,'MTF_DC_REJECT_EXIT_TF','15m')}_px{mark:.4f}", new_trail, new_ever
        else:
            band = float(arrays.get("dc_reject_low", np.zeros(1))[i]) if "dc_reject_low" in arrays else 0.0
            if band > 0:
                if mark < band:
                    new_ever = True
                elif new_ever and mark > band:
                    return True, f"MTF_DC_REJECT_{getattr(config,'MTF_DC_REJECT_EXIT_TF','15m')}_px{mark:.4f}", new_trail, new_ever
    # 5. BB reject
    if bool(getattr(config, "MTF_BB_REJECT_EXIT_ENABLED", True)):
        if arrays.get("bb_reject_mask") is not None and arrays["bb_reject_mask"][i]:
            return True, f"MTF_BB_REJECT_{getattr(config,'MTF_BB_REJECT_EXIT_TF','15m')}", new_trail, new_ever
    # Research/direct mode: close on every against-position WT cross, without
    # requiring the GR exit mask. For SHORT this is WT1 crossing up through WT2.
    if bool(getattr(config, "MTF_WT_CROSS_EXIT_DIRECT_ENABLED", False)) and bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", True)):
        wt_tf = str(getattr(config, "MTF_WT_CROSS_EXIT_TF", "15m"))
        if wt_tf == "either":
            wt_fired = bool(arrays.get("wt_cross_15m", np.zeros(1))[i] or arrays.get("wt_cross_1h", np.zeros(1))[i])
        else:
            wt_fired = bool(arrays.get(f"wt_cross_{wt_tf}", np.zeros(1))[i])
        if wt_fired:
            return True, f"MTF_WT_DIRECT_EXIT_{wt_tf}", new_trail, new_ever
    # 2+3. GR+WT compound (require BOTH)
    gr_fire = arrays.get("gr_exit_mask")
    if gr_fire is not None and bool(getattr(config, "MTF_GR_EXIT_GATE_ENABLED", True)) and gr_fire[i]:
        wt_tf = str(getattr(config, "MTF_WT_CROSS_EXIT_TF", "15m"))
        wt_fired = False
        if wt_tf == "either":
            wt_fired = arrays.get("wt_cross_15m", np.zeros(1))[i] or arrays.get("wt_cross_1h", np.zeros(1))[i]
        else:
            wt_fired = arrays.get(f"wt_cross_{wt_tf}", np.zeros(1))[i]
        if bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", True)) and wt_fired:
            return True, f"MTF_GR_WT_EXIT_{wt_tf}", new_trail, new_ever
    return False, "", new_trail, new_ever


def _get(npz, key: str, n: int) -> np.ndarray:
    a = npz.get(key)
    if a is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(a, nan=0.0).astype(np.float32)


def _shift_prev(arr: np.ndarray) -> np.ndarray:
    out = np.empty_like(arr)
    out[0] = arr[0] if arr.size else 0.0
    if arr.size > 1:
        out[1:] = arr[:-1]
    return out


def _ffill_armed(events_on: np.ndarray, events_off: np.ndarray, init_state: bool = False) -> np.ndarray:
    """Return boolean array: ARMED state forward-filled.
    Each bar: if events_on[i] → True; if events_off[i] → False; else carry prior.
    """
    n = events_on.shape[0]
    out = np.zeros(n, dtype=bool)
    state = init_state
    for i in range(n):
        if events_on[i]: state = True
        if events_off[i]: state = False
        out[i] = state
    return out


def build_armed_arrays(npz: dict, n: int, is_long: bool, config) -> dict:
    """Build per-TF×bandtype armed boolean arrays on base TF index.

    Returns dict keyed by 'armed_{TF}_{bandtype}' → bool[n].
    Plus 'armed_any' → bool[n] (OR across all).
    """
    out = {}
    if not bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        out["armed_any"] = np.zeros(n, dtype=bool)
        return out
    tf_list = str(getattr(config, "MTF_ARMED_HTF_LIST", "1h,4h,D,W")).split(",")
    bandtypes = str(getattr(config, "MTF_ARMED_BANDTYPES", "dc,bb,wt")).split(",")
    armed_any = np.zeros(n, dtype=bool)
    for tf in tf_list:
        tf = tf.strip()
        if not tf: continue
        c = _get(npz, f"close_{tf}", n)
        for bt in bandtypes:
            bt = bt.strip()
            if bt == "dc":
                if is_long:
                    band_up = _get(npz, f"dc_high_{tf}", n)
                    band_dn = _get(npz, f"dc_low_{tf}", n)
                else:
                    band_up = _get(npz, f"dc_low_{tf}", n)
                    band_dn = _get(npz, f"dc_high_{tf}", n)
                prev_up = _shift_prev(band_up)
                prev_dn = _shift_prev(band_dn)
                if is_long:
                    on = (c > 0) & (prev_up > 0) & (c > prev_up)
                    off = (c > 0) & (prev_dn > 0) & (c < prev_dn)
                else:
                    on = (c > 0) & (prev_up > 0) & (c < prev_up)
                    off = (c > 0) & (prev_dn > 0) & (c > prev_dn)
                init_state = bool((c[0] > band_dn[0]) if is_long else (c[0] < band_dn[0])) if band_dn[0] > 0 else False
            elif bt == "bb":
                if is_long:
                    band_up = _get(npz, f"bb_upper_{tf}", n)
                    band_dn = _get(npz, f"bb_lower_{tf}", n)
                else:
                    band_up = _get(npz, f"bb_lower_{tf}", n)
                    band_dn = _get(npz, f"bb_upper_{tf}", n)
                prev_up = _shift_prev(band_up)
                prev_dn = _shift_prev(band_dn)
                if is_long:
                    on = (c > 0) & (prev_up > 0) & (c > prev_up)
                    off = (c > 0) & (prev_dn > 0) & (c < prev_dn)
                else:
                    on = (c > 0) & (prev_up > 0) & (c < prev_up)
                    off = (c > 0) & (prev_dn > 0) & (c > prev_dn)
                init_state = bool((c[0] > band_dn[0]) if is_long else (c[0] < band_dn[0])) if band_dn[0] > 0 else False
            elif bt == "wt":
                wt1 = _get(npz, f"wt1_{tf}", n)
                wt2 = _get(npz, f"wt2_{tf}", n)
                wt1_prev = _shift_prev(wt1)
                wt2_prev = _shift_prev(wt2)
                if is_long:
                    on = (wt1 > wt2) & (wt1_prev <= wt2_prev)
                    off = (wt1 < wt2) & (wt1_prev >= wt2_prev)
                else:
                    on = (wt1 < wt2) & (wt1_prev >= wt2_prev)
                    off = (wt1 > wt2) & (wt1_prev <= wt2_prev)
                init_state = bool((wt1[0] > wt2[0]) if is_long else (wt1[0] < wt2[0]))
            else:
                continue
            armed = _ffill_armed(on, off, init_state)
            if bool(getattr(config, "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED", True)):
                wt1_tf = _arr_get(npz, f"wt1_{tf}", n)
                wt1_tf_prev = _shift_prev(wt1_tf)
                if is_long:
                    wt_favorable = (wt1_tf > wt1_tf_prev)
                else:
                    wt_favorable = (wt1_tf < wt1_tf_prev)
                armed = armed & wt_favorable
            out[f"armed_{tf}_{bt}"] = armed
            armed_any |= armed
    out["armed_any"] = armed_any
    return out

def _arr_get(npz, key, n):
    a = npz.get(key)
    if a is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(a, nan=0.0).astype(np.float32)


def build_wt_in_bb_triggers(npz: dict, n: int, is_long: bool, config) -> np.ndarray:
    """Rule (A): wt1 crosses above wt2 (LONG) on TF AND price within bb_TF."""
    if not bool(getattr(config, "MTF_TRIGGER_WT_IN_BB_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    tf = str(getattr(config, "MTF_TRIGGER_WT_TF", "15m"))
    wt1 = _get(npz, f"wt1_{tf}", n)
    wt2 = _get(npz, f"wt2_{tf}", n)
    wt1_p = _shift_prev(wt1)
    wt2_p = _shift_prev(wt2)
    close = _get(npz, "close_3m", n)  # base TF for crypto = 3m, tradier = 5m — passed via npz
    if (close == 0).all():
        close = _get(npz, "close_5m", n)
    if (close == 0).all():
        # Use base close from outer scope (best-effort)
        return np.zeros(n, dtype=bool)
    bbu = _get(npz, f"bb_upper_{tf}", n)
    bbl = _get(npz, f"bb_lower_{tf}", n)
    within_bb = (bbu > 0) & (bbl > 0) & (close > bbl) & (close < bbu)
    if is_long:
        cross = (wt1 > wt2) & (wt1_p <= wt2_p)
    else:
        cross = (wt1 < wt2) & (wt1_p >= wt2_p)
    return cross & within_bb


def build_direct_breakout_triggers(npz: dict, n: int, is_long: bool, config) -> dict:
    """Rules (B) and (C): direct breakout on 1h / 15m (dc or bb)."""
    out = {"trig_1h": np.zeros(n, dtype=bool), "trig_15m": np.zeros(n, dtype=bool)}
    for tf, knob_e, knob_bt in (
        ("1h", "MTF_TRIGGER_1H_DIRECT_ENABLED", "MTF_TRIGGER_1H_DIRECT_BANDTYPE"),
        ("15m", "MTF_TRIGGER_15M_DIRECT_ENABLED", "MTF_TRIGGER_15M_DIRECT_BANDTYPE"),
    ):
        if not bool(getattr(config, knob_e, False)):
            continue
        bt = str(getattr(config, knob_bt, "dc"))
        c = _get(npz, f"close_{tf}", n)
        if bt == "dc":
            band_up = _get(npz, f"dc_high_{tf}", n) if is_long else _get(npz, f"dc_low_{tf}", n)
        else:
            band_up = _get(npz, f"bb_upper_{tf}", n) if is_long else _get(npz, f"bb_lower_{tf}", n)
        prev_band = _shift_prev(band_up)
        if is_long:
            trig = (c > 0) & (prev_band > 0) & (c > prev_band)
        else:
            trig = (c > 0) & (prev_band > 0) & (c < prev_band)
        out[f"trig_{tf}"] = trig
    return out


def build_bounce_arrays(npz: dict, n: int, is_long: bool, tf: str) -> dict:
    """Build bounce + HH-validity arrays for a given TF (3m / 15m / 1h).

    Bounce = wt1_TF prints local low (LONG) / local high (SHORT):
       LONG: wt1[i] > wt1[i-1] AND wt1[i-1] < wt1[i-2]
       SHORT: wt1[i] < wt1[i-1] AND wt1[i-1] > wt1[i-2]
    HH-validity is checked at runtime against prior_bounce_price (stateful).
    Here we just emit the raw bounce mask + the base-TF close (for the HH compare).
    """
    wt1 = _get(npz, f"wt1_{tf}", n)
    wt1_p1 = _shift_prev(wt1)
    wt1_p2 = _shift_prev(wt1_p1)
    if is_long:
        bounce = (wt1 > wt1_p1) & (wt1_p1 < wt1_p2)
    else:
        bounce = (wt1 < wt1_p1) & (wt1_p1 > wt1_p2)
    return {"bounce": bounce}


def build_mtf_arrays(npz: dict, n: int, is_long: bool, config) -> dict:
    if not bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        return {"enabled": False}
    out = {"enabled": True}
    out.update(build_armed_arrays(npz, n, is_long, config))
    out["wt_in_bb_trig"] = build_wt_in_bb_triggers(npz, n, is_long, config)
    out.update(build_direct_breakout_triggers(npz, n, is_long, config))
    big_tf = str(getattr(config, "MTF_BIG_ADD_TF", "3m"))
    out["bounce_big"] = build_bounce_arrays(npz, n, is_long, big_tf)["bounce"]
    out["big_add_tf"] = big_tf
    return out


def check_mtf_small_entry(arrays: dict, i: int, config) -> tuple[bool, str]:
    """Returns (fire, reason) for a SMALL entry at base-TF bar i."""
    if not arrays.get("enabled"):
        return False, ""
    require_armed = bool(getattr(config, "MTF_REQUIRE_ARMED_ANY", True))
    if require_armed and not arrays["armed_any"][i]:
        return False, ""
    # Rule (A)
    if arrays["wt_in_bb_trig"][i]:
        return True, f"MTF_SMALL_WT_BB_{getattr(config, 'MTF_TRIGGER_WT_TF', '15m')}"
    # Rule (B)
    if arrays.get("trig_1h", np.zeros(1, dtype=bool))[i] if i < len(arrays.get("trig_1h", [])) else False:
        return True, f"MTF_SMALL_1H_{getattr(config, 'MTF_TRIGGER_1H_DIRECT_BANDTYPE', 'dc')}"
    # Rule (C)
    if arrays.get("trig_15m", np.zeros(1, dtype=bool))[i] if i < len(arrays.get("trig_15m", [])) else False:
        return True, f"MTF_SMALL_15M_{getattr(config, 'MTF_TRIGGER_15M_DIRECT_BANDTYPE', 'dc')}"
    return False, ""


def check_mtf_big_add(
    arrays: dict, i: int, mark: float, is_long: bool,
    prior_bounce_price: float, config,
) -> tuple[bool, str, float]:
    """Returns (fire, reason, new_prior_bounce_price)."""
    if not arrays.get("enabled"):
        return False, "", prior_bounce_price
    if not arrays["bounce_big"][i]:
        return False, "", prior_bounce_price
    # HH-validation
    require_hh = bool(getattr(config, "MTF_REQUIRE_HH_BOUNCE", True))
    new_prior = prior_bounce_price
    if require_hh:
        if prior_bounce_price > 0:
            if is_long and mark <= prior_bounce_price:
                # Invalid bounce — don't update prior, don't fire
                return False, "", prior_bounce_price
            if (not is_long) and mark >= prior_bounce_price:
                return False, "", prior_bounce_price
        new_prior = mark  # update to current bounce price (HH validated)
    tf = arrays.get("big_add_tf", "3m")
    return True, f"MTF_BIG_ADD_{tf}_bounce_px{mark:.4f}", new_prior


def check_mtf_slowdown(
    gain: float, gain_prev: float, stall_count: int, max_k_seen: float,
    k_3m: float, close: float, open_: float, wt1_3m: float, wt2_3m: float,
    big_added: bool, config, max_gain: float = 0.0, age_s: float = 0.0,
) -> tuple[bool, str, int, float]:
    """Returns (fire, reason, new_stall_count, new_max_k_seen).

    Phase F additions:
      MTF_SLOWDOWN_MASTER_ENABLED        — master kill switch (default True)
      MTF_SLOWDOWN_REQUIRE_PEAK_PCT      — only fire after gain peaked >= this (default 0.0)
      MTF_SLOWDOWN_MIN_AGE_S             — don't fire for first N seconds after open (default 0)
      MTF_SLOWDOWN_DISABLE_RED3M         — drop red-3m trigger
      MTF_SLOWDOWN_DISABLE_WTFLIP        — drop WT-3m-flip trigger
      MTF_SLOWDOWN_DISABLE_KDROP         — drop K-drop trigger
      MTF_SLOWDOWN_DISABLE_STALL         — drop stall trigger
    """
    if not bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        return False, "", stall_count, max_k_seen
    if not bool(getattr(config, "MTF_SLOWDOWN_MASTER_ENABLED", True)):
        return False, "", stall_count, max_k_seen
    require_no_big = bool(getattr(config, "MTF_SLOWDOWN_REQUIRE_BIG_ADD", True))
    if require_no_big and big_added:
        return False, "", stall_count, max_k_seen
    # Peak gate: only allow slowdown after gain has peaked above threshold
    require_peak = float(getattr(config, "MTF_SLOWDOWN_REQUIRE_PEAK_PCT", 0.0))
    if require_peak > 0.0 and max_gain < require_peak:
        return False, "", stall_count, max_k_seen
    # Min-age gate: don't fire for first N seconds
    min_age = float(getattr(config, "MTF_SLOWDOWN_MIN_AGE_S", 0.0))
    if min_age > 0.0 and age_s < min_age:
        return False, "", stall_count, max_k_seen
    stall_pct = float(getattr(config, "MTF_SLOWDOWN_STALL_PCT", 0.1))
    stall_bars = int(getattr(config, "MTF_SLOWDOWN_STALL_BARS", 5))
    new_stall = stall_count + 1 if gain < stall_pct else 0
    new_max_k = max(max_k_seen, k_3m)
    disable_red = bool(getattr(config, "MTF_SLOWDOWN_DISABLE_RED3M", False))
    disable_wt = bool(getattr(config, "MTF_SLOWDOWN_DISABLE_WTFLIP", False))
    disable_k = bool(getattr(config, "MTF_SLOWDOWN_DISABLE_KDROP", False))
    disable_stall = bool(getattr(config, "MTF_SLOWDOWN_DISABLE_STALL", False))
    if not disable_red and close > 0 and open_ > 0 and close < open_:
        return True, "MTF_SLOW_RED3m", new_stall, new_max_k
    if not disable_wt and wt1_3m > 0 and wt2_3m > 0 and wt1_3m < wt2_3m:
        return True, "MTF_SLOW_WTFLIP3m", new_stall, new_max_k
    if not disable_k and new_max_k >= 70.0 and k_3m < new_max_k - 10.0:
        return True, f"MTF_SLOW_KDROP_{int(new_max_k)}_{int(k_3m)}", new_stall, new_max_k
    if not disable_stall and new_stall >= stall_bars:
        return True, f"MTF_SLOW_STALL_{stall_bars}b", new_stall, new_max_k
    return False, "", new_stall, new_max_k
