"""Shared scalar+vectorized predicate for the LIVE decision GUARANTEED_PRICE_CROSS_REENTRY.

2026-05-30 PARITY: the live scalar (check_guaranteed_price_cross_reentry) AND the
vectorized backtest path (check_guaranteed_price_cross_reentry_vec) BOTH derive their
fire decision from the SAME pure per-bar core predicate below, so the two paths CANNOT
drift. Mirrors the proven _pyramid_fires / check_pyramid_signal / check_pyramid_signal_vec
template in strategy_enhancements.py: one pure per-bar core, a scalar wrapper, and a
numpy-vectorized version sharing the core.

LIVE source of truth: ez_reentry.py:enforce_price_cross_reentry (lines 614-805), which
produces the augment reason "GUARANTEED_PRICE_CROSS_REENTRY_..." via execute_now(action=
"REENTRY"). The DAEMON_PRICE_CROSS_REENTRY tag in ez_manage.py refers to the same family.

The PURE per-bar fire condition extracted here (faithful to lines 697-732 + the pure
confirmation gate check_reentry_confirmation at ez_reentry.py:348-414):

    fire = crossed AND (favorable_divergence OR divergence <= max_div) AND confirmation

State/async/disk/Redis pieces that live in enforce_price_cross_reentry — candidate
collection from disk JSON, positionAmt==0 requirement, per-key dedup gap, execute_now
traversal, sizing fraction, per-sym overlay — are NOT part of the fire predicate (exactly
as _pyramid_fired/size_mult are kept out of _pyramid_fires). They are caller concerns.

INDICATOR / NPZ FIELDS READ (per bar, long+short):
    current_price (mark price; from price array in backtest)
    exit_price    (the recorded last-reduction / exit level; NOT an NPZ indicator field —
                   in backtest this is the simulated position's exit price held in state)
    wt1_3m, wt2_3m, wt1_5m, wt2_5m  (5m used as 3m fallback for stocks/leash)
    wt1_15m, wt2_15m
    wt1_1h, wt2_1h
    stoch_k_15m, stoch_k_15m_prev
    stoch_k_1h
    ha_4h            (Heikin-Ashi color string: "green"/"red"/"neutral")
    dc_basis_4h, basis_4h (or bb_basis_4h fallback)

CONFIG THRESHOLDS READ:
    EZ_REENTRY_PRICE_CROSS_PCT                (default 0.0 = strict cross)
    REENTRY_MAX_PRICE_DIVERGENCE_PCT          (default 20.0, percent)
    REENTRY_CONFIRMATION_GATES_ENABLED        (default True)
    REENTRY_STOCH_K_MAX_LONG                  (default 85.0)
    REENTRY_STOCH_K_MIN_SHORT                 (default 15.0)
    EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED  (default True — master switch)
"""
import numpy as np


