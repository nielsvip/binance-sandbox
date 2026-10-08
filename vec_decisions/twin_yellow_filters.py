"""Twin predicates for the 44 YELLOW *_FILTER_TF switches (2026-10-04 wiring mandate).

Self-contained: numpy + stdlib only. Importable without v12_quick_engine (which
needs 235 vec_decisions modules absent from minimal checkouts) and without
ez_manage / tradier_manage. Live hooks import this module (or inline the hook
code from hook_spec_yellow_filters.json) — see "HOOK SITES" below.

WHAT A *_FILTER_TF MEANS (generic mechanism, reconstructed from call sites):
  v12_quick_engine builds entry/reduce/exit masks from FILTER_TF knobs via
  generic_filter_tf.build_masks + FILTER_TF_MAP + _cond (v12:12116-12131), and
  live applies the same gates in check_entry_alignment (ez_manage:558-561),
  _shared_direct_entry_claim (tradier_manage:13376-13430) and the execute_now
  fresh-OPEN chokepoint (ez_manage:30077-30197). Each FILTER_TF takes
  OFF/D/4h/1h/15m (bool/float/enum for the 8 non-TF names). OFF (or the family
  fallback below) = inert -> honest 0 per BACKTEST_BIBLE section 14.1.
  Filters only ever REMOVE trades (entry AND / exit+reduce confirmation /
  trigger TF scoping) per section 19 — never caps, never synthetic distinctness.

TWO INERTNESS RULES (both honored here):
  R1 master-gating (v12:12116 [JSN2]): a FILTER_TF whose family master
     <FAMILY>_ENABLED is OFF does nothing, regardless of the TF value.
  R2 family-knob fallback: where the family already has a live TF knob (e.g.
     BB_FROZEN_STOP_TF=1h), resolve_filter_tf returns the FAMILY knob value
     when the FILTER sits at its own config default, so all-defaults behavior
     is bit-identical to current live. A non-default FILTER value selects that
     TF instead (section 14.2: expected to move the ledger).

PER-SWITCH VERDICTS (WIRED-BOTH-SPEC = twin code below + exact hook_spec
insertions on both sides; NEEDS-OPERATOR-DECISION = blocked, evidence cited):
  WIRED MOM3_FILTER_TF (live ez:296-308 tr:13389-13396; vec twin mom3_entry_gate)
  WIRED MOMENTUM_BREAKOUT_FILTER_TF (live ez:309-315 tr:13397-13403; vec twin)
  WIRED DC_BREAK_FILTER_TF (live ez:326-337 tr:13419-13426; vec twin)
  WIRED FAST_RISER_FILTER_TF crypto (live ez:52893-52960; vec signal twin;
     age/k_3m terms are live-only, documented; stocks: no family -> NOD)
  WIRED KINDERGARTEN_FILTER_TF (live crypto ez:350-408; vec twin kg_block_mask;
     stocks live twin + config_tradier field; KG scan precedent)
  WIRED GR_FILTER_ALL_ENTRIES (live crypto ez:30166-30197; stocks live twin;
     vec = full-tree vec_decisions/ported_entry.py:79, parity to verify)
  WIRED EMA_BLANKET_FILTER_FILTER_TF (master-gated, KG-style scan restrict;
     guarded swap around wave4 blanket calls both sides)
  WIRED DC_BREACH_REDUCE_FILTER_TF crypto-scope (live ez:35140-35330 hardcoded
     15m; OFF->15m fallback; family is crypto-only)
  WIRED MTF_DC_REJECT_FILTER_TF (live both: crypto latch ez:48174-48192,
     stocks lookback tr:11299-11321 via mtf_exit_timing; per-venue step twins;
     vec staged vec_paths.mtf_dc_reject + crypto-latch walk twin)
  WIRED FROZEN_STOP_FILTER_TF crypto-scope (live ez:47532-47563 TF knob 1h;
     resolver + walk twin; stocks base has no TF term -> out of scope)
  NOD  MANDATORY_REENTRY_WT_FILTER_TF_MODE crypto (stocks live tr:12198-12210 +
     full-tree vec exist; crypto has no mandatory fire path + master defaults
     True -> placement/behavior decision; vec port + agreement test shipped)
  NOD  WT_15M_BOUNCE_* x5 crypto-live (vec inline v12:9696-2795 real; stocks
     live tr:1297-1355 real; crypto has no WT-bounce family path, only
     `if False` hooks ez:59965-59979 -> placement decision; claim twin shipped)
  NOD  EMA_9_21_FILTER_FILTER_TF (vec v12:9614 fallback is unreachable dead
     weight; live reads _TFS not _FILTER_TF; semantic collision)
  NOD  NEWBORN_LOSS_KILL_FILTER_TF (3-way TF default mismatch: live VEL_TF 3m
     ez:47012 vs vec/filter 15m v12:11990; vel-against core shipped PENDING)
  NOD  EXIT_R1_R2_FILTER_TF (R1 is 3m-fixed ez:47080+, R2 has R2_TF_LIST
     ez:47631; single-filter scope ambiguous; R2 resolver shipped PENDING)
  NOD  BREAKOUT_RETEST_FILTER_TF (live RULE_A_RETEST real both venues but uses
     5 hard TF legs D/W/15m/1h/3m; which leg the filter selects is undefined)
  NOD  EXIT_TOP_FADE / BREAKEVEN_GAIN_EROSION / CIRCUIT_SHARPE_GATES /
     DC_MOMENTUM_BOTA_SCORER / EXIT_TIGHT_BREAKOUT_SCORER /
     EXIT_TO_REDUCE_ADAPTER / FIRST_OPEN_THROTTLE / GOLDEN_RULE_ENFORCE /
     GOLDEN_RULE_HTF_VOTE / GR_FILTER_VEC / GR_V5_STATE /
     LIVE_ONLY_SIGNALS_BATCH5 / MTF_ARMED_ENTRIES / NOLOSS_BYPASS_WT5OF5 /
     OPEN_INTENT_SIZE_GATES / PARTIAL_PROFIT_LOCK_V2 (zero base logic in any
     live path; only stub farms `_ = 1` / `_=_v` / dead _batch1 gate)
  NOD  DELTA_ENGINE / LIVE_ENTRY_ENGINE (live-only stateful engines, the
     BACKTEST_BIBLE 17.3 exclusion class; no vectorizable TF term)
  NOD  EMERGENCY_BRAKE (churn counters ez:32205+, no TF-able signal)
  NOD  NEWBORN_PROTECT (age-based ez:32413+, TF meaningless)
  NOD  MTF_ATR_TRAIL (live real both venues; vec staged
     vec_decisions/mtf_atr_trail_exit signature unverifiable here; live twin
     resolver shipped, vec half pending staged-API confirmation)
  NOD  PEAK_GIVEBACK_BE_EROSION (stocks base is gain-based tr:19280-19312, no
     TF term; only dc_low4_5m sub-branch is TF-able, hardcoded 5m)
  NOD  FH_MOMENTUM (separate async loop ez:54502, not a gate)
  NOD  HAIKU_WINNER (overseer-based, live UNGATED per v12:6052; no TF term)

HOOK SITES (exact insertions live in hook_spec_yellow_filters.json):
  vec : BEFORE `# 2026-09-30 PORTED-SWITCH DISPATCHER` (v12:12268) for masks;
        walk hooks at `for i in range(n):` (v12:12823) state init + the exit
        chain (v12:13706) + reduce gate (v12:13804) + exit gate (v12:13905).
  live: ez_manage._parity_filter_tf_gates (no change — already wired),
        execute_now chokepoints (GR/KG/blanket twins), process_position exit
        blocks (MTF/frozen/breach resolvers); tradier _shared_direct_entry_claim
        (no change) + process_position twins. All live hooks per-sym
        (_psym_get / _cfg), fail-open, default-inert.
"""

from __future__ import annotations

import numpy as np

