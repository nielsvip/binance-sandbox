"""Fourth disjoint source-exact V12 reentry/augment/filter vector tranche.

Only persisted indicator arrays and explicit lifecycle state are accepted.  A
missing requested timeframe/field fails closed; no timeframe or constant proxy
is substituted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
class ChannelResult:
    exit_mask: np.ndarray
    ever_outside: np.ndarray
    available: bool
    reason: str = ""


def _fc(name, typ, default, grid, family, arrays, state, consumers, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays), tuple(state), tuple(consumers), source)


_LH = ("high_1h", "high_1h_prev", "high_4h", "high_4h_prev", "low_1h", "low_1h_prev", "low_4h", "low_4h_prev", "dc_high_1h", "dc_high_4h", "dc_low_1h", "dc_low_4h")
_TOR = ("close", "dc_high_{tf}", "dc_low_{tf}", "dc_high_{tf}_prev", "dc_low_{tf}_prev")
_GR = ("wt1_{tf}", "wt2_{tf}", "rsi_{tf}", "mfi_{tf}", "dc_pct_{tf}", "bb_pct_b_{tf}", "relative_volume_{tf}", "k_{tf}", "d_{tf}", "adx_{tf}", "macd_hist_{tf}", "ha_color_{tf}")
_VRE = ("close", "dc_high4_3m", "dc_low4_3m", "wt1_15m", "wt2_15m", "wt_velocity_15m")


FIELD_CONTRACTS = {c.name: c for c in (
    _fc("LH_HL_FILTER_ENABLED", "bool", False, [True, False], "LH_HL_FILTER", _LH, ("candidate_mask", "is_hedge", "is_augment"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12713-12779"),
    _fc("LH_HL_FILTER_HEDGE_GATE_ENABLED", "bool", False, [False, True], "LH_HL_FILTER", _LH, ("candidate_mask", "is_hedge", "is_augment"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12715-12718"),
    _fc("LH_HL_FILTER_MODE", "str", "STRICT_2BAR", ["STRICT_2BAR", "DC_REGRESS"], "LH_HL_FILTER", _LH, ("candidate_mask", "is_hedge", "is_augment"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12721-12752"),
    _fc("LH_HL_FILTER_REQUIRE_BOTH", "bool", False, [False, True], "LH_HL_FILTER", _LH, ("candidate_mask", "is_hedge", "is_augment"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12724,12754-12779"),
    _fc("LH_HL_FILTER_TF_REQ", "int", 2, [1, 2], "LH_HL_FILTER", _LH, ("candidate_mask", "is_hedge", "is_augment"), ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12722,12755-12779"),
    _fc("TOP_OF_RANGE_BLOCK_ENABLED", "bool", True, [True, False], "TOP_OF_RANGE", _TOR, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:24079-24151;vec_paths/top_of_range_block.py:68-124"),
    _fc("TOP_OF_RANGE_BLOCK_REQUIRE_ALL", "bool", True, [False, True], "TOP_OF_RANGE", _TOR, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:24086-24151"),
    _fc("TOP_OF_RANGE_BLOCK_TF_LIST", "str", "1h,4h,D", ["1h,4h,D"], "TOP_OF_RANGE", _TOR, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:24083-24089"),
    _fc("TOP_OF_RANGE_BLOCK_THRESHOLD", "float", 0.95, [0.95], "TOP_OF_RANGE", _TOR, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), "ez_manage.py:24082,24105-24151"),
    _fc("GR_FILTER_ALL_ENTRIES", "bool", True, [False, True], "GR_ENTRY_FILTER", _GR, ("candidate_mask", "is_fresh_entry"), ("ENTRY",), "ez_manage.py:23962-24004;vec_paths/gr_filter_vec.py:44-151"),
    _fc("MTF_GR_MIN_IND", "int", 7, [7], "GR_ENTRY_FILTER", _GR, ("candidate_mask", "is_fresh_entry"), ("ENTRY",), "vec_paths/gr_filter_vec.py:129-151"),
    _fc("MTF_GR_MIN_TFS", "int", 3, [3], "GR_ENTRY_FILTER", _GR, ("candidate_mask", "is_fresh_entry"), ("ENTRY",), "vec_paths/gr_filter_vec.py:127-151"),
    _fc("VEC_REENTRY_DC4_EXITPRICE_ENABLED", "bool", True, [True, False], "VEC_REENTRY_WINDOW", _VRE, ("candidate_mask", "has_prior_exit", "bars_since_exit", "last_exit_price", "previous_close"), ("REENTRY",), "v12_wide_engine.py:7672-7744"),
    _fc("VEC_REENTRY_REQUIRE_PRIOR_EXIT", "bool", False, [True, False], "VEC_REENTRY_WINDOW", (), ("candidate_mask", "has_prior_exit", "bars_since_exit"), ("REENTRY",), "v12_wide_engine.py:7737-7744"),
    _fc("VEC_REENTRY_WINDOW_BARS", "int", 400, [100, 400, 800, 1200], "VEC_REENTRY_WINDOW", (), ("candidate_mask", "has_prior_exit", "bars_since_exit"), ("REENTRY",), "v12_wide_engine.py:7739-7744"),
    _fc("REENTRY_WT15M_K_MAX", "float", 50.0, [50.0], "WT15M_REENTRY_FILTER", ("wt1_15m", "wt2_15m", "wt1_15m_prev", "wt2_15m_prev", "stoch_k_15m"), ("candidate_mask",), ("REENTRY",), "vec_paths/reentry.py:185-223"),
    _fc("CHANNEL_REENTRY_STOP_ENABLED", "bool", False, [False, True], "CHANNEL_REENTRY_STOP", ("close", "channel_level"), ("candidate_mask", "ever_outside"), ("EXIT",), "vec_paths/tight_breakout_stops.py:88-125"),
    _fc("K1M_EXTREME_REVERSE_ENABLED", "bool", False, [False, True], "K1M_REVERSE_REDUCE", ("stoch_k_1m", "stoch_k_1m_prev"), ("candidate_mask", "gain_pct"), ("REDUCE",), "ez_positions_quick.py:13541-13555;vec_paths/reduce_paths.py:66-126"),
)}


def _cfg(cfg, name, default=None):
    if default is None and name in FIELD_CONTRACTS:
        default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources):
    for source in sources:
        for value in source.values():
            a = np.asarray(value)
            if a.ndim and len(a):
                return len(a)
    return 0


def _req(source, names, n):
    out, missing, bad = {}, [], []
    for name in names:
        if name not in source:
            missing.append(name); continue
        a = np.asarray(source[name])
        if a.ndim == 0 or len(a) != n:
            bad.append(name); continue
        out[name] = a
    if missing or bad:
        return None, f"missing={','.join(missing)};misaligned={','.join(bad)}"
    return out, ""


def _na(n, reason):
    return MaskResult(np.zeros(n, bool), False, reason)


def lh_hl_filter_mask(arrays, state, is_long, cfg):
    n = _n(arrays, state); s, sr = _req(state, ("candidate_mask", "is_hedge", "is_augment"), n)
    if s is None: return _na(n, sr)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "LH_HL_FILTER_ENABLED")): return MaskResult(candidate.copy(), True)
    mode = str(_cfg(cfg, "LH_HL_FILTER_MODE"))
    core = _LH[:8]; required = _LH if mode == "DC_REGRESS" else core
    a, ar = _req(arrays, required, n)
    if a is None: return _na(n, ar)
    applies = (~np.asarray(s["is_hedge"], bool) | bool(_cfg(cfg, "LH_HL_FILTER_HEDGE_GATE_ENABLED"))) & (~np.asarray(s["is_augment"], bool) | bool(_cfg(cfg, "LH_HL_FILTER_AUGMENT_GATE_ENABLED", True)))
    threshold = float(_cfg(cfg, "LH_HL_FILTER_DC_THRESHOLD_PCT", .5)) / 100
    events = {}
    for tf in ("1h", "4h"):
        h, hp, lo, lop = (np.asarray(a[x], float) for x in (f"high_{tf}", f"high_{tf}_prev", f"low_{tf}", f"low_{tf}_prev"))
        if mode == "DC_REGRESS":
            dh, dl = np.asarray(a[f"dc_high_{tf}"], float), np.asarray(a[f"dc_low_{tf}"], float)
            lh = (h > 0) & (dh > 0) & (h < dh * (1 - threshold)); hl = (lo > 0) & (dl > 0) & (lo > dl * (1 + threshold))
        else:
            lh = (h > 0) & (hp > 0) & (h < hp); hl = (lo > 0) & (lop > 0) & (lo > lop)
        events[tf] = (lh, hl, (lo > 0) & (lop > 0) & (lo < lop), (h > 0) & (hp > 0) & (h > hp))
    req = int(_cfg(cfg, "LH_HL_FILTER_TF_REQ")); both = bool(_cfg(cfg, "LH_HL_FILTER_REQUIRE_BOTH"))
    primary = sum((events[tf][0 if is_long else 1]).astype(np.int8) for tf in events) >= req
    confirm = sum((events[tf][2 if is_long else 3]).astype(np.int8) for tf in events) >= req
    block = applies & primary & (confirm if both else True)
    return MaskResult(candidate & ~block, True)


def top_of_range_filter_mask(arrays, state, is_long, cfg):
    n = _n(arrays, state); s, sr = _req(state, ("candidate_mask",), n)
    if s is None: return _na(n, sr)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "TOP_OF_RANGE_BLOCK_ENABLED")): return MaskResult(candidate.copy(), True)
    tfs = tuple(x.strip() for x in str(_cfg(cfg, "TOP_OF_RANGE_BLOCK_TF_LIST")).split(",") if x.strip())
    names = ("close",) + tuple(template.format(tf=tf) for tf in tfs for template in _TOR[1:])
    a, reason = _req(arrays, names, n)
    if a is None: return _na(n, reason)
    close = np.asarray(a["close"], float); extremes, breakouts = [], np.zeros(n, bool); threshold = float(_cfg(cfg, "TOP_OF_RANGE_BLOCK_THRESHOLD"))
    for tf in tfs:
        hi, lo = np.asarray(a[f"dc_high_{tf}"], float), np.asarray(a[f"dc_low_{tf}"], float); rng = hi - lo; valid = (rng > 0) & (close > 0)
        pos = np.divide(close - lo, rng, out=np.zeros(n), where=valid)
        extremes.append(valid & ((pos >= threshold) if is_long else (pos <= 1 - threshold)))
        prev = np.asarray(a[f"dc_high_prev_{tf}" if False else f"dc_high_{tf}_prev"], float) if is_long else np.asarray(a[f"dc_low_{tf}_prev"], float)
        breakouts |= valid & (prev > 0) & ((close > prev) if is_long else (close < prev))
    extreme = np.logical_and.reduce(extremes) if bool(_cfg(cfg, "TOP_OF_RANGE_BLOCK_REQUIRE_ALL")) else np.logical_or.reduce(extremes)
    return MaskResult(candidate & ~(extreme & ~breakouts), True)


def gr_entry_filter_mask(arrays, state, is_long, mode, cfg):
    n = _n(arrays, state); s, sr = _req(state, ("candidate_mask", "is_fresh_entry"), n)
    if s is None: return _na(n, sr)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "GR_FILTER_ALL_ENTRIES")): return MaskResult(candidate.copy(), True)
    tfs = ("3m", "15m", "1h", "4h", "D", "W") if mode == "crypto" else ("5m", "15m", "1h", "4h", "D", "W")
    # A declared GR filter must consume every timeframe that is actually
    # present in the frozen NPZ, but a missing optional timeframe (notably W on
    # short Tradier histories) must not erase the entire entry ledger.  The
    # previous all-or-nothing requirement converted valid WT/GR signals into
    # zero trades and made every switch delta invalid.
    available_tfs = tuple(tf for tf in tfs if all(template.format(tf=tf) in arrays for template in _GR))
    if not available_tfs:
        # No frozen GR arrays means this filter cannot be evaluated for this
        # symbol/timeframe.  Per the backtest contract, skip the unavailable
        # filter rather than rejecting every otherwise valid entry.
        return MaskResult(candidate.copy(), True, "GR timeframe unavailable; skipped")
    names = tuple(template.format(tf=tf) for tf in available_tfs for template in _GR)
    a, reason = _req(arrays, names, n)
    if a is None: return _na(n, reason)
    passed = np.zeros(n, np.int8); total_score = np.zeros(n, np.int16)
    min_ind = int(_cfg(cfg, "MTF_GR_MIN_IND")); total_min = int(_cfg(cfg, "GR_TOTAL_VOTE_SCORE_MIN", 0) or 0)
    invert = bool(_cfg(cfg, "MTF_GR_INVERT_DC_BB", False))
    for tf in available_tfs:
        wt1, wt2, rsi, mfi, dc, bb, rv, k, d, adx, macd, ha = (np.asarray(a[t.format(tf=tf)], float) for t in _GR)
        score = (((wt1 != 0) & (wt2 != 0) & ((wt1 > wt2) if is_long else (wt1 < wt2)))).astype(np.int8)
        score += (rsi > 0) & ((rsi > 50) if is_long else (rsi < 50)); score += (mfi > 0) & ((mfi > 50) if is_long else (mfi < 50))
        score += ((dc >= .65) if is_long else (dc <= .35)) if invert else ((dc < .65) if is_long else (dc > .35))
        score += ((bb >= .75) if is_long else (bb <= .25)) if invert else ((bb < .75) if is_long else (bb > .25))
        score += rv > 1; score += (k > 0) & ((k < 80) if is_long else (k > 20)); score += adx > 20; score += (macd > 0) if is_long else (macd < 0); score += (ha > 0) if is_long else (ha < 0); score += (k > 0) & (d > 0) & ((k > d) if is_long else (k < d))
        total_score += score
        passed += score >= min_ind
    allowed = (total_score >= total_min) if total_min > 0 else (passed >= int(_cfg(cfg, "MTF_GR_MIN_TFS")))
    applies = np.asarray(s["is_fresh_entry"], bool)
    return MaskResult(candidate & (~applies | allowed), True)


def vec_reentry_window_mask(arrays, state, is_long, cfg):
    n = _n(arrays, state); base_names = ("candidate_mask", "has_prior_exit", "bars_since_exit"); s, sr = _req(state, base_names, n)
    if s is None: return _na(n, sr)
    candidate = np.asarray(s["candidate_mask"], bool).copy(); prior = np.asarray(s["has_prior_exit"], bool); since = np.asarray(s["bars_since_exit"], int)
    if bool(_cfg(cfg, "VEC_REENTRY_REQUIRE_PRIOR_EXIT")): candidate &= prior & (since <= int(_cfg(cfg, "VEC_REENTRY_WINDOW_BARS")))
    if not bool(_cfg(cfg, "VEC_REENTRY_DC4_EXITPRICE_ENABLED")): return MaskResult(candidate, True)
    a, ar = _req(arrays, _VRE, n); extra, er = _req(state, ("last_exit_price", "previous_close"), n)
    if a is None or extra is None: return _na(n, ar or er)
    s = {**s, **extra}
    close, prev = np.asarray(a["close"], float), np.asarray(s["previous_close"], float); exit_px = np.asarray(s["last_exit_price"], float); hour = int(_cfg(cfg, "VEC_REENTRY_HOUR_BARS", 12))
    wt = (np.asarray(a["wt1_15m"], float) > np.asarray(a["wt2_15m"], float)) & (np.asarray(a["wt_velocity_15m"], float) >= 0) if is_long else (np.asarray(a["wt1_15m"], float) < np.asarray(a["wt2_15m"], float)) & (np.asarray(a["wt_velocity_15m"], float) <= 0)
    dc = np.asarray(a["dc_high4_3m" if is_long else "dc_low4_3m"], float); trigger = np.where(since <= hour, ((close > dc) & (prev <= dc)) if is_long else ((close < dc) & (prev >= dc)), ((close >= exit_px) & (prev < exit_px)) if is_long else ((close <= exit_px) & (prev > exit_px)))
    return MaskResult(candidate & prior & (exit_px > 0) & wt & trigger, True)


def wt15m_reentry_k_mask(arrays, state, is_long, cfg):
    n = _n(arrays, state); names = ("wt1_15m", "wt2_15m", "wt1_15m_prev", "wt2_15m_prev", "stoch_k_15m"); a, ar = _req(arrays, names, n); s, sr = _req(state, ("candidate_mask",), n)
    if a is None or s is None: return _na(n, ar or sr)
    w1,w2,w1p,w2p,k=(np.asarray(a[x],float) for x in names); limit=float(_cfg(cfg,"REENTRY_WT15M_K_MAX")); cross=((w1>w2)&(w1p<=w2p)&(k<limit)) if is_long else ((w1<w2)&(w1p>=w2p)&(k>100-limit)); return MaskResult(np.asarray(s["candidate_mask"],bool)&cross,True)


def channel_reentry_exit_mask(arrays, state, is_long, cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask","ever_outside"),n)
    if s is None:return ChannelResult(np.zeros(n,bool),np.zeros(n,bool),False,sr)
    ever=np.asarray(s["ever_outside"],bool).copy();candidate=np.asarray(s["candidate_mask"],bool)
    if not bool(_cfg(cfg,"CHANNEL_REENTRY_STOP_ENABLED")):return ChannelResult(np.zeros(n,bool),ever,True)
    a,ar=_req(arrays,("close","channel_level"),n)
    if a is None:return ChannelResult(np.zeros(n,bool),ever,False,ar)
    close,level=np.asarray(a["close"],float),np.asarray(a["channel_level"],float)
    outside=(close>level) if is_long else (close<level);exitmask=candidate&ever&~outside&(level>0);return ChannelResult(exitmask,ever|((level>0)&outside),True)


def k1m_reverse_reduce_mask(arrays,state,is_long,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask","gain_pct"),n)
    if s is None:return _na(n,sr)
    if not bool(_cfg(cfg,"K1M_EXTREME_REVERSE_ENABLED")):return MaskResult(np.zeros(n,bool),True)
    a,ar=_req(arrays,("stoch_k_1m","stoch_k_1m_prev"),n)
    if a is None:return _na(n,ar)
    k,kp=np.asarray(a["stoch_k_1m"],float),np.asarray(a["stoch_k_1m_prev"],float);turn=(k>float(_cfg(cfg,"K1M_EXTREME_HIGH",90)))&(k<kp) if is_long else (k<float(_cfg(cfg,"K1M_EXTREME_LOW",10)))&(k>kp);profit=(np.asarray(s["gain_pct"],float)>=0)|~bool(_cfg(cfg,"K1M_REVERSE_REQUIRES_PROFIT",True));return MaskResult(np.asarray(s["candidate_mask"],bool)&turn&profit,True)


_FAMILY_API={"LH_HL_FILTER":"lh_hl_filter_mask","TOP_OF_RANGE":"top_of_range_filter_mask","GR_ENTRY_FILTER":"gr_entry_filter_mask","VEC_REENTRY_WINDOW":"vec_reentry_window_mask","WT15M_REENTRY_FILTER":"wt15m_reentry_k_mask","CHANNEL_REENTRY_STOP":"channel_reentry_exit_mask","K1M_REVERSE_REDUCE":"k1m_reverse_reduce_mask"}
LIFECYCLE_FILTER_APIS={n:{x:_FAMILY_API[c.family] for x in c.lifecycle_filter_consumers} for n,c in FIELD_CONTRACTS.items()}

__all__=["FIELD_CONTRACTS","LIFECYCLE_FILTER_APIS","ChannelResult","FieldContract","MaskResult","channel_reentry_exit_mask","gr_entry_filter_mask","k1m_reverse_reduce_mask","lh_hl_filter_mask","top_of_range_filter_mask","vec_reentry_window_mask","wt15m_reentry_k_mask"]
