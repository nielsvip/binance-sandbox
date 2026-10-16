#!/usr/bin/env python3
"""per_sym_trade_reason_audit — every /history trade decided AFTER its sym_side's live settings were
applied must be reachable under those settings.

For each account in data/history/, each event with ts >= the sym_side's per_sym_active.updated_at is
classified by its reason string and checked against the gate(s) that reason's emitter actually reads.
Events before that timestamp were decided under an older set and are counted as `pre_settings`, not
judged. GATES / ANY_GATES / TF_GATES are sourced from the emitter lines in v12_quick_engine.py,
ez_manage.py, tradier_manage.py and vec_decisions/*.py — never inferred from the reason's name
(REDUCE_TO_FLAT is shared by REGIME_GATE / EXIT_DC_BREACH_REDUCE / FAST_RISER; PROFIT_TP_EXHAUSTION is
gated by MOMENTUM_TP_ENABLED). A reason with no verified gate is reported as `unmapped` with a count
and is NOT asserted either way.

Companion to tools/per_sym_live_parity_audit.py, which checks the settings layers themselves.
Usage: REASON_AUDIT_OUT=<dir> python3 tools/per_sym_trade_reason_audit.py"""
import collections
import glob
import json
import os
import re
import sqlite3
import sys

ROOT = "/Users/niels/Documents/binance"
SC = os.environ.get("REASON_AUDIT_OUT", "/tmp")
sys.path.insert(0, ROOT)
from per_sym_store import _maybe_decompress, _mandate_sanitize

