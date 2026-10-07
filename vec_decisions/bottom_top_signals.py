"""Bottom/top reversal signal masks over NPZ arrays (pure predicates, no engine edits).

Each function returns a boolean mask (True = signal fires). Live mirrors read the
same-named live snapshot fields; see data/reports/indicators/WIRING_SPEC.md.
Conventions: finiteness required (NaN abstains), thresholds match live emitters.
"""
import numpy as np


def _a(npz, key, n, default=np.nan):
    try:
        v = np.asarray(npz[key], dtype=np.float64)
        if v.shape[0] != n:
            return np.full(n, default)
        return v
    except Exception:
        return np.full(n, default)


def div_bull(npz, n, tf, hidden=True):
    m = np.zeros(n, dtype=bool)
    for k in ([f"div_reg_bull_wt_{tf}", f"div_reg_bull_mfi_{tf}"] + ([f"div_hid_bull_wt_{tf}", f"div_hid_bull_mfi_{tf}"] if hidden else [])):
        m |= _a(npz, k, n) > 0
    return m


def div_bear(npz, n, tf, hidden=True):
    m = np.zeros(n, dtype=bool)
    for k in ([f"div_reg_bear_wt_{tf}", f"div_reg_bear_mfi_{tf}"] + ([f"div_hid_bear_wt_{tf}", f"div_hid_bear_mfi_{tf}"] if hidden else [])):
        m |= _a(npz, k, n) > 0
    return m


def _stoch_k(npz, n, tf):
    v = _a(npz, f"stoch_k_{tf}", n)
    return np.where(np.isfinite(v), v, _a(npz, f"stoch_k_{tf}_prev", n))


def _stoch_d(npz, n, tf):
    v = _a(npz, f"stoch_d_{tf}", n)
    return np.where(np.isfinite(v), v, _a(npz, f"stoch_d_{tf}_prev", n))


def stoch_oversold(npz, n, tf, level=20.0):
    k = _stoch_k(npz, n, tf)
    return np.isfinite(k) & (k < level)


def stoch_overbought(npz, n, tf, level=80.0):
    k = _stoch_k(npz, n, tf)
    return np.isfinite(k) & (k > level)


def stoch_cross_up(npz, n, tf):
    return _a(npz, f"stoch_crossover_{tf}", n) > 0


def stoch_cross_down(npz, n, tf):
    return _a(npz, f"stoch_crossunder_{tf}", n) > 0


def _rsi2_v(npz, n, tf):
    v = _a(npz, f"rsi_2_{tf}", n)
    return np.where(np.isfinite(v), v, _a(npz, f"rsi2_{tf}", n))


def rsi2_oversold(npz, n, tf, level=10.0):
    v = _rsi2_v(npz, n, tf)
    return np.isfinite(v) & (v < level)


def rsi2_overbought(npz, n, tf, level=90.0):
    v = _rsi2_v(npz, n, tf)
    return np.isfinite(v) & (v > level)


def funding_crowded_long(npz, n, z=2.0):
    v = _a(npz, "funding_zscore", n)
    return np.isfinite(v) & (v > z)


def funding_crowded_short(npz, n, z=2.0):
    v = _a(npz, "funding_zscore", n)
    return np.isfinite(v) & (v < -z)


def _oi_v(npz, n):
    v = _a(npz, "oi_vel_4h", n)
    return np.where(np.isfinite(v), v, 0.0)


def oi_surge_up(npz, n, pct=3.0):
    return _oi_v(npz, n) > pct


def oi_surge_down(npz, n, pct=3.0):
    return _oi_v(npz, n) < -pct


def _vwap_v(npz, n):
    v = _a(npz, "vwap_distance_pct", n)
    return np.where(np.isfinite(v), v, 0.0)


def vwap_stretched_below(npz, n, pct=1.0):
    return _vwap_v(npz, n) < -abs(pct)


