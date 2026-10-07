"""GR_CONSENSUS vec veto — faithful numpy port of golden_rule_htf._run_gate
as called by the tradier queue path.

LIVE SITE (tradier_manage.py:25264-25276, queue_trade_action — every OPEN /
AUGMENT / REENTRY / QUICK_OPEN / REVERSE / HEDGE_OPEN unless mandatory
reclaim or MTF_ARROW / LR_BAND reason):
    score_entry_htf(i, is_long, "tradier", MIN_TFS, MIN_IND, px, True)
    MIN_TFS = GOLDEN_RULE_HTF_MIN_TFS (live 1)
    MIN_IND = GOLDEN_RULE_MIN_IND (live 2)
    invert_dc_bb = True (breakout semantics: DC/BB extension = bullish)

Live _run_gate stages (golden_rule_htf.py:219-307), all mirrored here:
  1. vote mode when GR_TOTAL_VOTE_SCORE_MIN > 0 (live 0 -> off).
  2. activation when GOLDEN_RULE_REQUIRE_ACTIVATION (live True): >=1 TF of
     GOLDEN_RULE_ACTIVATION_TF_LIST (live [D,4h]) shows breakout
     (bb_pctb or dc_position crosses the extended threshold); else REFUSE.
  3. legacy MIN_TFS x MIN_IND over GOLDEN_RULE_ENTRY_TF_LIST
     (live [1h,15m,3m]) with the 11-indicator _ind_score (verbatim).

VEC GAP (why vec over-enters): the inline twin (v12:9487-9538) scores all
6 TFs with NO activation stage (GOLDEN_RULE_REQUIRE_ACTIVATION is read
nowhere in the engine) and ANDs only into _base_entry, so every downstream
OR block / rally / watchdog fire bypasses it. Lane-D: live REFUSED 743/743
via GOLDEN_RULE_CONSENSUS_BLOCK_ENTRY while vec opened 24-36 on GOOGL/AMZN.

PLACEMENT (staged): post-reason fire veto (live vetoes in queue_trade_action
with the reason known; MTF_ARROW / LR_BAND reasons exempt — enforced at the
veto site, not in this mask) + precompute beside _ec_choke_block. Returns
None when MIN_TFS <= 0 (live GATE_OFF passes everything).

Missing NPZ arrays: a TF whose indicator arrays are absent scores 0 on every
indicator (live: missing fields are skipped -> 0) and so never confirms.
No fail-open invention: absence == no confirmation, exactly like live.
"""
from __future__ import annotations

import numpy as np


_TRADIER_TFS = ["5m", "15m", "1h", "4h", "D", "W"]


def _lst(cfg, name, default):
    try:
        v = getattr(cfg, name, None)
        if v is None:
            return list(default)
        if isinstance(v, str):
            return [p.strip() for p in v.replace(",", " ").split() if p.strip()]
        return [str(t) for t in list(v)]
    except Exception:
        return list(default)


def _tf_score(npz, tf, n, is_long, dc_l, bb_l, safe):
    """Per-bar count of bullish indicators on one TF (live _ind_score verbatim,
    breakout semantics: invert_dc_bb=True)."""
    s = np.zeros(n, dtype=np.int32)
    wt1 = safe(npz, "wt1_%s" % tf, n, 0.0)
    wt2 = safe(npz, "wt2_%s" % tf, n, 0.0)
    present = (wt1 != 0) | (wt2 != 0)
    s += np.where(present, ((wt1 > wt2) if is_long else (wt1 < wt2)).astype(np.int32), 0)
    rsi = safe(npz, "rsi_%s" % tf, n, -1.0)
    s += np.where(rsi >= 0, ((rsi > 50) if is_long else (rsi < 50)).astype(np.int32), 0)
    mfi = safe(npz, "mfi_%s" % tf, n, -1.0)
    s += np.where(mfi >= 0, ((mfi > 50) if is_long else (mfi < 50)).astype(np.int32), 0)
    dc_pos = safe(npz, "dc_position_%s" % tf, n, -1.0)
    if ("dc_position_%s" % tf) not in npz:
        # Live fallback (_ind_score:98-103): derive from bands + price.
        dc_h = safe(npz, "dc_high_%s" % tf, n, 0.0)
        dc_l = safe(npz, "dc_low_%s" % tf, n, 0.0)
        px = safe(npz, "close", n, 0.0)
        ok = (dc_h > dc_l) & (dc_l > 0) & (px > 0)
        dc_pos = np.where(ok, (px - dc_l) / np.maximum(dc_h - dc_l, 1e-9), -1.0)
    dc_s = 1.0 - dc_l
    s += np.where(dc_pos >= 0, ((dc_pos >= dc_l) if is_long else (dc_pos <= dc_s)).astype(np.int32), 0)
    bb = safe(npz, "bb_pct_b_%s" % tf, n, -1.0)
    if ("bb_pct_b_%s" % tf) not in npz:
        # Live fallback (_ind_score:115-120).
        bb_u = safe(npz, "bb_upper_%s" % tf, n, 0.0)
        bb_l = safe(npz, "bb_lower_%s" % tf, n, 0.0)
        px = safe(npz, "close", n, 0.0)
        ok = (bb_u > bb_l) & (bb_l > 0) & (px > 0)
        bb = np.where(ok, (px - bb_l) / np.maximum(bb_u - bb_l, 1e-9), -1.0)
    bb_s = 1.0 - bb_l
    s += np.where(bb >= 0, ((bb >= bb_l) if is_long else (bb <= bb_s)).astype(np.int32), 0)
    rvol = safe(npz, "relative_volume_%s" % tf, n, -1.0)
    if ("relative_volume_%s" % tf) not in npz:
        rvol = safe(npz, "rel_vol_%s" % tf, n, -1.0)
    s += np.where(rvol >= 0, (rvol > 1.0).astype(np.int32), 0)
    k = safe(npz, "stoch_k_%s" % tf, n, -1.0)
    s += np.where(k >= 0, ((k < 80.0) if is_long else (k > 20.0)).astype(np.int32), 0)
    adx = safe(npz, "adx_%s" % tf, n, -1.0)
    s += np.where(adx > 0, (adx > 20.0).astype(np.int32), 0)
    mh = safe(npz, "macd_hist_%s" % tf, n, 0.0)
    s += np.where(mh != 0, ((mh > 0) if is_long else (mh < 0)).astype(np.int32), 0)
    ha = safe(npz, "ha_color_%s" % tf, n, 0.0)
    if ("ha_color_%s" % tf) not in npz:
        ha = safe(npz, "ha_%s" % tf, n, 0.0)
    s += np.where(ha != 0, ((ha > 0) if is_long else (ha < 0)).astype(np.int32), 0)
    d = safe(npz, "stoch_d_%s" % tf, n, -1.0)
    s += np.where((d >= 0) & (k >= 0), ((k > d) if is_long else (k < d)).astype(np.int32), 0)
    return s


