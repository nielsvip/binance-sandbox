"""Structured trade provenance for /history/ events (USER 2026-10-20: "what switch caused the trade and what filters approved it").

METADATA ONLY. Nothing in here is read by any decision path: it maps the label the vector engine already emitted for a fired path
(ledger `reason`: B12, B_KZONE, WT_LOWER_CROSS_EXIT, EXIT_VELOCITY_WT ...) to the template switch that enables that path, reads the switch
value from the per_sym set in force, and lists the sweep-promoted filters of the lifecycle stage. The engine (v12_quick_engine.py) is NOT
modified: the label is the engine's own, authoritative output, and the entry-block -> switch table below is generated from the engine's
`compute_reentry_blocks` AST (tests/test_history_prov.py regenerates it and fails on drift).

Block shape (all values strings/JSON):
  {"v":1,"src":"VEC_EXACT|FIXED_STOP|EXECUTION|BROKER|NATIVE","label":<engine label>,"stage":ENTRY|REENTRY|AUGMENT|REDUCE|EXIT,
   "switch":<template switch key>|"NONE"|"UNATTRIBUTED:<reason>","cand":<its value in the set in force>,"cand_src":"set|default|absent",
   "cands":[...] (only when the label is an OR over several switches: the ones active in the set),
   "params":{knob:value} (path-specific knobs), "winning":true|false|null (switch value differs from the template default snapshot = the
   sweep promoted it), "filters":["KEY=val"] (stage filters whose value differs from the template default = promoted by the sweep),
   "filters_n_active":<count of all active stage filters incl. defaults>,"filters_basis":...,"tf":"15m","set_id":<sha256>,"ev":<type>}

Never raises: every public function returns a block (possibly UNATTRIBUTED) or None when HISTORY_PROV_ENABLED is off.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

VERSION = 1
EXECUTION_TF = "15m"
_MISSING = object()

# ---- entry block label -> enabling switch (generated from v12_quick_engine.compute_reentry_blocks by AST; drift-tested) ----------------
ENTRY_BLOCK_KEYS = {
    "B02": ["REENTRY_B02_BC156_BOTTOM_ENABLED"],
    "B04": ["REENTRY_B04_DC_RETEST_ENABLED"],
    "B09": ["REENTRY_B09_SNAPBACK_ENABLED"],
    "B10": ["REENTRY_B10_STOCH_REV_ENABLED"],
    "B11": ["REENTRY_B11_DC_BREAK_ENABLED"],
    "B12": ["REENTRY_B12_WT_MOM_ENABLED"],
    "B14": ["REENTRY_B14_HA_TREND_ENABLED"],
    "B15": ["REENTRY_B15_STRONG_TREND_ENABLED"],
    "B_BBPCTB": ["BB_PCTB_ENTRY_ENABLED"],
    "B_BBSQUEEZE": ["BB_SQUEEZE_ENTRY_ENABLED"],
    "B_BBSQUEEZE2": ["BB_SQUEEZE_ENABLED"],
    "B_BOUNCE_DEEP_TURN": ["ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED"],
    "B_BOUNCE_DONCHIAN": ["ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED"],
    "B_CONNORS_ENTRY": ["CONNORS_RSI_ENABLED"],
    "B_DAYTRADE": ["DC_DAYTRADE_ENABLED", "TRADIER_DC_DAYTRADE_ENABLED"],
    "B_DELTAENTRY": ["DELTA_ENGINE_ENABLED", "DELTA_ENTRY_ENABLED"],
    "B_EMA20SLOPE": ["EMA20_SLOPE_ENTRY_ENABLED"],
    "B_EMADIST": ["EMA_DIST_ENTRY_ENABLED"],
    "B_FHMOMENTUM": [],
    "B_KZONE": [],
    "B_KZONE_TRADIER": ["K_ZONE_ENTRY_ENABLED_TRADIER"],
    "B_MI_ENTRY": ["MI_ENTRY_ENABLED_TRADIER", "TRADIER_MI_ENTRY_ENABLED_TRADIER"],
    "B_MOM3": ["MOM3_ENTRY_ENABLED"],
    "B_MOM5": ["MOM5_ENTRY_ENABLED"],
    "B_PULL1": ["REENTRY_PULL1_ENABLED"],
    "B_PULL2": ["REENTRY_PULL2_ENABLED"],
    "B_PULL3": ["REENTRY_PULL3_ENABLED"],
    "B_PULL4": ["REENTRY_PULL4_ENABLED"],
    "B_REENTRY2": ["REENTRY_2_ENABLED"],
    "B_RSI2_ENTRY": ["RSI2_ENABLED"],
    "B_RZBOTTOM": ["RZ_ENTRY_ENABLED"],
    "B_SATOSHIT_ENTRY": ["SATOSHIT_ENTRY_ENABLED"],
    "B_SMA200DIST": ["SMA200_DIST_ENTRY_ENABLED"],
    "B_SQUEEZE": ["SQUEEZE_ENABLED"],
    "B_SRS_ENTRY": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "B_STDEV_BOUNCE": ["MODE"],
    "B_STDEV_BREAKOUT": ["MODE"],
    "B_STOCH_ENTRY": ["STOCH_ENTRY_ENABLED"],
    "B_STOCH_HHHL_DIRECT": ["ENTRY_STOCH_HHHL_DIRECT_ENABLED"],
    "B_STOCH_PARENT_DIRECT": ["ENTRY_STOCH_PARENT_DIRECT_ENABLED"],
    "B_TRADIERRSI": ["MODE"],
    "B_TRADIERSTOCH": ["MODE", "STOCH_CROSS_ENTRY_TRADIER"],
    "B_VWAPBOUNCE": ["VWAP_BOUNCE_ENTRY_ENABLED"],
    "B_WT_15M_CROSS_ENTRY": ["WT_15M_CROSS_ENTRY_ENABLED"],
    "B_WT_ENTRY": ["WT_ENTRY_ENABLED"],
    "DC_BREAKOUT_ENTRY": ["DC_BREAKOUT_ENTRY_ENABLED"],
    "FORMATION_ENTRY": [],
    "GOLDEN_RULE_ENTRY": ["GOLDEN_RULE_ENABLED"],
    "LH_TRIGGER_ENTRY": ["ENTRY_LH_TRIGGER_ENABLED"],
    "REENTRY_15M_BB1H_LOW_BOUNCE_HTF": ["REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED"],
    "REENTRY_15M_DC_BASIS_CROSS_HTF": ["REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED"],
    "REENTRY_15M_LRL_PULLBACK_HTF": ["REENTRY_15M_LRL_PULLBACK_HTF_ENABLED"],
    "VOL_SPIKE_REVERSAL": ["VOL_SPIKE_ENABLED"],
    "WR_PULLBACK": ["WR_PULLBACK_ENABLED"],
}

# Blocks whose AST guard is empty / MODE-only / inverted: resolved by hand from the engine source (evidence per entry).
ENTRY_BLOCK_OVERRIDES = {
    "B_KZONE": {"switch": "K_ZONE_ENTRY_ENABLED", "params": ["K_ZONE_LONG_THRESHOLD", "K_ZONE_SHORT_THRESHOLD"], "ev": "v12:10743-10750 (_kz_on = not tradier and K_ZONE_ENTRY_ENABLED)"},
    "B_STDEV_BREAKOUT": {"switch": "STDEV_BREAKOUT_ENABLED", "ev": "v12:11585 comment 'inert unless STDEV_BREAKOUT_ENABLED' + _ENTRY_FAMILY_MASTERS"},
    "B_STDEV_BOUNCE": {"switch": "STDEV_BOUNCE_ENABLED", "ev": "v12:11596 comment 'inert unless STDEV_BOUNCE_ENABLED' + _ENTRY_FAMILY_MASTERS"},
    "B_SRS_ENTRY": {"switch": "STRUCTURAL_RANGE_SHIFT_EXIT", "params": ["STRUCTURAL_RANGE_SHIFT_TF"], "ev": "v12:10954-10974 (entry permission = NOT the SRS pctb block)"},
    "B_DAYTRADE": {"by_venue": {"crypto": "DC_DAYTRADE_ENABLED", "stocks": "TRADIER_DC_DAYTRADE_ENABLED"}, "ev": "v12:17273-17275 daytrade_on"},
    "B_DELTAENTRY": {"switch": "DELTA_ENTRY_ENABLED", "requires": ["DELTA_ENGINE_ENABLED"], "ev": "v12:11236 both guards"},
    "B_MI_ENTRY": {"switch": "MI_ENTRY_ENABLED_TRADIER", "ev": "v12:11362"},
    "B_FHMOMENTUM": {"unattr": "block_has_no_enabling_switch_in_engine"},
    "FORMATION_ENTRY": {"unattr": "block_has_no_enabling_switch_in_engine"},
    "B_TRADIERRSI": {"unattr": "threshold_gate_without_switch"},
    "B_TRADIERSTOCH": {"unattr": "inverted_guard_STOCH_CROSS_ENTRY_TRADIER_off"},
}

# ---- non-block labels (regex on the engine label, case-sensitive prefix) -> spec; `ev` = where the emitter + guard were read ----------
_LABEL_SPECS = [
    (r"^REENTRY_EXIT_PRICE_BREAK", {"switch": "REENTRY_ON_EXIT_PRICE_BREAK_ENABLED", "absent_default": True, "fixed": ["WT15M_CONFIRM"], "stage": "REENTRY", "ev": "vec_decisions/reentry_triggers.py:47"}),
    (r"^REENTRY_DC4_15M_BREAK", {"switch": "REENTRY_ON_DC4_15M_BREAK_ENABLED", "absent_default": True, "fixed": ["WT15M_CONFIRM"], "stage": "REENTRY", "ev": "vec_decisions/reentry_triggers.py:49-53"}),
    (r"^REENTRY_(HIGHER_LOW|LOWER_HIGH)", {"switch": "REENTRY_ON_HIGHER_LOW_ENABLED", "absent_default": True, "fixed": ["WT15M_CONFIRM"], "stage": "REENTRY", "ev": "vec_decisions/reentry_triggers.py:55-57"}),
    (r"^HARDCODED_RALLY_REENTRY", {"switch": "HARDCODED_RALLY_REENTRY_ENABLED", "stage": "REENTRY", "ev": "v12:19291-19296"}),
    (r"^EXIT_VELOCITY_WT", {"switch": "EXIT_VELOCITY_WT_ENABLED", "params": ["EXIT_VELOCITY_WT_TFS", "EXIT_VELOCITY_WT_MIN_TFS"], "ev": "v12:16895 gate + 20455-20520 emitter"}),
    (r"^WT_LOWER_CROSS_EXIT", {"switch": "WT_LOWER_CROSS_EXIT_TF", "ev": "v12:20372 (TF != OFF is the enable)"}),
    (r"^WT_CROSS_EXIT_", {"switch": "WT_CROSS_EXIT_ENABLED", "params": ["WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", "WT_CROSS_EXIT_MIN_AGE_MINUTES"], "ev": "vec_decisions/check_exit_candidates_crypto__wt_cross_exit.py docstring (caller gate)"}),
    (r"^MULTI_TF_EXIT", {"by_venue": {"crypto": "MULTI_TF_EXIT_ENABLED", "stocks": "MULTI_TF_EXIT_ENABLED_TRADIER"}, "ev": "v12:18021-18027"}),
    (r"^MTF_BB_REJECT", {"switch": "MTF_BB_REJECT_EXIT_ENABLED", "params": ["MTF_BB_REJECT_EXIT_TF", "MTF_BB_REJECT_EXIT_LOOKBACK"], "ev": "v12:17505-17511"}),
    (r"^MTF_DC_REJECT", {"switch": "MTF_DC_REJECT_EXIT_ENABLED", "params": ["MTF_DC_REJECT_EXIT_TF", "MTF_DC_REJECT_EXIT_LOOKBACK"], "ev": "v12:16970"}),
    (r"^MTF_GR_WT_EXIT", {"switch": "MTF_WT_CROSS_EXIT_ENABLED", "requires": ["MTF_GR_EXIT_GATE_ENABLED"], "params": ["MTF_WT_CROSS_EXIT_TF"], "ev": "v12:17527-17535 + memory reason_to_gate_mapping"}),
    (r"^UNCOND_DC_(HIGH|LOW)_1H", {"switch": "DC_HARD_STOP_1H_ENABLED", "ev": "v12:20236-20241"}),
    (r"^UNCOND_DC_(HIGH|LOW)_15M", {"switch": "DC_HARD_STOP_15M_ENABLED", "ev": "v12:20242-20249"}),
    (r"^UNCOND_DC_(HIGH|LOW)_4H", {"switch": "NONE", "unattr": "UNCOND_DC_4H_has_no_switch_in_engine", "ev": "v12:20233-20235 (ungated)"}),
    (r"^BB_FROZEN_STOP_BREACH", {"switch": "BB_FROZEN_STOP_ENABLED", "ev": "v12:21485"}),
    (r"^PEAK_GIVEBACK_GAIN_EROSION_STOP", {"switch": "PEAK_GIVEBACK_PROTECTION_ENABLED", "any": ["PEAK_GIVEBACK_HARD_ZERO_ENABLED", "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED"], "ev": "v12:17588"}),
    (r"^PPL_(TP|SL_CLOSE)", {"switch": "PARTIAL_PROFIT_LOCK_ENABLED", "ev": "vec_decisions/reduce_profit_lock.py:ppl_params/ppl_step"}),
    (r"^DD_BOUNCE_STOP", {"switch": "WT_D_BOUNCE_DD_STOP_ENABLED", "ev": "vec_decisions/reduce_profit_lock.py:74"}),
    (r"^(DAYTRADE_TARGET|DAYTRADE_STOP|DT_DC_)", {"by_venue": {"crypto": "DC_DAYTRADE_ENABLED", "stocks": "TRADIER_DC_DAYTRADE_ENABLED"}, "ev": "v12:17273-17275 daytrade_on; vec_decisions/dc_channel_exits.py:122-141"}),
    (r"^MI_EXIT", {"by_venue": {"crypto": "MI_EXIT_ENABLED", "stocks": "MI_EXIT_ENABLED_TRADIER"}, "params": ["MI_MIN_GAIN_EXIT", "MI_TF_AGREE_MIN"], "ev": "v12:19980-19990 (_mi_arr voter)"}),
    (r"^STATEFUL_PORTED_EXIT", {"switch": "UNATTRIBUTED:OR_of_stateful_exits_member_not_recorded", "any": ["WT_4H_VEL_EXIT_ENABLED", "WT_EXHAUST_EXIT_ENABLED", "WT_DC_EXIT_ENABLED", "WRONG_SIDE_ABS_KILL_ENABLED", "MU_CORRECTION_EXIT_ENABLED"], "ev": "v12:19861-19890 + ported_stateful_exit.masks"}),
    (r"^(REGIME_REDUCE|REDUCE_TO_FLAT)", {"switch": "UNATTRIBUTED:OR_of_reduce_signals_member_not_recorded", "any": ["REGIME_GATE_ENABLED", "EXIT_DC_BREACH_REDUCE_ENABLED", "ENABLE_FAST_RISER_REDUCE"], "ev": "memory reason_to_gate_mapping (v12:21858 reduce_sig OR)"}),
    (r"^HLR_TOP_EXIT_SELL_TOP", {"switch": "HLR_TOP_EXIT_ENABLED", "ev": "v12:21733-21738 (_qr_fire)"}),
    (r"^LH_LL_TOP_EXIT", {"switch": "LH_LL_TOP_EXIT_ENABLED", "params": ["LH_LL_TOP_EXIT_MODE"], "ev": "vec_decisions/lh_ll_top_exit.py:152"}),
    (r"^ALL_TF_AGAINST_CLOSE", {"switch": "ALL_TF_AGAINST_CLOSE_ENABLED", "ev": "v12:284"}),
    (r"^PROFIT_TP_EXHAUSTION", {"switch": "MOMENTUM_TP_ENABLED", "ev": "memory reason_to_gate_mapping (ez_manage:57602)"}),
    (r"^HAIKU_REDUCE", {"switch": "UNATTRIBUTED:HAIKU_REDUCE_switch_not_verified", "params": ["HAIKU_REDUCE_GAIN_THRESHOLD"], "ev": "vec_decisions/haiku_augment.py:139"}),
    (r"^SATOSHIT_EXIT_PARTIAL", {"switch": "UNATTRIBUTED:generic_exit_sig_composite", "params": ["SATOSHIT_EXIT_PARTIAL_PCT"], "ev": "v12:22148-22183 (exit_sig composite)"}),
]
_LABEL_RES = [(re.compile(p), s) for p, s in _LABEL_SPECS]

_REENTRY_PREFIXES = ("REENTRY_", "HARDCODED_RALLY", "MANDATORY_REENTRY", "TIER", "HTF_WT_CHURN_REENTRY", "GAP_MORNING_REENTRY", "STOP_RECLAIM")
_MAKER_PREFIX = "MAKER_PROFIT_EXIT_"
_STAGE_GROUPS = {
    "ENTRY": ("ABLATION_DISABLE_FILTER_ENTRY", "ABLATION_DISABLE_FILTER_MTF_HTF"),
    "REENTRY": ("ABLATION_DISABLE_FILTER_REENTRY", "ABLATION_DISABLE_FILTER_ENTRY", "ABLATION_DISABLE_FILTER_MTF_HTF"),
    "AUGMENT": ("ABLATION_DISABLE_FILTER_AUGMENT", "ABLATION_DISABLE_FILTER_ENTRY"),
    "REDUCE": ("ABLATION_DISABLE_FILTER_REDUCE", "ABLATION_DISABLE_FILTER_EXIT"),
    "EXIT": ("ABLATION_DISABLE_FILTER_EXIT", "ABLATION_DISABLE_FILTER_MTF_HTF"),
}

_EXEC_TOKENS = ("VEC_STALE_FLATTEN", "VEC_EXIT_OWED", "VEC_CONVERGE", "EXIT_OWED_", "VEC_DRIVEN_CLOSE_RECONCILE")
_BROKER_TOKENS = ("BROKER_RECONCILED", "BROKER_SYNC", "SYNC_DETECTION", "Broker", "REENTRY_MONITOR")


def enabled(cfg=None) -> bool:
    """HISTORY_PROV_ENABLED kill switch (config.py / config_tradier.py, default False). Env HISTORY_PROV_ENABLED=0|1 overrides (tests)."""
    e = os.environ.get("HISTORY_PROV_ENABLED")
    if e is not None and e.strip() != "":
        return e.strip().lower() in ("1", "true", "yes", "on")
    try:
        if cfg is not None and hasattr(cfg, "HISTORY_PROV_ENABLED"):
            return bool(getattr(cfg, "HISTORY_PROV_ENABLED"))
    except Exception:
        pass
    for m in ("config", "config_tradier"):
        try:
            mod = sys.modules.get(m)
            c = getattr(mod, "config", None) or getattr(mod, "TradierConfig", None) or mod
            if c is not None and hasattr(c, "HISTORY_PROV_ENABLED"):
                return bool(getattr(c, "HISTORY_PROV_ENABLED"))
        except Exception:
            continue
    return False


_CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def venue_of(symbol: str, full_set=None) -> str:
    """'crypto' for exchange-quote symbols (per_sym_live_set.CRYPTO_SUFFIXES), else 'stocks'. The set's MODE is NOT trusted: the sqlite
    overrides of stock rows do not carry it (CRWD_LONG), only the JSON full_config does."""
    sym = str(symbol or "").upper()
    if sym.endswith(_CRYPTO_SUFFIXES):
        return "crypto"
    if sym:
        return "stocks"
    return "stocks" if str((full_set or {}).get("MODE", "crypto")) == "tradier" else "crypto"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def set_id(full_set) -> str:
    """sha256 of the canonical JSON of the full per_sym set in force."""
    try:
        return hashlib.sha256(canon(dict(full_set or {})).encode("utf-8")).hexdigest()
    except Exception:
        return "UNAVAILABLE"


_SID_MEMO = {}
_DEFAULTS_MEMO = {}


def set_id_memo(symside: str, full_set) -> str:
    """set_id with a per-(symside, id/len) memo so a 3.5k-key set is hashed once per set revision, not once per event."""
    try:
        k = (str(symside), id(full_set), len(full_set or {}))
        hit = _SID_MEMO.get(k)
        if hit is not None:
            return hit
        sid = set_id(full_set)
        if len(_SID_MEMO) > 4096:
            _SID_MEMO.clear()
        _SID_MEMO[k] = sid
        return sid
    except Exception:
        return set_id(full_set)


def _default_snapshot(symside: str):
    """Template default snapshot of this sym_side (per_sym_store entry), memoised 300 s. None when the store is unavailable."""
    hit = _DEFAULTS_MEMO.get(symside)
    now = time.time()
    if hit is not None and now - hit[0] < 300.0:
        return hit[1]
    snap = None
    try:
        import per_sym_store as _pss

        e = _pss.get(symside) or {}
        snap = e.get("defaults_snapshot") or None
    except Exception:
        snap = None
    _DEFAULTS_MEMO[symside] = (now, snap)
    return snap


def _is_active(v) -> bool:
    return not (v is None or v is False or str(v).strip().upper() in ("OFF", "FALSE", "NONE", "", "0"))


def _engine_default(key):
    try:
        qc = getattr(sys.modules.get("v12_quick_engine"), "QuickConfig", None)
        if qc is not None and hasattr(qc, key):
            return getattr(qc, key)
    except Exception:
        pass
    return _MISSING


def _val(full_set, key):
    if key in full_set:
        return full_set[key], "set"
    d = _engine_default(key)
    if d is not _MISSING:
        return d, "default"
    return None, "absent"


def _s(v):
    return v if isinstance(v, (int, float, bool)) or v is None else str(v)


def _stage(label: str, ev_type: str) -> str:
    t = str(ev_type or "").upper()
    if label.startswith(_REENTRY_PREFIXES):
        return "REENTRY"
    if t == "OPEN":
        return "ENTRY"
    if t == "AUGMENT":
        return "AUGMENT"
    if t == "REDUCE":
        return "REDUCE"
    return "EXIT"


def strip_label(reason: str) -> str:
    """The engine label inside a reason: strips the vec tags and the maker-exit prefix (execute_now adds MAKER_PROFIT_EXIT_)."""
    r = str(reason or "").strip()
    for tag in (" |VEC_EXACT", "|VEC_EXACT"):
        i = r.find(tag)
        if i >= 0:
            r = r[:i]
    r = r.strip()
    if r.startswith(_MAKER_PREFIX):
        r = r[len(_MAKER_PREFIX):]
    m = re.match(r"^VEC_EXACT_(OPEN|AUGMENT|REDUCE|CLOSE)_(.*)$", r)
    if m:
        r = m.group(2)
    return r.strip()


def classify_token(reason: str):
    """(src, switch) for reasons that are not a vector ledger path: execution-layer and broker-layer events. None = not such a token."""
    r = str(reason or "")
    lab = strip_label(r)
    if lab.startswith("FIXED_STOP"):
        return "FIXED_STOP", "NONE"
    if lab.startswith("VEC_CONVERGE_OPEN") or lab.startswith(_MAKER_PREFIX + "VEC_CONVERGE_OPEN"):
        return "EXECUTION", "UNATTRIBUTED:converge_open_origin_not_resolved"
    if any(lab.startswith(t) or lab.startswith(_MAKER_PREFIX + t) for t in _EXEC_TOKENS):
        return "EXECUTION", "NONE"
    if any(lab.startswith(t) for t in _BROKER_TOKENS):
        return "BROKER", "NONE"
    return None


def _resolve_spec(label: str, venue: str):
    """-> (spec dict | None). spec: switch/requires/params/fixed/any/unattr/ev."""
    head = re.split(r"[ ,(]", label, 1)[0]
    if head in ENTRY_BLOCK_KEYS:
        o = ENTRY_BLOCK_OVERRIDES.get(head)
        if o is not None:
            sp = dict(o)
            if "by_venue" in sp:
                sp["switch"] = sp["by_venue"].get(venue, sp["by_venue"].get("crypto"))
            if "unattr" in sp:
                sp["switch"] = "UNATTRIBUTED:" + sp.pop("unattr")
            return sp
        keys = ENTRY_BLOCK_KEYS[head]
        if keys:
            sp = {"switch": keys[-1], "ev": "v12:compute_reentry_blocks (AST)"}
            if len(keys) > 1:
                sp["requires"] = keys[:-1]
            return sp
        return {"switch": "UNATTRIBUTED:block_has_no_enabling_switch_in_engine"}
    for rx, sp in _LABEL_RES:
        if rx.match(label):
            sp = dict(sp)
            if "by_venue" in sp:
                sp["switch"] = sp["by_venue"].get(venue, sp["by_venue"].get("crypto"))
            if sp.get("unattr"):
                sp["switch"] = "UNATTRIBUTED:" + sp["unattr"]
            return sp
    return None


def _promoted(symside: str, full_set):
    """{key} whose value differs from the template default snapshot of this sym_side; None when no snapshot is available."""
    snap = _default_snapshot(symside)
    if not snap:
        return None
    out = set()
    for k, v in full_set.items():
        if snap.get(k, _MISSING) != v:
            out.add(k)
    return out


def _stage_filters(stage: str, full_set, promoted):
    try:
        from vec_decisions import filter_ablation_groups as _g
    except Exception:
        return [], 0, "NO_FILTER_GROUPS"
    keys = []
    for grp in _STAGE_GROUPS.get(stage, ()):
        for k in _g.GROUPS.get(grp, ()):
            if k not in keys:
                keys.append(k)
    active = [k for k in keys if k in full_set and _is_active(full_set[k])]
    if promoted is None:
        return [], len(active), "PROMOTED_UNKNOWN_NO_DEFAULT_SNAPSHOT"
    chosen = [f"{k}={full_set[k]}" for k in active if k in promoted]
    return chosen, len(active), "PROMOTED_BY_SWEEP_AND_ACTIVE" if chosen else "NO_PROMOTED_FILTER_IN_STAGE"


def attribute(reason: str, ev_type: str, side: str, venue: str, full_set, symside: str = "", src: str = "VEC_EXACT") -> dict:
    """Provenance block for a vector ledger path. `reason` is the engine label (possibly decorated); never raises."""
    try:
        full_set = full_set if isinstance(full_set, dict) else dict(full_set or {})
        label = strip_label(reason)
        tok = classify_token(reason)
        sid = set_id_memo(symside, full_set)
        base = {"v": VERSION, "src": src, "label": label, "stage": _stage(label, ev_type), "tf": EXECUTION_TF, "set_id": sid, "ev": str(ev_type or "").upper()}
        if tok is not None:
            base["src"], base["switch"] = tok
            base["cand"] = None
            base["filters"] = []
            return base
        sp = _resolve_spec(label, venue)
        if sp is None:
            base["switch"] = "UNATTRIBUTED:label_not_in_verified_table"
            base["cand"] = None
            base["cand_src"] = "absent"
            return base
        sw = sp.get("switch")
        if sp.get("stage"):
            base["stage"] = sp["stage"]
        base["switch"] = sw
        if sw and not str(sw).startswith("UNATTRIBUTED") and sw != "NONE":
            v, vs = _val(full_set, sw)
            base["cand"], base["cand_src"] = _s(v), vs
            if vs == "absent" and "absent_default" in sp:
                base["cand"], base["cand_src"] = sp["absent_default"], "engine_default_when_absent"
        else:
            base["cand"], base["cand_src"] = None, "absent"
        if sp.get("any"):
            base["cands"] = [f"{k}={_s(_val(full_set, k)[0])}" for k in sp["any"] if _is_active(_val(full_set, k)[0])]
        if sp.get("requires"):
            base["requires"] = [f"{k}={_s(_val(full_set, k)[0])}" for k in sp["requires"]]
        if sp.get("params"):
            base["params"] = {k: _s(_val(full_set, k)[0]) for k in sp["params"] if _val(full_set, k)[1] != "absent"}
        promoted = _promoted(symside, full_set) if symside else None
        base["winning"] = (sw in promoted) if (promoted is not None and sw and not str(sw).startswith("UNATTRIBUTED") and sw != "NONE") else None
        flt, nact, basis = _stage_filters(base["stage"], full_set, promoted)
        if sp.get("fixed"):
            flt = list(flt) + ["fixed:" + f for f in sp["fixed"]]
        base["filters"], base["filters_n_active"], base["filters_basis"] = flt, nact, basis
        return base
    except Exception as exc:
        return {"v": VERSION, "src": src, "label": str(reason)[:80], "switch": "UNATTRIBUTED:attribution_error:" + type(exc).__name__, "cand": None, "filters": [], "tf": EXECUTION_TF, "set_id": "UNAVAILABLE", "ev": str(ev_type or "").upper()}


def classify_event(reason: str, trade_type: str) -> dict:
    """Block for a history event with no registered vector provenance (broker sync, native paths, stops written outside the vec dispatch)."""
    try:
        tok = classify_token(reason)
        r = str(reason or "")
        if tok is not None:
            return {"v": VERSION, "src": tok[0], "label": strip_label(r)[:80], "switch": tok[1], "cand": None, "filters": [], "tf": EXECUTION_TF, "set_id": None, "ev": str(trade_type or "").upper()}
        if "|VEC_EXACT" in r or r.startswith("VEC_EXACT_"):
            return {"v": VERSION, "src": "VEC_EXACT", "label": strip_label(r)[:80], "switch": "UNATTRIBUTED:vec_prov_not_matched_to_fill", "cand": None, "filters": [], "tf": EXECUTION_TF, "set_id": None, "ev": str(trade_type or "").upper()}
        return {"v": VERSION, "src": "NATIVE", "label": strip_label(r)[:80], "switch": "UNATTRIBUTED:non_vector_reason", "cand": None, "filters": [], "tf": EXECUTION_TF, "set_id": None, "ev": str(trade_type or "").upper()}
    except Exception:
        return {"v": VERSION, "src": "NATIVE", "switch": "UNATTRIBUTED:classify_error", "filters": []}


def human_tail(prov) -> str:
    """'|SW=<switch>|F=<f1,f2,...>' (F=- when none). Appended to the history reason AFTER the existing ' |VEC_EXACT' tag."""
    try:
        sw = str((prov or {}).get("switch") or "NONE")
        f = ",".join(str(x) for x in ((prov or {}).get("filters") or [])) or "-"
        return f"|SW={sw}|F={f}"
    except Exception:
        return "|SW=UNATTRIBUTED:tail_error|F=-"


def with_tail(reason: str, prov) -> str:
    r = str(reason or "")
    if "|SW=" in r:
        return r
    return r + human_tail(prov)


# ---- dispatch registry (stocks transport: queue_trade_action runs the order on a different task, so a ContextVar cannot carry it) ----
_DISPATCH = {}


def note_dispatch(position_key: str, reason: str, prov) -> None:
    """Register the provenance of a vec order at the moment it is queued: matched at the history write by (position_key, reason substring)."""
    try:
        if not prov:
            return
        lst = _DISPATCH.setdefault(str(position_key), [])
        now = time.time()
        lst[:] = [x for x in lst if now - x[0] <= 3600.0]
        lst.append((now, str(reason or ""), prov))
    except Exception:
        pass


def resolve_dispatch(position_key: str, history_reason: str, window_s: float = 1800.0):
    """Newest registered prov (within window_s) whose dispatched reason is contained in the history reason. None when there is none."""
    try:
        lst = _DISPATCH.get(str(position_key)) or []
        now = time.time()
        hr = str(history_reason or "")
        for ts, rs, pv in reversed(lst):
            if now - ts > window_s:
                break
            if rs and rs in hr:
                return pv
    except Exception:
        pass
    return None


def register_dispatch(position_key: str, reason: str, prov, ev_type: str = "", cfg=None) -> None:
    """Dispatch-site hook (stocks): when HISTORY_PROV_ENABLED, register `prov` (or a token classification when the act carries none)
    against the exact reason string handed to the order queue. Never raises, never alters the reason."""
    try:
        if not enabled(cfg):
            return
        note_dispatch(position_key, reason, prov or classify_event(reason, ev_type))
    except Exception:
        pass


def is_entry_label(reason: str) -> bool:
    """True when the engine label is an ENTRY/RE-ENTRY/CONVERGE-OPEN path (never an exit family)."""
    lab = strip_label(reason)
    head = re.split(r"[ ,(]", lab, 1)[0]
    if head in ENTRY_BLOCK_KEYS or lab.startswith(_REENTRY_PREFIXES) or lab.startswith("VEC_CONVERGE_OPEN") or lab.startswith(("REENTRY_", "HARDCODED_RALLY")):
        return True
    return False


def external_exit_block(reason: str, trade_type: str, source=None, recent=None) -> dict:
    """Exit history event with NO matched bot order (exchange-side stop / manual / api-absence detection). The text `reason` of such an event is
    INHERITED from the previous history line (ez_positions_service._fetch_decision_context falls back to the last event of the sym_side), so an
    entry label on a REDUCE/CLOSE is not its reason. `recent` = the last vec exit dispatched for the key (basis stated), else UNATTRIBUTED."""
    inherited = strip_label(reason)[:80]
    if recent:
        b = dict(recent)
        b["attribution_basis"] = "LAST_VEC_EXIT_DISPATCH_WITHIN_30MIN_NO_ORDER_MATCH"
    else:
        b = {"v": VERSION, "src": "EXTERNAL", "switch": "UNATTRIBUTED:exit_without_bot_order", "cand": None, "filters": [], "tf": EXECUTION_TF, "set_id": None, "ev": str(trade_type or "").upper()}
    b["reason_inherited"] = True
    b["inherited_label"] = inherited
    if source:
        b["detected_by"] = str(source)
    return b


def broker_echo_block(reason: str, trade_type: str, echo_of_bot_event: bool) -> dict:
    b = classify_event(reason, trade_type)
    b["src"] = "BROKER"
    b["echo_of_bot_event"] = bool(echo_of_bot_event)
    if not echo_of_bot_event:
        b["switch"] = "UNATTRIBUTED:broker_fill_without_bot_event"
    return b
