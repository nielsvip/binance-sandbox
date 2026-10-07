"""grey_wire_exits — ONE predicate set for exits wired 2026-09-30 (grey-switch wiring job), shared by
LIVE stocks (tradier_manage.process_position GREY_WIRE hook) and the vector engine
(v12_quick_engine.simulate_one). Crypto live keeps its own original implementation in
ez_manage.process_position (the source of truth each predicate below mirrors line-for-line);
these predicates are the vec twins for crypto and the live+vec implementation for stocks.

Supersedes the stale, never-called twins process_position_crypto__{dc_hopeless_exit,wt_4h_vel_exit,
wt_percentile_exit,e1_wt_delta_exit,e3_structure_exit} for the engine/stock-live call sites (the
percentile twin lacked the live wt1_15m<wt2_15m confirm; the structure twin could not read the NPZ
int8 encoding).

Predicate contract:  fn(c, g, st, is_long) -> (fire: bool, reason: str)
  c(key, default)  config value (live: tradier _cfg per-sym chain / vec: getattr(cfg))
  g(key, default)  CURRENT-bar indicator value (live: snapshot dict / vec: npz[key][i]); NPZ int8
                   string encodings are decoded with INT_DECODE (backtest_v8_harness._INT_DECODE)
  st               {'gain': %, 'age_s': seconds since open, 'entry_px': avg entry, 'px': price, 'max_gain': peak %,
                    'reentered': bool, 'augmented': bool}
Every enable default is OFF (zero behaviour change at the default in both engines).
"""
from typing import Callable, List, Tuple

# backtest_v8_harness._INT_DECODE — the scalar verifier's NPZ int8 -> live string mapping
INT_DECODE = {
    "wt_momentum_state": {-2: "EXHAUST_DOWN", -1: "IMPULSE_DOWN", 0: "NEUTRAL", 1: "IMPULSE_UP", 2: "EXHAUST_UP"},
    "wt_peak_structure": {-1: "LH", 0: "NEUTRAL", 1: "HH"},
    "wt_trough_structure": {-1: "LL", 0: "NEUTRAL", 1: "HL"},
    "wt_structure": {-1: "LH", 0: "NEUTRAL", 1: "HH"},
}


def _f(v, d=0.0) -> float:
    try:
        if v is None:
            return float(d)
        return float(v)
    except (TypeError, ValueError):
        return float(d)


def str_field(g, family: str, tf: str) -> str:
    """live string field or NPZ int8 code -> upper-case live string."""
    v = g(f"{family}_{tf}", "")
    if isinstance(v, str):
        return v.upper()
    try:
        return INT_DECODE.get(family, {}).get(int(v), "").upper()
    except (TypeError, ValueError):
        return ""