def _activation_ok(npz, n, is_long, act_list, dc_l, bb_l, safe):
    """Live _check_activation verbatim (no DC/BB fallback there)."""
    bb_s = 1.0 - bb_l
    dc_s = 1.0 - dc_l
    any_tf = np.zeros(n, dtype=bool)
    for tf in act_list:
        bb = safe(npz, "bb_pct_b_%s" % tf, n, -1.0)
        dc = safe(npz, "dc_position_%s" % tf, n, -1.0)
        if is_long:
            hit = (bb >= bb_l) | (dc >= dc_l)
        else:
            hit = ((bb >= 0) & (bb <= bb_s)) | ((dc >= 0) & (dc <= dc_s))
        any_tf |= np.asarray(hit, dtype=bool)
    return any_tf


def block_mask(npz, n, is_long, cfg, close, safe):
    """Bool[n], True = live GR consensus would REFUSE. None = gate off."""
    try:
        min_tfs = int(float(getattr(cfg, "GOLDEN_RULE_HTF_MIN_TFS", getattr(cfg, "GOLDEN_RULE_MIN_TFS", 0)) or 0))
    except Exception:
        min_tfs = 0
    try:
        min_ind = int(float(getattr(cfg, "GOLDEN_RULE_MIN_IND", 0) or 0))
    except Exception:
        min_ind = 0
    if min_tfs <= 0:
        return None  # live GATE_OFF: passes everything
    try:
        dc_l = float(getattr(cfg, "GR_DC_EXTENDED_LONG", 0) or 0)
    except Exception:
        dc_l = 0.0
    if dc_l <= 0:
        dc_l = 0.65
    try:
        bb_l = float(getattr(cfg, "GR_BB_EXTENDED_LONG", 0) or 0)
    except Exception:
        bb_l = 0.0
    if bb_l <= 0:
        bb_l = 0.75
    try:
        vote_min = int(float(getattr(cfg, "GR_TOTAL_VOTE_SCORE_MIN", 0) or 0))
    except Exception:
        vote_min = 0
    if vote_min > 0:
        total = np.zeros(n, dtype=np.int32)
        for tf in _TRADIER_TFS:
            total += _tf_score(npz, tf, n, is_long, dc_l, bb_l, safe)
        return np.asarray(total < vote_min, dtype=bool)
    require_act = bool(getattr(cfg, "GOLDEN_RULE_REQUIRE_ACTIVATION", False))
    act_list = _lst(cfg, "GOLDEN_RULE_ACTIVATION_TF_LIST", ["D", "4h"])
    entry_list = _lst(cfg, "GOLDEN_RULE_ENTRY_TF_LIST", ["1h", "15m", "3m"])
    if require_act and act_list:
        act = _activation_ok(npz, n, is_long, act_list, dc_l, bb_l, safe)
        tfs = entry_list if entry_list else list(_TRADIER_TFS)
    else:
        act = np.ones(n, dtype=bool)
        tfs = list(_TRADIER_TFS)
    confirmed = np.zeros(n, dtype=np.int32)
    for tf in tfs:
        confirmed += (_tf_score(npz, tf, n, is_long, dc_l, bb_l, safe) >= min_ind).astype(np.int32)
    passes = act & (confirmed >= min_tfs)
    return np.asarray(~passes, dtype=bool)