def vwap_stretched_above(npz, n, pct=1.0):
    return _vwap_v(npz, n) > abs(pct)


def _bb_v(npz, n, tf):
    v = _a(npz, f"bb_pct_b_{tf}", n)
    return np.where(np.isfinite(v), v, _a(npz, f"bb_pct_{tf}", n))


def bb_pct_b_low(npz, n, tf, level=0.0):
    v = _bb_v(npz, n, tf)
    return np.isfinite(v) & (v < level)


def bb_pct_b_high(npz, n, tf, level=1.0):
    v = _bb_v(npz, n, tf)
    return np.isfinite(v) & (v > level)


def _kc_pos_vec(npz, n, tf):
    v = _a(npz, f"kc_position_{tf}", n)
    up = _a(npz, f"kc_upper_{tf}", n)
    dn = _a(npz, f"kc_lower_{tf}", n)
    cl = _a(npz, f"close_{tf}", n)
    with np.errstate(divide="ignore", invalid="ignore"):
        tri = np.where((up > dn) & np.isfinite(up) & np.isfinite(dn) & np.isfinite(cl), (cl - dn) / np.maximum(up - dn, 1e-10), np.nan)
    return np.where(np.isfinite(v), v, tri)


def kc_outside_low(npz, n, tf):
    v = _kc_pos_vec(npz, n, tf)
    return np.isfinite(v) & (v < 0.0)


def kc_outside_high(npz, n, tf):
    v = _kc_pos_vec(npz, n, tf)
    return np.isfinite(v) & (v > 1.0)


def hammer_wick(npz, n, tf, lower_min=0.5, body_max=0.35):
    lw = _a(npz, f"bar_lower_wick_{tf}", n)
    bo = _a(npz, f"bar_body_ratio_{tf}", n)
    return np.isfinite(lw) & np.isfinite(bo) & (lw > lower_min) & (bo < body_max)


def shooting_star_wick(npz, n, tf, upper_min=0.5, body_max=0.35):
    uw = _a(npz, f"bar_upper_wick_{tf}", n)
    bo = _a(npz, f"bar_body_ratio_{tf}", n)
    return np.isfinite(uw) & np.isfinite(bo) & (uw > upper_min) & (bo < body_max)


def smfi_bull(npz, n, tf):
    return _a(npz, f"smfi_bull_div_{tf}", n) > 0


def smfi_bear(npz, n, tf):
    return _a(npz, f"smfi_bear_div_{tf}", n) > 0


def formation_bull(npz, n, tf, min_score=0.6):
    m = np.zeros(n, dtype=bool)
    for fam in ("double_top_bottom", "head_shoulders", "wedge", "triangle", "flag_pennant", "cup_handle", "trend_structure"):
        sig = _a(npz, f"formation_{fam}_bull_{tf}", n) > 0
        sc = _a(npz, f"formation_{fam}_bull_score_{tf}", n)
        m |= sig & (np.where(np.isfinite(sc), sc, 100.0) >= min_score)
    return m


def formation_bear(npz, n, tf, min_score=0.6):
    m = np.zeros(n, dtype=bool)
    for fam in ("double_top_bottom", "head_shoulders", "wedge", "triangle", "flag_pennant", "cup_handle", "trend_structure"):
        sig = _a(npz, f"formation_{fam}_bear_{tf}", n) > 0
        sc = _a(npz, f"formation_{fam}_bear_score_{tf}", n)
        m |= sig & (np.where(np.isfinite(sc), sc, 100.0) >= min_score)
    return m


def orb_break_up(npz, n):
    v = _a(npz, "orb_position", n)
    return np.isfinite(v) & (v > 1.0)


def orb_break_down(npz, n):
    v = _a(npz, "orb_position", n)
    return np.isfinite(v) & (v < 0.0)


