"""vec_decisions.golden_loop — shared Golden Rule loop twin (USER 2026-10-06: live and vector identically).

LIVE SOURCES (mirrored exactly):
  trigger phases .. ez_manage._golden_rule_loop (breakout edge 0.1x / retest 5.0x, k-turn, 4h window)
  consensus ...... golden_rule_htf._run_gate + _ind_score (activation/entry split, vote mode, thresholds, invert)
  veto ........... ez_manage._golden_rule_loop HTF veto block (D/4h HA+WT + HTF_VETO_REQUIRE_D)
  reason ......... f"GOLDEN_RULE_{SIDE}_mult{mult}" (live exact)

NATIVE TF (NO-1m/3m/5m parity): while USE_1M_3M_SIGNALS_ENABLED is False (False for months),
live reads 15m in place of 3m/5m (ez_manage NO-3M fallback) and NPZ has no 1m/3m/5m keys, so the
twin uses 15m-native series. When the switch flips True AND 3m/5m keys exist, the twin uses them
(the same rule is applied to live golden_rule_htf — NOTE_3M_REENABLE both sides).

Pure numpy. No live imports. n = n bars. All comparisons causal (same-bar close values; prev via
shift). Engines own cooldown/notional/tick-cap/portfolio state; this module returns per-bar signal.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def _cfg(cfg: Any, name: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _arr(source: Mapping, key: str, n: int, default: float = 0.0) -> np.ndarray:
    try:
        v = source.get(key) if hasattr(source, "get") else None
    except Exception:
        v = None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return v.astype(np.float64)
        except Exception:
            pass
    return np.full(n, default, dtype=np.float64)


def _list_cfg(cfg: Any, name: str) -> list:
    raw = _cfg(cfg, name, [])
    if raw is None:
        return []
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    try:
        return [str(x) for x in list(raw) if str(x)]
    except Exception:
        return []


def _low_tf_allowed(cfg: Any, arrays: Mapping, tf: str) -> bool:
    """3m/5m usable only when the 1m/3m switch is on AND real keys exist (mirrors live skip)."""
    if tf not in ("3m", "5m"):
        return True
    if not bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)):
        return False
    try:
        keys = set(arrays.keys()) if hasattr(arrays, "keys") else set()
    except Exception:
        return False
    return any(f"_{tf}" in str(k) or str(k).endswith(tf) for k in keys)


def native_tf(mode: str, cfg: Any, arrays: Mapping) -> str:
    """Native trigger TF: 3m/5m only when allowed, else 15m (== live NO-3M fallback)."""
    low = "3m" if str(mode).lower() != "tradier" else "5m"
    if _low_tf_allowed(cfg, arrays, low):
        return low
    return "15m"


def _native_key(field: str, ntf: str) -> str:
    return f"{field}_{ntf}" if ntf in ("3m", "5m") else f"{field}_15m"


def trigger_masks(arrays: Mapping, ts: np.ndarray, is_long: bool, mode: str, cfg: Any) -> dict:
    """Breakout/retest trigger per bar. Returns {fire, phase(0/1/2), mult, breakout_fire}.

    Mirrors ez_manage._golden_rule_loop lines ~17990-18075:
      Phase 1 BREAKOUT: edge cross above dc_high_1h/bb_upper_1h (below dc_low/bb_lower short)
                        + WT aligned -> mult GOLDEN_RULE_MULT_BREAKOUT (0.1).
      Phase 2 RETEST:   back at/through dc_basis_1h + k turning + WT aligned + within
                        GOLDEN_RULE_RETEST_WINDOW_S (14400) of last cross -> GOLDEN_RULE_MULT_RETEST (5.0).
    Breakout ts is stamped on EVERY cross (even WT-failed), exactly like live.
    """
    n = int(len(np.asarray(ts))) if ts is not None else 0
    out = {"fire": np.zeros(n, bool), "phase": np.zeros(n, np.int8), "mult": np.zeros(n), "breakout_fire": np.zeros(n, bool)}
    if n <= 0:
        return out
    ntf = native_tf(mode, cfg, arrays)
    close = _arr(arrays, "close", n)
    dc_h = _arr(arrays, "dc_high_1h", n)
    dc_l = _arr(arrays, "dc_low_1h", n)
    dc_b = _arr(arrays, "dc_basis_1h", n)
    bb_u = _arr(arrays, "bb_upper_1h", n)
    bb_l = _arr(arrays, "bb_lower_1h", n)
    w1 = _arr(arrays, _native_key("wt1", ntf), n)
    w2 = _arr(arrays, _native_key("wt2", ntf), n)
    kk = _arr(arrays, _native_key("stoch_k", ntf), n, 50.0)
    dd = _arr(arrays, _native_key("stoch_d", ntf), n, 50.0)
    tsv = np.asarray(ts, dtype=float)
    k_prev = np.roll(kk, 1)
    if n:
        k_prev[0] = kk[0]
    m_break = float(_cfg(cfg, "GOLDEN_RULE_MULT_BREAKOUT", 0.1))
    m_retest = float(_cfg(cfg, "GOLDEN_RULE_MULT_RETEST", 5.0))
    win_s = float(_cfg(cfg, "GOLDEN_RULE_RETEST_WINDOW_S", 14400.0))
    valid = np.isfinite(close) & (close > 0) & np.isfinite(w1) & np.isfinite(w2)
    if is_long:
        above = ((dc_h != 0) & (close > dc_h)) | ((bb_u != 0) & (close > bb_u))
    else:
        above = ((dc_l != 0) & (close < dc_l)) | ((bb_l != 0) & (close < bb_l))
    prev_above = np.roll(above, 1)
    if n:
        prev_above[0] = False
    cross = above & (~prev_above)
    cross_ts = np.where(cross, tsv, 0.0)
    last_cross_ts = np.maximum.accumulate(cross_ts)
    wt_ok = (w1 > w2) if is_long else (w1 < w2)
    fire1 = cross & wt_ok & valid
    if is_long:
        at_basis = (dc_b != 0) & (close >= dc_b)
        k_turn = (kk > dd) & (kk > k_prev)
    else:
        at_basis = (dc_b != 0) & (close <= dc_b)
        k_turn = (kk < dd) & (kk < k_prev)
    in_window = (tsv - last_cross_ts) <= win_s
    fire2 = (~above) & at_basis & in_window & k_turn & wt_ok & valid
    fire = fire1 | fire2
    phase = np.where(fire2, 2, np.where(fire1, 1, 0)).astype(np.int8)
    mult = np.where(fire2, m_retest, np.where(fire1, m_break, 0.0))
    out["fire"] = fire
    out["phase"] = phase
    out["mult"] = mult
    out["breakout_fire"] = fire1
    return out


def _tf_ind_matrix(arrays: Mapping, tf: str, n: int, is_long: bool, px: np.ndarray, invert: bool, dc_thr: float, bb_thr: float) -> np.ndarray:
    """Per-bar indicator-agreement count for one TF. Mirrors golden_rule_htf._ind_score exactly."""
    s = np.zeros(n, dtype=np.int16)
    w1 = _arr(arrays, f"wt1_{tf}", n)
    w2 = _arr(arrays, f"wt2_{tf}", n)
    has = (w1 != 0) | (w2 != 0)
    ok = (w1 > w2) if is_long else (w1 < w2)
    s += np.where(has, ok.astype(np.int16), 0)
    rsi = _arr(arrays, f"rsi_{tf}", n, -1.0)
    has = rsi >= 0
    ok = (rsi > 50) if is_long else (rsi < 50)
    s += np.where(has, ok.astype(np.int16), 0)
    mfi = _arr(arrays, f"mfi_{tf}", n, -1.0)
    has = mfi >= 0
    ok = (mfi > 50) if is_long else (mfi < 50)
    s += np.where(has, ok.astype(np.int16), 0)
    dc_pos = _arr(arrays, f"dc_position_{tf}", n, -1.0)
    miss = dc_pos < 0
    if bool(np.any(miss)):
        dc_h = _arr(arrays, f"dc_high_{tf}", n)
        dc_l = _arr(arrays, f"dc_low_{tf}", n)
        ref = px
        good = (dc_h > dc_l) & (dc_l > 0) & (ref > 0)
        calc = np.where(good, (ref - dc_l) / np.maximum(dc_h - dc_l, 1e-12), -1.0)
        dc_pos = np.where(miss, calc, dc_pos)
    dc_l = dc_thr if dc_thr > 0 else 0.65
    dc_s = 1.0 - dc_l
    has = dc_pos >= 0
    if invert:
        ok = (dc_pos >= dc_l) if is_long else (dc_pos <= dc_s)
    else:
        ok = (dc_pos < dc_l) if is_long else (dc_pos > dc_s)
    s += np.where(has, ok.astype(np.int16), 0)
    bb = _arr(arrays, f"bb_pct_b_{tf}", n, -1.0)
    miss = bb < 0
    if bool(np.any(miss)):
        bb_u = _arr(arrays, f"bb_upper_{tf}", n)
        bb_l = _arr(arrays, f"bb_lower_{tf}", n)
        ref = px
        good = (bb_u > bb_l) & (bb_l > 0) & (ref > 0)
        calc = np.where(good, (ref - bb_l) / np.maximum(bb_u - bb_l, 1e-12), -1.0)
        bb = np.where(miss, calc, bb)
    bb_l = bb_thr if bb_thr > 0 else 0.75
    bb_s = 1.0 - bb_l
    has = bb >= 0
    if invert:
        ok = (bb >= bb_l) if is_long else (bb <= bb_s)
    else:
        ok = (bb < bb_l) if is_long else (bb > bb_s)
    s += np.where(has, ok.astype(np.int16), 0)
    rvol = _arr(arrays, f"relative_volume_{tf}", n, -1.0)
    has = rvol >= 0
    s += np.where(has, (rvol > 1.0).astype(np.int16), 0)
    stk = _arr(arrays, f"stoch_k_{tf}", n, -1.0)
    has = stk >= 0
    ok = (stk < 80.0) if is_long else (stk > 20.0)
    s += np.where(has, ok.astype(np.int16), 0)
    adx = _arr(arrays, f"adx_{tf}", n, -1.0)
    has = adx > 0
    s += np.where(has, (adx > 20.0).astype(np.int16), 0)
    mh = _arr(arrays, f"macd_hist_{tf}", n, 0.0)
    has = mh != 0
    ok = (mh > 0) if is_long else (mh < 0)
    s += np.where(has, ok.astype(np.int16), 0)
    ha = _arr(arrays, f"ha_color_{tf}", n, 0.0)
    has = ha != 0
    ok = (ha > 0) if is_long else (ha < 0)
    s += np.where(has, ok.astype(np.int16), 0)
    std = _arr(arrays, f"stoch_d_{tf}", n, -1.0)
    has = (std >= 0) & (stk >= 0)
    ok = (stk > std) if is_long else (stk < std)
    s += np.where(has, ok.astype(np.int16), 0)
    return s


def consensus_pass(arrays: Mapping, ts: np.ndarray, is_long: bool, mode: str, cfg: Any, px: np.ndarray | None = None, invert_dc_bb: bool = True) -> np.ndarray:
    """Full golden_rule_htf._run_gate mirror (activation/entry split, vote mode, thresholds, invert).

    TF universe == live _CRYPTO_TFS/_TRADIER_TFS minus 3m/5m while the 1m/3m switch is off
    (live golden_rule_htf applies the identical skip — NOTE_3M_REENABLE both sides).
    """
    n = int(len(np.asarray(ts))) if ts is not None else 0
    if n <= 0:
        return np.zeros(0, bool)
    is_tradier = str(mode).lower() == "tradier"
    default_tfs = ["5m", "15m", "1h", "4h", "D", "W"] if is_tradier else ["3m", "15m", "1h", "4h", "D", "W"]
    require_act = bool(_cfg(cfg, "GOLDEN_RULE_REQUIRE_ACTIVATION", False))
    act_list = [t for t in _list_cfg(cfg, "GOLDEN_RULE_ACTIVATION_TF_LIST") if _low_tf_allowed(cfg, arrays, t)]
    entry_list = [t for t in _list_cfg(cfg, "GOLDEN_RULE_ENTRY_TF_LIST") if _low_tf_allowed(cfg, arrays, t)]
    vote_min = int(_cfg(cfg, "GR_TOTAL_VOTE_SCORE_MIN", 0) or 0)
    dc_thr = float(_cfg(cfg, "GR_DC_EXTENDED_LONG", _cfg(cfg, "GOLDEN_RULE_DC_THRESHOLD", 0)) or 0)
    bb_thr = float(_cfg(cfg, "GR_BB_EXTENDED_LONG", _cfg(cfg, "GOLDEN_RULE_BB_THRESHOLD", 0)) or 0)
    min_tfs = int(_cfg(cfg, "GOLDEN_RULE_HTF_MIN_TFS", _cfg(cfg, "GOLDEN_RULE_MIN_TFS", 0)) or 0)
    min_ind = int(_cfg(cfg, "GOLDEN_RULE_MIN_IND", 0) or 0)
    close = np.asarray(px, dtype=float) if px is not None else _arr(arrays, "close", n)
    dc_l = dc_thr if dc_thr > 0 else 0.65
    bb_l = bb_thr if bb_thr > 0 else 0.75
    if require_act and act_list:
        act_ok = np.zeros(n, bool)
        for tf in act_list:
            bb = _arr(arrays, f"bb_pct_b_{tf}", n, -1.0)
            dc = _arr(arrays, f"dc_position_{tf}", n, -1.0)
            if is_long:
                tf_active = (bb >= bb_l) | (dc >= dc_l)
            else:
                tf_active = ((bb >= 0) & (bb <= 1.0 - bb_l)) | ((dc >= 0) & (dc <= 1.0 - dc_l))
            act_ok |= tf_active
        gated = ~act_ok
        tfs = [t for t in (entry_list if entry_list else default_tfs) if _low_tf_allowed(cfg, arrays, t)]
    else:
        gated = np.zeros(n, bool)
        tfs = [t for t in default_tfs if _low_tf_allowed(cfg, arrays, t)]
    if vote_min > 0:
        total = np.zeros(n, dtype=np.int32)
        for tf in tfs:
            total += _tf_ind_matrix(arrays, tf, n, is_long, close, invert_dc_bb, dc_thr, bb_thr).astype(np.int32)
        passes = total >= vote_min
    elif min_tfs <= 0:
        passes = np.ones(n, bool)
    else:
        confirmed = np.zeros(n, dtype=np.int16)
        for tf in tfs:
            confirmed += (_tf_ind_matrix(arrays, tf, n, is_long, close, invert_dc_bb, dc_thr, bb_thr) >= min_ind).astype(np.int16)
        passes = confirmed >= min_tfs
    return passes & (~gated)


def veto_fire(arrays: Mapping, ts: np.ndarray, is_long: bool, cfg: Any) -> np.ndarray:
    """HTF veto mirror (ez_manage loop block). Returns True where the fire must be REFUSED."""
    n = int(len(np.asarray(ts))) if ts is not None else 0
    if n <= 0:
        return np.zeros(0, bool)
    if not bool(_cfg(cfg, "GOLDEN_RULE_HTF_VETO_ENABLED", True)):
        return np.zeros(n, bool)
    require_d = bool(_cfg(cfg, "HTF_VETO_REQUIRE_D", True))
    w1d = _arr(arrays, "wt1_D", n)
    w2d = _arr(arrays, "wt2_D", n)
    had = _arr(arrays, "ha_color_D", n)
    w14 = _arr(arrays, "wt1_4h", n)
    w24 = _arr(arrays, "wt2_4h", n)
    ha4 = _arr(arrays, "ha_color_4h", n)
    d_bear = (had < 0) & (w1d < w2d)
    d_bull = (had > 0) & (w1d > w2d)
    h4_bear = (ha4 < 0) & (w14 < w24)
    h4_bull = (ha4 > 0) & (w14 > w24)
    if is_long:
        if require_d:
            return d_bear
        return d_bear | h4_bear
    if require_d:
        return d_bull
    return d_bull | h4_bull


def golden_entry(arrays: Mapping, ts: np.ndarray, is_long: bool, mode: str, cfg: Any) -> dict:
    """trigger AND consensus AND NOT veto — the live loop admission order. Returns masks + sizing."""
    n = int(len(np.asarray(ts))) if ts is not None else 0
    empty = {"fire": np.zeros(n, bool), "phase": np.zeros(n, np.int8), "mult": np.zeros(n), "target_usd": np.zeros(n), "consensus": np.zeros(n, bool), "vetoed": np.zeros(n, bool)}
    if n <= 0:
        return empty
    if not bool(_cfg(cfg, "GOLDEN_RULE_ENABLED", True)):
        return empty
    trig = trigger_masks(arrays, ts, is_long, mode, cfg)
    cons = consensus_pass(arrays, ts, is_long, mode, cfg, invert_dc_bb=True)
    veto = veto_fire(arrays, ts, is_long, cfg)
    fire = trig["fire"] & cons & (~veto)
    base_usd = float(_cfg(cfg, "GOLDEN_RULE_BASE_USD", 5.0))
    mult = np.where(fire, trig["mult"], 0.0)
    return {"fire": fire, "phase": np.where(fire, trig["phase"], 0).astype(np.int8), "mult": mult, "target_usd": mult * base_usd, "consensus": cons, "vetoed": veto & trig["fire"]}


def golden_reason(is_long: bool, mult: float) -> str:
    """Live-exact reason string."""
    return f"GOLDEN_RULE_{'LONG' if is_long else 'SHORT'}_mult{float(mult)}"


def scalar_fire(row: Mapping, prev_above: bool, breakout_ts: float, ts_now: float, is_long: bool, mode: str, cfg: Any) -> dict:
    """One-bar scalar admission for scalar loops (backtest, wide). row = {key: value} indicator dict.

    Returns {fire, phase, mult, target_usd, reason, prev_above, breakout_ts}. prev_above/breakout_ts
    are the updated state the caller must persist per sym_side (== live self._golden_prev_above/_breakout_ts).
    """
    zero = {"fire": False, "phase": 0, "mult": 0.0, "target_usd": 0.0, "reason": "", "prev_above": bool(prev_above), "breakout_ts": float(breakout_ts or 0.0)}
    if not bool(_cfg(cfg, "GOLDEN_RULE_ENABLED", True)):
        return zero

    def _f(name: str, default: float = 0.0) -> float:
        try:
            v = row.get(name, default)
        except Exception:
            v = default
        if v is None:
            return float(default)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("green",):
                return 1.0
            if s in ("red",):
                return -1.0
            if s in ("neutral", ""):
                return 0.0
            try:
                return float(s)
            except Exception:
                return float(default)
        try:
            return float(v)
        except Exception:
            return float(default)
    ntf = native_tf(mode, cfg, row if isinstance(row, Mapping) else {})
    wk = (lambda f: f"{f}_{ntf}") if ntf in ("3m", "5m") else (lambda f: f"{f}_15m")
    px = _f("current_price", 0.0) or _f("close", 0.0)
    w1 = row.get(wk("wt1"), None)
    w2 = row.get(wk("wt2"), None)
    try:
        w1 = float(w1) if w1 is not None else None
        w2 = float(w2) if w2 is not None else None
    except Exception:
        w1, w2 = None, None
    if w1 is None or w2 is None or px is None or px <= 0:
        return zero
    dc_h = _f("dc_high_1h")
    dc_l = _f("dc_low_1h")
    dc_b = _f("dc_basis_1h")
    bb_u = _f("bb_upper_1h")
    bb_l = _f("bb_lower_1h")
    k_key = "k_15m" if ntf == "15m" else ("k_3m" if ntf == "3m" else "k_5m")
    d_key = "d_15m" if ntf == "15m" else ("d_3m" if ntf == "3m" else "d_5m")
    sk_key = "stoch_k_15m" if ntf == "15m" else ("stoch_k_3m" if ntf == "3m" else "stoch_k_5m")
    sd_key = "stoch_d_15m" if ntf == "15m" else ("stoch_d_3m" if ntf == "3m" else "stoch_d_5m")
    _has = lambda k: bool(hasattr(row, "__contains__") and k in row)
    kk = _f(k_key if _has(k_key) else sk_key, 50.0)
    dd = _f(d_key if _has(d_key) else sd_key, 50.0)
    k_prev = _f(k_key + "_prev", kk)
    if is_long:
        above = bool((dc_h and px > dc_h) or (bb_u and px > bb_u))
    else:
        above = bool((dc_l and px < dc_l) or (bb_l and px < bb_l))
    m_break = float(_cfg(cfg, "GOLDEN_RULE_MULT_BREAKOUT", 0.1))
    m_retest = float(_cfg(cfg, "GOLDEN_RULE_MULT_RETEST", 5.0))
    win_s = float(_cfg(cfg, "GOLDEN_RULE_RETEST_WINDOW_S", 14400.0))
    out = dict(zero)
    out["prev_above"] = above
    crossed = above and not bool(prev_above)
    if crossed:
        out["breakout_ts"] = float(ts_now)
    bts = out["breakout_ts"]
    wt_ok = (w1 > w2) if is_long else (w1 < w2)
    phase = 0
    mult = 0.0
    if crossed and wt_ok:
        phase, mult = 1, m_break
    elif not above and dc_b and ((px >= dc_b) if is_long else (px <= dc_b)) and (float(ts_now) - bts <= win_s):
        k_turn = (kk > dd and kk > k_prev) if is_long else (kk < dd and kk < k_prev)
        if k_turn and wt_ok:
            phase, mult = 2, m_retest
    if phase == 0:
        return out
    arr1 = {}
    try:
        keys = list(row.keys())
    except Exception:
        keys = []
    for k in keys:
        try:
            arr1[k] = np.asarray([float(row[k]) if row[k] is not None else 0.0], dtype=np.float64)
        except Exception:
            continue
    ts1 = np.asarray([float(ts_now)])
    cons = bool(consensus_pass(arr1, ts1, is_long, mode, cfg, px=np.asarray([px]), invert_dc_bb=True)[0])
    if not cons:
        return out
    veto = bool(veto_fire(arr1, ts1, is_long, cfg)[0])
    if veto:
        return out
    base_usd = float(_cfg(cfg, "GOLDEN_RULE_BASE_USD", 5.0))
    out["fire"] = True
    out["phase"] = phase
    out["mult"] = float(mult)
    out["target_usd"] = float(mult) * base_usd
    out["reason"] = golden_reason(is_long, mult)
    return out
