#!/usr/bin/env python3
"""v15_knowledge_graph — ENCYCLOPEDIA v2: a knowledge graph of switches, filters and function families (director 2026-10-06).

Built from CODE first, evidence second (never guessed):
  * reads   : AST scan of v12_quick_engine.py + vec_decisions/*.py — every getattr(<cfg>, 'FIELD') / <cfg>.FIELD whose FIELD is a
              QuickConfig field, with (file, top-level function, line). A switch "parameterises" every function that reads it.
  * calls   : AST scan of the same files — which vec_decisions module.function each top-level function calls (and on which line).
  * families: every reading function belongs to a function family (ENTRY_OPEN / ENTRY_GATE / REENTRY / EXIT_CLOSE / EXIT_LABEL /
              EXIT_VETO / AUGMENT / REDUCE / SIZING / GLOBAL). The family of a vec module is rule-derived from its module/function
              name (table FAMILY_RULES, printed in graph.md); a switch read only inside simulate_one/compute_* gets the family of
              its name stem + SWITCH_BIBLE kind + TEMPLATE tab.
  * filters : the TEMPLATE yellow cells (fill FFFFFF00) — header FILTER=opt yellow on switch row X => edge filter -[gates]-> switch.
  * order   : exit precedence = first occurrence of each exit's reason anchor inside simulate_one's POSITION branch (first hit wins,
              ENCYCLOPEDIA ch.05 §3.3.2) -> edge exit_k -[preempts]-> exit_k+1. Open-choke gate modules -[starves]-> every opener.
  * masters : code blocks that force other switches off (e.g. CRYPTO_REENTRY_PATHWAYS_ENABLED) -> edge master -[enables]-> switch.
  * utility : what each switch=value is FOR, from real engine evidence only: data/causal/causal_map_{cat}.csv (single-step Δ of
              every TEMPLATE row vs 91 sym_sides' bases), raw repair JSONs (trade-autopsy row recommendations: losers fixed,
              premature exits fixed, augments fixed), SPREADSHEETS/v15_avg_delta_latest.xlsx (avg_delta, pos_sym), and — when
              present — group-ablation results of tools/v15_graph_search.py (data/encyclopedia_v2/ablation/*.json).
Outputs: data/encyclopedia_v2/graph.json, graph.md, template_row_utility.csv (+ .md).
usage: python tools/v15_knowledge_graph.py [--ablation-dir data/encyclopedia_v2/ablation]
"""
from __future__ import annotations

import argparse
import ast
import csv
import glob
import json
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "encyclopedia_v2"
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY",
                 "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")
GENERIC_FUNCS = {"simulate_one", "compute_entry_signals", "compute_exit_signals", "compute_reentry_blocks", "compute_augment_signals_ex",
                 "compute_reduce_signals", "compute_regime_sizing_mult", "clamp_config", "apply_tradier_defaults", "apply_cat_side_defaults"}
# (regex on "module.function", family). First match wins. Module/function names are the code's own naming (ENCYCLOPEDIA ch.06 catalog).
FAMILY_RULES = [
    (r"entry_vet|rsi_t55|strict_open|funding_gate|oi_confirm|htf_direction|wt_div_entry|stdev_macro|gr_consensus|kindergarten|kg_entry|entry_hard_gates|entry_dc_gate|counter_trend|bb_pullback_gate|filter_tf|generic_filter|lh_hl_filter|htf_causal|mtf_gr_filter|overtrade_guard|live_unw_gates|lane_vec_gates2|stocks_trend_gates|stocks_live_session|ema_alignment|rsi_sma_mfi_gates|rvol_lrpctb|htf_w_m_align|strict_stoch|sba_gate|trend_entry_gate|bb_squeeze_gate|signal_entry_threshold|wtdc_entry_gates|delta_htf_gate|market_crash_blanket|blacklist", "ENTRY_GATE"),
    (r"reentry|rally|recross|obligatory|mandatory_reentry|guaranteed_price_cross|htf_wt_churn", "REENTRY"),
    (r"augment|haiku|pyramid|ladder_aug|band_ladder|gain_ladder|lr_band_ladder", "AUGMENT"),
    (r"reduce|profit_lock|ppl|partial|quick_breakeven|satoshit", "REDUCE"),
    (r"sizing|size_mult|sizer|regime_scale|slope_sizing|band_ladder_mult", "SIZING"),
    (r"veto|noloss|winner_protect|bottom_exit|hold", "EXIT_VETO"),
    (r"exit|stop|trail|kill|giveback|dc_channel|dc_break|reject|crash|breach|frozen|moc|gapmoc|gap_risk|top_exit|lh_ll|exhaust|momentum_tp|all_tf_against|wt15m_against|mtf_compound|mtf_exit|scorer_exit|exitscorer|struct|formation|delta_exit|hopeless|wt_cross|percentile|vel_exit|vel_slow|technical_breakdown|reversal_exit|ibs|e1_wt|e3_structure|htf_quick_tp|htf_flip|options_d|watchdog", "EXIT_CLOSE"),
    (r"entr|open|breakout|bounce|sba|stdev_breakout|vol_spike|wr_lr|compression|scalp|squeeze|lr_band|short_elevator|gr_htf_direct|higher_wt_cross|alt_entries|force_open|rule_a_retest|ported_entry|twin_entr", "ENTRY_OPEN"),
]
# exit precedence anchors (first occurrence AFTER the position-branch start). label = the reason family the ledger shows.
EXIT_ANCHORS = [("NEWBORN_LOSS_KILL", "NEWBORN_LOSS_KILL", "NEWBORN_LOSS_KILL_ENABLED"), ("ALL_TF_AGAINST_CLOSE", "ALL_TF_AGAINST_CLOSE", "ALL_TF_AGAINST_CLOSE_ENABLED"),
                ("LIVE_EXIT_CHAIN", "_lec", "LIVE_EXIT_CHAIN_ENABLED"), ("STOP_FUNCTIONS_KILL", "STOP_FUNCTIONS_KILL", "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED"),
                ("STATEFUL_PORTED_EXIT", "STATEFUL_PORTED_EXIT", "WT_EXHAUST_EXIT_ENABLED"), ("MULTI_TF_EXIT", "MULTI_TF_EXIT", "MIN_EXIT_GAIN_PCT"),
                ("MI_EXIT", "MI_EXIT", "MI_EXIT_ENABLED"), ("VIGILANCE_DC4_STOP", "VIGILANCE_DC4", "VIGILANCE_GUARD_ENABLED"),
                ("ULTIMATE_DC_HARD_STOP", "_HARD_STOP at", "DC_HARD_STOP_TF"), ("WT_LOWER_CROSS_EXIT", "WT_LOWER_CROSS_EXIT", "WT_LOWER_CROSS_EXIT_TF"),
                ("LH_LL_TOP_EXIT", "LH_LL_TOP_EXIT", "LH_LL_TOP_EXIT_ENABLED"), ("DYN_STRUCT_TRAIL", "DYN_STRUCT_TRAIL", "DYN_STRUCT_TRAIL_ENABLED"),
                ("AUGMENT_BLOCK", "gain_ladder", "MAX_AUGMENTS_PER_POSITION"), ("PARTIAL_PROFIT_LOCK", "ppl_step", "PARTIAL_PROFIT_LOCK_ENABLED"),
                ("DD_BOUNCE_STOP", "DD_BOUNCE_STOP", "WT_D_BOUNCE_DD_STOP_ENABLED"), ("DAYTRADE_DC", "daytrade_dc_exit", "DAYTRADE_DC_TARGET_TF"),
                ("QUICK_REDUCE/SELL_TOP", "HLR_TOP_EXIT_SELL_TOP", "HLR_TOP_EXIT_LIVE_SANCTIONED"), ("MTF_ATR_TRAIL", "MTF_ATR_TRAIL_", "MTF_ATR_TRAIL_ENABLED"),
                ("MTF_COMPOUND(DC/BB/GR)", "bb_reject_step", "MTF_EXIT_USE_COMPOUND"), ("GAP_MOC_EXIT", "GAP_MOC_EXIT", "GAP_MOC_EXIT_ENABLED"),
                ("TECHNICAL_EXIT(exit_sig)", "TECHNICAL_EXIT", "VEC_EXIT_SIG_IS_TRIGGER"), ("FINAL_MTM", "FINAL_MTM", "")]