BOTTOM_SIGNALS = ("div_bull", "stoch_oversold", "stoch_cross_up", "rsi2_oversold", "funding_crowded_short", "oi_surge_down", "vwap_stretched_below", "bb_pct_b_low", "kc_outside_low", "hammer_wick", "smfi_bull", "formation_bull")
TOP_SIGNALS = ("div_bear", "stoch_overbought", "stoch_cross_down", "rsi2_overbought", "funding_crowded_long", "oi_surge_up", "vwap_stretched_above", "bb_pct_b_high", "kc_outside_high", "shooting_star_wick", "smfi_bear", "formation_bear")


def _cfg_get(cfg, key, default):
    try:
        if callable(cfg):
            v = cfg(key, default)
        else:
            v = cfg.get(key, default) if isinstance(cfg, dict) else getattr(cfg, key, default)
        return default if v is None else v
    except Exception:
        return default


RESEARCH_ONLY_FAMS = frozenset()


def _bull_mask(npz, n, fam, p, research=False):
    if fam in RESEARCH_ONLY_FAMS and not research:
        return np.zeros(n, dtype=bool)
    if fam == "DIV":
        return div_bull(npz, n, p["tf"], p["hidden"])
    if fam == "STOCH_XTREME":
        return stoch_oversold(npz, n, p["tf"])
    if fam == "RSI2_XTREME":
        return rsi2_oversold(npz, n, p["tf"])
    if fam == "BBKC":
        m = bb_pct_b_low(npz, n, p["tf"])
        return m | kc_outside_low(npz, n, p["tf"])
    if fam == "VWAP_STRETCH":
        return vwap_stretched_below(npz, n, p["pct"])
    if fam == "FUNDING_CROWD":
        return funding_crowded_short(npz, n, p["z"])
    if fam == "OI_SURGE":
        return oi_surge_down(npz, n, p["pct"])
    if fam == "SMFI_DIV":
        return smfi_bull(npz, n, p["tf"])
    if fam == "WICK_REJECT":
        return hammer_wick(npz, n, p["tf"])
    if fam == "FORMATION":
        return formation_bull(npz, n, p["tf"], p["score"])
    if fam == "ORB_BREAK":
        return orb_break_up(npz, n)
    return np.zeros(n, dtype=bool)


def _bear_mask(npz, n, fam, p, research=False):
    if fam in RESEARCH_ONLY_FAMS and not research:
        return np.zeros(n, dtype=bool)
    if fam == "DIV":
        return div_bear(npz, n, p["tf"], p["hidden"])
    if fam == "STOCH_XTREME":
        return stoch_overbought(npz, n, p["tf"])
    if fam == "RSI2_XTREME":
        return rsi2_overbought(npz, n, p["tf"])
    if fam == "BBKC":
        m = bb_pct_b_high(npz, n, p["tf"])
        return m | kc_outside_high(npz, n, p["tf"])
    if fam == "VWAP_STRETCH":
        return vwap_stretched_above(npz, n, p["pct"])
    if fam == "FUNDING_CROWD":
        return funding_crowded_long(npz, n, p["z"])
    if fam == "OI_SURGE":
        return oi_surge_up(npz, n, p["pct"])
    if fam == "SMFI_DIV":
        return smfi_bear(npz, n, p["tf"])
    if fam == "WICK_REJECT":
        return shooting_star_wick(npz, n, p["tf"])
    if fam == "FORMATION":
        return formation_bear(npz, n, p["tf"], p["score"])
    if fam == "ORB_BREAK":
        return orb_break_down(npz, n)
    return np.zeros(n, dtype=bool)


def _div_finite(npz, n, tf, hidden, want_bull):
    side = "bull" if want_bull else "bear"
    keys = [f"div_reg_{side}_wt_{tf}", f"div_reg_{side}_mfi_{tf}"] + ([f"div_hid_{side}_wt_{tf}", f"div_hid_{side}_mfi_{tf}"] if hidden else [])
    m = np.zeros(n, dtype=bool)
    for k in keys:
        m |= np.isfinite(_a(npz, k, n))
    return m


