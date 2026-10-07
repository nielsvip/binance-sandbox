"""GR_HTF_DIRECT_ENTRY vec twin — faithful numpy port of golden_rule_htf.py.

LIVE SOURCE (ez_manage.py:40830-40869 → golden_rule_htf.score_entry_htf):
  score_entry_htf(i, is_long, "crypto", min_tfs, min_ind, px) with
  invert_dc_bb=False (default) → _run_gate → (passes, n_tfs, detail).
  Live FIRES on score = n_tfs * min_ind >= SCORE_MIN (conviction=score),
  size ×2 when score >= DOUBLE_SCORE. The `passes` bool is NOT the fire
  condition — the score threshold is (transcribed exactly).

Gate modes inside _run_gate (all three transcribed):
  1. activation (GOLDEN_RULE_REQUIRE_ACTIVATION=True, ACT_LIST=[D,4h]):
     ≥1 activation TF breaking out (bb_pct_b or dc_position past extended
     thresholds), else (False,0,NO_ACTIVATION) — entry TFs never scored.
  2. vote (GR_TOTAL_VOTE_SCORE_MIN>0, config 0=off): total votes vs min.
  3. legacy MIN_TFS×MIN_IND binary gate (config MIN_IND=2, MIN_TFS=1).

Per-indicator scoring (_ind_score, 11 indicators) transcribed operator-for-
operator incl. presence guards (absent indicator = skipped, never a NO vote):
WT(≠0-present) / RSI / MFI / DC(room-to-run, 0.65) / BB(room-to-run, 0.75) /
RVOL(>1) / K(L<80/S>20) / ADX(>20) / MH(sign) / HA(sign) / K-vs-D.
bb_pct_b_{tf} read DIRECTLY from NPZ (auto-tuned arrays verified S1);
dc_position likewise; band fallbacks mirror live.

TF lists from cfg (ENTRY_TF_LIST default [1h,15m,3m], ACT_LIST [D,4h]);
3m/1m terms DROPPED; 5m BINDS (W2-GR5M) (§40 floor — no arrays; USER 2026-10-01 IGNORE rule).
Thresholds: SCORE_MIN default 23.0, DOUBLE 34.0 (= config; live _psym_get
falls through per-sym→cat_side→config, call-site 12/18 are last resort).

Vec placement: entry SOURCE → fire mask ORed into final entry_sig;
double_mask returned for the sizing lane (size ×2).

DEAD-AT-DEFAULTS (verified by formula + smoke): live score =
confirmed_TFs × min_ind; with activation ON the entry list is [1h,15m,3m]
(3m dropped in vec → [1h,15m]) so max live score = 3 × MIN_IND(2) = 6 <<
SCORE_MIN 23. The gate can only fire via per-sym/cat_side SCORE_MIN override
(the config "max 66" comment assumes the 6-TF default list, not the 3-TF
entry list). The twin transcribes the formula exactly — including the
deadness. T2 post-cut MUST use an override (e.g. SCORE_MIN=4 → fires where
both 15m+1h confirm) to prove binding; at defaults twin-on ≡ twin-off
(honest-zero, threshold cause).

W2-GR5M ADDENDUM (staged): (a) the 5m leg binds — entry/act/default TF lists
keep 5m and _ind_votes scores it (absent keys = 0 votes until the sidecar
lands); (b) default TFs are MODE-aware (tradier 6 incl 5m, crypto 6 with 3m
dropped per IGNORE); (c) min_tfs -> live DIRECT hardcodes 1 on tradier,
HTF_MIN_TFS on crypto (was: HTF_MIN_TFS both — over-vetoed tradier at 0);
(d) gr_direct_exit_mask + gr_direct_scores added (opposite-direction score +
2-bar confirm; gain/age guard lives in-loop at the generic close).
"""
from __future__ import annotations

import numpy as np

# W2-GR5M: 5m BINDS (was dropped with 3m/1m). Absent *_5m keys read NaN
# via _g -> every leg skips -> honest 0 until the w2-data3m sidecar lands,
# when the leg contributes with no code change. 3m/1m stay dropped (IGNORE).
_NO_ARR = ("3m", "1m")
_TRADIER_TFS = ("5m", "15m", "1h", "4h", "D", "W")  # golden_rule_htf._TRADIER_TFS
_CRYPTO_TFS = ("3m", "15m", "1h", "4h", "D", "W")  # golden_rule_htf._CRYPTO_TFS
_DC_LVL = 0.65
_BB_LVL = 0.75
# 5m sidecar key family (lane SIDECAR_SCHEMA_5M.md): the predicate binds to
# exactly these keys at 15m-base bar alignment; any absent subset reads NaN.
SIDECAR_5M_KEYS = (
    "wt1_5m", "wt2_5m", "rsi_5m", "mfi_5m",
    "dc_position_5m", "dc_high_5m", "dc_low_5m",
    "bb_pct_b_5m", "bb_upper_5m", "bb_lower_5m",
    "relative_volume_5m", "stoch_k_5m", "stoch_d_5m",
    "adx_5m", "macd_hist_5m", "ha_color_5m",
)