NO_OFF_TOKENS = ("PARITY", "VEC_ONLY", "STRICT_VEC", "ABLATION", "TF_HTF", "BASE_TF", "MODE")
CHOKE_MODULES = ("entry_vet_gate", "stdev_macro_entry", "wave4_families", "gr_consensus_veto", "entry_vet_stocks", "funding")
UTILITY_FAULTS = {  # fault -> utilities that address it (ENCYCLOPEDIA §3 playbook, tested by the search)
    "TOO_FEW_TRADES": ("MORE_TRADES",), "FEW_TRADES": ("MORE_TRADES",), "TOO_MANY_TRADES": ("FEWER_TRADES", "FEWER_LOSERS"),
    "LOW_EDGE": ("FEWER_LOSERS", "HIGHER_GAIN"), "TIM_HIGH": ("LOWER_TIM", "EARLIER_EXIT"), "TIM_LOW": ("HIGHER_TIM", "LATER_EXIT", "MORE_TRADES"),
    "DD_HIGH": ("LOWER_DD", "FEWER_LOSERS"), "DD_WARN": ("LOWER_DD",), "GAIN_NEG": ("FEWER_LOSERS", "HIGHER_GAIN"), "LOW_WR": ("FEWER_LOSERS",),
    "BELOW_BH_MATERIAL": ("LATER_EXIT", "BIGGER_WINNERS", "HIGHER_TIM", "HIGHER_GAIN"), "PREMATURE_EXITS": ("LATER_EXIT",),
    "MISSED_AUGMENTS": ("BIGGER_WINNERS",), "MISSED_MOVES": ("MORE_TRADES", "HIGHER_TIM"), "FAIL_365D_DD": ("LOWER_DD", "FEWER_LOSERS", "EARLIER_EXIT"),
    "FAIL_365D_TRADES": ("MORE_TRADES",), "FAIL_365D_GAIN": ("FEWER_LOSERS", "HIGHER_GAIN"), "FAIL_365D_TIM": ("LOWER_TIM", "EARLIER_EXIT"),
}