def _bbkc_finite(npz, n, tf):
    return np.isfinite(_bb_v(npz, n, tf)) | np.isfinite(_kc_pos_vec(npz, n, tf))


def _wick_finite(npz, n, tf, want_bull):
    w = _a(npz, f"bar_lower_wick_{tf}", n) if want_bull else _a(npz, f"bar_upper_wick_{tf}", n)
    return np.isfinite(w) & np.isfinite(_a(npz, f"bar_body_ratio_{tf}", n))


def _formation_finite(npz, n, tf):
    m = np.zeros(n, dtype=bool)
    for fam in ("double_top_bottom", "head_shoulders", "wedge", "triangle", "flag_pennant", "cup_handle", "trend_structure"):
        m |= np.isfinite(_a(npz, f"formation_{fam}_bull_{tf}", n)) | np.isfinite(_a(npz, f"formation_{fam}_bear_{tf}", n))
    return m


def _bull_finite(npz, n, fam, p, research=False):
    if fam in RESEARCH_ONLY_FAMS and not research:
        return np.zeros(n, dtype=bool)
    if fam == "DIV":
        return _div_finite(npz, n, p["tf"], p["hidden"], True)
    if fam == "STOCH_XTREME":
        return np.isfinite(_stoch_k(npz, n, p["tf"]))
    if fam == "RSI2_XTREME":
        return np.isfinite(_rsi2_v(npz, n, p["tf"]))
    if fam == "BBKC":
        return _bbkc_finite(npz, n, p["tf"])
    if fam in ("VWAP_STRETCH", "OI_SURGE"):
        return np.ones(n, dtype=bool)
    if fam == "FUNDING_CROWD":
        return np.isfinite(_a(npz, "funding_zscore", n))
    if fam == "SMFI_DIV":
        return np.isfinite(_a(npz, f"smfi_bull_div_{p['tf']}", n))
    if fam == "WICK_REJECT":
        return _wick_finite(npz, n, p["tf"], True)
    if fam == "FORMATION":
        return _formation_finite(npz, n, p["tf"])
    if fam == "ORB_BREAK":
        return np.isfinite(_a(npz, "orb_position", n))
    return np.zeros(n, dtype=bool)


def _bear_finite(npz, n, fam, p, research=False):
    if fam in RESEARCH_ONLY_FAMS and not research:
        return np.zeros(n, dtype=bool)
    if fam == "DIV":
        return _div_finite(npz, n, p["tf"], p["hidden"], False)
    if fam == "STOCH_XTREME":
        return np.isfinite(_stoch_k(npz, n, p["tf"]))
    if fam == "RSI2_XTREME":
        return np.isfinite(_rsi2_v(npz, n, p["tf"]))
    if fam == "BBKC":
        return _bbkc_finite(npz, n, p["tf"])
    if fam in ("VWAP_STRETCH", "OI_SURGE"):
        return np.ones(n, dtype=bool)
    if fam == "FUNDING_CROWD":
        return np.isfinite(_a(npz, "funding_zscore", n))
    if fam == "SMFI_DIV":
        return np.isfinite(_a(npz, f"smfi_bear_div_{p['tf']}", n))
    if fam == "WICK_REJECT":
        return _wick_finite(npz, n, p["tf"], False)
    if fam == "FORMATION":
        return _formation_finite(npz, n, p["tf"])
    if fam == "ORB_BREAK":
        return np.isfinite(_a(npz, "orb_position", n))
    return np.zeros(n, dtype=bool)


def _params(cfg, prefix):
    return {"tf": str(_cfg_get(cfg, prefix + "_TF", "1h")), "hidden": bool(_cfg_get(cfg, prefix + "_HIDDEN", True)), "z": float(_cfg_get(cfg, prefix + "_Z", 2.0)), "pct": float(_cfg_get(cfg, prefix + "_PCT", 1.0 if "VWAP" in prefix else 3.0)), "score": float(_cfg_get(cfg, prefix + "_MIN_SCORE", 0.6))}