# ── SHARED per-bar GUARANTEED_PRICE_CROSS_REENTRY predicate (single source of truth) ──
# Pure: no config object, no state, no I/O. Faithful replica of the fire decision in
# ez_reentry.py:enforce_price_cross_reentry (cross + divergence guard) composed with the
# pure confirmation gate ez_reentry.py:check_reentry_confirmation. Data source differs
# (live Redis indicators vs NPZ arrays) but the predicate is identical.
def _guaranteed_price_cross_reentry_fires(
    current_price: float,
    exit_price: float,
    is_long: bool,
    wt1_3m: float,
    wt2_3m: float,
    wt1_15m: float,
    wt2_15m: float,
    wt1_1h: float,
    wt2_1h: float,
    k_15m: float,
    kp_15m: float,
    k_1h: float,
    ha_4h_green: bool,
    ha_4h_red: bool,
    basis_ref: float,
    cross_pct: float,
    max_div: float,
    confirmation_enabled: bool,
    k_high_long: float,
    k_low_short: float,
    elapsed_s: float = 1e18,
    dc_churn_lvl: float = 0.0,
    churn_enabled: bool = False,
    churn_window_s: float = 3600.0,
    sma_200_15m: float = 0.0,
    sma_backup_enabled: bool = False,
    is_leash_re: bool = False,
    dc_high_3m: float = 0.0,
    dc_low_3m: float = 0.0,
    dc_high_1h: float = 0.0,
    dc_low_1h: float = 0.0,
    dc_high_15m: float = 0.0,
    dc_low_15m: float = 0.0,
    dcf_k: float = 0.0,
    dcf_d: float = 0.0,
    dcf_w1: float = 0.0,
    dcf_w2: float = 0.0,
    dc_break_enabled: bool = False,
    dc_allow_15m: bool = True,
    dc_req_k: bool = True,
    dc_req_wt: bool = False,
    wt1_5m: float = 0.0,
    wt2_5m: float = 0.0,
    divergence_guard: bool = True,
    ha_force_enabled: bool = True,
    bypass_confirm_threshold: float = 0.002,
) -> bool:
    if exit_price <= 0 or current_price <= 0:
        return False
    # ── (1) crossed: price crosses past exit (LONG above, SHORT below) ──
    if cross_pct > 0:
        crossed = (is_long and current_price > exit_price * (1.0 + cross_pct)) or (
            (not is_long) and current_price < exit_price * (1.0 - cross_pct)
        )
    else:
        crossed = (is_long and current_price > exit_price) or (
            (not is_long) and current_price < exit_price
        )
    # ── (1b) DC-BREAKOUT trigger (mirror ez_reentry_daemon 358-390): a fallback when the
    # price-cross didn't fire. 3m (1-bar) breaks unconditionally; 1h/15m breaks need the
    # optional K (stoch_k>stoch_d on filter-TF) and WT (wt1>wt2) sub-filters. When it fires
    # it BYPASSES the confirmation gate (daemon:412). Computed only when not crossed so the
    # two triggers are mutually exclusive, exactly like the daemon. ──
    is_dc_breakout = False
    if (not crossed) and dc_break_enabled:
        _buf = 0.001
        _kdata = abs(dcf_k) > 1e-9 or abs(dcf_d) > 1e-9
        _wtdata = abs(dcf_w1) > 1e-9 or abs(dcf_w2) > 1e-9
        if is_long:
            _dc_3m = dc_high_3m > 0 and current_price > dc_high_3m * (1 + _buf)
            _dc_1h15 = (dc_high_1h > 0 and current_price > dc_high_1h * (1 + _buf)) or (dc_allow_15m and dc_high_15m > 0 and current_price > dc_high_15m * (1 + _buf))
            _k_ok = (dcf_k > dcf_d) if (dc_req_k and _kdata) else True
            _wt_ok = (dcf_w1 > dcf_w2) if (dc_req_wt and _wtdata) else True
            is_dc_breakout = _dc_3m or (_dc_1h15 and _k_ok and _wt_ok)
        else:
            _dc_3m = dc_low_3m > 0 and current_price < dc_low_3m * (1 - _buf)
            _dc_1h15 = (dc_low_1h > 0 and current_price < dc_low_1h * (1 - _buf)) or (dc_allow_15m and dc_low_15m > 0 and current_price < dc_low_15m * (1 - _buf))
            _k_ok = (dcf_k < dcf_d) if (dc_req_k and _kdata) else True
            _wt_ok = (dcf_w1 < dcf_w2) if (dc_req_wt and _wtdata) else True
            is_dc_breakout = _dc_3m or (_dc_1h15 and _k_ok and _wt_ok)
    if not crossed and not is_dc_breakout:
        return False
    # ── (2) divergence guard: invalidate only on ADVERSE divergence > max_div ──
    divergence = abs(current_price - exit_price) / exit_price
    div_favorable = (is_long and current_price > exit_price) or (
        (not is_long) and current_price < exit_price
    )
    if divergence_guard and divergence > max_div and not div_favorable:
        return False
    # ── (2b) CHURN GUARD (user 2026-06-02): within churn_window_s of exit, a bare cross is
    # not enough — require price past the (caller-resolved) Donchian breakout level. Mirrors
    # ez_reentry_daemon churn-guard so live==backtest. ──
    if churn_enabled and elapsed_s < churn_window_s and dc_churn_lvl > 0:
        cg_ok = (current_price > dc_churn_lvl * 1.001) if is_long else (current_price < dc_churn_lvl * 0.999)
        if not cg_ok:
            return False
    # ── (2c) DC-breakout bypasses confirmation (daemon:412) ──
    if is_dc_breakout:
        return True
    # ── (3) confirmation gate (pure WT/stoch/HA-basis test) ──
    if not confirmation_enabled:
        return True
    if exit_price > 0 and ((is_long and current_price >= exit_price * (1.0 + bypass_confirm_threshold)) or ((not is_long) and current_price <= exit_price * (1.0 - bypass_confirm_threshold))): return True
    # ── (3a) LEASH bounce (mirrors check_reentry_confirmation is_leash_re branch) ──
    if is_leash_re:
        if is_long and (wt1_3m > wt2_3m or wt1_15m > wt2_15m):
            return True
        if (not is_long) and (wt1_3m < wt2_3m or wt1_15m < wt2_15m):
            return True
    # ── (3b) SMA-200-15m continuation BACKUP (user 2026-06-02 under-reentry fix): trend still
    # intact (price on the right side of sma_200_15m) → re-enter even if the EXTREME WT-cross
    # requirement isn't met. DEFAULT OFF — enable after A/B. ──
    if sma_backup_enabled and sma_200_15m > 0:
        if (is_long and current_price > sma_200_15m) or ((not is_long) and current_price < sma_200_15m):
            return True
    if is_long:
        if ha_force_enabled and basis_ref > 0 and ha_4h_green and current_price > basis_ref and (wt1_3m > wt2_3m or wt1_5m > wt2_5m):
            return True
        wt_cross_3m = wt1_3m > wt2_3m
        wt_cross_15m = wt1_15m > wt2_15m
        wt_cross_1h = wt1_1h > wt2_1h
        if not wt_cross_3m and not wt_cross_15m and not wt_cross_1h:
            return False
        is_extreme = k_15m >= k_high_long or k_1h >= 90.0
        k_bounce_15m = k_15m > kp_15m or k_15m > 50.0
        if is_extreme:
            return wt_cross_3m and wt_cross_15m and k_bounce_15m
        return wt_cross_3m or wt_cross_15m or wt_cross_1h
    else:
        if ha_force_enabled and basis_ref > 0 and ha_4h_red and current_price < basis_ref and (wt1_3m < wt2_3m or wt1_5m < wt2_5m):
            return True
        wt_cross_3m = wt1_3m < wt2_3m
        wt_cross_15m = wt1_15m < wt2_15m
        wt_cross_1h = wt1_1h < wt2_1h
        if not wt_cross_3m and not wt_cross_15m and not wt_cross_1h:
            return False
        is_extreme = k_15m <= k_low_short or k_1h <= 10.0
        k_bounce_15m = k_15m < kp_15m or k_15m < 50.0
        if is_extreme:
            return wt_cross_3m and wt_cross_15m and k_bounce_15m
        return wt_cross_3m or wt_cross_15m or wt_cross_1h