def cat_side_of(ss: str) -> str:
    sym, side = ss.rsplit("_", 1)
    return ("CRYPTO" if sym.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD")) else "STOCKS") + "_" + side


# ───────────────────────── code scan ─────────────────────────
def quickconfig_fields(engine: Path) -> set:
    tree = ast.parse(engine.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QuickConfig":
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    out.add(st.target.id)
                elif isinstance(st, ast.Assign):
                    for t in st.targets:
                        if isinstance(t, ast.Name):
                            out.add(t.id)
    return {f for f in out if f.isupper() or (f[:1].isupper() and "_" in f)}


def scan_file(path: Path, fields: set) -> tuple:
    """-> (reads [(field, func, line)], calls [(func, module, callee, line)], funcs {name: (start, end)})."""
    src = path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], [], {}
    reads, calls, funcs = [], [], {}
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(top, ast.ClassDef) and top.name == "QuickConfig":
            continue
        fname = top.name
        funcs[fname] = (top.lineno, getattr(top, "end_lineno", top.lineno))
        alias = {}
        for node in ast.walk(top):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("vec_decisions."):
                        alias[a.asname or a.name] = a.name.split(".", 1)[1]
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("vec_decisions"):
                for a in node.names:
                    mod = node.module.split(".", 1)[1] if "." in node.module else a.name
                    alias[a.asname or a.name] = mod if "." in node.module else a.name
        for node in ast.walk(top):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2:
                a1 = node.args[1]
                if isinstance(a1, ast.Constant) and isinstance(a1.value, str) and a1.value in fields:
                    reads.append((a1.value, fname, node.lineno))
            elif isinstance(node, ast.Attribute) and node.attr in fields and isinstance(node.value, ast.Name) and node.value.id in ("cfg", "c", "qc", "config", "self", "_cfg", "conf"):
                reads.append((node.attr, fname, node.lineno))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in alias:
                calls.append((fname, alias[node.func.value.id], node.func.attr, node.lineno))
    return reads, calls, funcs


def family_of_function(module: str, func: str) -> str:
    key = f"{module}.{func}".lower()
    for rx, fam in FAMILY_RULES:
        if re.search(rx, key):
            return fam
    return "GLOBAL"


def lifecycle_of_switch(sw: str, kind: str, tabs: set) -> str:
    s = sw.upper()
    if "ABLATION_DISABLE" in s:
        return "ABLATION"
    if any(t in s for t in ("SIZING", "SIZE_MULT", "POSITION_SIZE", "ORDER_VALUE")):
        return "SIZING"
    if s.endswith("_FILTER_TF") or "FILTER" in s or "GATE" in s or "VETO" in s or "BLOCK" in s or "REQUIRE" in s or "CONFIRM" in s:
        base = "ENTRY_GATE"
        if "EXIT" in s or "NOLOSS" in s:
            base = "EXIT_VETO"
        if "REENTRY" in s:
            base = "REENTRY_GATE"
        if "AUGMENT" in s:
            base = "AUGMENT_GATE"
        return base
    k = {"entry": "ENTRY_OPEN", "exit": "EXIT_CLOSE", "reentry": "REENTRY", "augment": "AUGMENT", "reduce": "REDUCE", "sizing": "SIZING", "filter": "ENTRY_GATE", "global": "GLOBAL"}.get(kind or "")
    if k:
        return k
    for t, lc in (("ENTRY", "ENTRY_OPEN"), ("EXIT", "EXIT_CLOSE"), ("REENTRY", "REENTRY"), ("AUGMENT", "AUGMENT"), ("REDUCE", "REDUCE"), ("STDEV", "SIZING"), ("GLOBAL", "GLOBAL")):
        if any(t in tb for tb in tabs):
            return lc
    return "GLOBAL"


def stem_of(sw: str) -> str:
    toks = [t for t in sw.upper().split("_") if t]
    stop = {"ENABLED", "TF", "PCT", "MIN", "MAX", "MULT", "FILTER", "BARS", "TRADIER", "LONG", "SHORT", "THRESHOLD", "MODE", "SEC", "MINUTES"}
    core = [t for t in toks if t not in stop] or toks
    return "_".join(core[:2])


def exit_precedence(engine: Path) -> list:
    lines = engine.read_text().splitlines()
    s0 = next(i for i, l in enumerate(lines) if l.startswith("def simulate_one"))
    s1 = next((i for i in range(s0 + 1, len(lines)) if lines[i].startswith("def ")), len(lines))
    body = lines[s0:s1]
    pos0 = next((i for i, l in enumerate(body) if re.search(r"bars_in_pos\s*\+=\s*1", l)), 0)
    out = []
    for label, anchor, master in EXIT_ANCHORS:
        hit = next((i for i in range(pos0, len(body)) if anchor in body[i] and not body[i].lstrip().startswith("#")), None)
        out.append({"exit": label, "anchor": anchor, "line": (s0 + hit + 1) if hit is not None else None, "master": master})
    out = [o for o in out if o["line"] is not None]
    out.sort(key=lambda o: o["line"])
    return out


def master_blocks(engine: Path) -> list:
    """`if ... getattr(cfg, 'M', ...) ...: for k in ('A','B'): setattr(cfg, k, False)` -> M forces A,B off (crypto reentry master)."""
    src = engine.read_text()
    out = []
    for m in re.finditer(r"(if [^\n]*getattr\(cfg,\s*'[A-Z0-9_]+'[^\n]*)\n(?:[^\n]*\n){0,4}?\s*for \w+ in \(([^)]*)\):\s*\n\s*setattr\(cfg,\s*\w+,\s*False\)", src):
        keys = re.findall(r"'([A-Z0-9_]+)'", m.group(2))
        conds = re.findall(r"getattr\(cfg,\s*'([A-Z0-9_]+)'", m.group(1))
        master = next((c for c in reversed(conds) if c.endswith("_ENABLED")), conds[-1])
        out.append({"master": master, "condition": m.group(1).strip()[:200], "forces_off_when_false": keys, "line": src[:m.start()].count("\n") + 1})
    return out


# ───────────────────────── templates ─────────────────────────
def template_scan(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(path))
    rows, yellow = [], defaultdict(set)
    for tab in SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        hdr = [c.value for c in ws[2]]
        col = {h: i for i, h in enumerate(hdr) if isinstance(h, str)}
        isd = col.get("is_default")
        avg = col.get("AVG_DELTA")
        pos = col.get("POS_SYM")
        fam = col.get("Family")
        for r in ws.iter_rows(min_row=3):
            sw, opt = r[0].value, (r[1].value if len(r) > 1 else None)
            if sw in (None, "") or opt in (None, ""):
                continue
            sw = str(sw).strip()
            if sw.lower() in ("switch", "general", "blanket", "filter", "option value"):
                continue
            try:
                grey = str(r[0].font.color.rgb if r[0].font and r[0].font.color else "").upper() == "FFBFBFBF"
            except Exception:
                grey = False
            try:
                orange = str(r[0].fill.fgColor.rgb or "").upper().endswith("FFE699")
            except Exception:
                orange = False
            ys = []
            for i, c in enumerate(r):
                if i < 14 or i >= len(hdr) or not isinstance(hdr[i], str) or "=" not in hdr[i]:
                    continue
                try:
                    if c.fill is not None and c.fill.fill_type and str(c.fill.fgColor.rgb).upper().endswith("FFFF00"):
                        ys.append(hdr[i].split("=", 1)[0].strip())
                except Exception:
                    pass
            ys = sorted(set(ys))
            yellow[sw].update(ys)
            rows.append({"tab": tab, "row": r[0].row, "switch": sw, "option": str(opt), "grey": grey, "orange": orange,
                         "default": str(r[isd].value).strip().upper() == "YES" if isd is not None and isd < len(r) else False,
                         "family_col": r[fam].value if fam is not None and fam < len(r) else None,
                         "avg_delta": r[avg].value if avg is not None and avg < len(r) else None, "pos_sym": r[pos].value if pos is not None and pos < len(r) else None,
                         "yellow_filters": ys})
    filters = set()
    for tab in SWITCH_SHEETS:
        if tab in wb.sheetnames:
            for h in [c.value for c in wb[tab][2]][14:]:
                if isinstance(h, str) and "=" in h:
                    filters.add(h.split("=", 1)[0].strip())
    return {"rows": rows, "yellow": {k: sorted(v) for k, v in yellow.items()}, "filters": sorted(filters)}


# ───────────────────────── evidence ─────────────────────────
def load_causal(cat: str) -> dict:
    p = ROOT / "data" / "causal" / f"causal_map_{cat}.csv"
    out = {}
    if not p.exists():
        return out
    for r in csv.DictReader(open(p)):
        try:
            out[r["lever"]] = {"n": int(r["n"]), "mean_dgain": float(r["mean_dgain"]), "share_pos": float(r["share_pos"]), "share_neg": float(r["share_neg"]),
                               "dtrades": float(r["mean_dtrades"]), "dtim": float(r["mean_dtim"]), "ddd": float(r["mean_ddd"]), "kept": int(r["kept"])}
        except Exception:
            continue
    return out


def load_autopsy(cat: str) -> dict:
    agg = defaultdict(lambda: Counter())
    for p in glob.glob(str(ROOT / "data" / "causal" / "raw" / "*" / "*.json")):
        ss = Path(p).stem
        if cat_side_of(ss) != cat:
            continue
        try:
            j = json.loads(Path(p).read_text())
        except Exception:
            continue
        for r in j.get("row_recommendations") or []:
            lab = f"{r['switch']}={r['cand']}"
            a = agg[lab]
            a["n_sym"] += 1
            for k in ("losers_fixed", "premature_fixed", "aug_fixed", "captured_moves"):
                a[k] += int(r.get(k) or 0)
            for k in ("saved_pp", "hurt_pp", "net_pp"):
                a[k] += float(r.get(k) or 0.0)
    return {k: dict(v) for k, v in agg.items()}


def load_avg_delta(cat: str) -> dict:
    p = ROOT / "SPREADSHEETS" / "v15_avg_delta_latest.xlsx"
    out = {}
    if not p.exists():
        return out
    import openpyxl
    wb = openpyxl.load_workbook(str(p), read_only=True)
    if cat not in wb.sheetnames:
        return out
    for r in wb[cat].iter_rows(min_row=2, values_only=True):
        if not r or not r[1]:
            continue
        name = str(r[1])
        try:
            out[name] = {"tab": r[0], "kind": r[2], "pos_sym": int(r[3] or 0), "avg_delta": float(r[4]), "median_delta": float(r[5]), "n": int(r[6] or 0)}
        except Exception:
            continue
    return out


def load_fresh(runs: Path, cat: str) -> dict:
    """lever evidence on the CURRENT NPZ generation from tools/v15_graph_search runs ({SS}.gs.json lever_map + trade attribution):
    {lever: {n, mean_dgain, share_pos, share_neg, dtrades, dtim, ddd, losers_fixed, premature_fixed, aug_fixed, kept}}."""
    agg = defaultdict(lambda: {"dg": [], "dt": [], "dtim": [], "ddd": [], "L": 0, "P": 0, "A": 0, "C": 0, "kept": 0})
    if not runs or not runs.exists():
        return {}
    for p in glob.glob(str(runs / "*.gs.json")):
        try:
            j = json.loads(Path(p).read_text())
        except Exception:
            continue
        ss = j.get("symside") or Path(p).name.split(".")[0]
        if cat_side_of(ss) != cat:
            continue
        for lm in j.get("lever_map") or []:
            d = lm.get("d") or {}
            if d.get("gain") is None:
                continue
            a = agg[f"{lm['switch']}={lm['cand']}"]
            a["dg"].append(d["gain"]); a["dt"].append(d.get("trades") or 0)
            if d.get("tim") is not None:
                a["dtim"].append(d["tim"])
            if d.get("dd") is not None:
                a["ddd"].append(d["dd"])
            at = lm.get("attr") or {}
            a["L"] += int(at.get("losers_fixed") or 0); a["P"] += int(at.get("premature_fixed") or 0); a["A"] += int(at.get("aug_fixed") or 0); a["C"] += int(at.get("captured") or 0)
        for st in j.get("steps") or []:
            lab = str(st.get("applied") or "")
            if "=" in lab and lab in agg:
                agg[lab]["kept"] += 1
    out = {}
    for lab, a in agg.items():
        n = len(a["dg"])
        out[lab] = {"n": n, "mean_dgain": round(statistics.mean(a["dg"]), 4), "share_pos": round(sum(1 for x in a["dg"] if x > 1e-9) / n, 3), "share_neg": round(sum(1 for x in a["dg"] if x < -1e-9) / n, 3),
                    "dtrades": round(statistics.mean(a["dt"]), 2), "dtim": round(statistics.mean(a["dtim"]), 3) if a["dtim"] else 0.0, "ddd": round(statistics.mean(a["ddd"]), 3) if a["ddd"] else 0.0,
                    "losers_fixed": a["L"], "premature_fixed": a["P"], "aug_fixed": a["A"], "captured_moves": a["C"], "n_sym": n, "kept": a["kept"]}
    return out


def load_inventory() -> dict:
    """{cat: {fault: {lever: {n, med_dgain, wins, status, src}}}} from v15_lever_inventory.py output.
    Accepted-winner evidence (stronger than screened candidates): searched in results dirs, missing-safe."""
    for cand in (ROOT / "SPREADSHEETS" / "S6_365" / "lever_inventory.json", ROOT / "data" / "s6_365" / "lever_inventory.json",
                 ROOT / "data" / "encyclopedia_v2" / "lever_inventory.json"):
        try:
            j = json.loads(cand.read_text())
        except (OSError, ValueError):
            continue
        out = defaultdict(lambda: defaultdict(dict))
        for key, levers in (j.get("by_fault") or {}).items():
            cat, _, fault = key.partition("|")
            for lv in levers or []:
                n = lv.get("n", 0)
                if lv.get("src") == "recorded" and n >= 2:
                    ok = True
                elif n >= 5:
                    ok = True
                else:
                    continue
                if ok:
                    out[cat][fault][f"{lv['switch']}={lv['to']}"] = lv
        for key, lv in (j.get("by_switch_365") or {}).items():  # USER 2026-10-08: measured single-change 365D flips -> FAIL_365D_GAIN levers
            cat, _, rest = key.partition("|")
            sw, _, to = rest.partition("|")
            try:
                to_v = json.loads(to)
            except Exception:
                to_v = to
            n = lv.get("n", 0)
            if n >= 5 and (lv.get("greens", 0) > 0 or (lv.get("med_dgain365") or 0) > 0):
                out[cat]["FAIL_365D_GAIN"][f"{sw}={to_v}"] = {"n": n, "med_dgain": lv.get("med_dgain365"), "wins": lv.get("wins"),
                                                              "status": lv.get("status"), "src": "flip365-measured"}
        return out
    return {}


def load_ablation(d: Path) -> dict:
    """{cat_side: {group: [ {ss, d_gain, d_trades, d_tim, d_dd, mode} ]}} from v15_graph_search reports."""
    out = defaultdict(lambda: defaultdict(list))
    for p in glob.glob(str(d / "*.json")):
        try:
            j = json.loads(Path(p).read_text())
        except Exception:
            continue
        ss = j.get("symside")
        if not ss:
            continue
        for a in j.get("ablation", []):
            if a.get("d") is None:
                continue
            out[cat_side_of(ss)][a["group"]].append({"ss": ss, "mode": a.get("mode"), **a["d"], "valid": a.get("valid")})
    return out


def utilities(ev: dict, lifecycle: str) -> list:
    """utility tags from real deltas (causal Δ + autopsy counts). Thresholds are deliberately coarse: a tag = a direction.
    Fresh (current NPZ generation, graph-search runs) evidence wins over the older causal map when it has >= 2 sym_sides."""
    tags = []
    fr = ev.get("fresh") or {}
    c, a = (fr if fr.get("n", 0) >= 2 else (ev.get("causal") or {})), ((fr if fr.get("n", 0) >= 2 else None) or ev.get("autopsy") or {})
    if c:
        if c["dtrades"] >= 3:
            tags.append("MORE_TRADES")
        elif c["dtrades"] <= -3:
            tags.append("FEWER_TRADES")
        if c["dtim"] >= 2:
            tags.append("HIGHER_TIM")
        elif c["dtim"] <= -2:
            tags.append("LOWER_TIM")
        if c["ddd"] <= -0.5:
            tags.append("LOWER_DD")
        elif c["ddd"] >= 0.5:
            tags.append("HIGHER_DD")
        if c["mean_dgain"] > 0 and c["share_pos"] >= 0.5:
            tags.append("HIGHER_GAIN")
        if lifecycle.startswith("EXIT") and c["dtim"] <= -2:
            tags.append("EARLIER_EXIT")
        if (lifecycle.startswith("EXIT") or lifecycle == "REDUCE") and c["dtim"] >= 2:
            tags.append("LATER_EXIT")
    if a:
        n = max(1, a.get("n_sym", 1))
        if a.get("losers_fixed", 0) / n >= 1.0:
            tags.append("FEWER_LOSERS")
        if a.get("premature_fixed", 0) / n >= 1.0:
            tags.append("LATER_EXIT")
        if a.get("aug_fixed", 0) / n >= 1.0:
            tags.append("BIGGER_WINNERS")
        if a.get("captured_moves", 0) / n >= 1.0:
            tags.append("MORE_TRADES")
    if lifecycle == "AUGMENT" and c and c["mean_dgain"] > 0 and abs(c["dtrades"]) < 3:
        tags.append("BIGGER_WINNERS")
    return sorted(set(tags))


# ───────────────────────── build ─────────────────────────
def build(ablation_dir: Path | None = None, runs_dir: Path | None = None) -> dict:
    t0 = time.time()
    engine = ROOT / "v12_quick_engine.py"
    fields = quickconfig_fields(engine)
    files = [engine] + sorted(p for p in (ROOT / "vec_decisions").glob("*.py") if not p.name.startswith("test_") and p.name != "__init__.py")
    reads = defaultdict(list)
    calls, func_nodes = [], {}
    for f in files:
        mod = "v12_quick_engine" if f == engine else f.stem
        r, c, fn = scan_file(f, fields)
        for field, func, line in r:
            reads[field].append((mod, func, line))
        for func, cm, callee, line in c:
            calls.append({"caller": f"{mod}.{func}", "callee": f"{cm}.{callee}", "line": line})
        for name, (a, b) in fn.items():
            func_nodes[f"{mod}.{name}"] = {"module": mod, "func": name, "lines": [a, b], "family": "GENERIC" if (mod == "v12_quick_engine" and name in GENERIC_FUNCS) else family_of_function(mod, name)}
    called = {c["callee"] for c in calls if c["caller"].startswith("v12_quick_engine.")}
    reach = set(called)
    frontier = set(called)
    while frontier:
        nxt = {c["callee"] for c in calls if c["caller"] in frontier} - reach
        reach |= nxt
        frontier = nxt
    for k, v in func_nodes.items():
        v["reachable_from_engine"] = v["module"] == "v12_quick_engine" or k in reach
    sb = json.load(open(ROOT / "data" / "SWITCH_BIBLE.json"))["switches"]
    tpl = {}
    for cat in CAT_SIDES:
        p = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cat}.xlsx"
        if not p.exists():
            p = ROOT / "SPREADSHEETS" / f"TEMPLATE_{cat}.xlsx"
        tpl[cat] = template_scan(p)
    ev = {cat: {"causal": load_causal(cat), "autopsy": load_autopsy(cat), "avg": load_avg_delta(cat), "fresh": load_fresh(runs_dir, cat)} for cat in CAT_SIDES}
    abl = load_ablation(ablation_dir) if ablation_dir and ablation_dir.exists() else {}
    prec = exit_precedence(engine)
    masters = master_blocks(engine)
    nodes, edges = {}, []
    # switches (every TEMPLATE row switch, all 4 cat_sides) + filters (yellow headers)
    all_sw = set()
    for cat in CAT_SIDES:
        all_sw |= {r["switch"] for r in tpl[cat]["rows"]}
    all_filters = set()
    for cat in CAT_SIDES:
        all_filters |= set(tpl[cat]["filters"])
    for sw in sorted(all_sw | all_filters):
        b = sb.get(sw) or {}
        tabs = set()
        trows = {}
        for cat in CAT_SIDES:
            rs = [r for r in tpl[cat]["rows"] if r["switch"] == sw]
            tabs |= {r["tab"] for r in rs}
            if rs:
                trows[cat] = [{k: r[k] for k in ("tab", "row", "option", "default", "grey", "orange", "avg_delta", "pos_sym")} for r in rs]
        lc = lifecycle_of_switch(sw, b.get("kind"), tabs)
        rd = reads.get(sw, [])
        fams = Counter()
        for mod, func, _l in rd:
            fn = func_nodes.get(f"{mod}.{func}", {})
            if fn.get("family") not in (None, "GENERIC", "GLOBAL"):
                fams[fn["family"]] += 1
        code_family = fams.most_common(1)[0][0] if fams else None
        roles = (["switch"] if sw in all_sw else []) + (["filter"] if sw in all_filters else [])
        opts = sorted({o["option"] for rs in trows.values() for o in rs})
        # OFF = a real "disable" option (bool False / TF 'OFF'); a numeric 0 is a value, not an off switch; parity/infra fields never have one
        off = None if any(t in sw.upper() for t in NO_OFF_TOKENS) else next((o for o in opts if o.upper() in ("OFF", "FALSE", "NONE")), None)
        nodes[f"switch:{sw}"] = {"type": "filter" if roles == ["filter"] else "switch", "name": sw, "roles": roles, "lifecycle": lc, "code_family": code_family,
                                 "family": f"{lc}:{stem_of(sw)}", "kind_bible": b.get("kind"), "value_type": b.get("type"), "tabs": sorted(tabs), "options": opts, "off_value": off,
                                 "defaults": (b.get("defaults") or {}).get("cat_side_defaults_4"), "status": b.get("status"), "vec_read_count": b.get("vec_read_count"),
                                 "reads": [f"{m}.{f}:{l}" for m, f, l in rd][:40], "template_rows": trows, "utility": {}, "evidence": {}}
        for mod, func, line in rd:
            edges.append({"src": f"switch:{sw}", "dst": f"func:{mod}.{func}", "type": "enables" if (str(sw).endswith(("_ENABLED", "_TF")) or off) else "parameterises", "line": line})
    for k, v in func_nodes.items():
        nodes[f"func:{k}"] = {"type": "function", **v}
    for lc in sorted({v["family"] for v in func_nodes.values()} | {v["lifecycle"] for v in nodes.values() if v.get("lifecycle")}):
        nodes[f"lifecycle:{lc}"] = {"type": "lifecycle", "name": lc}
    # families
    fam_members = defaultdict(set)
    for k, v in nodes.items():
        if v["type"] in ("switch", "filter"):
            fam_members[v["family"]].add(v["name"])
    for fam, mem in fam_members.items():
        nodes[f"family:{fam}"] = {"type": "family", "name": fam, "lifecycle": fam.split(":", 1)[0], "members": sorted(mem)}
        for m in mem:
            edges.append({"src": f"switch:{m}", "dst": f"family:{fam}", "type": "member_of"})
    for k, v in func_nodes.items():
        edges.append({"src": f"func:{k}", "dst": f"lifecycle:{v['family']}", "type": "member_of"})
    for c in calls:
        edges.append({"src": f"func:{c['caller']}", "dst": f"func:{c['callee']}", "type": "calls", "line": c["line"]})
    # filter -> switch (yellow cells), per cat_side
    for cat in CAT_SIDES:
        for sw, fl in tpl[cat]["yellow"].items():
            for f in fl:
                edges.append({"src": f"switch:{f}", "dst": f"switch:{sw}", "type": "gates", "cat_side": cat, "source": "TEMPLATE yellow cell"})
    # exit precedence + choke starvation + masters
    for a, b in zip(prec, prec[1:]):
        edges.append({"src": f"exit:{a['exit']}", "dst": f"exit:{b['exit']}", "type": "preempts", "line": a["line"], "source": "simulate_one position branch order (first hit wins)"})
    for k, v in func_nodes.items():
        if any(cm in v["module"] for cm in CHOKE_MODULES) and v["family"] == "ENTRY_GATE":
            edges.append({"src": f"func:{k}", "dst": "lifecycle:ENTRY_OPEN", "type": "starves", "source": "open choke (_strict_open_block/_ec_choke_block) binds every open incl. reentry"})
            edges.append({"src": f"func:{k}", "dst": "lifecycle:REENTRY", "type": "starves", "source": "open choke binds reentries too"})
    for m in masters:
        for k in m["forces_off_when_false"]:
            edges.append({"src": f"switch:{m['master']}", "dst": f"switch:{k}", "type": "enables", "source": f"simulate_one:{m['line']} forces off when master False"})
    # evidence + utilities per cat_side
    for cat in CAT_SIDES:
        e = ev[cat]
        for r in tpl[cat]["rows"]:
            lab = f"{r['switch']}={r['option']}"
            n = nodes.get(f"switch:{r['switch']}")
            if n is None:
                continue
            evd = {"causal": e["causal"].get(lab), "autopsy": e["autopsy"].get(lab), "avg": e["avg"].get(lab), "fresh": e["fresh"].get(lab)}
            if any(evd.values()):
                n["evidence"].setdefault(cat, {})[r["option"]] = evd
                n["utility"].setdefault(cat, {})[r["option"]] = utilities(evd, n["lifecycle"])
        for f in tpl[cat]["filters"]:
            n = nodes.get(f"switch:{f}")
            if n is None:
                continue
            for lab, v in e["avg"].items():
                if lab.startswith(f + "="):
                    opt = lab.split("=", 1)[1]
                    evd = {"causal": e["causal"].get(lab), "autopsy": e["autopsy"].get(lab), "avg": v, "fresh": e["fresh"].get(lab)}
                    n["evidence"].setdefault(cat, {}).setdefault(opt, evd)
                    n["utility"].setdefault(cat, {}).setdefault(opt, utilities(evd, n["lifecycle"]))
    # fault index: fault -> levers whose utility addresses it, ranked by evidence (mean dgain * share_pos, autopsy net)
    # USER 2026-10-07: accepted-winner inventory merges in — observed repairs outrank inferred utility.
    inv = load_inventory()
    fault_index = {}
    for cat in CAT_SIDES:
        fi = {}
        for fault, utils in UTILITY_FAULTS.items():
            lev = []
            for k, n in nodes.items():
                if n["type"] not in ("switch", "filter"):
                    continue
                for opt, tags in (n["utility"].get(cat) or {}).items():
                    if not set(tags) & set(utils):
                        continue
                    evd = n["evidence"][cat][opt]
                    fr = evd.get("fresh") or {}
                    c, a = (fr if fr.get("n", 0) >= 2 else (evd.get("causal") or {})), evd.get("autopsy") or {}
                    score = (c.get("mean_dgain", 0.0) * c.get("share_pos", 0.0)) + 0.1 * a.get("net_pp", 0.0) / max(1, a.get("n_sym", 1)) + 0.2 * (fr.get("kept", 0) if fr else 0)
                    lev.append({"lever": f"{n['name']}={opt}", "family": n["family"], "lifecycle": n["lifecycle"], "tags": tags, "score": round(score, 4),
                                "n": c.get("n"), "mean_dgain": c.get("mean_dgain"), "share_pos": c.get("share_pos"), "dtrades": c.get("dtrades"), "dtim": c.get("dtim"), "ddd": c.get("ddd")})
            for ilev, lv in (inv.get(cat) or {}).get(fault, {}).items():
                _sw, _, _to = ilev.partition("=")
                _isc = round((lv.get("med_dgain", 0.0) or 0.0) * (lv.get("wins", 0) / max(1, lv.get("n", 1))), 4)
                _hit = next((e for e in lev if e["lever"].split("=", 1)[0] == _sw and str(e["lever"].split("=", 1)[1]) == str(_to)), None)
                if _hit is not None:
                    _hit["score"] = round(max(_hit["score"], _isc) + 1.0, 4)
                    _hit["tags"] = sorted(set(_hit["tags"]) | {"INVENTORY_OBSERVED"})
                    _hit["inv_n"], _hit["inv_med_dgain"] = lv.get("n"), lv.get("med_dgain")
                else:
                    _nd = next((n for n in nodes.values() if n["type"] in ("switch", "filter") and n["name"] == _sw), None)
                    if _nd is not None:  # observed repair beats inferred tags: append even without tag match
                        lev.append({"lever": ilev, "family": _nd["family"], "lifecycle": _nd["lifecycle"], "tags": ["INVENTORY_OBSERVED"],
                                    "score": round(_isc + 1.0, 4), "n": lv.get("n"), "mean_dgain": lv.get("mean_dgain"),
                                    "share_pos": lv.get("wins", 0) / max(1, lv.get("n", 1)), "dtrades": None, "dtim": None, "ddd": None,
                                    "inv_n": lv.get("n"), "inv_med_dgain": lv.get("med_dgain")})
                    # unknown-to-TEMPLATE levers are skipped: unrankable, untestable
            lev.sort(key=lambda x: -x["score"])
            fi[fault] = lev[:60]
        fault_index[cat] = fi
    # groups for ablation (per cat_side): tabs, lifecycles, families with >= 3 members
    groups = {}
    for cat in CAT_SIDES:
        g = defaultdict(set)
        for r in tpl[cat]["rows"]:
            if r["grey"]:
                continue
            g[f"TAB:{r['tab']}"].add(r["switch"])
            n = nodes.get(f"switch:{r['switch']}")
            if n:
                g[f"LIFECYCLE:{n['lifecycle']}"].add(r["switch"])
                g[f"FAMILY:{n['family']}"].add(r["switch"])
                if n.get("code_family"):
                    g[f"CODEFAM:{n['code_family']}"].add(r["switch"])
        g["FILTERS:ALL_FILTER_TF"] = {f for f in tpl[cat]["filters"] if f.endswith(("_FILTER_TF", "_TF_REQ"))}
        # 2026-10-07 filter-ablation lane: coherent per-lifecycle FILTERS groups from the
        # shared mapping (vec_decisions/filter_ablation_groups.py) — same membership the
        # ABLATION_DISABLE_FILTER_* engine flags force off, so FLAG ablation and group-OFF
        # ablation measure the same layer (members restricted to quickconfig fields).
        try:
            import sys as _sys
            if str(ROOT) not in _sys.path:
                _sys.path.insert(0, str(ROOT))
            from vec_decisions import filter_ablation_groups as _fab
            _fab_names = {"ABLATION_DISABLE_FILTER_ENTRY": "FILTERS:ENTRY", "ABLATION_DISABLE_FILTER_MTF_HTF": "FILTERS:MTF_HTF",
                          "ABLATION_DISABLE_FILTER_REENTRY": "FILTERS:REENTRY", "ABLATION_DISABLE_FILTER_EXIT": "FILTERS:EXIT",
                          "ABLATION_DISABLE_FILTER_AUGMENT": "FILTERS:AUGMENT", "ABLATION_DISABLE_FILTER_REDUCE": "FILTERS:REDUCE"}
            for _flag, _gname in _fab_names.items():
                g[_gname] = {m for m in _fab.GROUPS[_flag] if m in fields}
        except Exception:
            pass
        groups[cat] = {k: sorted(v) for k, v in g.items() if len(v) >= (1 if k.startswith(("TAB:", "LIFECYCLE:", "FILTERS:")) else 3)}
    # measured group utilities (ablation)
    group_evidence = {}
    for cat, gd in abl.items():
        group_evidence[cat] = {}
        for gname, lst in gd.items():
            for mode in sorted({x.get("mode") for x in lst}):
                xs = [x for x in lst if x.get("mode") == mode]
                if not xs:
                    continue
                dg = [x["gain"] for x in xs if x.get("gain") is not None]
                group_evidence[cat][f"{gname}|{mode}"] = {"n": len(xs), "mean_dgain": round(statistics.mean(dg), 4) if dg else None,
                                                          "share_removal_helps": round(sum(1 for v in dg if v > 0) / len(dg), 3) if dg else None,
                                                          "mean_dtrades": round(statistics.mean([x["trades"] for x in xs]), 1),
                                                          "mean_dtim": round(statistics.mean([x["tim"] for x in xs if x.get("tim") is not None] or [0]), 2),
                                                          "mean_ddd": round(statistics.mean([x["dd"] for x in xs if x.get("dd") is not None] or [0]), 2)}
    g = {"meta": {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "builder": "tools/v15_knowledge_graph.py", "secs": round(time.time() - t0, 1),
                  "engine_md5": __import__("hashlib").md5(engine.read_bytes()).hexdigest(), "n_quickconfig_fields": len(fields),
                  "n_switch_nodes": sum(1 for v in nodes.values() if v["type"] == "switch"), "n_filter_nodes": sum(1 for v in nodes.values() if "filter" in v.get("roles", [])),
                  "n_function_nodes": sum(1 for v in nodes.values() if v["type"] == "function"), "n_family_nodes": sum(1 for v in nodes.values() if v["type"] == "family"),
                  "n_edges": len(edges), "family_rules": FAMILY_RULES, "utility_faults": UTILITY_FAULTS, "exit_precedence": prec, "masters": masters,
                  "evidence_sources": ["data/causal/causal_map_{cat}.csv", "data/causal/raw/*/*.json row_recommendations", "SPREADSHEETS/v15_avg_delta_latest.xlsx", str(ablation_dir or ""), str(runs_dir or "")],
                  "n_fresh_levers": {cat: len(ev[cat]["fresh"]) for cat in CAT_SIDES}},
         "nodes": nodes, "edges": edges, "groups": groups, "fault_index": fault_index, "group_evidence": group_evidence, "templates": {cat: {"yellow": tpl[cat]["yellow"]} for cat in CAT_SIDES}}
    return g, tpl