ENTRY_FAMS = ("DIV", "STOCH_XTREME", "RSI2_XTREME", "BBKC", "VWAP_STRETCH", "FUNDING_CROWD", "OI_SURGE", "SMFI_DIV", "WICK_REJECT", "FORMATION", "ORB_BREAK")
EXIT_FAMS = ("DIV", "STOCH_XTREME", "RSI2_XTREME", "BBKC", "VWAP_STRETCH", "FUNDING_CROWD", "OI_SURGE", "SMFI_DIV", "WICK_REJECT", "FORMATION")


def _probe_ok(npz, fam, tf):
    probes = {"DIV": (f"div_reg_bull_wt_{tf}", f"div_reg_bear_wt_{tf}"), "STOCH_XTREME": (f"stoch_k_{tf}",), "RSI2_XTREME": (f"rsi_2_{tf}", f"rsi2_{tf}"), "BBKC": (f"bb_pct_b_{tf}", f"bb_pct_{tf}", f"kc_position_{tf}"), "VWAP_STRETCH": ("vwap_distance_pct",), "FUNDING_CROWD": ("funding_zscore",), "OI_SURGE": ("oi_vel_4h",), "SMFI_DIV": (f"smfi_bull_div_{tf}", f"smfi_bear_div_{tf}"), "WICK_REJECT": (f"bar_upper_wick_{tf}", f"bar_lower_wick_{tf}"), "FORMATION": (f"formation_direction_{tf}",), "ORB_BREAK": ("orb_position",)}
    try:
        return any(k in npz for k in probes.get(fam, ()))
    except Exception:
        return False


def entry_veto_masks(npz, n, is_long, cfg):
    out = {}
    research = bool(_cfg_get(cfg, "BT_RESEARCH_UNLOCKED", False))
    for fam in ENTRY_FAMS:
        pre = fam + "_ENTRY"
        if not bool(_cfg_get(cfg, pre + "_ENABLED", False)):
            continue
        p = _params(cfg, pre)
        if not _probe_ok(npz, fam, p["tf"]):
            continue
        if is_long:
            m = _bull_mask(npz, n, fam, p, research)
            f = _bull_finite(npz, n, fam, p, research)
        else:
            m = _bear_mask(npz, n, fam, p, research)
            f = _bear_finite(npz, n, fam, p, research)
        m = np.asarray(m, dtype=bool)
        f = np.asarray(f, dtype=bool)
        if m.shape[0] == n and f.shape[0] == n:
            out[fam] = f & ~m
    return out


def exit_fire_masks(npz, n, is_long, cfg):
    out = {}
    research = bool(_cfg_get(cfg, "BT_RESEARCH_UNLOCKED", False))
    for fam in EXIT_FAMS:
        pre = fam + "_EXIT"
        if not bool(_cfg_get(cfg, pre + "_ENABLED", False)):
            continue
        p = _params(cfg, pre)
        if not _probe_ok(npz, fam, p["tf"]):
            continue
        if is_long:
            m = _bear_mask(npz, n, fam, p, research)
            f = _bear_finite(npz, n, fam, p, research)
        else:
            m = _bull_mask(npz, n, fam, p, research)
            f = _bull_finite(npz, n, fam, p, research)
        m = np.asarray(m, dtype=bool)
        f = np.asarray(f, dtype=bool)
        if m.shape[0] == n and f.shape[0] == n:
            out[fam] = f & m
    return out


def _fnum(ind, key):
    try:
        v = ind.get(key, None)
        if v is None or v == "":
            return None
        f = float(v)
        return f if f == f else None
    except Exception:
        return None