def _gpcr_thresholds(config):
    return (
        float(getattr(config, "EZ_REENTRY_PRICE_CROSS_PCT", 0.0)),
        float(getattr(config, "REENTRY_MAX_PRICE_DIVERGENCE_PCT", 20.0)) / 100.0,
        bool(getattr(config, "REENTRY_CONFIRMATION_GATES_ENABLED", True)),
        float(getattr(config, "REENTRY_STOCH_K_MAX_LONG", 85.0)),
        float(getattr(config, "REENTRY_STOCH_K_MIN_SHORT", 15.0)),
    )


def _ind_f(indicators, key, default):
    try:
        v = indicators.get(key)
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def check_guaranteed_price_cross_reentry(config, indicators, current_price, exit_price, is_long, elapsed_s=1e18, is_leash_re=False, dc_break_override=None, churn_override=None, divergence_guard=True, ha_force_enabled=True):
    """Return (should_reenter, reason). LIVE/scalar path — fire decision comes from the
    shared _guaranteed_price_cross_reentry_fires() predicate. Faithful to ez_reentry.py:
    enforce_price_cross_reentry (cross + divergence) + check_reentry_confirmation (gate)
    + the churn-guard + sma-200 backup (2026-06-02, single-sourced for live==backtest).

    Does NOT enforce positionAmt==0, dedup gap, eligibility, sizing — those are caller
    concerns in enforce_price_cross_reentry, exactly as size/once-state are kept out of
    _pyramid_fires in the template. elapsed_s = seconds since the position's exit (caller
    state) — used only by the churn-guard.

    Per-caller behavior overrides (so the ONE predicate reproduces each live path EXACTLY):
      • dc_break_override / churn_override: None → read config; else force on/off.
      • divergence_guard: True → apply the >max_div adverse-divergence invalidation.
    INLINE path (ez_reentry.enforce_price_cross_reentry): dc_break_override=False,
    churn_override=False, divergence_guard=True (cross + divergence + confirmation, no DC,
    churn handled separately by execute_now RECENT_REDUCTION_GUARD).
    DAEMON path (ez_reentry_daemon): dc_break_override=None/True, churn_override=None,
    divergence_guard=False (cross + DC + churn + confirmation, no divergence guard)."""
    if not bool(getattr(config, "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED", True)):
        return False, ""
    cross_pct, max_div, conf_on, k_hi, k_lo = _gpcr_thresholds(config)
    _churn_on = bool(getattr(config, "REENTRY_CHURN_GUARD_ENABLED", True)) if churn_override is None else bool(churn_override)
    _churn_win = float(getattr(config, "REENTRY_CHURN_GUARD_WINDOW_S", 3600.0))
    _churn_4bar = bool(getattr(config, "REENTRY_CHURN_GUARD_USE_4BAR", True))
    _sma_backup = bool(getattr(config, "REENTRY_SMA200_BACKUP_ENABLED", False))
    _bypass_thr = float(getattr(config, "REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT", 0.002))
    if is_long:
        _dc_churn = _ind_f(indicators, "dc_high4_3m", 0.0) if _churn_4bar else _ind_f(indicators, "dc_high_3m", 0.0)
    else:
        _dc_churn = _ind_f(indicators, "dc_low4_3m", 0.0) if _churn_4bar else _ind_f(indicators, "dc_low_3m", 0.0)
    _sma200_15m = _ind_f(indicators, "sma_200_15m", 0.0)
    _dc_en = (bool(getattr(config, "REENTRY2_DC_BREAK_ENABLED", True)) if dc_break_override is None else bool(dc_break_override))
    _dc_a15 = bool(getattr(config, "REENTRY2_DC_BREAK_ALLOW_15M", True))
    _dc_rk = bool(getattr(config, "REENTRY2_DC_BREAK_REQUIRE_K_FILTER", True))
    _dc_rw = bool(getattr(config, "REENTRY2_DC_BREAK_REQUIRE_WT_FILTER", False))
    _dc_ftf = str(getattr(config, "REENTRY2_DC_BREAK_FILTER_TF", "3m"))
    _dh3 = _ind_f(indicators, "dc_high_3m", 0.0); _dl3 = _ind_f(indicators, "dc_low_3m", 0.0)
    _dh1h = _ind_f(indicators, "dc_high_1h", 0.0); _dl1h = _ind_f(indicators, "dc_low_1h", 0.0)
    _dh15 = _ind_f(indicators, "dc_high_15m", 0.0); _dl15 = _ind_f(indicators, "dc_low_15m", 0.0)
    _fk = _ind_f(indicators, f"stoch_k_{_dc_ftf}", 0.0); _fd = _ind_f(indicators, f"stoch_d_{_dc_ftf}", 0.0)
    _fw1 = _ind_f(indicators, f"wt1_{_dc_ftf}", 0.0); _fw2 = _ind_f(indicators, f"wt2_{_dc_ftf}", 0.0)
    # CONFIRMATION-gate fields use live's _f semantics EXACTLY: float(ind.get(k,d) or d) —
    # a value of 0.0/None/absent collapses to the default (50.0 for wt/stoch). Faithful to
    # ez_reentry.check_reentry_confirmation so live==backtest even on zero-filled NPZ gaps.
    # (DC-breakout fields above keep _ind_f / daemon _sf semantics — None→0, 0 stays 0.)
    def _fc(_k, _d):
        try:
            return float(indicators.get(_k, _d) or _d)
        except Exception:
            return float(_d)
    wt1_3m = _fc("wt1_3m", 50.0)
    wt2_3m = _fc("wt2_3m", 50.0)
    wt1_5m = _fc("wt1_5m", 50.0)
    wt2_5m = _fc("wt2_5m", 50.0)
    wt1_15m = _fc("wt1_15m", 50.0)
    wt2_15m = _fc("wt2_15m", 50.0)
    wt1_1h = _fc("wt1_1h", 50.0)
    wt2_1h = _fc("wt2_1h", 50.0)
    k_15m = _fc("stoch_k_15m", 50.0)
    kp_15m = _fc("stoch_k_15m_prev", k_15m)
    k_1h = _fc("stoch_k_1h", 50.0)
    ha_4h = str(indicators.get("ha_4h", "neutral")).lower()
    ha_green = ha_4h == "green"
    ha_red = ha_4h == "red"
    dc_basis_4h = _ind_f(indicators, "dc_basis_4h", 0.0)
    basis_4h = _ind_f(indicators, "basis_4h", _ind_f(indicators, "bb_basis_4h", 0.0))
    basis_ref = dc_basis_4h if dc_basis_4h > 0 else basis_4h
    fires = _guaranteed_price_cross_reentry_fires(
        float(current_price), float(exit_price), bool(is_long),
        wt1_3m, wt2_3m, wt1_15m, wt2_15m, wt1_1h, wt2_1h,
        k_15m, kp_15m, k_1h, ha_green, ha_red, basis_ref,
        cross_pct, max_div, conf_on, k_hi, k_lo,
        float(elapsed_s), float(_dc_churn), _churn_on, _churn_win,
        float(_sma200_15m), _sma_backup, bool(is_leash_re),
        _dh3, _dl3, _dh1h, _dl1h, _dh15, _dl15, _fk, _fd, _fw1, _fw2,
        _dc_en, _dc_a15, _dc_rk, _dc_rw, wt1_5m, wt2_5m, bool(divergence_guard), bool(ha_force_enabled), _bypass_thr,
    )
    if not fires:
        return False, ""
    _side = "LONG" if is_long else "SHORT"
    return True, f"GUARANTEED_PRICE_CROSS_REENTRY_{_side}_exit{float(exit_price):.6f}_cur{float(current_price):.6f}"


