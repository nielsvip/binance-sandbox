#!/usr/bin/env python3
"""Build data/v17_filter_exemptions_schema.json + data/v17_filter_exemptions.json (V17 Task B, 2026-10-13).

Consumer: the pilot redesign agent. For every (switch, filter) pair it gets the override dict that exempts that switch's
entry/reentry/augment path from that filter:
  * an EXISTING key where one already scopes that filter away from that path (path-specific filter = its own off value,
    HTF_GATE_APPLY_TO_AUGMENT, SCALP_V3_BYPASS_HTF_DIRECTION_GATE), else
  * the V17 mechanism {"FILTER_EXEMPTIONS": {path_id: [filter_key]}} (vec_decisions/filter_exemptions.py).
Inputs are the real sources only: the 4 TEMPLATE_FINAL_NORM templates (ENTRY_*/REENTRY_*/AUGMENT_* tab switches),
v12_quick_engine._ENTRY_FAMILY_ALIASES/_ENTRY_FAMILY_MASTERS + the block names assigned in compute_reentry_blocks /
compute_entry_signals, vec_decisions.filter_ablation_groups.GROUPS, and grep read-counts of the existing exemption keys.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENTRY_TABS = ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "STDEV_SLOPE_SIZING")
REENTRY_TABS = ("REENTRY_WINDOWED", "REENTRY_ADAPTIVE")
AUG_TABS = ("AUGMENT_TREND", "AUGMENT_RISK_SIZING")
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
AUG_SOURCE_OF = [
    (r"^(PYRAMID_|BOUNCE_AUGMENT_|SHORT_DC_LOW_BREAK|SHORT_PARTIAL_RECOVERY|AUGMENT_BULL_KILL|DELTA_PYRAMID|AUGMENT_TYPED_MIN_GAIN)", "AUGMENT_SIGNAL"),
    (r"^(AUGMENT_MIN_GAIN_PCT|MIN_GAIN_TO_BUY_AGGRESSIVELY|PULLBACK_AUGMENT|UNIVERSAL_AUGMENT_GAIN_GATE)", "AUGMENT_LADDER"),
    (r"^FAST_RISER", "AUGMENT_FAST_RISER"),
    (r"^AUGMENT_AT_LOSS", "AUGMENT_AT_LOSS"),
    (r"^HAIKU_WINNER", "AUGMENT_HAIKU"),
    (r"^DD_BOUNCE", "AUGMENT_DD_BOUNCE"),
    (r"^(DC_TIER|TRAILING_AUG|WT_D_BOUNCE_AUG|LR_BAND_LADDER|STOCK_LIVE_AUGMENT)", "AUGMENT_STOCK_LIVE"),
]
# existing scoped exemptions: (path predicate, filter) -> override
EXISTING_RULES = [
    ("SCALP_V3", "HTF_DIRECTION_GATE_ENABLED", {"SCALP_V3_BYPASS_HTF_DIRECTION_GATE": True}),
]
# existing keys whose scope is wider than one switch (all augment sources / all reentries): listed as alternatives only,
# never used as the per-switch value (a per-switch measurement must not change other paths).
GLOBAL_ALTERNATIVES = {
    ("AUGMENT", "HTF_GATE_APPLY_TO_AUGMENT"): {"HTF_GATE_APPLY_TO_AUGMENT": False},
    ("AUGMENT", "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"): {"UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": False},
    ("AUGMENT", "AUG_GAIN_GATE_LIVE_TWIN_ENABLED"): {"AUG_GAIN_GATE_LIVE_TWIN_ENABLED": False},
    ("REENTRY", "*entry-stack*"): {"REENTRY_APPLY_ENTRY_GATES_ENABLED": False, "REENTRY_ENTRY_FILTER_ENABLED": False},
}
EXEMPT_KEY_PAT = re.compile(r"BYPASS|IGNORE|EXEMPT|APPLY_TO|APPLY_ENTRY|FILTER_ALL|_SCOPE|ON_FINAL|ENTRY_GATES|REENTRY_ENTRY_FILTER|REQUIRE_PRIOR|_ONLY_FOR|ISOLATE")
PATH_TOKENS = ("MANDATORY_REENTRY", "GUARANTEED_REENTRY", "HARDCODED_RALLY", "DELTA_REENTRY", "REENTRY2_DC_BREAK", "SCALP_V3", "BTC_GUARANTEED_REENTRY", "BTC_BREAKOUT_REENTRY", "DAEMON_REENTRY", "GAP_RISK_REENTRY", "TRADIER_REENTRY", "REENTRY_WAVETREND", "REENTRY_BAR_TURN", "VEC_REENTRY", "REENTRY_SYMGATE")


def template_switches():
    import openpyxl
    out = {}
    for cat in CATS:
        p = os.path.join(ROOT, "SPREADSHEETS", "TEMPLATE_FINAL_NORM", f"TEMPLATE_{cat}.xlsx")
        wb = openpyxl.load_workbook(p, read_only=True)
        for tab in ENTRY_TABS + REENTRY_TABS + AUG_TABS:
            if tab not in wb.sheetnames:
                continue
            for r in wb[tab].iter_rows(min_row=3, max_col=1, values_only=True):
                if r and r[0]:
                    d = out.setdefault(str(r[0]).strip(), {"tabs": set(), "cats": set()})
                    d["tabs"].add(tab)
                    d["cats"].add(cat)
    return out


def block_names(src):
    a = src.index("def compute_reentry_blocks")
    b = src.index("def compute_exit_signals")
    return sorted(set(re.findall(r'blocks\["([A-Z0-9_]+)"\]', src[a:b])))


def read_counts(key):
    out = {}
    for f in ("v12_quick_engine.py", "ez_manage.py", "ez_positions_quick.py", "tradier_manage.py"):
        try:
            out[f] = len(re.findall(r"\b" + re.escape(key) + r"\b", open(os.path.join(ROOT, f)).read()))
        except Exception:
            out[f] = None
    vd = 0
    for fn in os.listdir(os.path.join(ROOT, "vec_decisions")):
        if fn.endswith(".py") and not fn.startswith("test_"):
            try:
                vd += len(re.findall(r"\b" + re.escape(key) + r"\b", open(os.path.join(ROOT, "vec_decisions", fn)).read()))
            except Exception:
                pass
    out["vec_decisions/*"] = vd
    return out


def main():
    import dataclasses
    import v12_quick_engine as V
    import vec_decisions.filter_ablation_groups as G
    import vec_decisions.filter_exemptions as FX
    src = open(os.path.join(ROOT, "v12_quick_engine.py")).read()
    qnames = [f.name for f in dataclasses.fields(V.QuickConfig)]
    aliases = dict(V._ENTRY_FAMILY_ALIASES)
    masters = dict(V._ENTRY_FAMILY_MASTERS)
    blocks = block_names(src)
    master_to_fam = {m: fam for fam, ms in masters.items() for m in ms}
    fam_prefix = {fam: [m.replace("_ENABLED_TRADIER", "").replace("_ENABLED", "") for m in ms] for fam, ms in masters.items()}
    filt_group = {f: g for g, fs in G.GROUPS.items() for f in fs}
    entry_filters = sorted(f for f, g in filt_group.items() if g in ("ABLATION_DISABLE_FILTER_ENTRY", "ABLATION_DISABLE_FILTER_MTF_HTF", "ABLATION_DISABLE_FILTER_REENTRY"))
    sw = template_switches()
    path_of, unmapped = {}, {}
    for s, meta in sorted(sw.items()):
        tabs = meta["tabs"]
        if s in FX.FILTER_KEYS or s.startswith("ABLATION_"):
            unmapped[s] = "is a filter / ablation key itself (exempt FROM, not a path)"
            continue
        if tabs & set(AUG_TABS):
            src_id = next((pid for pat, pid in AUG_SOURCE_OF if re.search(pat, s)), None)
            if src_id:
                path_of[s] = src_id
                continue
        if s in master_to_fam:
            path_of[s] = master_to_fam[s]
            continue
        if tabs & set(REENTRY_TABS) or "REENTRY" in s:
            path_of[s] = "REENTRY"
            continue
        if tabs & set(ENTRY_TABS):
            fam = None
            for al in sorted(aliases, key=len, reverse=True):
                if s.startswith(al + "_"):
                    fam = al
                    break
            if fam is None:
                for f, pfx in fam_prefix.items():
                    if any(s.startswith(p + "_") or s == p for p in pfx):
                        fam = f
                        break
            if fam is None:
                b = next((b for b in blocks if b.replace("B_", "") and s.startswith(b.replace("B_", "") + "_")), None)
                fam = b
            if fam:
                path_of[s] = fam
                continue
            unmapped[s] = "entry-tab knob with no vec entry family/block (sizing / global gate / non-block OR-source)"
            continue
        unmapped[s] = "augment-tab knob that is not an augment source (sizing multiplier / cap / cooldown / filter TF)"
    pairs = {}
    existing_pairs = []
    for s, pid in sorted(path_of.items()):
        kind = FX.classify(pid)
        flts = list(FX.AUGMENT_GATE_KEYS) if kind == "AUGMENT" else entry_filters
        row = {}
        for f in flts:
            ov = None
            for pred, fk, eov in EXISTING_RULES:
                if fk == f and ((pred == "AUGMENT_*" and kind == "AUGMENT") or (pred != "AUGMENT_*" and pred in s)):
                    ov = dict(eov)
            if ov is None and kind == "REENTRY" and filt_group.get(f) == "ABLATION_DISABLE_FILTER_REENTRY":
                tok = next((t for t in PATH_TOKENS if t in f), None)
                if tok and tok in s:
                    ov = {f: FX.off_value(getattr(V.QuickConfig(), f, True)) if FX.off_value(getattr(V.QuickConfig(), f, True)) is not None else False}
            if ov is not None:
                existing_pairs.append([s, f, ov])
            else:
                ov = {"FILTER_EXEMPTIONS": {pid: [f]}}
            row[f] = ov
        pairs[s] = row
    inv = {}
    for k in sorted(set(n for n in qnames if EXEMPT_KEY_PAT.search(n))):
        inv[k] = {"reads": read_counts(k)}
    schema = {
        "_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "generator": "tools/v17_build_filter_exemptions.py", "engine_md5_hint": "v12_quick_engine.py with V17 FILTER_EXEMPTIONS hooks"},
        "mechanism": {
            "config_key": "FILTER_EXEMPTIONS",
            "type": "dict {path_id: [filter_key, ...]}",
            "default": {},
            "default_behaviour": "identical to pre-V17 (empty map -> no extra reads/passes)",
            "module": "vec_decisions/filter_exemptions.py",
            "path_kinds": {
                "ENTRY": "entry family alias or raw entry block name; second simulate_one prelude with the filters off + ENTRY_ISOLATE_FAMILY=<path>; extra opens R = raw_block_A & entry_B, open chokes take the filters-off state on R bars; a family whose master is off has no raw bars (never enabled by an exemption)",
                "REENTRY_granularity": "path-wide: the exemption applies to EVERY reentry fire, not only the switch's own reentry family (the vector resolves a reentry fire's origin after the open gates). Path-specific reentry filters (e.g. MANDATORY_REENTRY_WT_FILTER_ENABLED) are listed in existing_key_pairs as their own off value instead",
                "REENTRY": "all reentry fires (post-close opens that are not fresh entry_sig bars); second prelude with the filters off; reentry fires read its open-gate arrays (_strict_open_block, _ec_choke_block, _atf_entry_block, _qta_ct_block, _w2_gr_block, _w2_dc4_block, _bt_veto, _dg_block, _rag_pass, _entry_filter_masks)",
                "AUGMENT": "AUGMENT (all sources) or AUGMENT_<SRC>; in-loop augment choke gates bypassed when any active source of the bar is exempt",
            },
            "augment_sources": list(FX.AUGMENT_SOURCES),
            "augment_gate_keys": list(FX.AUGMENT_GATE_KEYS),
            "filter_keys_valid_for_entry_reentry": "members of vec_decisions/filter_ablation_groups.GROUPS (bool -> False, TF -> 'OFF'); numeric thresholds ignored",
            "live": "crypto: honoured by construction (PARITY_VEC_EXACT_MODE=True, families ENTRY,EXIT,AUGMENT -> live decision = this simulate_one with the per-sym promoted set). stocks: honoured only with config_tradier.PARITY_VEC_EXACT_MODE=True (tradier_vec_exact). Native live paths (exact mode off) do NOT read FILTER_EXEMPTIONS -> pilot must not promote it for a venue running native.",
            "pilot_protocol (USER)": "1) filter ablation ON -> find switches with positive delta; 2) pinpoint which filter suppresses each (pair delta: switch+exemption vs switch alone, filters ON); 3) promote {switch: value} + the pair's override dict from v17_filter_exemptions.json; 4) filters back ON, delta measured vs the previous best gain",
        },
        "path_ids": {"entry_family_aliases": aliases, "entry_family_masters": masters, "entry_block_names": blocks, "reentry": ["REENTRY"], "augment": ["AUGMENT"] + ["AUGMENT_" + s for s in FX.AUGMENT_SOURCES]},
        "switch_to_path": path_of,
        "switches_unmapped": unmapped,
        "filters": {f: {"group": filt_group.get(f), "off_value_kind": "TF->OFF" if f.endswith("_TF") or "FILTER_TF" in f else "bool->False"} for f in entry_filters},
        "augment_gate_sites": {
            "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": "vec_decisions.uagain_gate.uagain_gate_pass on SIGNAL/FAST_RISER sources (live execute_now UAG choke)",
            "AUG_GAIN_GATE_LIVE_TWIN_ENABLED": "vec_decisions.aug_gain_gate.passes (live execute_trade_action AUGMENT gain gate)",
            "HTF_GATE_APPLY_TO_AUGMENT": "_htf_aug_ok (live execute_trade_wrapper HTF gate on AUGMENT)",
            "HTF_DIRECTION_GATE_ENABLED": "_htf_aug_ok",
            "COUNTER_TREND_ADD_BLOCK_ENABLED": "_qta_ct_block (stocks queue_trade_action)",
            "STOCKS_OPENING_BUFFER_ENTRY_ENABLED": "_sg_obuf",
            "AUGMENT_ONLY_WHEN_PROFITABLE": "_augment_allowed",
            "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER": "_augment_allowed",
        },
        "existing_exemption_keys": inv,
        "existing_key_pairs": existing_pairs,
        "global_scope_alternatives": [{"path_kind": k[0], "filter": k[1], "override": v, "note": "wider than one switch — alternative only"} for k, v in GLOBAL_ALTERNATIVES.items()],
        "coverage": {"template_switches": len(sw), "mapped_switches": len(path_of), "unmapped": len(unmapped), "pairs": sum(len(v) for v in pairs.values()), "existing_key_pairs": len(existing_pairs), "entry_reentry_filters": len(entry_filters), "augment_gates": len(FX.AUGMENT_GATE_KEYS)},
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump(schema, open(os.path.join(ROOT, "data", "v17_filter_exemptions_schema.json"), "w"), indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    json.dump(pairs, open(os.path.join(ROOT, "data", "v17_filter_exemptions.json"), "w"), indent=0, sort_keys=True)
    print(json.dumps(schema["coverage"], indent=1))


if __name__ == "__main__":
    main()
