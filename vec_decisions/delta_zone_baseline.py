"""SHARED scalar+vectorized predicate for the DELTA zone BASELINE-BOUNCE entry.

LIVE SOURCE OF TRUTH: wt_dc_delta.py zone section (emits
BASELINE_BOUNCE_LONG / BASELINE_BOUNCE_SHORT as sig.zone_reason; the live
scoring worker opens them as QUICK_OPEN_STRONG_BUY/SELL).

The scalar mirrors the live dict-based evaluation EXACTLY: same cfg keys and
defaults (rz_entry_enabled True, rz_baseline_tol 0.03, rz_k_exit 80.0,
rz_div_block_min 2, rz_top_bb 0.85, rz_bot_bb 0.15, rz_zscore_zone_enabled
True), same skip-if-absent + fail-open fallbacks, same indicator-prev
preference order (WT fields: {f}_prev_{tf} then stored prev; DC basis/high/low:
{f}_{tf}_prev only; dc_position/dc_width: stored prev only).

The mask replays it per bar from NPZ with presence checks — an absent key is
live's missing-indicator skip, NEVER a default-filled fabrication:
- 3m TF fields are absent from NPZs (SUB15M lane) and skipped exactly like
  live's missing-indicator path (live RSR legs prove 3m-missing still fires).
- bb_mid_* is absent from NPZs: the BB-mid touch branch stays inert until
  precompute adds it (basis touch carries RSR-type legs).
- current_price -> close. WT {f}_prev_{tf} absent -> bar diff (live
  stored-prev fallback). DC {f}_{tf}_prev present -> used (live
  indicator-prev preference).
- Live updates stored prevs every tick (mostly ~0 intra-bar deltas); the mask
  uses bar-step diffs, which is what those ticks sum to per bar.
"""
TFS = ["3m", "15m", "1h", "4h", "D"]
from wt_dc_delta import DEFAULT_CFG as _LIVE_DELTA_DEFAULT_CFG
WT_DELTA_FIELDS = [
    "wt1", "wt2", "wt_score", "wt_velocity", "wt_acceleration",
    "wt_percentile", "wt_zscore",
]
WT_INT_FIELDS = ["wt_bullish", "wt_cross_bull", "wt_cross_bear", "wt_momentum_state"]
DC_DELTA_FIELDS = ["dc_basis", "dc_high", "dc_low"]
DC_POSITION_FIELDS = ["dc_position", "dc_width"]


def _C(cfg_get, key, default):
    """Board-governance lookup: UPPERCASE registry keys first (boards/template rows
    speak UPPER), lowercase live-code keys second, default last. Without this,
    board overrides never reach the twin (the RZ case-split bug, 2026-10-24)."""
    try:
        v = cfg_get(key.upper(), None)
    except Exception:
        v = None
    if v is None:
        try:
            v = cfg_get(key, None)
        except Exception:
            v = None
    return default if v is None else v


def _tf_weights(cfg_get):
    stock_mode = str(_C(cfg_get, "mode", "crypto") or "crypto").lower() == "tradier"
    tw = _C(cfg_get, "delta_tf_weights_stock" if stock_mode else "delta_tf_weights", None)
    if not isinstance(tw, dict) or not tw:
        tw = _C(cfg_get, "tf_weights", None)
    if not isinstance(tw, dict) or not tw:
        tw = {"1h": 2.0, "4h": 3.0, "D": 2.0} if stock_mode else _LIVE_DELTA_DEFAULT_CFG["tf_weights"]
    return tw


def _tfs(cfg_get):
    return list(_tf_weights(cfg_get))


def _tw(cfg_get):
    tw = _tf_weights(cfg_get)
    if not isinstance(tw, dict):
        tw = {}
    return {tf: float(tw.get(tf, 1.0)) for tf in _tfs(cfg_get)}


def _f(x, d=0.0):
    try:
        v = float(x)
    except Exception:
        return d
    return v