def _g(npz, key, n):
    try:
        a = np.asarray(npz.get(key, None), dtype=float)
        if a is None or a.size != n:
            return np.full(n, np.nan)
        return a
    except Exception:
        return np.full(n, np.nan)


def _lst(cfg, name, default):
    try:
        v = getattr(cfg, name, default)
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return [str(t).strip() for t in (v or default)]
    except Exception:
        return list(default)


def _ind_votes(npz, n, tf, is_long, px, dc_thr, bb_thr):
    """Int[n] votes for one TF (presence-guarded, live _ind_score)."""
    votes = np.zeros(n, dtype=np.int16)
    wt1 = _g(npz, f"wt1_{tf}", n)
    wt2 = _g(npz, f"wt2_{tf}", n)
    wt1 = np.where(np.isfinite(wt1), wt1, 0.0)
    wt2 = np.where(np.isfinite(wt2), wt2, 0.0)
    present = (wt1 != 0) | (wt2 != 0)
    ok = (wt1 > wt2) if is_long else (wt1 < wt2)
    votes += (present & ok).astype(np.int16)
    rsi = _g(npz, f"rsi_{tf}", n)
    ok = (rsi > 50) if is_long else (rsi < 50)
    votes += ((rsi >= 0) & ok).astype(np.int16)
    mfi = _g(npz, f"mfi_{tf}", n)
    ok = (mfi > 50) if is_long else (mfi < 50)
    votes += ((mfi >= 0) & ok).astype(np.int16)
    dc = _g(npz, f"dc_position_{tf}", n)
    need = ~(dc >= 0)
    if np.any(need):
        h = _g(npz, f"dc_high_{tf}", n)
        lo = _g(npz, f"dc_low_{tf}", n)
        h = np.where(np.isfinite(h), h, 0.0)
        lo = np.where(np.isfinite(lo), lo, 0.0)
        ref = np.where(px > 0, px, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            fb = np.where((h > lo) & (lo > 0) & (ref > 0), (ref - lo) / np.where(h - lo != 0, h - lo, 1.0), -1.0)
        dc = np.where(need, fb, dc)
    dl = dc_thr if dc_thr > 0 else _DC_LVL
    ok = (dc < dl) if is_long else (dc > 1.0 - dl)
    votes += ((dc >= 0) & ok).astype(np.int16)
    bb = _g(npz, f"bb_pct_b_{tf}", n)
    need = ~(bb >= 0)
    if np.any(need):
        u = _g(npz, f"bb_upper_{tf}", n)
        lw = _g(npz, f"bb_lower_{tf}", n)
        u = np.where(np.isfinite(u), u, 0.0)
        lw = np.where(np.isfinite(lw), lw, 0.0)
        ref = np.where(px > 0, px, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            fb = np.where((u > lw) & (lw > 0) & (ref > 0), (ref - lw) / np.where(u - lw != 0, u - lw, 1.0), -1.0)
        bb = np.where(need, fb, bb)
    bl = bb_thr if bb_thr > 0 else _BB_LVL
    ok = (bb < bl) if is_long else (bb > 1.0 - bl)
    votes += ((bb >= 0) & ok).astype(np.int16)
    rvol = _g(npz, f"relative_volume_{tf}", n)
    votes += ((rvol >= 0) & (rvol > 1.0)).astype(np.int16)
    stk = _g(npz, f"stoch_k_{tf}", n)
    ok = (stk < 80.0) if is_long else (stk > 20.0)
    votes += ((stk >= 0) & ok).astype(np.int16)
    adx = _g(npz, f"adx_{tf}", n)
    votes += ((adx > 0) & (adx > 20.0)).astype(np.int16)
    mh = _g(npz, f"macd_hist_{tf}", n)
    ok = (mh > 0) if is_long else (mh < 0)
    votes += (np.isfinite(mh) & (mh != 0.0) & ok).astype(np.int16)
    try:
        ha_raw = np.asarray(npz.get(f"ha_color_{tf}", None))
        if ha_raw is not None and ha_raw.size == n:
            if ha_raw.dtype.kind in ("U", "S", "O"):
                s = np.array([str(x).lower() for x in ha_raw])
                ok = (s == "green") if is_long else (s == "red")
                votes += ok.astype(np.int16)
            else:
                ha = ha_raw.astype(float)
                ok = (ha > 0) if is_long else (ha < 0)
                votes += (np.isfinite(ha) & (ha != 0.0) & ok).astype(np.int16)
    except Exception:
        pass
    std = _g(npz, f"stoch_d_{tf}", n)
    ok = (stk > std) if is_long else (stk < std)
    votes += ((std >= 0) & (stk >= 0) & ok).astype(np.int16)
    return votes


def _resolve_gate(cfg):
    """Gate params shared by entry/exit/scores. Returns dict."""
    try:
        _mode = str(getattr(cfg, "MODE", "crypto")).lower()
    except Exception:
        _mode = "crypto"
    is_tradier = (_mode == "tradier")
    if is_tradier:
        # W2-GR5M: live DIRECT hardcodes min_tfs=1 on tradier
        # (tradier_manage GR_HTF_DIRECT_ENTRY/EXIT call sites).
        min_tfs = 1
    else:
        try:
            min_tfs = int(getattr(cfg, "GOLDEN_RULE_HTF_MIN_TFS", 1))
        except Exception:
            min_tfs = 1
    try:
        min_ind = int(getattr(cfg, "GOLDEN_RULE_MIN_IND", 2))
    except Exception:
        min_ind = 2
    try:
        dc_thr = float(getattr(cfg, "GR_DC_EXTENDED_LONG", 0) or 0)
    except Exception:
        dc_thr = 0.0
    try:
        bb_thr = float(getattr(cfg, "GR_BB_EXTENDED_LONG", 0) or 0)
    except Exception:
        bb_thr = 0.0
    try:
        vote_min = int(getattr(cfg, "GR_TOTAL_VOTE_SCORE_MIN", 0) or 0)
    except Exception:
        vote_min = 0
    req_act = bool(getattr(cfg, "GOLDEN_RULE_REQUIRE_ACTIVATION", False))
    act_list = [t for t in _lst(cfg, "GOLDEN_RULE_ACTIVATION_TF_LIST", ["D", "4h"]) if t not in _NO_ARR]
    entry_list = [t for t in _lst(cfg, "GOLDEN_RULE_ENTRY_TF_LIST", ["1h", "15m", "3m"]) if t not in _NO_ARR]
    # W2-GR5M: MODE-aware 6-TF default (was crypto-shaped both modes).
    _tfs6 = _TRADIER_TFS if is_tradier else _CRYPTO_TFS
    default_tfs = [t for t in _tfs6 if t not in _NO_ARR]
    return {"min_tfs": min_tfs, "min_ind": min_ind, "dc_thr": dc_thr,
            "bb_thr": bb_thr, "vote_min": vote_min, "req_act": req_act,
            "act_list": act_list, "entry_list": entry_list,
            "default_tfs": default_tfs}


def _gate_score_vec(npz, n, is_long, px, dc_thr, bb_thr, vote_min, min_tfs,
                    req_act, act_list, entry_list, default_tfs, min_ind):
    """score vec = confirmed(or total-vote) x min_ind, activation-zeroed.

    Extracted core of gr_direct_masks (behavior-preserving): activation
    split / vote mode / legacy gate / GATE_OFF exactly as before.
    """
    if req_act and act_list:
        bl = bb_thr if bb_thr > 0 else _BB_LVL
        dl = dc_thr if dc_thr > 0 else _DC_LVL
        act_ok = np.zeros(n, dtype=bool)
        for tf in act_list:
            bb = _g(npz, f"bb_pct_b_{tf}", n)
            dc = _g(npz, f"dc_position_{tf}", n)
            if is_long:
                bb_ok = bb >= bl
                dc_ok = dc >= dl
            else:
                bb_ok = (bb >= 0) & (bb <= 1.0 - bl)
                dc_ok = (dc >= 0) & (dc <= 1.0 - dl)
            act_ok |= np.asarray(bb_ok | dc_ok, dtype=bool)
        tfs = entry_list if entry_list else default_tfs
    else:
        act_ok = np.ones(n, dtype=bool)
        tfs = default_tfs
    if vote_min > 0:
        total = np.zeros(n, dtype=np.int32)
        for tf in tfs:
            total += _ind_votes(npz, n, tf, is_long, px, dc_thr, bb_thr).astype(np.int32)
        second = total
    else:
        if min_tfs <= 0:
            second = np.zeros(n, dtype=np.int32)  # live GATE_OFF: (True,0) → score 0
        else:
            confirmed = np.zeros(n, dtype=np.int32)
            for tf in tfs:
                confirmed += (_ind_votes(npz, n, tf, is_long, px, dc_thr, bb_thr) >= min_ind).astype(np.int32)
            second = confirmed
    score = second.astype(np.float64) * float(min_ind)
    score = np.where(act_ok, score, 0.0)
    return score


def gr_direct_masks(npz, n, is_long, cfg, close):
    """(fire, double) bool masks. (None, None) when disabled.

    fire = score >= SCORE_MIN with score = confirmed_tfs × min_ind (vote
    mode: total_votes × min_ind — live multiplies the returned 2nd tuple
    value regardless of mode). double = score >= DOUBLE_SCORE.
    W2-GR5M: same contract; 5m binds, MODE-aware defaults, tradier min_tfs=1.
    """
    if not bool(getattr(cfg, "GR_HTF_DIRECT_ENTRY_ENABLED", True)):
        return None, None
    g = _resolve_gate(cfg)
    px = np.asarray(close, dtype=float)
    score = _gate_score_vec(npz, n, is_long, px, g["dc_thr"], g["bb_thr"],
                            g["vote_min"], g["min_tfs"], g["req_act"],
                            g["act_list"], g["entry_list"], g["default_tfs"],
                            g["min_ind"])
    smin = float(getattr(cfg, "GR_HTF_DIRECT_ENTRY_SCORE_MIN", 23.0))
    dbl = float(getattr(cfg, "GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE", 34.0))
    fire = score >= smin
    double = score >= dbl
    return np.asarray(fire, dtype=bool), np.asarray(double, dtype=bool)


def gr_direct_scores(npz, n, is_long, cfg, close):
    """(entry_score_vec, exit_score_vec) float vecs for reason attribution.

    Raw DIRECT scores (same formula as the masks); zeros when the matching
    ENABLED flag is off. No thresholds applied.
    """
    g = _resolve_gate(cfg)
    px = np.asarray(close, dtype=float)
    entry = _gate_score_vec(npz, n, is_long, px, g["dc_thr"], g["bb_thr"],
                            g["vote_min"], g["min_tfs"], g["req_act"],
                            g["act_list"], g["entry_list"], g["default_tfs"],
                            g["min_ind"])
    opp = _gate_score_vec(npz, n, not is_long, px, g["dc_thr"], g["bb_thr"],
                          g["vote_min"], g["min_tfs"], g["req_act"],
                          g["act_list"], g["entry_list"], g["default_tfs"],
                          g["min_ind"])
    if not bool(getattr(cfg, "GR_HTF_DIRECT_ENTRY_ENABLED", True)):
        entry = np.zeros(n, dtype=np.float64)
    if not bool(getattr(cfg, "GR_HTF_DIRECT_EXIT_ENABLED", False)):
        opp = np.zeros(n, dtype=np.float64)
    return entry, opp


def gr_direct_exit_mask(npz, n, is_long, cfg, close):
    """Bool mask for GR_HTF_DIRECT_EXIT. None when disabled (entry convention).

    Live (tradier_manage ~19298): opposite-direction score >= EXIT_SCORE
    with 2-bar consecutive confirm (count>=2, reset on miss; bar 0 never
    fires — same as live's cold counter). Gain/age churn guard is position
    state -> staged in-loop at the generic close (sole-GR closes only);
    HTF-trend gate fail-open (no trend_* NPZ keys = live's missing branch);
    structural veto via the engine's existing global veto.
    """
    if not bool(getattr(cfg, "GR_HTF_DIRECT_EXIT_ENABLED", False)):
        return None
    try:
        smin = float(getattr(cfg, "GR_HTF_DIRECT_EXIT_SCORE", 22.0))
    except Exception:
        smin = 22.0
    g = _resolve_gate(cfg)
    px = np.asarray(close, dtype=float)
    score = _gate_score_vec(npz, n, not is_long, px, g["dc_thr"], g["bb_thr"],
                            g["vote_min"], g["min_tfs"], g["req_act"],
                            g["act_list"], g["entry_list"], g["default_tfs"],
                            g["min_ind"])
    ok = score >= smin
    out = np.zeros(n, dtype=bool)
    if n > 1:
        out[1:] = ok[1:] & ok[:-1]
    return out