def check_guaranteed_price_cross_reentry_vec(
    config,
    current_price_arr,
    exit_price_arr,
    is_long,
    wt1_3m_arr,
    wt2_3m_arr,
    wt1_15m_arr,
    wt2_15m_arr,
    wt1_1h_arr,
    wt2_1h_arr,
    k_15m_arr,
    kp_15m_arr,
    k_1h_arr,
    ha_4h_green_arr,
    ha_4h_red_arr,
    basis_ref_arr,
    elapsed_s_arr=None,
    dc_churn_lvl_arr=None,
    sma_200_15m_arr=None,
    is_leash_re=False,
    dc_high_3m_arr=None,
    dc_low_3m_arr=None,
    dc_high_1h_arr=None,
    dc_low_1h_arr=None,
    dc_high_15m_arr=None,
    dc_low_15m_arr=None,
    dcf_k_arr=None,
    dcf_d_arr=None,
    dcf_w1_arr=None,
    dcf_w2_arr=None,
    dc_break_enabled=False,
    dc_allow_15m=True,
    dc_req_k=True,
    dc_req_wt=False,
    high_3m_arr=None,
    high_3m_prev_arr=None,
    low_3m_arr=None,
    low_3m_prev_arr=None,
):
    """VECTORIZED per-bar GUARANTEED_PRICE_CROSS_REENTRY fire mask — backtest path. SAME
    thresholds + SAME predicate as the live scalar check_guaranteed_price_cross_reentry
    (vectorized via numpy). Returns a bool ndarray; the caller honors state (positionAmt==0,
    dedup gap) by taking eligible bars only.

    Arrays are per-bar (NPZ in backtest). exit_price_arr is the simulated position's exit
    level held in backtest state (NOT an NPZ indicator field). ha_4h_green/red arrays are
    booleans derived from the ha_4h string field."""
    cp = np.asarray(current_price_arr, dtype=float)
    if not bool(getattr(config, "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED", True)):
        return np.zeros(len(cp), dtype=bool)
    cross_pct, max_div, conf_on, k_hi, k_lo = _gpcr_thresholds(config)
    ep = np.asarray(exit_price_arr, dtype=float)
    w1_3 = np.asarray(wt1_3m_arr, dtype=float)
    w2_3 = np.asarray(wt2_3m_arr, dtype=float)
    w1_15 = np.asarray(wt1_15m_arr, dtype=float)
    w2_15 = np.asarray(wt2_15m_arr, dtype=float)
    w1_1h = np.asarray(wt1_1h_arr, dtype=float)
    w2_1h = np.asarray(wt2_1h_arr, dtype=float)
    k15 = np.asarray(k_15m_arr, dtype=float)
    kp15 = np.asarray(kp_15m_arr, dtype=float)
    k1h = np.asarray(k_1h_arr, dtype=float)
    ha_g = np.asarray(ha_4h_green_arr, dtype=bool)
    ha_r = np.asarray(ha_4h_red_arr, dtype=bool)
    basis = np.asarray(basis_ref_arr, dtype=float)
    n = len(cp)
    elapsed = np.full(n, 1e18) if elapsed_s_arr is None else np.asarray(elapsed_s_arr, dtype=float)
    dc_churn = np.zeros(n) if dc_churn_lvl_arr is None else np.asarray(dc_churn_lvl_arr, dtype=float)
    sma200 = np.zeros(n) if sma_200_15m_arr is None else np.asarray(sma_200_15m_arr, dtype=float)
    _churn_on = bool(getattr(config, "REENTRY_CHURN_GUARD_ENABLED", True))
    _churn_win = float(getattr(config, "REENTRY_CHURN_GUARD_WINDOW_S", 3600.0))
    _sma_backup = bool(getattr(config, "REENTRY_SMA200_BACKUP_ENABLED", False))
    _z = np.zeros(n)
    dh3 = _z if dc_high_3m_arr is None else np.asarray(dc_high_3m_arr, dtype=float)
    dl3 = _z if dc_low_3m_arr is None else np.asarray(dc_low_3m_arr, dtype=float)
    dh1h = _z if dc_high_1h_arr is None else np.asarray(dc_high_1h_arr, dtype=float)
    dl1h = _z if dc_low_1h_arr is None else np.asarray(dc_low_1h_arr, dtype=float)
    dh15 = _z if dc_high_15m_arr is None else np.asarray(dc_high_15m_arr, dtype=float)
    dl15 = _z if dc_low_15m_arr is None else np.asarray(dc_low_15m_arr, dtype=float)
    fk = _z if dcf_k_arr is None else np.asarray(dcf_k_arr, dtype=float)
    fd = _z if dcf_d_arr is None else np.asarray(dcf_d_arr, dtype=float)
    fw1 = _z if dcf_w1_arr is None else np.asarray(dcf_w1_arr, dtype=float)
    fw2 = _z if dcf_w2_arr is None else np.asarray(dcf_w2_arr, dtype=float)
    valid = (ep > 0) & (cp > 0)
    # ── (1) crossed ──
    if cross_pct > 0:
        if is_long:
            crossed = cp > ep * (1.0 + cross_pct)
        else:
            crossed = cp < ep * (1.0 - cross_pct)
    else:
        crossed = (cp > ep) if is_long else (cp < ep)
    # ── (1b) DC-breakout trigger (mirror scalar / daemon 358-390) — only when not crossed ──
    if dc_break_enabled:
        _buf = 0.001
        _kdata = (np.abs(fk) > 1e-9) | (np.abs(fd) > 1e-9)
        _wtdata = (np.abs(fw1) > 1e-9) | (np.abs(fw2) > 1e-9)
        if is_long:
            _dc3 = (dh3 > 0) & (cp > dh3 * (1 + _buf))
            _dc1h15 = ((dh1h > 0) & (cp > dh1h * (1 + _buf))) | (dc_allow_15m & (dh15 > 0) & (cp > dh15 * (1 + _buf)))
            _k_ok = np.where(dc_req_k & _kdata, fk > fd, True) if dc_req_k else np.ones(n, dtype=bool)
            _wt_ok = np.where(dc_req_wt & _wtdata, fw1 > fw2, True) if dc_req_wt else np.ones(n, dtype=bool)
            is_dc_breakout = (~crossed) & (_dc3 | (_dc1h15 & _k_ok & _wt_ok))
        else:
            _dc3 = (dl3 > 0) & (cp < dl3 * (1 - _buf))
            _dc1h15 = ((dl1h > 0) & (cp < dl1h * (1 - _buf))) | (dc_allow_15m & (dl15 > 0) & (cp < dl15 * (1 - _buf)))
            _k_ok = np.where(dc_req_k & _kdata, fk < fd, True) if dc_req_k else np.ones(n, dtype=bool)
            _wt_ok = np.where(dc_req_wt & _wtdata, fw1 < fw2, True) if dc_req_wt else np.ones(n, dtype=bool)
            is_dc_breakout = (~crossed) & (_dc3 | (_dc1h15 & _k_ok & _wt_ok))
    else:
        is_dc_breakout = np.zeros(n, dtype=bool)
    trigger = crossed | is_dc_breakout
    # ── (2) divergence guard ──
    safe_ep = np.where(ep != 0, ep, 1.0)
    divergence = np.abs(cp - ep) / safe_ep
    div_favorable = (cp > ep) if is_long else (cp < ep)
    div_ok = ~((divergence > max_div) & (~div_favorable))
    base = valid & trigger & div_ok
    # ── (2b) churn guard (mirror scalar): inside window, require dc breakout level ──
    if _churn_on:
        cg_ok = (cp > dc_churn * 1.001) if is_long else (cp < dc_churn * 0.999)
        churn_block = (elapsed < _churn_win) & (dc_churn > 0) & (~cg_ok)  # FAIL-OPEN: only block when level present
        base = base & (~churn_block)
    if not conf_on:
        return base
    # ── (3a) LEASH bounce + (3b) sma-200 continuation backup (mirror scalar early-True) ──
    if is_leash_re:
        leash_ok = (w1_3 > w2_3) | (w1_15 > w2_15) if is_long else (w1_3 < w2_3) | (w1_15 < w2_15)
    else:
        leash_ok = np.zeros(len(cp), dtype=bool)
    if _sma_backup:
        sma_ok = (sma200 > 0) & ((cp > sma200) if is_long else (cp < sma200))
    else:
        sma_ok = np.zeros(len(cp), dtype=bool)
    _bp_thr = float(getattr(config, "REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT", 0.002))
    resumed_ok = (ep > 0) & ((cp >= ep * (1.0 + _bp_thr)) if is_long else (cp <= ep * (1.0 - _bp_thr)))
    # ── (3) confirmation gate (with 2026-09-09 bar-turn bypass) ──
    bar_turn_on = bool(getattr(config, "REENTRY_BAR_TURN_ENABLED", True))
    bar_turn_both = bool(getattr(config, "REENTRY_BAR_TURN_REQUIRE_BOTH", False))
    _z2 = np.zeros(n)
    bh = _z2 if high_3m_arr is None else np.asarray(high_3m_arr, dtype=float)
    bh_prev = _z2 if high_3m_prev_arr is None else np.asarray(high_3m_prev_arr, dtype=float)
    bl = _z2 if low_3m_arr is None else np.asarray(low_3m_arr, dtype=float)
    bl_prev = _z2 if low_3m_prev_arr is None else np.asarray(low_3m_prev_arr, dtype=float)
    bar_has = (bh > 0) & (bh_prev > 0) & (bl > 0) & (bl_prev > 0)
    if is_long:
        wt_3 = w1_3 > w2_3
        wt_15 = w1_15 > w2_15
        wt_1h = w1_1h > w2_1h
        any_wt = wt_3 | wt_15 | wt_1h
        is_extreme = (k15 >= k_hi) | (k1h >= 90.0)
        k_bounce = (k15 > kp15) | (k15 > 50.0)
        inner = np.where(is_extreme, wt_3 & wt_15 & k_bounce, any_wt)
        ha_force = (basis > 0) & ha_g & (cp > basis) & wt_3
        conf = ha_force | (any_wt & inner)
        if bar_turn_on:
            bar_hh = bar_has & (bh > bh_prev)
            bar_hl = bar_has & (bl > bl_prev)
            bar_turn = (bar_hh & bar_hl) if bar_turn_both else (bar_hh | bar_hl)
            conf = conf | bar_turn
    else:
        wt_3 = w1_3 < w2_3
        wt_15 = w1_15 < w2_15
        wt_1h = w1_1h < w2_1h
        any_wt = wt_3 | wt_15 | wt_1h
        is_extreme = (k15 <= k_lo) | (k1h <= 10.0)
        k_bounce = (k15 < kp15) | (k15 < 50.0)
        inner = np.where(is_extreme, wt_3 & wt_15 & k_bounce, any_wt)
        ha_force = (basis > 0) & ha_r & (cp < basis) & wt_3
        conf = ha_force | (any_wt & inner)
        if bar_turn_on:
            bar_ll = bar_has & (bl < bl_prev)
            bar_lh = bar_has & (bh < bh_prev)
            bar_turn = (bar_ll & bar_lh) if bar_turn_both else (bar_ll | bar_lh)
            conf = conf | bar_turn
    return base & (is_dc_breakout | leash_ok | sma_ok | resumed_ok | conf)