def baseline_bounce_fires_scalar(ind, is_long, cfg_get, prev=None, update_prev=True):
    """Exact live mirror. ind: indicator dict. prev: mutable state dict (may be
    empty on first call). Returns (fires, touched_tf)."""
    if prev is None:
        prev = {}
    _cg = cfg_get
    cfg_get = lambda k, d=None: _C(_cg, k, d)  # noqa: E731 -- board UPPER keys govern
    tfs = _tfs(cfg_get)
    tw = _tw(cfg_get)
    tf_bull = {tf: 0.0 for tf in tfs}
    tf_bear = {tf: 0.0 for tf in tfs}
    tf_count = {tf: 0 for tf in tfs}

    def _get(k):
        try:
            v = (ind or {}).get(k)
        except Exception:
            v = None
        return v

    def _num(k):
        v = _get(k)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    for tf in tfs:
        for field in WT_DELTA_FIELDS + WT_INT_FIELDS:
            k = f"{field}_{tf}"
            cur = _num(k)
            if cur is None:
                continue
            prv = _num(f"{field}_prev_{tf}")
            if prv is None:
                prv = prev.get(k)
            if prv is None:
                continue
            try:
                prv = float(prv)
            except Exception:
                continue
            delta = cur - prv
            if delta > 0:
                tf_bull[tf] += delta
            else:
                tf_bear[tf] += -delta
            tf_count[tf] += 1
            if update_prev:
                prev[k] = cur
        for field in DC_DELTA_FIELDS:
            k = f"{field}_{tf}"
            cur = _num(k)
            prv = _num(f"{k}_prev")
            if cur is None or prv is None:
                continue
            delta = cur - prv
            if delta > 0:
                tf_bull[tf] += delta
            else:
                tf_bear[tf] += -delta
            tf_count[tf] += 1
        for field in DC_POSITION_FIELDS:
            k = f"{field}_{tf}"
            cur = _num(k)
            if cur is None:
                continue
            if k not in prev:
                continue
            try:
                prv = float(prev.get(k))
            except Exception:
                continue
            delta = cur - prv
            if delta > 0:
                tf_bull[tf] += delta
            else:
                tf_bear[tf] += -delta
            tf_count[tf] += 1
            if update_prev:
                prev[k] = cur

    total_bull = 0.0
    total_bear = 0.0
    for tf in tfs:
        c = tf_count[tf]
        b = tf_bull[tf] / c if c > 0 else 0.0
        r = tf_bear[tf] / c if c > 0 else 0.0
        total_bull += b * tw.get(tf, 1.0)
        total_bear += r * tw.get(tf, 1.0)

    px = _num("current_price")
    if px is None:
        px = _num("close")
    px = px or 0.0
    micro = str(cfg_get("rz_ltf_micro", "3m") or "3m")
    tol = _f(cfg_get("rz_baseline_tol", 0.03), 0.03)
    if not (tol > 0):
        tol = 0.03

    touched_tf = None
    for btf in tfs:
        basis = _num(f"dc_basis_{btf}") or 0.0
        if basis > 0 and px > 0 and abs(px - basis) / basis < tol:
            touched_tf = btf
            break
        mid = _num(f"bb_mid_{btf}") or 0.0
        if mid > 0 and px > 0 and abs(px - mid) / mid < tol:
            touched_tf = btf + "_bb"
            break

    bb_1h = _num("bb_pct_b_1h")
    bb_1h = 0.5 if bb_1h is None else bb_1h
    dc_pos_1h = _num("dc_position_1h")
    dc_pos_1h = 0.5 if dc_pos_1h is None else dc_pos_1h
    bb_up_1h = _num("bb_upper_1h") or 0.0
    bb_lo_1h = _num("bb_lower_1h") or 0.0
    dc_hi_1h = _num("dc_high_1h") or 0.0
    dc_lo_1h = _num("dc_low_1h") or 0.0
    zs_1h = _num("wt_zscore_1h") or 0.0
    zs_4h = _num("wt_zscore_4h") or 0.0
    z_on = bool(cfg_get("rz_zscore_zone_enabled", True))
    top_bb = _f(cfg_get("rz_top_bb", 0.85), 0.85)
    bot_bb = _f(cfg_get("rz_bot_bb", 0.15), 0.15)
    near_top = (
        (bb_up_1h > 0 and px > bb_up_1h)
        or (dc_hi_1h > 0 and px > dc_hi_1h)
        or bb_1h > top_bb
        or dc_pos_1h > top_bb
        or (z_on and (zs_4h > 2.0 or (zs_1h > 2.0 and zs_4h > 1.5)))
    )
    near_bottom = (
        (bb_lo_1h > 0 and 0 < px < bb_lo_1h)
        or (dc_lo_1h > 0 and 0 < px < dc_lo_1h)
        or bb_1h < bot_bb
        or dc_pos_1h < bot_bb
        or (z_on and (zs_4h < -2.0 or (zs_1h < -2.0 and zs_4h < -1.5)))
    )
    near_baseline = touched_tf is not None and not near_top and not near_bottom

    from wt_dc_delta import _momentum_state_int
    mom_4h = _momentum_state_int(_get("wt_momentum_state_4h"))
    mom_D = _momentum_state_int(_get("wt_momentum_state_D"))
    htf_bull_ok = not (mom_4h == -1 and mom_D == -1)
    htf_bear_ok = not (mom_4h == 1 and mom_D == 1)
    k_1h = _num("stoch_k_1h")
    k_1h = 50.0 if k_1h is None else k_1h
    k_thr = _f(cfg_get("rz_k_exit", 80.0), 80.0)
    div_min = int(_f(cfg_get("rz_div_block_min", 2), 2))
    from wt_dc_delta import _divergence_int
    div_1h = _divergence_int(_get("wt_divergence_1h"))
    div_4h = _divergence_int(_get("wt_divergence_4h"))
    div_D = _divergence_int(_get("wt_divergence_D"))
    bull_div_n = sum(1 for v in (div_1h, div_4h, div_D) if v == -1)
    bear_div_n = sum(1 for v in (div_1h, div_4h, div_D) if v == 1)

    vel_3m = _num(f"wt_velocity_{micro}")
    if vel_3m is None:
        vel_3m = _num("wt_velocity_3m")
    vel_3m = vel_3m or 0.0
    vel_15m = _num("wt_velocity_15m") or 0.0
    vel_1h = _num("wt_velocity_1h") or 0.0

    if touched_tf is None or not near_baseline:
        return False, touched_tf
    if is_long:
        if not bool(cfg_get("rz_entry_enabled", True)):
            return False, touched_tf
        ltf_ok = vel_3m > 0 or vel_15m > 0 or vel_1h > 0
        if total_bull > total_bear and ltf_ok and htf_bull_ok and k_1h < k_thr and bull_div_n < div_min:
            return True, touched_tf
        return False, touched_tf
    if not bool(cfg_get("rz_baseline_bounce_short_enabled", True)):
        return False, touched_tf
    ltf_ok = vel_3m < 0 or vel_15m < 0 or vel_1h < 0
    if total_bear > total_bull and ltf_ok and htf_bear_ok and k_1h > (100 - k_thr) and bear_div_n < div_min:
        return True, touched_tf
    return False, touched_tf