# reason-prefix -> gate switch(es) (ALL must be truthy) — sourced from v12_quick_engine.py /
# vec_decisions/*.py / ez_manage.py line refs recorded in the report
GATES = {
    "B09": ["REENTRY_B09_SNAPBACK_ENABLED"],
    "B11": ["REENTRY_B11_DC_BREAK_ENABLED"],
    "B12": ["REENTRY_B12_WT_MOM_ENABLED"],
    "B14": ["REENTRY_B14_HA_TREND_ENABLED"],
    "B15": ["REENTRY_B15_STRONG_TREND_ENABLED"],
    "B_KZONE": ["K_ZONE_ENTRY_ENABLED"],
    "B_SRS_ENTRY": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "B_EMA20SLOPE": ["EMA20_SLOPE_ENTRY_ENABLED"],
    "B_EMADIST": ["EMA_DIST_ENTRY_ENABLED"],
    "B_SMA200DIST": ["SMA200_DIST_ENTRY_ENABLED"],
    "B_MOM3": ["MOM3_ENTRY_ENABLED"],
    "B_MOM5": ["MOM5_ENTRY_ENABLED"],
    "B_BBSQUEEZE": ["BB_SQUEEZE_ENTRY_ENABLED"],
    "B_BBPCTB": ["BB_PCTB_ENTRY_ENABLED"],
    "B_RSI2_ENTRY": ["RSI2_ENABLED"],
    "B_CONNORS_ENTRY": ["CONNORS_RSI_ENABLED"],
    "B_WT_DC_LIVE": ["WT_DC_ENABLED"],
    "B_STOCH_ENTRY": ["STOCH_ENTRY_ENABLED"],
    "B_WT_ENTRY": ["WT_ENTRY_ENABLED"],
    "B_SATOSHIT_ENTRY": ["SATOSHIT_ENTRY_ENABLED"],
    "DC_BREAKOUT_ENTRY": ["DC_BREAKOUT_ENTRY_ENABLED"],
    "GOLDEN_RULE_ENTRY": ["GOLDEN_RULE_ENABLED"],
    "WR_PULLBACK": ["WR_PULLBACK_ENABLED"],
    "FORMATION_ENTRY": ["WR_PULLBACK_ENABLED"],
    "EXIT_VELOCITY_WT": ["EXIT_VELOCITY_WT_ENABLED"],
    "LH_LL_TOP_EXIT": ["LH_LL_TOP_EXIT_ENABLED"],
    "HARDCODED_RALLY_REENTRY": ["HARDCODED_RALLY_REENTRY_ENABLED"],
    "REENTRY_EXIT_PRICE_BREAK": ["REENTRY_ON_EXIT_PRICE_BREAK_ENABLED"],
    "REENTRY_DC4_15M_BREAK": ["REENTRY_ON_DC4_15M_BREAK_ENABLED"],
    "REENTRY_HIGHER_LOW": ["REENTRY_ON_HIGHER_LOW_ENABLED"],
    "REENTRY_LOWER_HIGH": ["REENTRY_ON_HIGHER_LOW_ENABLED"],
    "DAYTRADE_TARGET": ["DC_DAYTRADE_ENABLED"],
    "DAYTRADE_STOP": ["DC_DAYTRADE_ENABLED"],
    "WT_CROSS_EXIT": ["WT_CROSS_EXIT_ENABLED"],
    "STRUCTURAL_RANGE_SHIFT": ["STRUCTURAL_RANGE_SHIFT_EXIT"],
    "SPIKE_FADE": ["SPIKE_FADE_EXIT_ENABLED"],
    "VOL_SPIKE_REVERSAL": ["VOL_SPIKE_ENABLED"],
    "MFI2_MEAN_REVERSION": ["MFI2_MEAN_REVERSION_ENABLED"],
    "MULTI_TF_EXIT": ["MULTI_TF_EXIT_ENABLED"],
    "MTF_BB_REJECT": ["MTF_EXIT_USE_COMPOUND", "MTF_BB_REJECT_EXIT_ENABLED"],
    "MTF_GR_WT_EXIT": ["MTF_EXIT_USE_COMPOUND", "MTF_GR_EXIT_GATE_ENABLED", "MTF_WT_CROSS_EXIT_ENABLED"],
    "RSI2_XTREME_ENTRY": ["RSI2_XTREME_ENTRY_ENABLED"],
    "WT_CROSSUNDER_FINAL": ["WT_CROSSUNDER_FINAL_ENABLED"],
    "PROFIT_TP_EXHAUSTION": ["MOMENTUM_TP_ENABLED"],  # ez_manage.py:57602 — there is no PROFIT_TP_EXHAUSTION_ENABLED switch
    "BREAKOUT_LEASH_EXIT": ["BREAKOUT_LEASH_ENABLED"],
    "CYCLE_TP_TIERED": ["CYCLE_TP_TIERED_ENABLED"],
    "MTF_DC_REJECT": ["MTF_DC_REJECT_EXIT_ENABLED"],
    "STDEV_REJECT_EXIT": ["STDEV_REJECT_EXIT_ENABLED"],
    "MI_EXIT": ["MI_EXIT_ENABLED"],
    "BB_FROZEN_STOP_BREACH": ["BB_FROZEN_STOP_ENABLED"],
    "ALL_TF_AGAINST_CLOSE": ["ALL_TF_AGAINST_CLOSE_ENABLED"],
    "REGIME_REDUCE": ["REGIME_GATE_ENABLED"],
    "QUICK_REDUCE_STRONG": ["HLR_TOP_EXIT_ENABLED"],
    "PPL_TP": ["PARTIAL_PROFIT_LOCK_ENABLED"],
    "PPL_SL": ["PARTIAL_PROFIT_LOCK_ENABLED"],
    "PPL_SL_CLOSE": ["PARTIAL_PROFIT_LOCK_ENABLED"],
    "UNCOND_DC_HIGH_15M": ["DC_HARD_STOP_15M_ENABLED"],
    "UNCOND_DC_LOW_15M": ["DC_HARD_STOP_15M_ENABLED"],
    "UNCOND_DC_HIGH_1H": ["DC_HARD_STOP_1H_ENABLED"],
    "UNCOND_DC_LOW_1H": ["DC_HARD_STOP_1H_ENABLED"],
    "DC_BREAK_HIGH_15M": ["DC_BREAKOUT_ENTRY_ENABLED"],
    "DC_BREAK_HIGH_5M": ["DC_BREAKOUT_ENTRY_ENABLED"],
    "DC_BREAK_LOW_15M": ["DC_BREAKOUT_ENTRY_ENABLED"],
    "DC_BREAK_LOW_5M": ["DC_BREAKOUT_ENTRY_ENABLED"],
}
# TF-style gates: fire only when the switch is not OFF/None
ANY_GATES = {
    "REDUCE_TO_FLAT": ["REGIME_GATE_ENABLED", "EXIT_DC_BREACH_REDUCE_ENABLED", "ENABLE_FAST_RISER_REDUCE"],
    "Winner": ["HAIKU_WINNER_ENABLED", "HAIKU_WINNER_AUGMENT_ENABLED"],
    "HAIKU_WINNER_AUG": ["HAIKU_WINNER_ENABLED", "HAIKU_WINNER_AUGMENT_ENABLED"],
    "STATEFUL_PORTED_EXIT": ["WT_4H_VEL_EXIT_ENABLED", "WT_EXHAUST_EXIT_ENABLED", "WT_DC_EXIT_ENABLED",
                             "WRONG_SIDE_ABS_KILL_ENABLED", "MU_CORRECTION_EXIT_ENABLED"],
    "E_3_STRUCTURE_EXIT": ["E_3_USE_WT_STRUCTURE_EXIT_MODE"],
    "HAIKU_REDUCE": ["HAIKU_WINNER_AUGMENT_ENABLED", "HAIKU_WINNER_ENABLED"],
    "PEAK_GIVEBACK_GAIN_EROSION_STOP": ["PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", "BREAKEVEN_GAIN_EROSION_ENABLED",
                                        "HARD_BREAKEVEN_FLOOR_ENABLED"],
}
TF_GATES = {"WT_LOWER_CROSS_EXIT": "WT_LOWER_CROSS_EXIT_TF",
            "TECHNICAL_TARGET": "TECHNICAL_DC_TARGET_TF",
            "TECHNICAL_STOP": "TECHNICAL_DC_STOP_TF",
            "VIGILANCE_DC4": "VIGILANCE_DC4_STOP_TF",
            "HTF_WT_CHURN_REENTRY": "HTF_WT_CHURN_REENTRY_MAX_AGE_MIN"}