VALID_TFS = ("15m", "1h", "4h", "D")
LEGACY_KG_SCAN = ("D", "4h", "1h", "15m")

REGISTRY = {
    "MOM3_FILTER_TF": {"target": "entry", "kind": "mom3", "master": None, "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "MOMENTUM_BREAKOUT_FILTER_TF": {"target": "entry", "kind": "momentum_breakout", "master": None, "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "DC_BREAK_FILTER_TF": {"target": "entry", "kind": "dc_break", "master": None, "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "FAST_RISER_FILTER_TF": {"target": "reduce", "kind": "fast_riser", "master": "FAST_RISER_DOUBLE_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "KINDERGARTEN_FILTER_TF": {"target": "entry", "kind": "kindergarten", "master": "KINDERGARTEN_EMA_GATE_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "WIRED-BOTH-SPEC"},
    "GR_FILTER_ALL_ENTRIES": {"target": "entry", "kind": "gr_all_entries", "master": None, "family_tf_knob": None, "filter_default": False, "verdict": "WIRED-BOTH-SPEC"},
    "EMA_BLANKET_FILTER_FILTER_TF": {"target": "entry", "kind": "ema_blanket", "master": "EMA_BLANKET_FILTER_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "DC_BREACH_REDUCE_FILTER_TF": {"target": "reduce", "kind": "dc_breach", "master": "EXIT_DC_BREACH_REDUCE_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "WIRED-BOTH-SPEC"},
    "MTF_DC_REJECT_FILTER_TF": {"target": "exit", "kind": "mtf_dc_reject", "master": "MTF_DC_REJECT_EXIT_ENABLED", "family_tf_knob": "MTF_DC_REJECT_EXIT_TF", "filter_default": "15m", "verdict": "WIRED-BOTH-SPEC"},
    "FROZEN_STOP_FILTER_TF": {"target": "exit", "kind": "frozen_stop", "master": "BB_FROZEN_STOP_ENABLED", "family_tf_knob": "BB_FROZEN_STOP_TF", "filter_default": "15m", "verdict": "WIRED-BOTH-SPEC"},
    "MANDATORY_REENTRY_WT_FILTER_TF_MODE": {"target": "reentry", "kind": "mandatory_wt", "master": "MANDATORY_REENTRY_WT_FILTER_ENABLED", "family_tf_knob": None, "filter_default": "15m_only", "verdict": "WIRED-STOCKS-ONLY"},
    "WT_15M_BOUNCE_BB_MAX": {"target": "entry", "kind": "wt_bounce", "master": "WT_15M_BOUNCE_OPEN_ENABLED", "family_tf_knob": None, "filter_default": 0.95, "verdict": "NEEDS-OPERATOR-DECISION"},
    "WT_15M_BOUNCE_BB_MIN": {"target": "entry", "kind": "wt_bounce", "master": "WT_15M_BOUNCE_OPEN_ENABLED", "family_tf_knob": None, "filter_default": 0.05, "verdict": "NEEDS-OPERATOR-DECISION"},
    "WT_15M_BOUNCE_HIGH_1H_GT_PREV": {"target": "entry", "kind": "wt_bounce", "master": "WT_15M_BOUNCE_OPEN_ENABLED", "family_tf_knob": None, "filter_default": False, "verdict": "NEEDS-OPERATOR-DECISION"},
    "WT_15M_BOUNCE_LOW_1H_GT_PREV": {"target": "entry", "kind": "wt_bounce", "master": "WT_15M_BOUNCE_OPEN_ENABLED", "family_tf_knob": None, "filter_default": False, "verdict": "NEEDS-OPERATOR-DECISION"},
    "WT_15M_BOUNCE_REL_VOL_GT_1": {"target": "entry", "kind": "wt_bounce", "master": "WT_15M_BOUNCE_OPEN_ENABLED", "family_tf_knob": None, "filter_default": False, "verdict": "NEEDS-OPERATOR-DECISION"},
    "EMA_9_21_FILTER_FILTER_TF": {"target": "entry", "kind": "ema921", "master": "EMA_9_21_FILTER_ENABLED", "family_tf_knob": "EMA_9_21_FILTER_TFS", "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "NEWBORN_LOSS_KILL_FILTER_TF": {"target": "exit", "kind": "newborn_vel", "master": "NEWBORN_LOSS_KILL_ENABLED", "family_tf_knob": "NEWBORN_LOSS_KILL_VEL_TF", "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "EXIT_R1_R2_FILTER_TF": {"target": "exit", "kind": "r1_r2", "master": None, "family_tf_knob": "R2_TF_LIST", "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "BREAKOUT_RETEST_FILTER_TF": {"target": "entry", "kind": "retest", "master": "BREAKOUT_RETEST_ARMED_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "NEEDS-OPERATOR-DECISION"},
    "EXIT_TOP_FADE_FILTER_TF": {"target": "exit", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "OFF", "verdict": "NEEDS-OPERATOR-DECISION"},
    "BREAKEVEN_GAIN_EROSION_FILTER_TF": {"target": "reduce", "kind": "none", "master": "BREAKEVEN_GAIN_EROSION_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "NEEDS-OPERATOR-DECISION"},
    "CIRCUIT_SHARPE_GATES_FILTER_TF": {"target": "entry", "kind": "wt_cross_side", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "WIRED-VEC-MAP"},
    "DC_MOMENTUM_BOTA_SCORER_FILTER_TF": {"target": "entry", "kind": "wt_cross_side", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "WIRED-VEC-MAP"},
    "EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "EXIT_TO_REDUCE_ADAPTER_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "FIRST_OPEN_THROTTLE_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "GOLDEN_RULE_ENFORCE_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "GOLDEN_RULE_HTF_VOTE_FILTER_TF": {"target": "entry", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "GR_FILTER_VEC_FILTER_TF": {"target": "entry", "kind": "none", "master": "GR_FILTER_VEC_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "GR_V5_STATE_FILTER_TF": {"target": "entry", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF": {"target": "entry", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "MTF_ARMED_ENTRIES_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "NOLOSS_BYPASS_WT5OF5_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "OPEN_INTENT_SIZE_GATES_FILTER_TF": {"target": "filter", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "PARTIAL_PROFIT_LOCK_V2_FILTER_TF": {"target": "reduce", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "DELTA_ENGINE_FILTER_TF": {"target": "entry", "kind": "wt_cross_side", "master": "DELTA_ENGINE_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "WIRED-VEC-MAP"},
    "LIVE_ENTRY_ENGINE_FILTER_TF": {"target": "entry", "kind": "none", "master": "LIVE_ENTRY_ENGINE_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "EMERGENCY_BRAKE_FILTER_TF": {"target": "entry", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "NEWBORN_PROTECT_FILTER_TF": {"target": "entry", "kind": "none", "master": None, "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "MTF_ATR_TRAIL_FILTER_TF": {"target": "entry", "kind": "mtf_atr_trail", "master": "MTF_ATR_TRAIL_ENABLED", "family_tf_knob": "MTF_ATR_TRAIL_TF", "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
    "PEAK_GIVEBACK_BE_EROSION_FILTER_TF": {"target": "filter", "kind": "none", "master": "PEAK_GIVEBACK_PROTECTION_ENABLED", "family_tf_knob": None, "filter_default": "OFF", "verdict": "NEEDS-OPERATOR-DECISION"},
    "FH_MOMENTUM_FILTER_TF": {"target": "entry", "kind": "fh_momentum_dc_leg", "master": "FH_MOMENTUM_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "WIRED-VEC-INLINE"},
    "HAIKU_WINNER_FILTER_TF": {"target": "filter", "kind": "none", "master": "HAIKU_WINNER_ENABLED", "family_tf_knob": None, "filter_default": "15m", "verdict": "NEEDS-OPERATOR-DECISION"},
}


def normalize_tf(raw):
    """' 15m ' -> '15m'; None/'' -> ''. Never raises."""
    try:
        return str(raw if raw is not None else "").strip()
    except Exception:
        return ""


def resolve_filter_tf(raw, family_raw=None, filter_default="OFF"):
    """Effective TF for a FILTER_TF knob. Returns None when inert.

    OFF (any case) or '' is always inert. When the family owns a live TF knob
    (family_raw given) and the filter sits exactly at its config default, the
    family knob wins (R2 family-knob fallback -> all-defaults == live today).
    Otherwise the filter value selects the TF. Unknown TF spellings pass
    through untouched (the predicate then fail-opens on missing keys, exactly
    like live `indicators.get(missing)` -> None -> allow).
    """
    r = normalize_tf(raw)
    if r == "" or r.upper() == "OFF":
        return None
    if family_raw is not None and r == str(filter_default):
        f = normalize_tf(family_raw)
        if f == "" or f.upper() == "OFF":
            return None
        return f
    return r


def safe_get(npz, key, n, default=0.0):
    """Standalone mirror of v12_quick_engine._safe (v12:4007-4011)."""
    try:
        v = npz.get(key)
    except Exception:
        v = None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return v.astype(np.float64)
        except Exception:
            pass
    return np.full(n, default, dtype=np.float64)


def safe_get_bool(npz, key, n):
    """Standalone mirror of v12_quick_engine._safeb (v12:4014-4018)."""
    try:
        v = npz.get(key)
    except Exception:
        v = None
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        try:
            return v.astype(bool)
        except Exception:
            pass
    return np.zeros(n, dtype=bool)


def _cfg_get(cfg, key, default):
    if callable(cfg):
        try:
            return cfg(key, default)
        except Exception:
            return default
    try:
        return getattr(cfg, key, default)
    except Exception:
        return default


def _fnum(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f


# ── MOM3 / MOMENTUM_BREAKOUT / DC_BREAK entry gates ──────────────────────────
# Live twins: ez_manage._parity_filter_tf_gates (ez:294-345, called from
# check_entry_alignment ez:558-561) and the tradier mirror in
# _shared_direct_entry_claim (tr:13381-13430). Formula notes:
#  - MOM3/MOMENTUM share m3 = (price - close_3bar_TF) / close_3bar_TF * 100
#    (live guards c3>0 and price>0 -> fail open). MOM3 compares against
#    MOM3_LONG/SHORT_THRESHOLD (-1.0/+1.0 all configs); MOMENTUM requires the
#    move to favor the side (mv>0 long / mv<0 short).
#  - DC_BREAK requires price beyond the PRIOR-bar channel edge:
#    long price > dc_high_TF_prev / short price < dc_low_TF_prev (fail open
#    when the level is missing/0).
# Vec form uses the bar close as price (== live current_price at bar close)
# and NPZ close_3bar_{TF} (precompute rolling-3 mean, same producer as the live
# close_3bar keys per live_parity_keys.py:13,62).

def mom3_entry_gate(npz, n, is_long, cfg, close, safe=None):
    tf = resolve_filter_tf(_cfg_get(cfg, "MOM3_FILTER_TF", "OFF"))
    if tf is None:
        return None
    s = safe or safe_get
    c3 = s(npz, "close_3bar_%s" % tf, n, 0.0)
    px = np.asarray(close, dtype=np.float64)
    have = (c3 > 0) & (px > 0)
    m3 = np.where(have, (px - c3) / np.maximum(c3, 1e-9) * 100.0, 0.0)
    if is_long:
        thr = float(_cfg_get(cfg, "MOM3_LONG_THRESHOLD", -1.0))
        ok = m3 < thr
    else:
        thr = float(_cfg_get(cfg, "MOM3_SHORT_THRESHOLD", 1.0))
        ok = m3 > thr
    return np.where(have, ok, True)


def momentum_breakout_gate(npz, n, is_long, cfg, close, safe=None):
    tf = resolve_filter_tf(_cfg_get(cfg, "MOMENTUM_BREAKOUT_FILTER_TF", "OFF"))
    if tf is None:
        return None
    s = safe or safe_get
    c3 = s(npz, "close_3bar_%s" % tf, n, 0.0)
    px = np.asarray(close, dtype=np.float64)
    have = (c3 > 0) & (px > 0)
    mv = np.where(have, (px - c3) / np.maximum(c3, 1e-9) * 100.0, 0.0)
    ok = (mv > 0) if is_long else (mv < 0)
    return np.where(have, ok, True)


def dc_break_entry_gate(npz, n, is_long, cfg, close, safe=None):
    tf = resolve_filter_tf(_cfg_get(cfg, "DC_BREAK_FILTER_TF", "OFF"))
    if tf is None:
        return None
    s = safe or safe_get
    lvl = s(npz, ("dc_high_%s_prev" % tf) if is_long else ("dc_low_%s_prev" % tf), n, 0.0)
    px = np.asarray(close, dtype=np.float64)
    have = (lvl != 0) & (px > 0)
    ok = (px > lvl) if is_long else (px < lvl)
    return np.where(have, ok, True)


def mom3_blocks_live(indicators, is_long, price, tf_raw, thr_long=-1.0, thr_short=1.0):
    """Scalar port of the live MOM3 veto (ez:300-308). True = BLOCKED."""
    tf = resolve_filter_tf(tf_raw)
    if tf is None or not (price > 0):
        return False
    try:
        c3 = float(indicators.get("close_3bar_%s" % tf, 0) or 0)
    except (TypeError, ValueError):
        return False
    if not (c3 > 0):
        return False
    m3 = (price - c3) / c3 * 100.0
    ok = (m3 < thr_long) if is_long else (m3 > thr_short)
    return not ok


def momentum_breakout_blocks_live(indicators, is_long, price, tf_raw):
    tf = resolve_filter_tf(tf_raw)
    if tf is None or not (price > 0):
        return False
    try:
        c3 = float(indicators.get("close_3bar_%s" % tf, 0) or 0)
    except (TypeError, ValueError):
        return False
    if not (c3 > 0):
        return False
    mv = (price - c3) / c3 * 100.0
    ok = (mv > 0) if is_long else (mv < 0)
    return not ok


def dc_break_blocks_live(indicators, is_long, price, tf_raw):
    tf = resolve_filter_tf(tf_raw)
    if tf is None or not (price > 0):
        return False
    try:
        lvl = indicators.get(("dc_high_%s_prev" % tf) if is_long else ("dc_low_%s_prev" % tf))
    except Exception:
        return False
    if not lvl:
        return False
    try:
        lv = float(lvl)
    except (TypeError, ValueError):
        return False
    ok = (price > lv) if is_long else (price < lv)
    return not ok


# ── KINDERGARTEN EMA gate ────────────────────────────────────────────────────
# Exact port of ez_manage._kindergarten_ema_gate (ez:350-408), the live crypto
# hard block on technical entries (check_entry_alignment ez:555-557) and on
# every fresh OPEN in execute_now (ez:30109-30117, per-sym scan_tf via
# _psym_get KINDERGARTEN_FILTER_TF). Scan rule (FLT2): FILTER_TF in
# D/4h/1h/15m restricts the scan to that TF, anything else (incl. OFF) runs the
# legacy D,4h,1h,15m scan. First TF carrying 200-data decides the bar/tick
# (pass AND block both return); TFs without 200-data fall back to the 50-cross
# alone; zero price allows; anything unparseable fails open.

def kg_scan_tfs(tf_raw):
    t = normalize_tf(tf_raw)
    if t in ("D", "4h", "1h", "15m"):
        return (t,)
    return LEGACY_KG_SCAN


def _kg_key_present(npz, key, n):
    try:
        v = npz.get(key)
    except Exception:
        return False
    return isinstance(v, np.ndarray) and len(v) == n


def kg_blocks_live(indicators, is_long, price, tf_raw, enabled=True):
    """Scalar port of _kindergarten_ema_gate. Returns (blocked, reason)."""
    try:
        if not bool(enabled):
            return False, "KG_OFF"
        try:
            px = float(price) if price else float(indicators.get("close") or indicators.get("close_3m") or 0)
        except (TypeError, ValueError):
            return False, "KG_ERR_price"
        for tf in kg_scan_tfs(tf_raw):
            ema = indicators.get("ema_200_%s" % tf)
            sma = indicators.get("sma_200_%s" % tf)
            ab = indicators.get("ema_9_above_21_%s" % tf)
            ema21 = indicators.get("ema_21_%s" % tf)
            sma50 = indicators.get("sma_50_%s" % tf)
            ema50 = indicators.get("ema_50_%s" % tf)
            cross50 = None
            if ema21 is not None and sma50 is not None:
                try:
                    cross50 = float(ema21) > float(sma50) if float(sma50) != 0 else None
                except (TypeError, ValueError):
                    cross50 = None
            elif ema50 is not None and sma50 is not None:
                try:
                    cross50 = float(ema50) > float(sma50) if float(sma50) != 0 else None
                except (TypeError, ValueError):
                    cross50 = None
            if ema is None or sma is None:
                if cross50 is not None:
                    if is_long and not cross50:
                        return True, "KG_LONG_BLOCK_50_tf=%s" % tf
                    if (not is_long) and cross50:
                        return True, "KG_SHORT_BLOCK_50_tf=%s" % tf
                continue
            try:
                ema_f = float(ema)
                sma_f = float(sma)
            except (TypeError, ValueError):
                continue
            if ema_f == 0 or sma_f == 0:
                continue
            if px == 0:
                return False, "KG_NO_PRICE_tf=%s" % tf
            ab_b = bool(ab) if ab is not None else None
            if is_long:
                ok = (px > ema_f) and (px > sma_f) and (ab_b if ab_b is not None else True) and (cross50 if cross50 is not None else True)
                if not ok:
                    return True, "KG_LONG_BLOCK_tf=%s" % tf
                return False, "KG_LONG_OK_tf=%s" % tf
            ok = (px < ema_f) and (px < sma_f) and ((not ab_b) if ab_b is not None else True) and ((not cross50) if cross50 is not None else True)
            if not ok:
                return True, "KG_SHORT_BLOCK_tf=%s" % tf
            return False, "KG_SHORT_OK_tf=%s" % tf
        return False, "KG_NO_HTF_DATA_ALLOW"
    except Exception:
        return False, "KG_ERR"


def kg_block_mask(npz, n, is_long, cfg, close, safe=None):
    """Vec twin of _kindergarten_ema_gate. True = BLOCK. None when master off."""
    if not bool(_cfg_get(cfg, "KINDERGARTEN_EMA_GATE_ENABLED", False)):
        return None
    tf_raw = _cfg_get(cfg, "KINDERGARTEN_FILTER_TF", "OFF")
    s = safe or safe_get
    px = np.asarray(close, dtype=np.float64)
    blocked = np.zeros(n, dtype=bool)
    undecided = np.ones(n, dtype=bool)
    for tf in kg_scan_tfs(tf_raw):
        if not bool(np.any(undecided)):
            break
        ema_k = "ema_200_%s" % tf
        sma_k = "sma_200_%s" % tf
        if not (_kg_key_present(npz, ema_k, n) and _kg_key_present(npz, sma_k, n)):
            cross50 = _kg_cross50_vec(npz, n, tf, s)
            if cross50 is not None:
                if is_long:
                    fire = undecided & (cross50 == False)  # noqa: E712 (None-safe: None rows never fire)
                else:
                    fire = undecided & (cross50 == True)  # noqa: E712
                blocked = blocked | fire
                undecided = undecided & ~fire
            continue
        ema = s(npz, ema_k, n, 0.0)
        sma = s(npz, sma_k, n, 0.0)
        has200 = undecided & (ema != 0) & (sma != 0)
        if not bool(np.any(has200)):
            continue
        ab_k = "ema_9_above_21_%s" % tf
        if _kg_key_present(npz, ab_k, n):
            ab = np.asarray(npz.get(ab_k), dtype=np.float64)
            ab_b = ab.astype(bool)
            ab_none = np.zeros(n, dtype=bool)
        else:
            ab_b = np.zeros(n, dtype=bool)
            ab_none = np.ones(n, dtype=bool)
        cross50 = _kg_cross50_vec(npz, n, tf, s)
        noprice = has200 & (px == 0)
        decide = has200 & ~noprice
        if is_long:
            ok200 = (px > ema) & (px > sma)
            ok9 = np.where(ab_none, True, ab_b)
            ok = ok200 & ok9
            if cross50 is not None:
                ok = ok & np.where(cross50 == True, True, np.where(cross50 == False, False, True))  # noqa: E712
            blocked = blocked | (decide & ~ok)
        else:
            ok200 = (px < ema) & (px < sma)
            ok9 = np.where(ab_none, True, ~ab_b)
            ok = ok200 & ok9
            if cross50 is not None:
                ok = ok & np.where(cross50 == True, False, np.where(cross50 == False, True, True))  # noqa: E712
            blocked = blocked | (decide & ~ok)
        undecided = undecided & ~has200
    return blocked


def _kg_cross50_vec(npz, n, tf, s):
    """Tri-state cross50 array (True/False/None) mirroring the live cascade."""
    e21 = "ema_21_%s" % tf
    s50 = "sma_50_%s" % tf
    e50 = "ema_50_%s" % tf
    if _kg_key_present(npz, e21, n) and _kg_key_present(npz, s50, n):
        a = s(npz, e21, n, 0.0)
        b = s(npz, s50, n, 0.0)
    elif _kg_key_present(npz, e50, n) and _kg_key_present(npz, s50, n):
        a = s(npz, e50, n, 0.0)
        b = s(npz, s50, n, 0.0)
    else:
        return None
    out = np.empty(n, dtype=object)
    out[:] = None
    try:
        nz = b != 0
        out[nz] = (a[nz] > b[nz])
    except Exception:
        return None
    return out


# ── FAST_RISER quick-jump ────────────────────────────────────────────────────
# Live crypto: ez_manage process_position reduce path (ez:52893-52960).
# FAST_RISER_FILTER_TF selects the TF of the quick-jump source series; OFF
# keeps the legacy 3m series (close_3m_prev/low_3m/low_3m_prev/ha_3m). Live
# price-jump core (vectorizable): jump = (price - close_prev_TF)/close_prev_TF,
# LONG fires when price > close_prev and |jump| >= 0.002 and ha_TF == green and
# low_TF < low_prev_TF (SHORT mirror). Live-only terms, NOT in the vec signal
# (documented parity gap, BACKTEST_BIBLE 17.3 class): NEW_POSITION age
# protection (position_age_seconds < NEW_POSITION_MIN_AGE_SECONDS), the
# k_3m/d_3m confirmation ((long k<60 and k>d) / (short k>40 and k<d)), and the
# master chain ENABLE_FAST_RISER_REDUCE & FAST_RISER_DOUBLE_ENABLED &
# not ABLATION_DISABLE_FAST_RISER. NPZ ha_{TF} is int8 (+1/-1/0, precompute
# full-HA series) while live ha is "green"/"red" — the twin maps green->>0,
# red-><0. NPZ has no ha_{TF}_prev; live reads only current ha (no prev needed).

def _fr_series_keys(tf_or_none):
    if tf_or_none is None:
        return ("close_3m_prev", "low_3m", "low_3m_prev", "ha_3m")
    return ("close_%s_prev" % tf_or_none, "low_%s" % tf_or_none, "low_%s_prev" % tf_or_none, "ha_%s" % tf_or_none)


def fast_riser_signal(npz, n, is_long, cfg, close, safe=None):
    """Vec twin of the live quick-jump core. None only if master chain off."""
    if not bool(_cfg_get(cfg, "ENABLE_FAST_RISER_REDUCE", False)):
        return None
    if not bool(_cfg_get(cfg, "FAST_RISER_DOUBLE_ENABLED", False)):
        return None
    if bool(_cfg_get(cfg, "ABLATION_DISABLE_FAST_RISER", False)):
        return None
    tf = resolve_filter_tf(_cfg_get(cfg, "FAST_RISER_FILTER_TF", "OFF"))
    s = safe or safe_get
    ck, lk, lpk, hk = _fr_series_keys(tf)
    close_prev = s(npz, ck, n, 0.0)
    low = s(npz, lk, n, 0.0)
    low_prev = s(npz, lpk, n, 0.0)
    ha_raw = npz.get(hk) if isinstance(npz, dict) else None
    if isinstance(ha_raw, np.ndarray) and len(ha_raw) == n:
        try:
            ha = ha_raw.astype(np.float64)
        except Exception:
            ha = np.zeros(n, dtype=np.float64)
    else:
        ha = np.zeros(n, dtype=np.float64)
    px = np.asarray(close, dtype=np.float64)
    have = (close_prev > 0) & (low > 0) & (low_prev > 0)
    jump = np.where(have, (px - close_prev) / np.maximum(close_prev, 1e-9), 0.0)
    if is_long:
        fire = (px > close_prev) & (np.abs(jump) >= 0.002) & (ha > 0) & (low < low_prev)
    else:
        fire = (px < close_prev) & (np.abs(jump) >= 0.002) & (ha < 0) & (low > low_prev)
    return have & fire


def fast_riser_jump_live(indicators_get, is_long, price, tf_raw):
    """Scalar quick-jump core (shared by the crypto block and stocks twin)."""
    tf = resolve_filter_tf(tf_raw)
    ck, lk, lpk, hk = _fr_series_keys(tf)
    try:
        cp = _fnum(indicators_get(ck, 0.0))
        lo = _fnum(indicators_get(lk, 0.0))
        lp = _fnum(indicators_get(lpk, 0.0))
        ha_raw = indicators_get(hk, 0)
    except Exception:
        return False
    if isinstance(ha_raw, str):
        ha = 1.0 if ha_raw == "green" else (-1.0 if ha_raw == "red" else 0.0)
    else:
        ha = _fnum(ha_raw, 0.0)
    if not (cp > 0 and lo > 0 and lp > 0):
        return False
    jump = (price - cp) / cp if cp > 0 else 0.0
    if is_long:
        return bool(price > cp and abs(jump) >= 0.002 and ha > 0 and lo < lp)
    return bool(price < cp and abs(jump) >= 0.002 and ha < 0 and lo > lp)


# ── EMA_BLANKET scan-restricted agree gate ────────────────────────────────────
# Live twins: fresh-OPEN gates in execute_now (crypto ez:30077-30097, stocks
# tr:14129-14145) calling the shared wave4 blanket pass over the FIXED 4-TF
# scan (15m/1h/4h/D, agree >= MIN_TFS, default min 3). Neither side reads
# EMA_BLANKET_FILTER_FILTER_TF today (QuickConfig default OFF, configs 15m).
# FILTER semantic (KG precedent, ez:356): a non-OFF FILTER_TF restricts the
# scan to that single TF; OFF keeps the legacy 4-TF scan. The hook_spec guards
# the swap: single-TF mode calls the twins below, legacy mode keeps the wave4
# pass untouched (zero behavior change at defaults; master OFF-gated too).
# Agree definition (from the live BLOCKED log text "ema9>21 agrees"): LONG
# needs ema_9_above_21_TF true, SHORT needs it false.

def blanket_scan_tfs(tf_raw):
    t = normalize_tf(tf_raw)
    if t in ("D", "4h", "1h", "15m"):
        return (t,)
    return ("15m", "1h", "4h", "D")


def ema_blanket_pass_vec(npz, n, is_long, cfg, safe=None):
    """Vec twin, single-TF-restricted. None when master off or FILTER OFF."""
    if not bool(_cfg_get(cfg, "EMA_BLANKET_FILTER_ENABLED", False)):
        return None
    tf = resolve_filter_tf(_cfg_get(cfg, "EMA_BLANKET_FILTER_FILTER_TF", "OFF"))
    if tf is None:
        return None
    min_tfs = int(_cfg_get(cfg, "EMA_BLANKET_FILTER_MIN_TFS", 3) or 3)
    s = safe or safe_get
    ab = s(npz, "ema_9_above_21_%s" % tf, n, -1.0)
    have = ab >= 0
    agree = (ab > 0.5) if is_long else (ab < 0.5)
    n_agree = np.where(have & agree, 1, 0)
    return np.where(have, n_agree >= min_tfs, True)


def ema_blanket_blocks_live(indicators_get, is_long, tf_raw, min_tfs):
    """Scalar twin. True = BLOCKED. Fail-open on missing keys."""
    tf = resolve_filter_tf(tf_raw)
    if tf is None:
        return False
    try:
        ab = indicators_get("ema_9_above_21_%s" % tf, None)
    except Exception:
        return False
    if ab is None:
        return False
    try:
        aligned = bool(ab) if is_long else (not bool(ab))
    except Exception:
        return False
    return not (int(aligned) >= int(min_tfs or 3))


# ── GR_FILTER_ALL_ENTRIES ────────────────────────────────────────────────────
# Live crypto: execute_now fresh-OPEN gate (ez:30166-30197, LONG+SHORT,
# exemptions REENTRY/AUGMENT/CLOSE/REDUCE/HEDGE/OBLIGATORY, fail-open). The
# gate relaxes MTF_GR_MIN_IND to 4 when price stretches >5% beyond sma_200_15m,
# then requires mtf_live_evaluator.gr_filter_pass. Stocks live is missing (stub
# only); vec is full-tree vec_decisions/ported_entry.py:79 (parity to verify).
# The twin below ports the relax + call contract; the hook injects
# mtf_live_evaluator.gr_filter_pass (crypto-proven) on the stocks path.

def gr_min_ind_effective(indicators_get, position_side, cfg_get, current_price):
    base = int(cfg_get("MTF_GR_MIN_IND", 7) or 7)
    try:
        sma = float(indicators_get("sma_200_15m", 0) or 0)
    except (TypeError, ValueError):
        return base
    try:
        px = float(current_price or 0)
    except (TypeError, ValueError):
        return base
    if sma > 0 and position_side == "LONG" and px > sma * 1.05:
        return min(base, 4)
    if sma > 0 and position_side == "SHORT" and px < sma * 0.95:
        return min(base, 4)
    return base


def gr_all_entries_blocks(indicators, position_side, cfg_get, current_price, gr_pass_fn, pass_cfg):
    """True = BLOCKED. gr_pass_fn = mtf_live_evaluator.gr_filter_pass (crypto-proven);
    pass_cfg = namespace carrying MTF_GR_* knobs (hook builds it, _EbNS-style). Fail-open."""
    try:
        if not bool(cfg_get("GR_FILTER_ALL_ENTRIES", False)):
            return False
        if not indicators:
            return False
        min_ind = gr_min_ind_effective(lambda k, d=None: indicators.get(k, d), position_side, cfg_get, current_price)
        return not bool(gr_pass_fn(indicators, position_side, "tradier", pass_cfg, min_ind=min_ind))
    except Exception:
        return False


# ── DC_BREACH_REDUCE ─────────────────────────────────────────────────────────
# Live crypto: monitor-loop reduce (ez:35140-35330, hedged + unhedged twins):
# LONG breaches when price < dc_low_15m, SHORT when price > dc_high_15m
# (levels must be > 0), then REDUCE-to-min via queue_trade_action with a 120s
# per-key cooldown. The 15m is hardcoded — no TF knob exists, so OFF (the
# config default on all 4 cat_sides) falls back to 15m == today's behavior.
# Master EXIT_DC_BREACH_REDUCE_ENABLED defaults True (live active).
# Vec twin = breach TRIGGER mask ORed into reduce_sig (frac 1.0); the 120s
# cooldown is live-only (documented; vec walk has _red_cd_ok ladder cooldown).

def dc_breach_tf(tf_raw):
    r = normalize_tf(tf_raw)
    if r == "" or r.upper() == "OFF":
        return "15m"
    return r


def dc_breach_reduce_mask(npz, n, is_long, cfg, close, safe=None):
    if not bool(_cfg_get(cfg, "EXIT_DC_BREACH_REDUCE_ENABLED", True)):
        return None
    tf = dc_breach_tf(_cfg_get(cfg, "DC_BREACH_REDUCE_FILTER_TF", "OFF"))
    s = safe or safe_get
    lvl = s(npz, ("dc_low_%s" % tf) if is_long else ("dc_high_%s" % tf), n, 0.0)
    px = np.asarray(close, dtype=np.float64)
    if is_long:
        return (lvl > 0) & (px < lvl)
    return (lvl > 0) & (px > lvl)


def dc_breach_fires_live(indicators_get, is_long, price, tf_raw):
    tf = dc_breach_tf(tf_raw)
    try:
        lvl = _fnum(indicators_get(("dc_low_%s" % tf) if is_long else ("dc_high_%s" % tf), 0.0))
    except Exception:
        return False
    if not (lvl > 0):
        return False
    return bool(price < lvl) if is_long else bool(price > lvl)


# ── MTF_DC_REJECT ────────────────────────────────────────────────────────────
# Two venue formulas, both TF-knobbed (family knob MTF_DC_REJECT_EXIT_TF):
#  crypto (ez:48174-48192): ever_outside_dc latch — LONG arms when price >
#    dc_high_TF and fires when it later prints < band (SHORT mirror on
#    dc_low_TF); optional dc4 band via MTF_DC_REJECT_USE_DC4 (reason _dc4).
#  stocks (tr:11299-11321): timestamp variant via mtf_exit_timing.dc_reject_step
#    (outside_ts + event_within_lookback(now, prior, LOOKBACK bars, TF)).
# FILTER_TF (default 15m) selects the TF with R2 fallback to the family knob
# (crypto family default 15m, stocks MTF_DC_REJECT_EXIT_TF_TRADIER/1h).
# Vec: the full-tree staged vec_paths.mtf_dc_reject mirrors the STOCKS variant
# (pinned by test_vec_mtf_dc_reject.py); the crypto latch walk twin below is
# new. Hook = TF resolver at the live TF-knob reads + staged/walk vec calls.

def mtf_dc_reject_tf(filter_raw, family_raw):
    return resolve_filter_tf(filter_raw, family_raw=family_raw, filter_default="15m")


def mtf_dc_band_key(tf, is_long, use_dc4=False):
    base = "dc_high4_" if use_dc4 else "dc_high_"
    if not is_long:
        base = "dc_low4_" if use_dc4 else "dc_low_"
    return "%s%s" % (base, tf)


def dc_reject_crypto_step(was_outside, price, band, is_long):
    """One-tick crypto latch port (ez:48181-48192). Returns (now_outside, fire)."""
    try:
        px = float(price)
        bd = float(band)
    except (TypeError, ValueError):
        return bool(was_outside), False
    if not (bd > 0):
        return bool(was_outside), False
    if is_long:
        if px > bd:
            return True, False
        if bool(was_outside) and px < bd:
            return True, True
        return bool(was_outside), False
    if px < bd:
        return True, False
    if bool(was_outside) and px > bd:
        return True, True
    return bool(was_outside), False


def dc_reject_crypto_walk(band_arr, px_arr, is_long):
    """Full-walk latch twin for the vec walk (state resets per position)."""
    band = np.asarray(band_arr, dtype=np.float64)
    px = np.asarray(px_arr, dtype=np.float64)
    fire = np.zeros(len(px), dtype=bool)
    outside = False
    for i in range(len(px)):
        outside, hit = dc_reject_crypto_step(outside, float(px[i]), float(band[i]), is_long)
        fire[i] = hit
    return fire


def _tf_seconds(tf):
    raw = str(tf or "").strip()
    if not raw:
        return 300
    unit, num = raw[-1], raw[:-1]
    try:
        amount = int(num) if num else 1
    except (TypeError, ValueError):
        return 300
    if amount <= 0:
        return 300
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "D": 86400, "w": 604800, "W": 604800}.get(unit)
    if mult is None:
        return 300
    return amount * mult


def dc_reject_stocks_step(outside_ts, now_ts, price, band, lookback_bars, tf, is_long):
    """Port of mtf_exit_timing.dc_reject_step (tr:11305-11315 contract)."""
    try:
        prior = float(outside_ts or 0)
        now = float(now_ts)
        px = float(price)
        edge = float(band)
    except (TypeError, ValueError):
        try:
            return float(outside_ts or 0), False
        except (TypeError, ValueError):
            return 0.0, False
    if now <= 0 or edge <= 0:
        return prior, False
    outside = px > edge if is_long else px < edge
    recross = px < edge if is_long else px > edge
    if outside:
        return now, False
    if recross:
        window = max(1, int(lookback_bars)) * _tf_seconds(tf)
        if prior > 0 and 0 <= (now - prior) <= window:
            return 0.0, True
        return 0.0, False
    return prior, False


def dc_reject_stocks_walk(ts_arr, px_arr, band_arr, lookback_bars, tf, is_long):
    ts = np.asarray(ts_arr, dtype=np.float64)
    px = np.asarray(px_arr, dtype=np.float64)
    band = np.asarray(band_arr, dtype=np.float64)
    fire = np.zeros(len(px), dtype=bool)
    outside_ts = 0.0
    for i in range(len(px)):
        outside_ts, hit = dc_reject_stocks_step(outside_ts, float(ts[i]), float(px[i]), float(band[i]), lookback_bars, tf, is_long)
        fire[i] = hit
    return fire


# ── FROZEN_STOP (BB_FROZEN_STOP TF selector, crypto scope) ────────────────────
# Live crypto exit (ez:49984-50019): TF knob BB_FROZEN_STOP_TF (default 1h),
# field knob BB_FROZEN_STOP_FIELD (default "lower"); field resolves to lower/
# upper by side when the knob reads lower/upper, else verbatim; the level
# bb_{field}_{TF} is frozen on first sight (position._frozen_bb_act) and fires
# a full CLOSE when LONG gain<0 and price < frozen (SHORT mirror). Master
# BB_FROZEN_STOP_ENABLED defaults False. FROZEN_STOP_FILTER_TF (default 15m)
# selects the TF with R2 fallback to the 1h family knob. Vec = walk twin
# (capture _ty_frozen_bb_arr + freeze-on-first-sight into pos['ty_frozen_bb'],
# breach-vs-level scalar fires() gated on live_pnl_pct < 0 at the hook site).
# Stocks (tr:16979 G2 probe) is a different ENTRY veto -> out of scope.

def frozen_stop_tf(filter_raw, family_raw):
    return resolve_filter_tf(filter_raw, family_raw=family_raw, filter_default="15m")


def frozen_bb_key(tf, field_opt, is_long):
    if field_opt in ("lower", "upper"):
        field = "lower" if is_long else "upper"
    else:
        field = field_opt
    return "bb_%s_%s" % (field, tf)


def frozen_stop_fires(frozen_level, price, gain_pct, is_long):
    """Scalar breach core (ez:50009-50010). frozen None/0 = no fire."""
    try:
        fr = float(frozen_level)
        px = float(price)
        g = float(gain_pct)
    except (TypeError, ValueError):
        return False
    if not (fr > 0):
        return False
    if not (g < 0):
        return False
    return bool(px < fr) if is_long else bool(px > fr)


def frozen_stop_breach_mask(npz, n, is_long, entry_bar, tf, field_opt, close, safe=None):
    """Price-vs-frozen-level mask from entry_bar on (hook ANDs gain<0)."""
    s = safe or safe_get
    lvl_arr = s(npz, frozen_bb_key(tf, field_opt, is_long), n, 0.0)
    px = np.asarray(close, dtype=np.float64)
    try:
        eb = int(entry_bar)
    except (TypeError, ValueError):
        return np.zeros(n, dtype=bool)
    if eb < 0 or eb >= n:
        return np.zeros(n, dtype=bool)
    frozen = float(lvl_arr[eb])
    if not (frozen > 0):
        return np.zeros(n, dtype=bool)
    mask = np.zeros(n, dtype=bool)
    if is_long:
        mask[eb:] = px[eb:] < frozen
    else:
        mask[eb:] = px[eb:] > frozen
    return mask


# ── WT_15M_BOUNCE claim predicate (stocks-live port; crypto hook PENDING) ─────
# Exact port of the tradier _shared_direct_entry_claim WT_15M_BOUNCE_OPEN block
# (tr:1297-1355): WT 15m cross (up LONG / down SHORT) + still-with-side +
# bb_pct_b_15m within [BB_MIN, BB_MAX] + 1h/4h rising-HTF (any/all via
# REQUIRE_BOTH_HTF) + HL/HH dc-channel filters (dc_low/high_1h rising, AND/OR
# via FILTER_MODE; aliases LOW/HIGH_1H_GT_PREV) + volume filter (relvol 1h
# primary vs VOLUME_THRESHOLD, or vol>vol_sma*thr; alias REL_VOL_GT_1).
# HL/HH on -> trigger switches from cross to still-with-side. Vec exists inline
# (v12:9731-2795+); crypto live has no WT-bounce family path (only `if False`
# hooks ez:59965-59979) -> crypto placement is NEEDS-OPERATOR-DECISION.
# KNOWN RESIDUAL (pre-existing, not introduced here): vec vol uses
# relative_volume_15m primary with 1h fallback (v12:2790-2793) while live uses
# 1h primary with 15m fallback (tr:1347); vec has no volume_sma key path.

def wt_bounce_claim(indicators_get, is_long, cfg_get):
    """Returns (eligible, detail). cfg_get(key, default) per-sym aware."""
    try:
        if not bool(cfg_get("WT_15M_BOUNCE_OPEN_ENABLED", False)):
            return False, "WT15_OFF"
        bb_min = float(cfg_get("WT_15M_BOUNCE_BB_MIN", 0.05) or 0.05)
        bb_max = float(cfg_get("WT_15M_BOUNCE_BB_MAX", 0.95) or 0.95)
        req_both = bool(cfg_get("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False))
        w1 = _fnum(indicators_get("wt1_15m", 0))
        w2 = _fnum(indicators_get("wt2_15m", 0))
        w1p = _fnum(indicators_get("wt1_15m_prev", w1))
        w2p = _fnum(indicators_get("wt2_15m_prev", w2))
        up = (w1p <= w2p) and (w1 > w2)
        down = (w1p >= w2p) and (w1 < w2)
        cross = up if is_long else down
        still = (w1 > w2) if is_long else (w1 < w2)
        bb_raw = indicators_get("bb_pct_b_15m", 0.5)
        bb = _fnum(bb_raw if bb_raw else 0.5, 0.5)
        bb_ok = (bb >= bb_min) and (bb <= bb_max)
        r1 = bool(indicators_get("wt_cross_rising_1h", False))
        r4 = bool(indicators_get("wt_cross_rising_4h", False))
        h1 = r1 if is_long else (not r1)
        h4 = r4 if is_long else (not r4)
        htf_ok = (h1 and h4) if req_both else (h1 or h4)
        hl_on = bool(cfg_get("WT_15M_BOUNCE_FILTER_HL_ENABLED", False) or cfg_get("WT_15M_BOUNCE_LOW_1H_GT_PREV", False))
        hh_on = bool(cfg_get("WT_15M_BOUNCE_FILTER_HH_ENABLED", False) or cfg_get("WT_15M_BOUNCE_HIGH_1H_GT_PREV", False))
        hl_hh_ok = True
        if hl_on or hh_on:
            dc_low = _fnum(indicators_get("dc_low_1h", 0))
            dc_low_prev = _fnum(indicators_get("dc_low_1h_prev", dc_low))
            dc_high = _fnum(indicators_get("dc_high_1h", 0))
            dc_high_prev = _fnum(indicators_get("dc_high_1h_prev", dc_high))
            hl_ok = (dc_low > dc_low_prev) if hl_on else True
            hh_ok = (dc_high > dc_high_prev) if hh_on else True
            mode = str(cfg_get("WT_15M_BOUNCE_FILTER_MODE", "AND") or "AND").upper()
            if mode == "OR":
                if hl_on and not hh_on:
                    hl_hh_ok = hl_ok
                elif (not hl_on) and hh_on:
                    hl_hh_ok = hh_ok
                else:
                    hl_hh_ok = hl_ok or hh_ok
            else:
                hl_hh_ok = hl_ok and hh_ok
        vol_ok = True
        vol_on = bool(cfg_get("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False) or cfg_get("WT_15M_BOUNCE_REL_VOL_GT_1", False))
        if vol_on:
            vmode = str(cfg_get("WT_15M_BOUNCE_VOLUME_MODE", "relvol") or "relvol").lower()
            vthr = float(cfg_get("WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0) or 1.0)
            if vmode == "relvol":
                rel_raw = indicators_get("relative_volume_1h", None)
                if rel_raw is None:
                    rel_raw = indicators_get("relative_volume_15m", 1.0)
                vol_ok = _fnum(rel_raw or 1.0, 1.0) > vthr
            else:
                vol = _fnum(indicators_get("volume_1h", 0) or indicators_get("volume_15m", 0))
                sma = _fnum(indicators_get("volume_sma_1h", 0) or indicators_get("volume_sma_15m", 0))
                vol_ok = vol > (sma * vthr) if sma else True
        trigger = still if (hl_on or hh_on) else cross
        eligible = bool(trigger and bb_ok and htf_ok and hl_hh_ok and vol_ok)
        return eligible, "WT15_still=%d_cross=%d_bb=%.2f_hlhh=%d_vol=%d" % (int(still), int(cross), bb, int(hl_hh_ok), int(vol_ok))
    except Exception:
        return False, "WT15_ERR"


# ── MANDATORY_REENTRY_WT vec port (contract mirror; crypto hook PENDING) ──────
# Live stocks: tradier_manage reentry path (tr:12198-12210) via the venue-neutral
# tradier_reentry_wt_contract.mandatory_reentry_wt_gate (importable here).
# Full-tree vec: vec_decisions/mandatory_reentry_wt_vec.py:85 (unverifiable in
# minimal checkouts). The port below mirrors the contract exactly: TF set from
# MODE (5m_only/15m_only else both), per-TF favorable + flip (explicit cross
# flag OR fresh favorable transition, prev via roll like v12:9741) + velocity
# favorable-or-flat + not-slowing (abs(v) >= max(min_v, abs(v_prev)*ratio)),
# pass when qualifying TFs >= MIN_TFS. Master-gated (None when off).

def mandatory_mode_tfs(mode_raw):
    mode = str(mode_raw or "5m_or_15m").lower().replace(" ", "")
    if mode in ("5m", "5m_only"):
        return ("5m",)
    if mode in ("15m", "15m_only"):
        return ("15m",)
    return ("5m", "15m")


def mandatory_wt_vec(npz, n, is_long, cfg, safe=None):
    if not bool(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_ENABLED", False)):
        return None
    tfs = mandatory_mode_tfs(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_TF_MODE", "5m_or_15m"))
    min_tfs = int(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_TFS", 1) or 1)
    require_flip = bool(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP", True))
    ratio = max(0.0, float(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO", 0.90) or 0.0))
    min_v = max(0.0, float(_cfg_get(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY", 0.0) or 0.0))
    s = safe or safe_get
    qualifying = np.zeros(n, dtype=np.int32)
    for tf in tfs:
        w1 = s(npz, "wt1_%s" % tf, n, 0.0)
        w2 = s(npz, "wt2_%s" % tf, n, 0.0)
        w1p = np.roll(w1, 1)
        w2p = np.roll(w2, 1)
        w1p[0] = w2[0]
        w2p[0] = w2[0]
        if is_long:
            fav = w1 > w2
            was_fav = w1p > w2p
        else:
            fav = w1 < w2
            was_fav = w1p < w2p
        cross_key = ("wt_cross_bull_%s" % tf) if is_long else ("wt_cross_bear_%s" % tf)
        if _kg_key_present(npz, cross_key, n):
            explicit = np.asarray(npz.get(cross_key)).astype(bool)
        else:
            explicit = np.zeros(n, dtype=bool)
        flipped = explicit | (fav & ~was_fav)
        vel = s(npz, "wt_velocity_%s" % tf, n, 0.0)
        vel_prev = np.roll(vel, 1)
        vel_prev[0] = vel[0]
        fav_vel = (vel >= min_v) if is_long else (vel <= -min_v)
        not_slowing = np.abs(vel) >= np.maximum(min_v, np.abs(vel_prev) * ratio)
        ok = fav & (flipped if require_flip else True) & fav_vel & not_slowing
        qualifying = qualifying + ok.astype(np.int32)
    return qualifying >= min_tfs


# ── MTF_ATR_TRAIL TF resolver (live READY; vec PENDING staged-API) ──────────────
# Live both venues read family knob MTF_ATR_TRAIL_TF (crypto _psym_get ez:48151,
# stocks _cfg TRADIER-first tr:11273); master MTF_ATR_TRAIL_ENABLED defaults
# False. FILTER (default 15m) selects with R2 fallback. Live hooks are exact;
# the vec half awaits vec_decisions/mtf_atr_trail_exit staged-API confirmation.

def mtf_atr_trail_tf(filter_raw, family_raw):
    return resolve_filter_tf(filter_raw, family_raw=family_raw, filter_default="15m")


# ── stocks KG 9/21 TF-list intersect (mirrors v12:9614-9626) ──────────────────
# Vec stocks intersect rule: keep family TFs that are FILTER-enabled ('15m' in
# the FILTER list keeps everything = default no-op; 'ALL' keeps everything);
# empty intersection falls back to the FILTER's first TF. OFF/empty FILTER =
# family list untouched. Live stocks hook applies this to _tfs_9_21 (tr:27774).

def kg_stocks_tfs_restrict(family_tfs, filter_raw):
    raw = normalize_tf(filter_raw)
    if raw in ("OFF", "off", ""):
        return list(family_tfs)
    filt = [t.strip() for t in raw.split(",") if t.strip()]
    if not filt:
        return list(family_tfs)
    kept = [tf for tf in family_tfs if tf in filt or "15m" in filt or raw == "ALL"]
    return kept if kept else filt[:1]


# ── PENDING cores (NEEDS-OPERATOR-DECISION; shipped so promotion is 1 hook) ──
# NEWBORN_LOSS_KILL velocity-against core. Blocked only by the TF-default
# conflict: live reads NEWBORN_LOSS_KILL_VEL_TF (default ""->3m, ez:47012)
# while vec reads NEWBORN_LOSS_KILL_FILTER_TF (default 15m, v12:11990-11995).
# The predicate itself (vel against side) is identical both sides.

def newborn_vel_against(vel, is_long):
    try:
        v = float(vel)
    except (TypeError, ValueError):
        return False
    return bool(v < 0) if is_long else bool(v > 0)


def newborn_vel_vec(npz, n, is_long, tf, safe=None):
    s = safe or safe_get
    vel = s(npz, "wt_velocity_%s" % tf, n, 0.0)
    return (vel < 0) if is_long else (vel > 0)


# R2-leg TF resolver for EXIT_R1_R2_FILTER_TF (PENDING: R1 is 3m-fixed with no
# TF knob, so one filter cannot honestly scope "R1+R2"; the R2 leg uses
# R2_TF_LIST default ("15m",) per ez:47631).

def r2_eff_tfs(filter_raw, family_list=("15m",), filter_default="15m"):
    r = normalize_tf(filter_raw)
    if r == "" or r.upper() == "OFF":
        return tuple(family_list or ("15m",))
    if r == str(filter_default):
        return tuple(family_list or ("15m",))
    return (r,)


# ── Batch appliers (called by the hook_spec insertions) ───────────────────────

def apply_yellow_entry_masks(npz, n, is_long, cfg, close, entry_sig, entry_filter_masks=None, safe=None, include_kg=True):
    """ANDs the WIRED entry-gate twins into entry_sig. Returns entry_sig.

    include_kg=False in tradier MODE: the KG hard block is the crypto-live
    semantic (ez:555-557); stocks live uses soft cumulative scoring and stocks
    vec keeps its _kg_signal path, so the hard block must not run there (43).
    """
    s = safe or safe_get
    for fn in (mom3_entry_gate, momentum_breakout_gate, dc_break_entry_gate):
        try:
            m = fn(npz, n, is_long, cfg, close, s)
        except Exception:
            m = None
        if m is not None:
            entry_sig = entry_sig & np.asarray(m, dtype=bool)
            if entry_filter_masks is not None:
                entry_filter_masks.append(np.asarray(m, dtype=bool))
    kb = None
    if include_kg:
        try:
            kb = kg_block_mask(npz, n, is_long, cfg, close, s)
        except Exception:
            kb = None
    if kb is not None:
        kb = np.asarray(kb, dtype=bool)
        entry_sig = entry_sig & ~kb
        if entry_filter_masks is not None:
            entry_filter_masks.append(~kb)
    try:
        eb = ema_blanket_pass_vec(npz, n, is_long, cfg, s)
    except Exception:
        eb = None
    if eb is not None:
        eb = np.asarray(eb, dtype=bool)
        entry_sig = entry_sig & eb
        if entry_filter_masks is not None:
            entry_filter_masks.append(eb)
    return entry_sig


def yellow_live_veto(indicators, is_long, price, cfg_get):
    """Scalar live entry veto over the WIRED entry twins. (blocked, reason)."""
    try:
        get = cfg_get if callable(cfg_get) else (lambda k, d=None: getattr(cfg_get, k, d))
        if mom3_blocks_live(indicators, is_long, price, get("MOM3_FILTER_TF", "OFF"), float(get("MOM3_LONG_THRESHOLD", -1.0) or -1.0), float(get("MOM3_SHORT_THRESHOLD", 1.0) or 1.0)):
            return True, "YELLOW_MOM3_BLOCK"
        if momentum_breakout_blocks_live(indicators, is_long, price, get("MOMENTUM_BREAKOUT_FILTER_TF", "OFF")):
            return True, "YELLOW_MOMENTUM_BREAKOUT_BLOCK"
        if dc_break_blocks_live(indicators, is_long, price, get("DC_BREAK_FILTER_TF", "OFF")):
            return True, "YELLOW_DC_BREAK_BLOCK"
        if bool(get("KINDERGARTEN_EMA_GATE_ENABLED", False)):
            blocked, why = kg_blocks_live(indicators, is_long, price, get("KINDERGARTEN_FILTER_TF", "OFF"), enabled=True)
            if blocked:
                return True, "YELLOW_%s" % why
        if bool(get("EMA_BLANKET_FILTER_ENABLED", False)):
            if ema_blanket_blocks_live(lambda k, d=None: indicators.get(k, d), is_long, get("EMA_BLANKET_FILTER_FILTER_TF", "OFF"), get("EMA_BLANKET_FILTER_MIN_TFS", 3)):
                return True, "YELLOW_EMA_BLANKET_BLOCK"
    except Exception:
        return False, "YELLOW_ERR_OPEN"
    return False, "YELLOW_OK"