def _bull_live(ind, fam, p):
    tf = p["tf"]
    if fam == "DIV":
        keys = [f"div_reg_bull_wt_{tf}", f"div_reg_bull_mfi_{tf}"] + ([f"div_hid_bull_wt_{tf}", f"div_hid_bull_mfi_{tf}"] if p["hidden"] else [])
        vals = [_fnum(ind, k) for k in keys]
        if all(v is None for v in vals):
            return None
        return any((v or 0) > 0 for v in vals)
    if fam == "STOCH_XTREME":
        v = _fnum(ind, f"stoch_k_{tf}")
        if v is None:
            v = _fnum(ind, f"stoch_k_{tf}_prev")
        return None if v is None else v < 20.0
    if fam == "RSI2_XTREME":
        v = _fnum(ind, f"rsi_2_npz_{tf}")
        if v is None:
            v = _fnum(ind, f"rsi_2_{tf}")
        if v is None:
            v = _fnum(ind, f"rsi2_{tf}")
        return None if v is None else v < 10.0
    if fam == "BBKC":
        b = _fnum(ind, f"bb_pct_b_{tf}")
        if b is None:
            b = _fnum(ind, f"bb_pct_{tf}")
        kc = _fnum(ind, f"kc_position_{tf}")
        if kc is None:
            up = _fnum(ind, f"kc_upper_{tf}")
            dn = _fnum(ind, f"kc_lower_{tf}")
            cl = _fnum(ind, f"close_{tf}")
            kc = (cl - dn) / (up - dn) if up is not None and dn is not None and cl is not None and up > dn else None
        if b is None and kc is None:
            return None
        return (b is not None and b < 0.0) or (kc is not None and kc < 0.0)
    if fam == "VWAP_STRETCH":
        v = _fnum(ind, "vwap_npz_distance_pct")
        if v is None:
            v = _fnum(ind, "vwap_distance_pct")
        return None if v is None else v < -abs(p["pct"])
    if fam == "FUNDING_CROWD":
        v = _fnum(ind, "funding_zscore")
        return None if v is None else v < -abs(p["z"])
    if fam == "OI_SURGE":
        v = _fnum(ind, "oi_vel_4h")
        if v is None:
            v = _fnum(ind, "oi_change_4h_pct")
        return None if v is None else v < -abs(p["pct"])
    if fam == "SMFI_DIV":
        v = _fnum(ind, f"smfi_bull_div_{tf}")
        if v is None and tf == "D":
            v = 1.0 if ind.get("smfi_bull_divergence") else (0.0 if "smfi_bull_divergence" in ind else None)
        return None if v is None else v > 0
    if fam == "WICK_REJECT":
        lw = _fnum(ind, f"bar_lower_wick_{tf}")
        bo = _fnum(ind, f"bar_body_ratio_{tf}")
        return None if lw is None or bo is None else (lw > 0.5 and bo < 0.35)
    if fam == "FORMATION":
        return _formation_live(ind, tf, True, p["score"])
    if fam == "ORB_BREAK":
        oh = _fnum(ind, "orb_high")
        ol = _fnum(ind, "orb_low")
        cl = _fnum(ind, "close_15m")
        if cl is None:
            cl = _fnum(ind, "close")
        if oh is None or ol is None or cl is None or oh <= ol:
            return None
        return cl > oh
    return None