def write_md(g: dict, tpl: dict) -> None:
    m = g["meta"]
    L = [f"# ENCYCLOPEDIA v2 — knowledge graph of switches, filters and function families", "",
         f"Built {m['built']} by `{m['builder']}` (engine md5 `{m['engine_md5'][:8]}`). {m['n_switch_nodes']} switch nodes, {m['n_filter_nodes']} filter nodes, "
         f"{m['n_function_nodes']} function nodes, {m['n_family_nodes']} families, {m['n_edges']} edges. Machine-readable: `data/encyclopedia_v2/graph.json`.", "",
         "Every edge is code-derived (AST reads/calls, TEMPLATE yellow cells, simulate_one exit order, master blocks) or evidence-derived (real engine deltas). "
         "Utility tags are directions measured on the fleet, not intentions.", "", "## Exit precedence in simulate_one (first hit wins; earlier pre-empts later)", "",
         "| # | exit | code line | master/param |", "|---|---|---|---|"]
    for i, e in enumerate(m["exit_precedence"], 1):
        L.append(f"| {i} | {e['exit']} | v12_quick_engine.py:{e['line']} | `{e['master']}` |")
    L += ["", "## Master switches found in code", ""]
    for x in m["masters"]:
        L.append(f"- `{x['master']}` (line {x['line']}): when False forces off {', '.join('`%s`' % k for k in x['forces_off_when_false'])}")
    L += ["", "## Fault -> graph path -> levers (top 8 per cat_side by fleet evidence)", ""]
    for cat in CAT_SIDES:
        L.append(f"### {cat}")
        L.append("")
        for fault, lev in g["fault_index"][cat].items():
            if not lev:
                continue
            top = ", ".join(f"`{x['lever']}` ({x['mean_dgain']:+.2f}pp/{(x['share_pos'] or 0)*100:.0f}%pos)" if x.get("mean_dgain") is not None else f"`{x['lever']}`" for x in lev[:8])
            utils = "/".join(UTILITY_FAULTS[fault])
            L.append(f"- **{fault}** -> {utils} -> {top}")
        L.append("")
    if g.get("group_evidence"):
        L += ["## Group ablation evidence (switching a whole group OFF / back to defaults on each sym_side's best set)", "",
              "Positive mean_dgain = REMOVING the group helped (the group is a culprit on this cat_side).", ""]
        for cat, ge in g["group_evidence"].items():
            L.append(f"### {cat}")
            L.append("")
            L.append("| group|mode | n | mean dgain (removal) | share removal helps | dtrades | dTIM | dDD |")
            L.append("|---|---|---|---|---|---|---|")
            for k, v in sorted(ge.items(), key=lambda x: -(x[1]["mean_dgain"] or -1e9)):
                L.append(f"| {k} | {v['n']} | {v['mean_dgain']} | {v['share_removal_helps']} | {v['mean_dtrades']} | {v['mean_dtim']} | {v['mean_ddd']} |")
            L.append("")
    L += ["## Function families (rule table on module/function names; reads are AST-derived)", "", "| regex | family |", "|---|---|"]
    for rx, fam in FAMILY_RULES:
        L.append(f"| `{rx[:120]}{'…' if len(rx) > 120 else ''}` | {fam} |")
    L += ["", "Per-row utilities: `data/encyclopedia_v2/template_row_utility.md` (every TEMPLATE row -> lifecycle, family, code reads, filters that gate it, utility tags, evidence)."]
    (OUT / "graph.md").write_text("\n".join(L) + "\n")


