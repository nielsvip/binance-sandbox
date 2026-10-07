# -*- coding: utf-8 -*-
"""ported_stateful_exit — vectorized INDICATOR masks + gain/age GATE specs for STATEFUL EXIT switches whose live
decision depends on position gain/age/count (not a pure per-bar indicator). The parent wires the gate
into simulate_one's bar-walk (where live_pnl_pct, held_bars, pos exist). Rules: IDENTICAL to ez_manage
(15m floor, NO proxies, NO fabrication). Each entry returns (mask, gate) where:
  mask  = np.ndarray[bool] of the gain-INDEPENDENT indicator condition (per bar)
  gate  = dict describing the position-state condition, e.g.
          {'gain_op':'>=','gain_thr':0.10}  or  {'gain_op':'<','gain_thr':0.0,'age_min_bars':N}
          or {'peak_giveback_pct':X}, etc. — documented per switch so the parent applies it faithfully.
An all-False mask (switch off / not differing from default) = the parent skips it (no per-bar cost).

Gate keys the parent understands (walk provides live_pnl_pct=current gain %, held_bars=age in bars,
bmin=minutes/bar, peak_pnl_pct):
  'gain_op'/'gain_thr'   -> fire only if live_pnl_pct <op> gain_thr
  'age_min_minutes'/'age_min_bars' -> minimum hold (live uses strict > for the seconds threshold noted below)
  'peak_giveback_pct'    -> fire if (peak_pnl_pct - live_pnl_pct) >= X
All keys in one gate dict must hold together (AND).

WIRED families (real gain/age-gated exits in ez_manage.process_position; NPZ keys confirmed):
  1. WT_4H_VEL_EXIT*  (ez_manage.py:49766-49841) — mask = 4h WT velocity against position AND (if
     WT_4H_VEL_EXIT_REQUIRE_K_EXTREME) stoch-K at extreme; gate = age > 360s AND (if REQUIRE_PROFIT)
     gain >= COMMISSION_BUFFER_PCT. Switches: ENABLED, LONG_VEL_MIN, SHORT_VEL_MIN, REQUIRE_K_EXTREME,
     K_EXTREME_HIGH, K_EXTREME_LOW, REQUIRE_PROFIT.
  2. WT_EXHAUST_EXIT_MIN_GAIN_PCT / WT_EXHAUST_EXIT_REQUIRE_GAIN (ez_manage.py:49901-49942) — mask =
     wt_momentum_state EXHAUST on 4h AND (1h OR 15m); gate = the gain condition set by those two knobs.
  3. MU_CORRECTION_EXIT_ENABLED (tradier_manage.py evaluate_stop MU_CORRECTION_PEAK_ROLLOVER block) —
     mask = LONG-only + symbol in MU_CORRECTION_SYMBOLS + HTF(1h/4h) K/RSI overbought + high/close
     rollover + LTF fall; gate = gain >= MU_CORRECTION_MIN_GAIN_PCT. The 5m LTF leg is 15m-floored
     (GUIDE section 4): LTF_TFS '5m+15m' counts the 15m predicate twice, exactly like live counts
     two legs when both map to the same predicate.

See VEC_UNSUPPORTED (bottom) for the switches that CANNOT be honestly ported and why.
"""
import numpy as np


# NPZ int8 decode of wt_momentum_state_<tf> (backtest_v8_precompute.py:1177-1180 assigns
#   +2 score>0&vel>0, +1 score>0&vel<=0, -1 score<0&vel>=0, -2 score<0&vel<0). The code-grounded
# EXHAUST decode of that same field is position_evaluator.py:603-609 (documented inline:
#   "mom = 1 -> score>0 AND vel<=0 -> EXHAUST_UP", "mom = -1 -> ... -> EXHAUST_DOWN"), which is the
# in-repo vectorized twin of this exact exit. (backtest_v8_harness.py:192 carries a DIFFERENT/legacy
# map 2=EXHAUST_UP; that map contradicts the precompute's own semantics and is not used here.)
_MOM_EXHAUST_UP = 1
_MOM_EXHAUST_DOWN = -1