def _bear_live(ind, fam, p):
    tf = p["tf"]
    if fam == "DIV":
        keys = [f"div_reg_bear_wt_{tf}", f"div_reg_bear_mfi_{tf}"] + ([f"div_hid_bear_wt_{tf}", f"div_hid_bear_mfi_{tf}"] if p["hidden"] else [])
        vals = [_fnum(ind, k) for k in keys]
        if all(v is None for v in vals):
            return None
        return any((v or 0) > 0 for v in vals)
    if fam == "STOCH_XTREME":
        v = _fnum(ind, f"stoch_k_{tf}")
        if v is None:
            v = _fnum(ind, f"stoch_k_{tf}_prev")
        return None if v is None else v > 80.0
    if fam == "RSI2_XTREME":
        v = _fnum(ind, f"rsi_2_npz_{tf}")
        if v is None:
            v = _fnum(ind, f"rsi_2_{tf}")
        if v is None:
            v = _fnum(ind, f"rsi2_{tf}")
        return None if v is None else v > 90.0
    if fam == "BBKC":
        b = _fnum(ind, f"bb_pct_b_{tf}")
        if b is None:
            b = _fnum(ind, f"bb_pct_{tf}")
        kc = _fnum(ind, f"kc_position_{tf}")
        if kc is None:
            up = _fnum(ind, f"kc_upper_{tf}")
            dn = _fnum(ind, f"kc_lower_{tf}")
            cl = _fnum(ind, f"close_{tf}")
            kc = (cl - dn) / (up - dn) if up is not None and dn is not None and cl is not None and up > dn else None
        if b is None and kc is None:
            return None
        return (b is not None and b > 1.0) or (kc is not None and kc > 1.0)
    if fam == "VWAP_STRETCH":
        v = _fnum(ind, "vwap_npz_distance_pct")
        if v is None:
            v = _fnum(ind, "vwap_distance_pct")
        return None if v is None else v > abs(p["pct"])
    if fam == "FUNDING_CROWD":
        v = _fnum(ind, "funding_zscore")
        return None if v is None else v > abs(p["z"])
    if fam == "OI_SURGE":
        v = _fnum(ind, "oi_vel_4h")
        if v is None:
            v = _fnum(ind, "oi_change_4h_pct")
        return None if v is None else v > abs(p["pct"])
    if fam == "SMFI_DIV":
        v = _fnum(ind, f"smfi_bear_div_{tf}")
        if v is None and tf == "D":
            v = 1.0 if ind.get("smfi_bear_divergence") else (0.0 if "smfi_bear_divergence" in ind else None)
        return None if v is None else v > 0
    if fam == "WICK_REJECT":
        uw = _fnum(ind, f"bar_upper_wick_{tf}")
        bo = _fnum(ind, f"bar_body_ratio_{tf}")
        return None if uw is None or bo is None else (uw > 0.5 and bo < 0.35)
    if fam == "FORMATION":
        return _formation_live(ind, tf, False, p["score"])
    if fam == "ORB_BREAK":
        oh = _fnum(ind, "orb_high")
        ol = _fnum(ind, "orb_low")
        cl = _fnum(ind, "close_15m")
        if cl is None:
            cl = _fnum(ind, "close")
        if oh is None or ol is None or cl is None or oh <= ol:
            return None
        return cl < ol
    return None


def _formation_live(ind, tf, want_bull, score):
    found = False
    for fam in ("double_top_bottom", "head_shoulders", "wedge", "triangle", "flag_pennant", "cup_handle", "trend_structure"):
        side = "bull" if want_bull else "bear"
        sig = _fnum(ind, f"formation_{fam}_{side}_{tf}")
        if sig is None:
            continue
        found = True
        sc = _fnum(ind, f"formation_{fam}_{side}_score_{tf}")
        if sig > 0 and (sc is None or sc >= score):
            return True
    return None if not found else False


def entry_veto_live(ind, is_long, cfg):
    for fam in ENTRY_FAMS:
        pre = fam + "_ENTRY"
        if not bool(_cfg_get(cfg, pre + "_ENABLED", False)):
            continue
        p = _params(cfg, pre)
        sig = _bull_live(ind, fam, p) if is_long else _bear_live(ind, fam, p)
        if sig is False:
            return True, pre
    return False, None


def exit_fire_live(ind, is_long, cfg):
    for fam in EXIT_FAMS:
        pre = fam + "_EXIT"
        if not bool(_cfg_get(cfg, pre + "_ENABLED", False)):
            continue
        p = _params(cfg, pre)
        sig = _bear_live(ind, fam, p) if is_long else _bull_live(ind, fam, p)
        if sig is True:
            return True, pre
    return False, None