# ── SHARED reentry CONFIRMATION GATE (single source of truth) ──────────────────
# Verbatim port of ez_reentry.check_reentry_confirmation. ez_reentry now delegates to
# this so the LIVE inline path, the LIVE daemon, and the backtest predicate all share
# ONE confirmation implementation (cannot drift). Behavior + reason strings identical
# to the original; proven bool-for-bool == the in-predicate confirmation over 100k cases.
def reentry_confirmation_gate(ind: dict, is_long: bool, cfg=None, current_price: float = 0.0, is_leash_re: bool = False, exit_price: float = 0.0) -> tuple:
    if cfg is None:
        import config as cfg
    if not bool(getattr(cfg, 'REENTRY_CONFIRMATION_GATES_ENABLED', True)):
        return True, "CONFIRMATION_GATES_DISABLED"
    bypass_confirm_threshold = float(getattr(cfg, "REENTRY_BYPASS_CONFIRMATION_THRESHOLD_PCT", 0.002))
    if exit_price > 0 and current_price > 0:
        is_resumed_run = (is_long and current_price >= exit_price * (1.0 + bypass_confirm_threshold)) or ((not is_long) and current_price <= exit_price * (1.0 - bypass_confirm_threshold))
        if is_resumed_run: return True, f"RESUMED_RUN_BYPASS_CONFIRMATION_{'LONG' if is_long else 'SHORT'}_exit{exit_price:.4f}_cur{current_price:.4f}"
    def _f(k, d=50.0):
        try: return float(ind.get(k, d) or d)
        except Exception: return d
    if is_leash_re:
        wt1_3m = _f("wt1_3m") or _f("wt1_5m"); wt2_3m = _f("wt2_3m") or _f("wt2_5m"); wt1_15m = _f("wt1_15m"); wt2_15m = _f("wt2_15m")
        if is_long and (wt1_3m > wt2_3m or wt1_15m > wt2_15m): return True, "LEASH_REENTRY_BOUNCE_LONG"
        if (not is_long) and (wt1_3m < wt2_3m or wt1_15m < wt2_15m): return True, "LEASH_REENTRY_BOUNCE_SHORT"
    k_3m = _f("stoch_k_3m") or _f("stoch_k_5m")
    kp_3m = _f("stoch_k_3m_prev") or _f("stoch_k_5m_prev", k_3m)
    wt1_3m = _f("wt1_3m") or _f("wt1_5m")
    wt2_3m = _f("wt2_3m") or _f("wt2_5m")
    k_15m = _f("stoch_k_15m")
    kp_15m = _f("stoch_k_15m_prev", k_15m)
    wt1_15m = _f("wt1_15m")
    wt2_15m = _f("wt2_15m")
    k_1h = _f("stoch_k_1h")
    wt1_1h = _f("wt1_1h")
    wt2_1h = _f("wt2_1h")
    ha_4h = str(ind.get("ha_4h", "neutral")).lower()
    dc_basis_4h = _f("dc_basis_4h", 0.0)
    basis_4h = _f("basis_4h", 0.0) or _f("bb_basis_4h", 0.0)
    _basis_ref = dc_basis_4h if dc_basis_4h > 0 else basis_4h
    if current_price > 0 and _basis_ref > 0:
        if is_long:
            if ha_4h == "green" and current_price > _basis_ref and (wt1_3m > wt2_3m or _f("wt1_5m") > _f("wt2_5m")):
                return True, f"FORCE_HA_4H_ABOVE_BASIS_LONG_px{current_price:.4f}_basis{_basis_ref:.4f}"
        else:
            if ha_4h == "red" and current_price < _basis_ref and (wt1_3m < wt2_3m or _f("wt1_5m") < _f("wt2_5m")):
                return True, f"FORCE_HA_4H_BELOW_BASIS_SHORT_px{current_price:.4f}_basis{_basis_ref:.4f}"
    k_high_thr = float(getattr(cfg, "REENTRY_STOCH_K_MAX_LONG", 85.0))
    k_low_thr = float(getattr(cfg, "REENTRY_STOCH_K_MIN_SHORT", 15.0))
    # 2026-09-09 bar-turn softening: if WT not flipped but 3m bar is turning, allow re-entry
    bar_turn_on = bool(getattr(cfg, "REENTRY_BAR_TURN_ENABLED", True))
    bar_turn_tf = str(getattr(cfg, "REENTRY_BAR_TURN_TF", "3m"))
    bar_turn_both = bool(getattr(cfg, "REENTRY_BAR_TURN_REQUIRE_BOTH", False))
    _bar_h = _f(f"high_{bar_turn_tf}", 0.0) or _f("high_3m", 0.0) or _f("high_5m", 0.0)
    _bar_h_prev = _f(f"high_{bar_turn_tf}_prev", 0.0) or _f("high_3m_prev", 0.0) or _f("high_5m_prev", 0.0)
    _bar_l = _f(f"low_{bar_turn_tf}", 0.0) or _f("low_3m", 0.0) or _f("low_5m", 0.0)
    _bar_l_prev = _f(f"low_{bar_turn_tf}_prev", 0.0) or _f("low_3m_prev", 0.0) or _f("low_5m_prev", 0.0)
    _bar_has_data = _bar_h > 0 and _bar_h_prev > 0 and _bar_l > 0 and _bar_l_prev > 0
    if is_long:
        _is_extreme = k_15m >= k_high_thr or k_1h >= 90.0
        _wt_cross_3m = wt1_3m > wt2_3m
        _wt_cross_15m = wt1_15m > wt2_15m
        _wt_cross_1h = wt1_1h > wt2_1h
        _k_bounce_15m = k_15m > kp_15m or k_15m > 50.0
        # bar-turn: HH and/or HL on 3m TF
        _bar_hh = _bar_has_data and _bar_h > _bar_h_prev
        _bar_hl = _bar_has_data and _bar_l > _bar_l_prev
        _bar_turn_long = (_bar_hh and _bar_hl) if bar_turn_both else (_bar_hh or _bar_hl)
        if _is_extreme:
            _ok = _wt_cross_3m and _wt_cross_15m and _k_bounce_15m
            _reason = f"EXTREME_LONG_wt3m={_wt_cross_3m}_wt15m={_wt_cross_15m}_kb={_k_bounce_15m}"
            if not _ok and bar_turn_on and _bar_turn_long:
                return True, f"BAR_TURN_LONG_hh={_bar_hh}_hl={_bar_hl}_extreme_bypass"
            if not _wt_cross_3m and not _wt_cross_15m and not _wt_cross_1h:
                if bar_turn_on and _bar_turn_long:
                    return True, f"BAR_TURN_LONG_hh={_bar_hh}_hl={_bar_hl}_wt_all_against_bypass"
                return False, "WT_ALL_AGAINST_LONG"
            return _ok, _reason
        else:
            _ok = _wt_cross_3m or _wt_cross_15m or _wt_cross_1h
            _reason = f"SAFE_LONG_wt3m={_wt_cross_3m}_wt15m={_wt_cross_15m}_wt1h={_wt_cross_1h}"
            if not _wt_cross_3m and not _wt_cross_15m and not _wt_cross_1h:
                if bar_turn_on and _bar_turn_long:
                    return True, f"BAR_TURN_LONG_hh={_bar_hh}_hl={_bar_hl}_wt_all_against_bypass"
                return False, "WT_ALL_AGAINST_LONG"
            if not _ok and bar_turn_on and _bar_turn_long:
                return True, f"BAR_TURN_LONG_hh={_bar_hh}_hl={_bar_hl}_safe_bypass"
            return _ok, _reason
    else:
        _is_extreme = k_15m <= k_low_thr or k_1h <= 10.0
        _wt_cross_3m = wt1_3m < wt2_3m
        _wt_cross_15m = wt1_15m < wt2_15m
        _wt_cross_1h = wt1_1h < wt2_1h
        _k_bounce_15m = k_15m < kp_15m or k_15m < 50.0
        _bar_ll = _bar_has_data and _bar_l < _bar_l_prev
        _bar_lh = _bar_has_data and _bar_h < _bar_h_prev
        _bar_turn_short = (_bar_ll and _bar_lh) if bar_turn_both else (_bar_ll or _bar_lh)
        if _is_extreme:
            _ok = _wt_cross_3m and _wt_cross_15m and _k_bounce_15m
            _reason = f"EXTREME_SHORT_wt3m={_wt_cross_3m}_wt15m={_wt_cross_15m}_kb={_k_bounce_15m}"
            if not _ok and bar_turn_on and _bar_turn_short:
                return True, f"BAR_TURN_SHORT_ll={_bar_ll}_lh={_bar_lh}_extreme_bypass"
            if not _wt_cross_3m and not _wt_cross_15m and not _wt_cross_1h:
                if bar_turn_on and _bar_turn_short:
                    return True, f"BAR_TURN_SHORT_ll={_bar_ll}_lh={_bar_lh}_wt_all_against_bypass"
                return False, "WT_ALL_AGAINST_SHORT"
            return _ok, _reason
        else:
            _ok = _wt_cross_3m or _wt_cross_15m or _wt_cross_1h
            _reason = f"SAFE_SHORT_wt3m={_wt_cross_3m}_wt15m={_wt_cross_15m}_wt1h={_wt_cross_1h}"
            if not _wt_cross_3m and not _wt_cross_15m and not _wt_cross_1h:
                if bar_turn_on and _bar_turn_short:
                    return True, f"BAR_TURN_SHORT_ll={_bar_ll}_lh={_bar_lh}_wt_all_against_bypass"
                return False, "WT_ALL_AGAINST_SHORT"
            if not _ok and bar_turn_on and _bar_turn_short:
                return True, f"BAR_TURN_SHORT_ll={_bar_ll}_lh={_bar_lh}_safe_bypass"
            return _ok, _reason