def _fnum(cfg, key, default):
    try:
        v = getattr(cfg, key, default)
        return float(v) if v is not None else float(default)
    except Exception:
        return float(default)


def _fbool(cfg, key, default):
    v = getattr(cfg, key, default)
    return bool(default) if v is None else bool(v)


_REF = None


def _ref_default(key, fallback):
    """Effective default = the value on a fresh QuickConfig baseline (the config/config_tradier bold
    value the parent flips FROM), falling back to the live-literal `fallback` if QuickConfig is
    unavailable. Lazy + cached to avoid an import cycle (v12_quick_engine imports vec_decisions)."""
    global _REF
    if _REF is None:
        try:
            import v12_quick_engine as _V
            _REF = _V.QuickConfig()
        except Exception:
            _REF = False
    if _REF not in (None, False):
        try:
            v = getattr(_REF, key)
            if v is not None:
                return v
        except Exception:
            pass
    return fallback


def _num_differs(cfg, key, default):
    d = _ref_default(key, default)
    try:
        d = float(d)
    except Exception:
        d = float(default)
    return abs(_fnum(cfg, key, d) - d) > 1e-9


def _bool_differs(cfg, key, default):
    d = bool(_ref_default(key, default))
    return _fbool(cfg, key, d) != d


def _wt_4h_vel(npz, n, is_long, cfg, _safe):
    """Vector twin of the WT_4H_VEL_EXIT block, ez_manage.py:49766-49841.
    Returns (indicator_mask, gate). mask is the gain-independent part; gate carries age + profit."""
    enabled = _fbool(cfg, "WT_4H_VEL_EXIT_ENABLED", True)
    vel4h = _safe(npz, "wt_velocity_4h", n, 0.0)
    if is_long:
        against = vel4h < _fnum(cfg, "WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0)
    else:
        against = vel4h > _fnum(cfg, "WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0)
    if _fbool(cfg, "WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True):
        # live reads k_3m OR k_15m; 3m->15m floor collapses both terms to k_15m (SWITCH_WIRING_GUIDE §4).
        k15 = _safe(npz, "k_15m", n, 50.0)
        if is_long:
            kx_ok = k15 >= _fnum(cfg, "WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0)
        else:
            kx_ok = k15 <= _fnum(cfg, "WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0)
    else:
        kx_ok = np.ones(n, dtype=bool)
    mask = (against & kx_ok) if enabled else np.zeros(n, dtype=bool)
    # gate: live requires _pos_age_s > 360 (strict, 360s == 6.0 min) AND, if REQUIRE_PROFIT,
    #   _pp_g >= COMMISSION_BUFFER_PCT (gain in %, default 0.10%).
    gate = {"age_min_minutes": 6.0}
    if _fbool(cfg, "WT_4H_VEL_EXIT_REQUIRE_PROFIT", True):
        gate["gain_op"] = ">="
        gate["gain_thr"] = _fnum(cfg, "COMMISSION_BUFFER_PCT", 0.10)
    return np.asarray(mask, dtype=bool), gate


def _wt_exhaust(npz, n, is_long, cfg, _safe):
    """Vector twin of the WT_EXHAUST_EXIT block, ez_manage.py:49901-49942.
    mask = wt_momentum_state EXHAUST on 4h AND (1h OR 15m); gate = the gain condition set by
    WT_EXHAUST_EXIT_REQUIRE_GAIN / WT_EXHAUST_EXIT_MIN_GAIN_PCT."""
    enabled = _fbool(cfg, "WT_EXHAUST_EXIT_ENABLED", True)
    m4 = _safe(npz, "wt_momentum_state_4h", n, 0.0)
    m1 = _safe(npz, "wt_momentum_state_1h", n, 0.0)
    m15 = _safe(npz, "wt_momentum_state_15m", n, 0.0)
    if is_long:
        mask = (m4 == _MOM_EXHAUST_UP) & ((m1 == _MOM_EXHAUST_UP) | (m15 == _MOM_EXHAUST_UP))
    else:
        mask = (m4 == _MOM_EXHAUST_DOWN) & ((m1 == _MOM_EXHAUST_DOWN) | (m15 == _MOM_EXHAUST_DOWN))
    if not enabled:
        mask = np.zeros(n, dtype=bool)
    # live: fire iff (not REQUIRE_GAIN or gain > 0) AND gain >= MIN_GAIN_PCT.
    req_gain = _fbool(cfg, "WT_EXHAUST_EXIT_REQUIRE_GAIN", False)
    min_gain = _fnum(cfg, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", 0.0)
    if req_gain and min_gain <= 0.0:
        gate = {"gain_op": ">", "gain_thr": 0.0}
    else:
        gate = {"gain_op": ">=", "gain_thr": min_gain}
    return np.asarray(mask, dtype=bool), gate


def _live_or_num(cfg, key, default):
    """Live `float(_cfg(key, default) or default)` semantics: a falsy cfg value (0/''/None)
    falls back to `default`, exactly like the MU block's `or` chains."""
    try:
        v = getattr(cfg, key, default)
        return float(v) if v else float(default)
    except Exception:
        return float(default)


def _mu_correction(npz, n, is_long, cfg, _safe, sym=None):
    """Vector twin of the MU_CORRECTION_PEAK_ROLLOVER exit, tradier_manage.py evaluate_stop.
    Live: LONG-only, symbol in MU_CORRECTION_SYMBOLS, HTF K/RSI overbought + high/close rollover
    on MU_CORRECTION_HTF_TFS, LTF momentum fall on MU_CORRECTION_LTF_FALL_TFS, gain >= MIN_GAIN_PCT.
    The 5m LTF leg is 15m-floored (no 3m/5m NPZ arrays): '5m+15m' evaluates the 15m predicate
    twice, so the count threshold keeps live's two-leg meaning. Returns (mask, gate)."""
    _z = np.zeros(n, dtype=bool)
    if not is_long:
        return _z, {"gain_op": ">=", "gain_thr": 0.0}
    try:
        _syms_raw = str(getattr(cfg, "MU_CORRECTION_SYMBOLS", "MU") or "MU")
    except Exception:
        _syms_raw = "MU"
    _allow = {s.strip().upper() for s in _syms_raw.replace(",", "+").split("+") if s.strip()}
    _bare = str(sym or "").upper()
    for _sfx in ("_LONG", "_SHORT"):
        if _bare.endswith(_sfx):
            _bare = _bare[: -len(_sfx)]
            break
    if not _bare or _bare not in _allow:
        return _z, {"gain_op": ">=", "gain_thr": 0.0}
    _k_min = _live_or_num(cfg, "MU_CORRECTION_HTF_K_MIN", 80.0)
    _rsi_min = _live_or_num(cfg, "MU_CORRECTION_HTF_RSI_MIN", 60.0)
    try:
        _need = max(1, int(getattr(cfg, "MU_CORRECTION_HTF_MIN_TFS", 1) or 1))
    except Exception:
        _need = 1
    _high_req = _fbool(cfg, "MU_CORRECTION_REQUIRE_HIGH_REVERSAL", True)
    _close_req = _fbool(cfg, "MU_CORRECTION_REQUIRE_CLOSE_REVERSAL", True)
    _min_gain = _live_or_num(cfg, "MU_CORRECTION_MIN_GAIN_PCT", 0.0)
    try:
        _htf_raw = str(getattr(cfg, "MU_CORRECTION_HTF_TFS", "1h+4h") or "1h+4h")
    except Exception:
        _htf_raw = "1h+4h"
    _htf_tfs = [t.strip() for t in _htf_raw.replace(",", "+").split("+") if t.strip() in ("1h", "4h")]
    if not _htf_tfs:
        _htf_tfs = ["1h", "4h"]
    try:
        _ltf_need = max(1, int(getattr(cfg, "MU_CORRECTION_LTF_FALL_MIN_TFS", 2) or 2))
    except Exception:
        _ltf_need = 2
    try:
        _ltf_raw = str(getattr(cfg, "MU_CORRECTION_LTF_FALL_TFS", "5m+15m") or "5m+15m")
    except Exception:
        _ltf_raw = "5m+15m"
    _ltf_tfs = [("15m" if t.strip() in ("3m", "5m") else t.strip()) for t in _ltf_raw.replace(",", "+").split("+") if t.strip()]

    def _or50(a):
        a = np.asarray(a, dtype=float)
        return np.where(a != 0, a, 50.0)

    _hits = np.zeros(n, dtype=int)
    _roll = np.zeros(n, dtype=int)
    for _tf in _htf_tfs:
        _k = _or50(_safe(npz, f"k_{_tf}", n, 50.0))
        _rsi = _or50(_safe(npz, f"rsi_{_tf}", n, 50.0))
        _hits = _hits + ((_k >= _k_min) | (_rsi >= _rsi_min)).astype(int)
        _hi = np.asarray(_safe(npz, f"high_{_tf}", n, 0.0), dtype=float)
        _hip = np.asarray(_safe(npz, f"high_{_tf}_prev", n, 0.0), dtype=float)
        _cl = np.asarray(_safe(npz, f"close_{_tf}", n, 0.0), dtype=float)
        _clp = np.asarray(_safe(npz, f"close_{_tf}_prev", n, 0.0), dtype=float)
        _hr = bool(not _high_req) | ((_hi > 0) & (_hip > 0) & (_hi >= _hip))
        _cr = bool(not _close_req) | ((_cl > 0) & (_clp > 0) & (_cl <= _clp))
        _roll = _roll + (np.asarray(_hr, dtype=bool) & np.asarray(_cr, dtype=bool)).astype(int)
    _fall = np.zeros(n, dtype=int)
    for _tf in _ltf_tfs:
        _k = _or50(_safe(npz, f"k_{_tf}", n, 50.0))
        _kp_raw = np.asarray(_safe(npz, f"k_{_tf}_prev", n, 0.0), dtype=float)
        _kp = np.where(_kp_raw != 0, _kp_raw, _k)
        _w1 = np.asarray(_safe(npz, f"wt1_{_tf}", n, 0.0), dtype=float)
        _w2 = np.asarray(_safe(npz, f"wt2_{_tf}", n, 0.0), dtype=float)
        _leg = (_k < _kp) | (((_w1 != 0) | (_w2 != 0)) & (_w1 < _w2))
        _fall = _fall + np.asarray(_leg, dtype=bool).astype(int)
    mask = (_hits >= _need) & (_roll >= _need) & (_fall >= _ltf_need)
    if not _fbool(cfg, "MU_CORRECTION_EXIT_ENABLED", False):
        mask = _z
    return np.asarray(mask, dtype=bool), {"gain_op": ">=", "gain_thr": float(_min_gain)}


def masks(npz, n, is_long, cfg, _safe, close, sym=None):
    """Return {SWITCH_NAME: (mask_ndarray_bool, gate_dict)} for every stateful switch that is ACTIVE
    (cfg value differs from its effective default). Each mask is the gain-INDEPENDENT indicator part;
    each gate is the position-state condition. Empty dict when nothing is active.
    sym = bare symbol (e.g. 'MU'); symbol-gated families (MU_CORRECTION) stay inert when unknown."""
    out = {}

    # === 1. WT_4H_VEL_EXIT family (ez_manage.py:49766-49841) ==================================
    # One real exit; each of its knobs that differs from default gets the SAME recomputed (mask,gate)
    # so the parent can attribute the ledger delta to whichever switch row it is testing.
    try:
        _wt4h_defaults = (
            ("WT_4H_VEL_EXIT_ENABLED", True, "b"),
            ("WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0, "n"),
            ("WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0, "n"),
            ("WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True, "b"),
            ("WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0, "n"),
            ("WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0, "n"),
            ("WT_4H_VEL_EXIT_REQUIRE_PROFIT", True, "b"),
        )
        _wt4h_active = [
            k for (k, d, t) in _wt4h_defaults
            if (_bool_differs(cfg, k, d) if t == "b" else _num_differs(cfg, k, d))
        ]
        if _wt4h_active:
            _m, _g = _wt_4h_vel(npz, n, is_long, cfg, _safe)
            for k in _wt4h_active:
                out[k] = (_m, dict(_g))
    except Exception:
        pass

    # === 2. WT_EXHAUST_EXIT gain gates (ez_manage.py:49901-49942) =============================
    try:
        _ex_active = []
        if _num_differs(cfg, "WT_EXHAUST_EXIT_MIN_GAIN_PCT", 0.0):
            _ex_active.append("WT_EXHAUST_EXIT_MIN_GAIN_PCT")
        if _bool_differs(cfg, "WT_EXHAUST_EXIT_REQUIRE_GAIN", False):
            _ex_active.append("WT_EXHAUST_EXIT_REQUIRE_GAIN")
        if _ex_active:
            _m, _g = _wt_exhaust(npz, n, is_long, cfg, _safe)
            for k in _ex_active:
                out[k] = (_m, dict(_g))
    except Exception:
        pass

    # === 3. WT_DC_EXIT scorer (wt_dc_delta.py:1039-1054) ======================================
    # Live: if WT_DC_EXIT_ENABLED and |wt_score_1h| >= WT_DC_EXIT_THRESHOLD and score AGAINST side and gain>0.2 -> exit.
    # Default ENABLED=True (live on), so emit whenever enabled (applies at baseline = live parity; sweep tests OFF).
    try:
        if bool(getattr(cfg, 'WT_DC_EXIT_ENABLED', True)):
            _wtthr = float(getattr(cfg, 'WT_DC_EXIT_THRESHOLD', 25.0) or 25.0)
            _wsc = _safe(npz, 'wt_score_1h', n, 0.0)
            if not bool((_wsc != 0).any()):
                _wsc = _safe(npz, 'wt_score_15m', n, 0.0)
            _wdc_m = (_wsc <= -_wtthr) if is_long else (_wsc >= _wtthr)
            _wdc_g = {'gain_op': '>', 'gain_thr': 0.2}
            out['WT_DC_EXIT_ENABLED'] = (np.asarray(_wdc_m, dtype=bool), dict(_wdc_g))
            if _num_differs(cfg, 'WT_DC_EXIT_THRESHOLD', 25.0):
                out['WT_DC_EXIT_THRESHOLD'] = (np.asarray(_wdc_m, dtype=bool), dict(_wdc_g))
    except Exception:
        pass

    # === 4. WRONG_SIDE_ABS_KILL (ez_manage.py 50356-50430; 2026-10-01 WIRING b1) =============================
    # Live (crypto only; tradier_manage has stub reads only): when WRONG_SIDE_ABS_KILL_ENABLED and the position is older than
    # WRONG_SIDE_MIN_AGE_MIN (>=, minutes) and it is not a hedge: count the WT TFs (3m/15m/1h/4h/D) against the side (a TF with wt1==0 and
    # wt2==0 is skipped) and the stoch TFs (3m/15m/1h) with K against D; fire QUICK_CLOSE when wt_against >= WRONG_SIDE_WT_TFS_REQUIRED and
    # k_against >= WRONG_SIDE_K_TFS_REQUIRED (no gain gate: 'bypasses STRICT_NO_LOSS by design'). 3m has no NPZ array -> 15m floor.
    # Live default ENABLED=False (config.py) -> baseline unchanged; flipping it (or the knobs while enabled) moves the ledger.
    try:
        if str(getattr(cfg, 'MODE', 'crypto')) != 'tradier' and _fbool(cfg, 'WRONG_SIDE_ABS_KILL_ENABLED', False):
            _ws_wt_req = int(_fnum(cfg, 'WRONG_SIDE_WT_TFS_REQUIRED', 5))
            _ws_k_req = int(_fnum(cfg, 'WRONG_SIDE_K_TFS_REQUIRED', 3))
            _ws_cnt = np.zeros(n, dtype=int)
            for _tf in ('15m', '1h', '4h', 'D'):   # 3m ignored (no 3m in vec)
                _src = _tf
                _a = _safe(npz, f'wt1_{_src}', n, 0.0); _b = _safe(npz, f'wt2_{_src}', n, 0.0)
                _skip = (_a == 0) & (_b == 0)
                _ws_cnt = _ws_cnt + ((~_skip) & ((_a < _b) if is_long else (_a > _b))).astype(int)
            _ws_k = np.zeros(n, dtype=int)
            for _tf in ('15m', '1h'):   # 3m ignored
                _src = _tf
                _k = _safe(npz, f'k_{_src}', n, 50.0); _d = _safe(npz, f'd_{_src}', n, 50.0)
                _ws_k = _ws_k + ((_k < _d) if is_long else (_k > _d)).astype(int)
            _ws_mask = (_ws_cnt >= _ws_wt_req) & (_ws_k >= _ws_k_req)
            _bt = str(getattr(cfg, 'BASE_TF', '15m') or '15m').lower()
            _bmin = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60}.get(_bt, 15)
            _ws_gate = {'age_min_bars': int(np.ceil(_fnum(cfg, 'WRONG_SIDE_MIN_AGE_MIN', 30.0) / float(_bmin)))}
            for _k_name in ('WRONG_SIDE_ABS_KILL_ENABLED', 'WRONG_SIDE_WT_TFS_REQUIRED', 'WRONG_SIDE_K_TFS_REQUIRED', 'WRONG_SIDE_MIN_AGE_MIN'):
                out[_k_name] = (np.asarray(_ws_mask, dtype=bool), dict(_ws_gate))
    except Exception:
        pass

    # === 5. MU_CORRECTION_EXIT (tradier_manage.py evaluate_stop MU_CORRECTION_PEAK_ROLLOVER) =====
    # Live (stocks, LONG-only, default OFF): completed 1h/4h high+close rollover in HTF overbought
    # (K>=80 or RSI>=60) confirmed by LTF momentum fall, symbol in MU_CORRECTION_SYMBOLS, full-qty
    # exit when gain >= MU_CORRECTION_MIN_GAIN_PCT. 5m LTF leg 15m-floored (see _mu_correction).
    try:
        if _bool_differs(cfg, "MU_CORRECTION_EXIT_ENABLED", False):
            _m, _g = _mu_correction(npz, n, is_long, cfg, _safe, sym)
            out["MU_CORRECTION_EXIT_ENABLED"] = (np.asarray(_m, dtype=bool), dict(_g))
    except Exception:
        pass

    return out


# Switches that cannot be honestly ported into an (indicator-mask, gain/age/peak-gate) pair.
# Reasons are code-grounded (grep + read of the real ez_manage decision site). NO proxies (NO-LIES §19).
VEC_UNSUPPORTED = {
    "DC_HOPELESS_EXIT_ENABLED":
        "Real site ez_manage.py:49849-49894 fires on entry_price outside the current dc_4h channel "
        "(LONG: entry_price > dc_high_4h; SHORT: entry_price < dc_low_4h). entry_price is position "
        "state NOT among {gain, age, peak}; the condition couples a fixed entry price with a per-bar "
        "indicator and does not decompose into an indicator mask AND an independent gain/age gate.",
    "DC_HOPELESS_EXIT_MIN_AGE_S":
        "Age knob of DC_HOPELESS_EXIT (age > _min_age, default 900s). Its exit is unsupported (entry_price "
        "coupling above), so the age gate has no exit to attach to. (ez_manage.py:49873.)",
    "WT_CROSS_EXIT_APPLIES_TO_WINNERS":
        "Only no-op stub-farm sites (ez_manage.py:59169-59172 '_=_wt'). The real WT-cross close is "
        "HTF_AGAINST_FORCE_CLOSE (ez_manage.py:46770-46818), which reads WT_CROSS_EXIT_REQUIRE_15M_CONFIRM "
        "(already wired in ported_exit.py #4) and never reads APPLIES_TO_WINNERS. Wiring it would fabricate a "
        "decision live does not make.",
    "WT_CROSS_EXIT_MIN_AGE_MINUTES":
        "Only no-op stub-farm sites (ez_manage.py:59177-59180 '_=_wt'); the real HTF_AGAINST_FORCE_CLOSE "
        "never reads this knob. No real gain/age-gated decision to port.",
    "BREAKEVEN_GAIN_EROSION_ENABLED":
        "Only a fabricated filter-farm (ez_manage.py:6711-6715, plus 6723/6729 comparing close price <= a "
        "gain threshold — meaningless) and a reason-string bypass (33149-33154). No real erosion exit exists "
        "in ez_manage; porting would encode the farm's phantom logic.",
    "BREAKEVEN_GAIN_EROSION_MIN_GAIN":
        "Only the fabricated filter-farm (ez_manage.py:6723-6727, 'if close <= thr'); no real decision site.",
    "BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT":
        "Only the fabricated filter-farm (ez_manage.py:6729-6733, 'if close <= thr'); no real decision site.",
    "HARD_BREAKEVEN_FLOOR_ENABLED":
        "Only a no-op stub farm (ez_manage.py:58652-58655) that reads position max_gain into _be and "
        "discards it ('_ = _be'). No real breakeven-floor exit exists in ez_manage; nothing to port.",
    "HARD_BREAKEVEN_MIN_PEAK_PCT":
        "Only a no-op stub farm (ez_manage.py:59508-59511) reading max_gain into _be and discarding it. "
        "No real decision site.",
    "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED":
        "Only no-op stub-farm sites (ez_manage.py:58958-58959 '_=getattr(...)', 59319-59320). No real "
        "peak-giveback exit in ez_manage.process_position to mirror.",
    "TREND_MIN_GAIN_EXIT":
        "Only a no-op stub farm (ez_manage.py:59108-59109 '_=getattr(...)'). The real in-gain trend "
        "harvest is a DIFFERENT switch (IN_GAIN_TREND_EXIT / IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED, "
        "ez_manage.py:51690); TREND_MIN_GAIN_EXIT itself has no real decision site.",
    "MU_CORRECTION_EXIT_ENABLED":
        "WIRED 2026-10-04 (lane A): the real site is tradier_manage.py evaluate_stop "
        "(MU_CORRECTION_PEAK_ROLLOVER block), not ez_manage — the earlier ez-only verdict was wrong. "
        "See _mu_correction() + masks() section 5.",
    "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED":
        "Real site ez_manage.py:52047-52106, but its firing is gated by position SIZE relative to "
        "START_POSITION_SIZE (positionAmt > 5*START_POSITION_SIZE/price, and outer > 0.5*START/price) and by "
        "a prior long_stop/short_stop large-loss flag — state beyond {gain, age, peak}. Not expressible with "
        "the gate keys; the k/d-cross elif is only one branch behind that size/stop gate.",
    "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED":
        "Depends on a stale-price-feed condition (last price unchanged) not present in the frozen NPZ; "
        "only stub-farm site in ez_manage (58713). No indicator basis.",
    "LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED":
        "Depends on hedge-account state + hedge-tracker timing (_hmb_unhedged wait minutes, active_hedges) "
        "(ez_manage.py:52129-52136) — not NPZ data and not among {gain, age, peak}.",
    "BOTTOM_EXIT_HTF_WT_VETO_ENABLED":
        "A VETO, not a fire: at ez_manage.py:46605/46702/46943 it SUPPRESSES the ULTIMATE_DC_4h hard stop "
        "and NEWBORN_LOSS_KILL when HTF WT (1h/15m/4h) is still with the position. The (mask, gate) contract "
        "only fires exits (exit_sig |= mask & gate); there is no veto primitive in the allowed gate keys, and "
        "the stops it vetoes are not among the switches wired here.",
}