# bookkeeping / reconciliation / infrastructure — not a strategy decision
BOOKKEEPING = (
    "BROKER_RECONCILED", "BROKER_SYNC", "BROKER_CLOSE", "GHOST_CLOSE", "Broker", "SYNC_DETECTION",
    "VEC_STALE_FLATTEN", "VEC_EXIT_OWED", "VEC_CONVERGE", "RECONCIL", "MANUAL", "ORPHAN",
    "FIXED_STOP", "EXCHANGE_STOP", "MANAGED_STOP", "PRE_CLOSE", "EOD", "EXPIRY",
    "HEDGE", "RATIO_", "CAPITAL_", "LIQUIDATION", "MARGIN", "STALE", "WATCHDOG",
)
WRAPPERS = ("MAKER_PROFIT_EXIT_", "VEC_EXACT_OPEN_", "VEC_EXACT_", "ULTIMATE_")


def truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def tf_on(v):
    return str(v).strip().upper() not in ("", "OFF", "NONE", "NULL", "0", "FALSE")


def classify(reason):
    """-> (kind, token). kind in {'gated','tf','book','unmapped'}"""
    r = str(reason or "").strip()
    if not r:
        return "book", "(empty)"
    core = r.split("|")[0].strip()
    for w in WRAPPERS:
        while core.startswith(w):
            core = core[len(w):]
    for b in BOOKKEEPING:
        if b.upper() in core.upper():
            return "book", b
    toks = [t for t in re.split(r"[ _]", core) if t]
    # longest-prefix match against the gate map
    for n in (4, 3, 2, 1):
        cand = "_".join(toks[:n])
        if cand in GATES:
            return "gated", cand
        if cand in ANY_GATES:
            return "any", cand
        if cand in TF_GATES:
            return "tf", cand
    for k in GATES:
        if core.upper().startswith(k.upper()):
            return "gated", k
    for k in ANY_GATES:
        if core.upper().startswith(k.upper()):
            return "any", k
    for k in TF_GATES:
        if core.upper().startswith(k.upper()):
            return "tf", k
    return "unmapped", re.split(r"[ |]", core)[0][:48]


def _load_pins():
    """live_twins/vec_exact per-position SET PIN (2026-10-14 twin-drift fix, pin_activate docstring
    ~line 119): "A held live position keeps the set it is held under until it is flat: exits
    (CLOSE/REDUCE) of that position come from the same set's state machine." So an EXIT whose gate is
    off in the CURRENTLY applied set is not a breach when the position was pinned to an older set —
    that is the design, and AUGMENT is already refused while pinned != latest (pin_is_latest).
    pin_release drops the entry once live goes flat (grace VEC_EXACT_OWNED_DROP_GRACE_S), so a pin
    that has since been released leaves NO record: those events are reported as
    `exit_pin_unresolved`, never as proven violations. Kill switch VEC_EXACT_PIN_SET_PER_POSITION."""
    pins = {}
    for p in glob.glob(os.path.join(ROOT, "data", "vec_exact_pinned_*.json")):
        acct = os.path.basename(p)[len("vec_exact_pinned_"):-len(".json")]
        try:
            for pk, ent in (json.load(open(p)) or {}).items():
                if not isinstance(ent, dict):
                    continue
                ov = ent.get("ov") or {}
                if ov:
                    pins[(acct, str(pk).split(":", 1)[-1].upper())] = (str(ent.get("at") or ""), ov)
        except Exception:
            continue
    return pins


EXIT_TYPES = ("REDUCE", "CLOSE", "GHOST_CLOSE", "BROKER_CLOSE")
PINS = _load_pins()