def write_row_table(g: dict, tpl: dict) -> None:
    cols = ["source", "cat_side", "tab", "row", "switch", "option", "default", "lifecycle", "family", "code_family", "utility", "n", "mean_dgain", "share_pos", "dtrades", "dtim", "ddd",
            "losers_fixed", "premature_fixed", "aug_fixed", "avg_delta", "pos_sym", "gated_by_filters", "reads"]
    rows = []
    for cat in CAT_SIDES:
        for r in tpl[cat]["rows"]:
            n = g["nodes"].get(f"switch:{r['switch']}") or {}
            evd = ((n.get("evidence") or {}).get(cat) or {}).get(r["option"]) or {}
            fr = evd.get("fresh") or {}
            src = "fresh" if fr.get("n", 0) >= 2 else ("fleet_causal" if evd.get("causal") else "")
            c, a, av = (fr if src == "fresh" else (evd.get("causal") or {})), (fr if src == "fresh" else (evd.get("autopsy") or {})), evd.get("avg") or {}
            rows.append({"source": src, "cat_side": cat, "tab": r["tab"], "row": r["row"], "switch": r["switch"], "option": r["option"], "default": r["default"], "lifecycle": n.get("lifecycle"),
                         "family": n.get("family"), "code_family": n.get("code_family"), "utility": "|".join(((n.get("utility") or {}).get(cat) or {}).get(r["option"], [])),
                         "n": c.get("n"), "mean_dgain": c.get("mean_dgain"), "share_pos": c.get("share_pos"), "dtrades": c.get("dtrades"), "dtim": c.get("dtim"), "ddd": c.get("ddd"),
                         "losers_fixed": a.get("losers_fixed"), "premature_fixed": a.get("premature_fixed"), "aug_fixed": a.get("aug_fixed"), "avg_delta": av.get("avg_delta"),
                         "pos_sym": av.get("pos_sym"), "gated_by_filters": len(r["yellow_filters"]), "reads": len(n.get("reads") or [])})
    with open(OUT / "template_row_utility.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    L = ["# TEMPLATE row -> utility (every row of the 4 TEMPLATE_FINAL_NORM templates)", "",
         "utility = measured direction (fleet causal map Δ + trade-autopsy counts): MORE_TRADES / FEWER_TRADES / HIGHER_TIM / LOWER_TIM / LOWER_DD / HIGHER_DD / "
         "HIGHER_GAIN / EARLIER_EXIT / LATER_EXIT / FEWER_LOSERS / BIGGER_WINNERS. Blank = no evidence (or no move). Full columns: `template_row_utility.csv`.",
         "src: F = fresh (graph-search screens on the current live-equal NPZ generation, >= 2 sym_sides), C = fleet causal map (older NPZ/TEMPLATE regime).", ""]
    for cat in CAT_SIDES:
        L += [f"## {cat}", "", "| tab | row | switch=option | lifecycle | utility | src | n | mean Δgain | %pos | Δtrades | ΔTIM | ΔDD | L/P/A fixed | filters |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            if r["cat_side"] != cat:
                continue
            md = f"{r['mean_dgain']:+.2f}" if r["mean_dgain"] is not None else ""
            sp = f"{r['share_pos']*100:.0f}" if r["share_pos"] is not None else ""
            lpa = f"{r['losers_fixed'] or 0}/{r['premature_fixed'] or 0}/{r['aug_fixed'] or 0}" if r["losers_fixed"] is not None else ""
            L.append(f"| {r['tab']} | {r['row']} | `{r['switch']}={r['option']}`{' **(default)**' if r['default'] else ''} | {r['lifecycle']} | {r['utility']} | {'F' if r['source'] == 'fresh' else ('C' if r['source'] else '')} | {r['n'] or ''} | {md} | {sp} | {r['dtrades'] if r['dtrades'] is not None else ''} | {r['dtim'] if r['dtim'] is not None else ''} | {r['ddd'] if r['ddd'] is not None else ''} | {lpa} | {r['gated_by_filters']} |")
        L.append("")
    (OUT / "template_row_utility.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation-dir", default=str(OUT / "ablation"))
    ap.add_argument("--runs-dir", default=str(OUT / "runs"))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    g, tpl = build(Path(a.ablation_dir), Path(a.runs_dir))
    _tmp = OUT / "graph.json.tmp"  # USER 2026-10-08: atomic swap, fleet GS reads this file at job start
    _tmp.write_text(json.dumps(g, default=str))
    _tmp.replace(OUT / "graph.json")
    write_md(g, tpl)
    write_row_table(g, tpl)
    print(json.dumps({k: v for k, v in g["meta"].items() if k.startswith("n_") or k == "secs"}))


if __name__ == "__main__":
    main()