def dc_hopeless(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~49842-49890 DC_HOPELESS_EXIT: entry price outside the dc_4h channel (LONG entry >
    dc_high_4h / SHORT entry < dc_low_4h) and age > DC_HOPELESS_EXIT_MIN_AGE_S."""
    dh, dl, ep = _f(g("dc_high_4h", 0)), _f(g("dc_low_4h", 0)), _f(st.get("entry_px"))
    if not (dh > 0 and dl > 0 and ep > 0):
        return False, ""
    hopeless = (ep > dh) if is_long else (ep < dl)
    if hopeless and _f(st.get("age_s")) > _f(c("DC_HOPELESS_EXIT_MIN_AGE_S", 900), 900):
        return True, f"DC_HOPELESS_entry={ep:.4f}_dc=[{dl:.4f},{dh:.4f}]"
    return False, ""


def wt_4h_vel(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~49766-49836 WT_4H_VEL_EXIT: 4h WT velocity against + age>360s + profit >=
    COMMISSION_BUFFER_PCT (REQUIRE_PROFIT) + stoch K extreme against (REQUIRE_K_EXTREME, k_3m or k_15m).
    COMMISSION_BUFFER_PCT fallback 0.08 = config.py/QuickConfig value; TradierConfig deliberately has no field
    (the scalar verifier reads it with its own 0.10 fallback for other paths)."""
    v = _f(g("wt_velocity_4h", 0))
    against = v < _f(c("WT_4H_VEL_EXIT_LONG_VEL_MIN", -2.0), -2.0) if is_long else v > _f(c("WT_4H_VEL_EXIT_SHORT_VEL_MIN", 2.0), 2.0)
    if not against or _f(st.get("age_s")) <= 360:
        return False, ""
    gain = _f(st.get("gain"))
    if bool(c("WT_4H_VEL_EXIT_REQUIRE_PROFIT", True)) and gain < _f(c("COMMISSION_BUFFER_PCT", 0.08), 0.08):
        return False, ""
    k3, k15 = _f(g("k_3m", 50.0), 50.0), _f(g("k_15m", 50.0), 50.0)
    if bool(c("WT_4H_VEL_EXIT_REQUIRE_K_EXTREME", True)):
        hi, lo = _f(c("WT_4H_VEL_EXIT_K_EXTREME_HIGH", 80.0), 80.0), _f(c("WT_4H_VEL_EXIT_K_EXTREME_LOW", 20.0), 20.0)
        if not ((k3 >= hi or k15 >= hi) if is_long else (k3 <= lo or k15 <= lo)):
            return False, ""
    return True, f"WT_4H_VEL_EXIT_vel={v:.1f}_g={gain:.2f}%_k3m={k3:.0f}_k15m={k15:.0f}"


def wt_percentile(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~49944-49985 WT_PERCENTILE_EXIT: D + 4h WT percentile extreme against AND 15m WT
    crossed against (wt1_15m<wt2_15m LONG / > SHORT)."""
    pd, p4 = _f(g("wt_percentile_D", 50), 50), _f(g("wt_percentile_4h", 50), 50)
    w1, w2 = _f(g("wt1_15m", 50), 50), _f(g("wt2_15m", 50), 50)
    if is_long:
        fire = pd > _f(c("WT_PERCENTILE_EXIT_OB_D", 90), 90) and p4 > _f(c("WT_PERCENTILE_EXIT_OB_4H", 75), 75) and w1 < w2
    else:
        fire = pd < _f(c("WT_PERCENTILE_EXIT_OS_D", 10), 10) and p4 < _f(c("WT_PERCENTILE_EXIT_OS_4H", 25), 25) and w1 > w2
    return (True, f"WT_PERCENTILE_pctD={pd:.0f}_4h={p4:.0f}") if fire else (False, "")


def e1_wt_delta(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~49987-50018 E_1_WT_DELTA_EXIT: wt_composite_delta beyond -/+E_1_EXIT_DELTA_THR."""
    raw = g("wt_composite_delta", None)
    if raw is None:
        return False, ""
    d, thr = _f(raw), _f(c("E_1_EXIT_DELTA_THR", 50.0), 50.0)
    fire = d < -thr if is_long else d > thr
    return (True, f"E_1_WT_DELTA_EXIT_delta={d:+.0f}_thr={thr:.0f}") if fire else (False, "")


def e3_structure(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~50019-50055 E_3_STRUCTURE_EXIT (mode 2 = live exit; mode 1 = shadow/log-only never
    trades): wt_structure against (LONG LH/LL, SHORT HH/HL) on >= 2 of 15m/1h/4h."""
    bad = ("LH", "LL") if is_long else ("HH", "HL")
    tfs = [tf for tf in ("15m", "1h", "4h") if str_field(g, "wt_structure", tf) in bad]
    return (True, f"E_3_STRUCTURE_EXIT_{len(tfs)}TF_{'_'.join(tfs)}") if len(tfs) >= 2 else (False, "")


def _against(w1: float, w2: float, is_long: bool) -> bool:
    return (w1 < w2) if is_long else (w1 > w2)


def htf_against_force_close(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_manage.py ~46764-46816 HTF_AGAINST_FORCE_CLOSE: wt1_1h against the side (gain-agnostic), confirmed
    by 15m (WT_CROSS_EXIT_REQUIRE_15M_CONFIRM), 4h (HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H), 3m/D (CONFIRM_3M/_D;
    skipped when the TF has no data). Min-hold = MIN_HOLD_BARS_BEFORE_EXIT x 3m bars unless price breaches
    dc_low_15m (LONG) / dc_high_15m (SHORT) (_dc_15m_hold_override)."""
    age_min = _f(st.get("age_s")) / 60.0
    if age_min < _f(c("MIN_HOLD_BARS_BEFORE_EXIT", 10), 10) * 3.0:
        px, dl, dh = _f(st.get("px")), _f(g("dc_low_15m", 0)), _f(g("dc_high_15m", 0))
        if not ((is_long and dl > 0 and px <= dl) or ((not is_long) and dh > 0 and px >= dh)):
            return False, ""
    w1, w2 = _f(g("wt1_1h", 0)), _f(g("wt2_1h", 0))
    if not ((abs(w1) > 1e-9 or abs(w2) > 1e-9) and _against(w1, w2, is_long)):
        return False, ""
    if bool(c("WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", True)) and not _against(_f(g("wt1_15m", 0)), _f(g("wt2_15m", 0)), is_long):
        return False, ""
    if bool(c("HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", False)) and not _against(_f(g("wt1_4h", 0)), _f(g("wt2_4h", 0)), is_long):
        return False, ""
    for tf, key in (("3m", "HTF_AGAINST_FORCE_CLOSE_CONFIRM_3M"), ("D", "HTF_AGAINST_FORCE_CLOSE_CONFIRM_D")):
        if bool(c(key, True)):
            a, b = _f(g(f"wt1_{tf}", 0)), _f(g(f"wt2_{tf}", 0))
            if (abs(a) > 1e-9 or abs(b) > 1e-9) and not _against(a, b, is_long):
                return False, ""
    return True, f"HTF_AGAINST_FORCE_CLOSE_wt1h{w1:.1f}vs{w2:.1f}_g{_f(st.get('gain')):.2f}pct"


def _with(w1: float, w2: float, is_long: bool) -> bool:
    return (w1 > w2) if is_long else (w1 < w2)


def breakeven_gain_erosion(c, g, st, is_long) -> Tuple[bool, str]:
    """ez_positions_quick.py ~14093-14570 BREAKEVEN_GAIN_EROSION_STOP via the shared pure core
    vec_decisions.quick_breakeven_gain_erosion._quick_breakeven_gain_erosion_fires, with the live runtime vetoes
    computed here from the snapshot: TREND_REGIME_VETO (wt 1h+4h+D all with the side), grace period (age < 3m, or
    REENTRY_GRACE_MINUTES for reentered positions, unless augmented), HTF_EXIT_VETO (|gain| <= MAX_LOSS and >=
    MIN_ALIGNED of 1h/4h/D with the side; overridden by HARD_BREAKEVEN_FLOOR once max_gain >= MIN_PEAK)."""
    import vec_decisions.quick_breakeven_gain_erosion as _be
    w = {tf: (_f(g(f"wt1_{tf}", 0.0)), _f(g(f"wt2_{tf}", 0.0))) for tf in ("1h", "4h", "D")}
    trend_veto = bool(c("TREND_REGIME_VETO_ENABLED", True)) and all(_with(a, b, is_long) for a, b in w.values())
    age_min, gain = _f(st.get("age_s")) / 60.0, _f(st.get("gain"))
    grace = _f(c("REENTRY_GRACE_MINUTES", 30.0), 30.0) if st.get("reentered") else 3.0
    if age_min < grace and not st.get("augmented"):
        return False, ""
    htf_veto = False
    if bool(c("HTF_EXIT_VETO_ENABLED", True)) and abs(gain) <= _f(c("HTF_EXIT_VETO_MAX_LOSS_PCT", 2.0), 2.0):
        htf_veto = sum(int(_with(a, b, is_long)) for a, b in w.values()) >= int(_f(c("HTF_EXIT_VETO_MIN_ALIGNED", 2), 2))
    fire = _be._quick_breakeven_gain_erosion_fires(
        gain, age_min, _f(st.get("max_gain")), htf_veto, trend_veto,
        _f(c("BREAKEVEN_GRACE_MINUTES", 5.0), 5.0), bool(c("BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT", True)),
        _f(c("BREAKEVEN_GAIN_EROSION_MIN_GAIN", 50.0), 50.0), bool(c("HARD_BREAKEVEN_FLOOR_ENABLED", True)),
        _f(c("HARD_BREAKEVEN_MIN_PEAK_PCT", 0.5), 0.5))
    return (True, f"BREAKEVEN_GAIN_EROSION_STOP_age{age_min:.0f}m_gain{gain:.2f}%") if fire else (False, "")


# (enable test, predicate) in the crypto live evaluation order (HTF_AGAINST runs first in ez_manage).
EXITS = (
    ("HTF_AGAINST_FORCE_CLOSE_ENABLED", lambda c: bool(c("HTF_AGAINST_FORCE_CLOSE_ENABLED", False)), htf_against_force_close),
    ("WT_4H_VEL_EXIT_ENABLED", lambda c: bool(c("WT_4H_VEL_EXIT_ENABLED", False)), wt_4h_vel),
    ("DC_HOPELESS_EXIT_ENABLED", lambda c: bool(c("DC_HOPELESS_EXIT_ENABLED", False)), dc_hopeless),
    ("WT_PERCENTILE_EXIT_ENABLED", lambda c: bool(c("WT_PERCENTILE_EXIT_ENABLED", False)), wt_percentile),
    ("E_1_WT_EXIT_USE_DELTA_ENABLED", lambda c: bool(c("E_1_WT_EXIT_USE_DELTA_ENABLED", False)), e1_wt_delta),
    ("E_3_USE_WT_STRUCTURE_EXIT_MODE", lambda c: int(_f(c("E_3_USE_WT_STRUCTURE_EXIT_MODE", 0))) == 2, e3_structure),
    ("BREAKEVEN_GAIN_EROSION_ENABLED", lambda c: bool(c("BREAKEVEN_GAIN_EROSION_ENABLED", False)), breakeven_gain_erosion),
)


class CfgView:
    """getattr-style view over a c(key, default) getter (live per-sym _cfg) for predicates written against a config
    object; a key the getter does not know raises AttributeError so getattr(view, k, default) returns default."""
    _MISS = object()

    def __init__(self, c):
        self._c = c

    def __getattr__(self, k):
        if k.startswith("_"):
            raise AttributeError(k)
        v = self._c(k, CfgView._MISS)
        if v is CfgView._MISS:
            raise AttributeError(k)
        return v


class IndView:
    """dict-style .get view over a g(key, default) getter."""

    def __init__(self, g):
        self._g = g

    def get(self, k, d=None):
        return self._g(k, d)


def hlr_top_exit(c, g, st, is_long) -> Tuple[bool, str]:
    """HLR_TOP_EXIT ("sell the top", ez_positions_quick.py ~3677-3720) via the SAME core the vector engine uses
    (vec_decisions.quick_reduce_strong: check_quick_reduce_strong == check_quick_reduce_strong_vec, shared
    _quick_reduce_strong_fires). LIVE-ONLY entry: simulate_one already applies it through qr_cond (full reduce)."""
    import vec_decisions.quick_reduce_strong as _qrs
    fire, reason = _qrs.check_quick_reduce_strong(CfgView(c), IndView(g), _f(st.get("gain")), is_long)
    return (True, reason) if fire else (False, "")


# live-only list: decisions whose vec twin already runs inside simulate_one through its own (same-core) path
LIVE_ONLY_EXITS = (
    ("HLR_TOP_EXIT_ENABLED", lambda c: bool(c("HLR_TOP_EXIT_ENABLED", False)), hlr_top_exit),
)


def active_live_only_exits(c: Callable) -> List[Callable]:
    out = []
    for _name, on, fn in LIVE_ONLY_EXITS:
        try:
            if on(c):
                out.append(fn)
        except Exception:
            continue
    return out


def active_exits(c: Callable) -> List[Callable]:
    """predicates whose switch is ON for this config (empty at the defaults -> zero cost, zero change)."""
    out = []
    for _name, on, fn in EXITS:
        try:
            if on(c):
                out.append(fn)
        except Exception:
            continue
    return out


def first_fire(fns, c, g, st, is_long) -> Tuple[bool, str]:
    for fn in fns:
        fire, reason = fn(c, g, st, is_long)
        if fire:
            return True, reason
    return False, ""


class NpzBar:
    """vec getter over NPZ arrays for one bar index (missing key -> default, like live dict.get)."""

    def __init__(self, npz, n: int):
        self._npz, self._n, self._cache, self.i = npz, n, {}, 0

    def __call__(self, key: str, default=None):
        a = self._cache.get(key)
        if a is None:
            if key not in self._npz:
                return default
            a = self._npz[key]
            self._cache[key] = a
        return a[self.i] if self.i < len(a) else default