def baseline_bounce_mask(npz, n, is_long, cfg, _safe):
    """Vectorized replay of baseline_bounce_fires_scalar. Presence-checked:
    absent NPZ key = live's missing-indicator skip."""
    import numpy as np

    def _has(k):
        try:
            return k in npz
        except Exception:
            return False

    def _arr(k, d=0.0):
        if not _has(k):
            return None
        try:
            a = np.asarray(_safe(npz, k, n, d), dtype=float)
        except Exception:
            return None
        if a.shape[0] != n:
            return None
        return a

    def _cfg(k, d):
        try:
            v = getattr(cfg, k.upper(), None)
        except Exception:
            v = None
        if v is None:
            try:
                v = getattr(cfg, k, None)
            except Exception:
                v = None
        return d if v is None else v

    try:
        tw_raw = _tf_weights(_cfg)
    except Exception:
        tw_raw = {}
    tfs = list(tw_raw.keys()) if tw_raw else list(TFS)
    bull = np.zeros(n)
    bear = np.zeros(n)
    for tf in tfs:
        w = float((tw_raw or {}).get(tf, 1.0))
        cnt = 0
        tb = np.zeros(n)
        tr = np.zeros(n)
        for field in WT_DELTA_FIELDS + WT_INT_FIELDS:
            a = _arr(f"{field}_{tf}")
            if a is None:
                continue
            d = np.empty(n)
            d[0] = 0.0
            d[1:] = a[1:] - a[:-1]
            tb += np.where(d > 0, d, 0.0)
            tr += np.where(d > 0, 0.0, -d)
            cnt += 1
        for field in DC_DELTA_FIELDS:
            a = _arr(f"{field}_{tf}")
            p = _arr(f"{field}_{tf}_prev")
            if a is None:
                continue
            d = (a - p) if p is not None else np.concatenate([[0.0], a[1:] - a[:-1]])
            tb += np.where(d > 0, d, 0.0)
            tr += np.where(d > 0, 0.0, -d)
            cnt += 1
        for field in DC_POSITION_FIELDS:
            a = _arr(f"{field}_{tf}")
            if a is None:
                continue
            d = np.empty(n)
            d[0] = 0.0
            d[1:] = a[1:] - a[:-1]
            tb += np.where(d > 0, d, 0.0)
            tr += np.where(d > 0, 0.0, -d)
            cnt += 1
        if cnt > 0:
            bull += (tb / cnt) * w
            bear += (tr / cnt) * w

    close = _arr("close")
    if close is None:
        return np.zeros(n, dtype=bool)
    tol = float(_cfg("rz_baseline_tol", 0.03) or 0.03)
    if not tol > 0:
        tol = 0.03
    touched = np.zeros(n, dtype=bool)
    for btf in tfs:
        basis = _arr(f"dc_basis_{btf}")
        if basis is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                hit = (basis > 0) & (close > 0) & (np.abs(close - basis) / np.maximum(basis, 1e-12) < tol)
            touched = touched | hit
        mid = _arr(f"bb_mid_{btf}")
        if mid is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                hitm = (mid > 0) & (close > 0) & (np.abs(close - mid) / np.maximum(mid, 1e-12) < tol)
            touched = touched | hitm

    def _a(k, d):
        a = _arr(k)
        return a if a is not None else np.full(n, d)

    bb_1h = _a("bb_pct_b_1h", 0.5)
    dc_pos_1h = _a("dc_position_1h", 0.5)
    bb_up_1h = _a("bb_upper_1h", 0.0)
    bb_lo_1h = _a("bb_lower_1h", 0.0)
    dc_hi_1h = _a("dc_high_1h", 0.0)
    dc_lo_1h = _a("dc_low_1h", 0.0)
    zs_1h = _a("wt_zscore_1h", 0.0)
    zs_4h = _a("wt_zscore_4h", 0.0)
    z_on = bool(_cfg("rz_zscore_zone_enabled", True))
    top_bb = float(_cfg("rz_top_bb", 0.85) or 0.85)
    bot_bb = float(_cfg("rz_bot_bb", 0.15) or 0.15)
    near_top = (bb_up_1h > 0) & (close > bb_up_1h)
    near_top = near_top | ((dc_hi_1h > 0) & (close > dc_hi_1h))
    near_top = near_top | (bb_1h > top_bb) | (dc_pos_1h > top_bb)
    if z_on:
        near_top = near_top | (zs_4h > 2.0) | ((zs_1h > 2.0) & (zs_4h > 1.5))
    near_bottom = (bb_lo_1h > 0) & (close > 0) & (close < bb_lo_1h)
    near_bottom = near_bottom | ((dc_lo_1h > 0) & (close > 0) & (close < dc_lo_1h))
    near_bottom = near_bottom | (bb_1h < bot_bb) | (dc_pos_1h < bot_bb)
    if z_on:
        near_bottom = near_bottom | (zs_4h < -2.0) | ((zs_1h < -2.0) & (zs_4h < -1.5))
    near_baseline = touched & ~near_top & ~near_bottom

    micro = str(_cfg("rz_ltf_micro", "3m") or "3m")
    vel_3m = _arr(f"wt_velocity_{micro}")
    if vel_3m is None:
        vel_3m = _arr("wt_velocity_3m")
    if vel_3m is None:
        vel_3m = np.zeros(n)
    vel_15m = _a("wt_velocity_15m", 0.0)
    vel_1h = _a("wt_velocity_1h", 0.0)
    mom_4h = _a("wt_momentum_state_4h", 0.0)
    mom_D = _a("wt_momentum_state_D", 0.0)
    k_1h = _a("stoch_k_1h", 50.0)
    k_thr = float(_cfg("rz_k_exit", 80.0) or 80.0)
    div_min = int(float(_cfg("rz_div_block_min", 2) or 2))
    div_1h = _a("wt_divergence_1h", 0.0)
    div_4h = _a("wt_divergence_4h", 0.0)
    div_D = _a("wt_divergence_D", 0.0)
    bull_div_n = ((div_1h == -1).astype(int) + (div_4h == -1).astype(int) + (div_D == -1).astype(int))
    bear_div_n = ((div_1h == 1).astype(int) + (div_4h == 1).astype(int) + (div_D == 1).astype(int))

    if is_long:
        if not bool(_cfg("rz_entry_enabled", True)):
            return np.zeros(n, dtype=bool)
        ltf_ok = (vel_3m > 0) | (vel_15m > 0) | (vel_1h > 0)
        htf_ok = ~((mom_4h == -1) & (mom_D == -1))
        return near_baseline & (bull > bear) & ltf_ok & htf_ok & (k_1h < k_thr) & (bull_div_n < div_min)
    if not bool(_cfg("rz_baseline_bounce_short_enabled", True)):
        return np.zeros(n, dtype=bool)
    ltf_ok = (vel_3m < 0) | (vel_15m < 0) | (vel_1h < 0)
    htf_ok = ~((mom_4h == 1) & (mom_D == 1))
    return near_baseline & (bear > bull) & ltf_ok & htf_ok & (k_1h > (100 - k_thr)) & (bear_div_n < div_min)