con = sqlite3.connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True, timeout=300)
con.row_factory = sqlite3.Row
live, applied_at = {}, {}
for r in con.execute("SELECT sym_side,updated_at,full_config_json FROM per_sym_active"):
    try:
        fc = json.loads(_maybe_decompress(r["full_config_json"]))
    except Exception:
        continue
    live[r["sym_side"]] = _mandate_sanitize(dict(fc))
    applied_at[r["sym_side"]] = str(r["updated_at"] or "")
print(f"live rows with full_config: {len(live)}", file=sys.stderr)

viol = []
pin_unresolved = []
stats = collections.Counter()
unmapped = collections.Counter()
norow = collections.Counter()
per_acct = collections.defaultdict(collections.Counter)
for acct in ["ang", "fin", "flz", "inf", "men", "trb"]:
    d = os.path.join(ROOT, "data/history", acct)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        ss = fn[:-6]
        cfg = live.get(ss)
        since = applied_at.get(ss)
        for line in open(os.path.join(d, fn), errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = str(e.get("ts") or "")
            if cfg is None:
                if ts >= "2026-10-09":
                    norow[f"{acct}/{ss}"] += 1
                continue
            if not since or ts < since:
                stats["pre_settings"] += 1
                continue
            kind, tok = classify(e.get("reason"))
            stats[kind] += 1
            per_acct[acct][kind] += 1
            if kind == "book":
                continue
            if kind == "unmapped":
                unmapped[tok] += 1
                continue
            bad = []
            if kind == "gated":
                for g in GATES[tok]:
                    if g not in cfg:
                        bad.append((g, "ABSENT"))
                    elif not truthy(cfg[g]):
                        bad.append((g, cfg[g]))
            elif kind == "any":
                vals = {g: cfg.get(g, "ABSENT") for g in ANY_GATES[tok]}
                if not any(truthy(v) for k2, v in vals.items() if v != "ABSENT"):
                    bad = sorted(vals.items())
            else:
                g = TF_GATES[tok]
                if g not in cfg:
                    bad.append((g, "ABSENT"))
                elif not tf_on(cfg[g]):
                    bad.append((g, cfg[g]))
            if bad and str(e.get("type") or "").upper() in EXIT_TYPES:
                # the exit may legitimately come from the position's PINNED set — see _load_pins()
                pin = PINS.get((acct, ss.upper()))
                if pin is not None and pin[0] and pin[0] <= ts:
                    pcfg = pin[1]
                    if all(truthy(pcfg.get(g)) if g in pcfg else False for g, _ in bad):
                        stats["exit_pinned_set"] += 1
                        per_acct[acct]["exit_pinned_set"] += 1
                        continue
                stats["exit_pin_unresolved"] += 1
                per_acct[acct]["exit_pin_unresolved"] += 1
                pin_unresolved.append({"acct": acct, "sym_side": ss, "ts": ts, "token": tok,
                                       "gates": bad, "applied_at": since,
                                       "pin": "released_or_absent" if pin is None else f"pinned_at {pin[0]}"})
                continue
            if bad:
                viol.append({"acct": acct, "sym_side": ss, "ts": ts, "type": e.get("type"),
                             "token": tok, "reason": str(e.get("reason"))[:120],
                             "gates": bad, "applied_at": since, "value": e.get("value")})

print(json.dumps({"stats": dict(stats), "per_acct": {k: dict(v) for k, v in per_acct.items()}}, indent=1))
print(f"\nEXITS ON A PINNED (older) SET — by design, not breaches: {stats['exit_pinned_set']}")
print(f"EXITS whose gate is off in the applied set and whose pin was released/absent "
      f"(UNRESOLVED, not proven breaches): {len(pin_unresolved)}")
for v in pin_unresolved[:12]:
    print(f"    {v['acct']:4s} {v['sym_side']:22s} {v['ts']}  {v['token']:24s} {v['gates'][0][0]}={v['gates'][0][1]}  {v['pin']}")
print(f"\nVIOLATIONS: {len(viol)}")
vb = collections.Counter((v["acct"], v["token"], v["gates"][0][0], str(v["gates"][0][1])) for v in viol)
for (a, t, g, val), n in vb.most_common(40):
    print(f"  {n:5d}  {a:4s} {t:26s} gate {g}={val}")
print(f"\nTRADED WITH NO LIVE ROW (events since 2026-10-09): {len(norow)}")
for k, n in norow.most_common(20):
    print(f"  {n:5d}  {k}")
print(f"\nUNMAPPED reason heads: {len(unmapped)}")
for k, n in unmapped.most_common(30):
    print(f"  {n:5d}  {k}")
json.dump(viol, open(SC + "/violations.json", "w"), indent=1)
