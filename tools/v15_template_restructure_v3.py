#!/usr/bin/env python3
"""v15_template_restructure_v2 — AGENT B (2026-09-30): STAGE a cleaned set of TEMPLATE_*.xlsx (never touches the live templates).

Reads SPREADSHEETS/TEMPLATE_{cat}.xlsx (read-only) and writes <out-dir>/TEMPLATE_{cat}.xlsx + <report-dir>/rows_{cat}.csv + summary JSON:
  1. ONE HOME per switch: a switch (col A) that was broadcast into several tabs is merged into its home tab (tools/v15_template_home_rules.py,
     evidence-based); ENTRY-tab switches whose name is EXIT/REENTRY/AUGMENT/REDUCE are moved to the right lifecycle tab. Whole rows move
     (yellow cells remapped by header name); duplicates go to sheet PARKED_ROWS (nothing is deleted).
  2. SIDE rule: a *_SHORT switch is not carried in a LONG template (dead there) and vice-versa; a missing own-side version is ADDED with the
     donor's options and the venue config default (never invented values).
  3. OPTION repair: options whose type does not fit the config field (e.g. '0.0' for a bool switch) are parked; exactly one default per
     switch (bold == is_default YES); the default option is added when absent; bool switches get both True/False.
  4. GREY = not calculable only: NOT_IN_CONFIG (no field in config/config_tradier/QuickConfig). NO_CONSUMER (only config/wiring stubs, nothing
     reads it in live or vec) is greyed as CONTAMINATION (proposal for erase). Everything else is un-greyed (must be calculated).
  5. ever-yellow json re-keyed to the new tabs (data/yellow_ever_nonzero.staged.json in the out dir).
Usage: python tools/v15_template_restructure_v2.py --out-dir SPREADSHEETS/TEMPLATE_STAGED/<ts> --report-dir data/template_audit/<ts> [--cat-side X]
"""
import os as _os, sys as _sys
if _os.path.exists(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "SPREADSHEETS", "TEMPLATES_FROZEN")) and "--dry-run" not in _sys.argv and not _os.environ.get("TEMPLATES_UNFREEZE"):
    _writes = any(a in _sys.argv for a in ("--apply", "--write", "--out-dir")) or "v15_daily_template_update" in __file__ or "restructure" in __file__
    if _writes and ("--apply" in _sys.argv or "v15_daily_template_update" in __file__ or "restructure" in __file__):
        _sys.exit("REFUSED: SPREADSHEETS/TEMPLATES_FROZEN exists (USER 2026-10-01: templates restored to the pre-trainwreck 20:36 version; no writer may touch them until the user approves a proposal). Remove the file only on explicit user approval.")
import argparse
import collections
import os
import copy
import csv
import dataclasses
import json
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_home_rules as H  # noqa: E402
import v15_template_options as O  # noqa: E402

TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
FIXED = ["Switch", "default", "override", "Family", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA", "LIVE_DELTA", "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS", "is_default", "AVG_DELTA", "POS_SYM"]
GREY = "FFBFBFBF"
ORANGE = "FFE699"
YELLOW = "FFFF00"
WIRE = re.compile(r"^(<module>|_ensure_.*|_batch\d.*|_wire_.*|apply_tradier_defaults|__init__|apply|run|_apply_research_only_live_gates)$")
VEC_FILE = "v12_quick_engine.py"   # vec side = v12_quick_engine.py + vec_decisions/*; every other scanned root module is live-side
MISSING = object()
SCAN, LINES = {}, {}
HIST, PROMOS, EVER_IDX, OMAP, F2S = {}, {}, {}, {}, {}   # filled by main(): option history, promotion ledger, ever-yellow index, opportune map, other-venue Fields
NOT_SIDE = {"ATR_LONG_WINDOW"}
TF_RE = re.compile(r"^(OFF|D|W|M|\d+[mhdwMHDW])(,(OFF|D|W|M|\d+[mhdwMHDW]))*$")


def nrm(v):
    return "" if v is None else str(v).strip()


def coerce(opt, ref, name=""):
    """(ok, normalized key) of option `opt` for a config field whose current value is `ref`."""
    s = nrm(opt)
    if isinstance(ref, bool):
        if s.lower() in ("true", "false"):
            return True, s.lower()
        return False, s
    if isinstance(ref, (int, float)):
        try:
            return True, repr(float(s))
        except Exception:
            return False, s
    if isinstance(ref, str):
        if TF_RE.match(ref) or name.upper().endswith("_TF"):
            return bool(TF_RE.match(s)) or s.upper() in ("OFF", "NONE"), s
        return True, s
    return True, s


def same(a, b):
    if a == b:
        return True
    try:
        return not isinstance(a, bool) and not isinstance(b, bool) and float(a) == float(b)
    except Exception:
        return nrm(a).lower() == nrm(b).lower()


class Fields:
    def __init__(self, cs):
        import config
        import config_tradier
        import v12_quick_engine as V
        self.stocks = cs.startswith("STOCKS")
        self.live = config_tradier.TradierConfig() if self.stocks else config.Config()
        self.qc = V.QuickConfig()
        if self.stocks:
            self.qc.apply_tradier_defaults()
        self.known = {f.name for f in dataclasses.fields(V.QuickConfig)} | {k for k in dir(config.Config) if not k.startswith("_")} | {k for k in dir(config_tradier.TradierConfig) if not k.startswith("_")}
        try:
            self.cs4 = (json.loads((ROOT / "data" / "cat_side_defaults_4.json").read_text()).get(cs) or {})
        except Exception:
            self.cs4 = {}

    def get(self, name):
        for o in (self.live, self.qc):
            try:
                return getattr(o, name)
            except Exception:
                continue
        return MISSING


def load_usage():
    p = ROOT / "data" / "template_audit"
    cands = sorted(p.glob("*/code_usage_full.json"))
    return json.loads(cands[-1].read_text()) if cands else {}


def consumers(usage, name):
    live = vec = 0
    for f, fn, c in usage.get(name, []):
        if WIRE.match(fn):
            continue
        if f == VEC_FILE or f.startswith("vec_decisions/"):
            vec += c
        else:
            live += c
    return live, vec


class Sheet:
    """one tab read into memory: headers by name; rows as {"rec": {hname: (value, style_array, comment_text)}, yellow/orange/grey/bold}"""

    def __init__(self, ws):
        self.ws = ws
        self.title = ws.title
        self.hdr = {}
        for c in range(1, ws.max_column + 1):
            h = ws.cell(2, c).value
            if h in (None, ""):
                continue
            h = str(h)
            if h not in self.hdr:
                self.hdr[h] = c
        self.meta_cols = {h for h in self.hdr if h in FIXED or h in ("Live Location", "Vec Hook", "Type", "Correct Options")}
        self.rows = []
        for r in range(3, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if a in (None, ""):
                continue
            rec, yel = {}, set()
            for h, c in self.hdr.items():
                cell = ws.cell(r, c)
                rec[h] = (cell.value, copy.copy(cell._style), cell.comment.text if cell.comment else None)
                if h not in self.meta_cols:
                    fl = cell.fill
                    if fl is not None and fl.fill_type == "solid" and str(fl.fgColor.rgb or "").upper().endswith(YELLOW):
                        yel.add(h)
            fa = ws.cell(r, 1).fill
            fo = ws.cell(r, 1).font
            self.rows.append({"rec": rec, "r": r, "tab": ws.title, "yellow": yel,
                              "orange": bool(fa and fa.fgColor and ORANGE in str(fa.fgColor.rgb or "")),
                              "grey": bool(fo and fo.color is not None and str(getattr(fo.color, "rgb", "") or "").upper() == GREY),
                              "bold": bool(ws.cell(r, 2).font.b), "foreign": False})


def is_orange(row):
    return row["orange"]


def yellow_hdrs(row):
    return set(row["yellow"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--cat-side", default="ALL")
    a = ap.parse_args()
    out, rep = Path(a.out_dir), Path(a.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep.mkdir(parents=True, exist_ok=True)
    usage = load_usage()
    try:
        usage = json.loads((rep / "code_usage_full.json").read_text())
        LINES.update(json.loads((rep / "code_usage_lines.json").read_text()))
        SCAN.update(json.loads((rep / "backup_scan.json").read_text()))
    except Exception as e_:
        print("WARN: usage/scan files not in report dir:", e_)
    ever = json.loads((ROOT / "data" / "yellow_ever_nonzero.json").read_text())
    ever_out = {"derived_from_built_at": ever.get("built_at"), "built_at": ever.get("built_at"), "rule": ever.get("rule"), "symsides": ever.get("symsides"), "cells": {}}
    # donors for missing own-side switches: read all 4 templates' groups once (options only)
    summary = {}
    sheets_all = {}
    for cs in (list(TEMPLATES) if a.cat_side == "ALL" else [a.cat_side]):
        sheets_all[cs] = openpyxl.load_workbook(str(Path(a.src_dir) / TEMPLATES[cs]))
    load_context(list(sheets_all), ever)
    donor_opts = collections.defaultdict(dict)  # name -> {cs: [(B, bold, recdict)]}
    for cs, wb in sheets_all.items():
        for tab in H.TABS:
            sh = Sheet(wb[tab])
            for row in sh.rows:
                row["foreign"] = True
                n = str(row["rec"]["Switch"][0]).strip()
                donor_opts[n].setdefault(cs, []).append(row)
    for cs, wb in sheets_all.items():
        summary[cs] = build_one(cs, wb, usage, ever, ever_out, donor_opts, out, rep)
    (out / "yellow_ever_nonzero.staged.json").write_text(json.dumps(ever_out))
    (rep / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: v["counts"] for k, v in summary.items()}, indent=1))


HIST_FILES = {}


def load_context(css, ever):
    """option history from the newest INTACT older backup (2026-09-22, family conflicts 0), promotion ledger, ever-yellow index, opportune map"""
    import glob
    import os
    import time
    for cs in css:
        cands = [f for f in glob.glob(str(ROOT / "backups" / f"*TEMPLATE*{cs}*.xlsx")) if time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))) == "2026-09-22" and os.path.getsize(f) > 1_000_000]
        cands.sort(key=os.path.getmtime)
        pref = [f for f in cands if "before_template_15m" in f]   # 2026-09-22 17:31: family conflicts 0 in the scan (intact)
        cands = pref or cands
        hist = {}
        HIST_FILES[cs] = None
        for f in reversed(cands):
            try:
                wbh = openpyxl.load_workbook(f, read_only=True)
            except Exception:
                continue
            for t in H.TABS:
                if t not in wbh.sheetnames:
                    continue
                hdr = None
                for i, r in enumerate(wbh[t].iter_rows(min_row=2, max_col=14, values_only=True)):
                    if i == 0:
                        hdr = [str(x) if x is not None else "" for x in r]
                        continue
                    if r[0] in (None, ""):
                        continue
                    n = str(r[0]).strip()
                    h = hist.setdefault(n, {"opts": [], "fam": set()})
                    h["opts"].append(r[1])
                    if len(r) > 3:
                        h["fam"].add(r[3])
            HIST_FILES[cs] = os.path.basename(f)
            break
        HIST[cs] = hist
        F2S[cs] = Fields(("STOCKS_" if cs.startswith("CRYPTO") else "CRYPTO_") + cs.split("_")[1])
        try:
            PROMOS[cs] = json.loads((ROOT / "data" / "cat_side_promotions.json").read_text()).get(cs) or {}
        except Exception:
            PROMOS[cs] = {}
        idx = collections.defaultdict(set)
        for key in ever.get("cells", {}).get(cs, []):
            tab, sb, hdr = key.split("\t", 2)
            idx[(tab, sb)].add(hdr)
        EVER_IDX[cs] = idx
    try:
        om = json.loads((ROOT / "data" / "opportune_filter_map.json").read_text())
    except Exception:
        om = {}
    for cs in css:
        d = collections.defaultdict(set)
        for tab, sws in (om.get(cs) or {}).items():
            if isinstance(sws, dict):
                for sw, fl in sws.items():
                    d[sw] |= set(fl)
        OMAPSET[cs] = d


YELLOW_TOK_MIN = 2


def allowed_yellow(cs, name, tab, tgt, meta_cols, o):
    """the filter headers that may be yellow for this row: name-token overlap >= 2 (pilot rule) OR the opportune map lists the filter for the
    switch OR an engine delta was ever recorded for (tab, switch=cand, filter) by a sym_side of this cat_side"""
    swt = O.tokens(name)
    mp = OMAPSET.get(cs, {}).get(name, set())
    ev = set()
    for sr in o["src"]:
        ev |= EVER_IDX.get(cs, {}).get((sr["tab"], f"{name}={nrm(sr['rec']['default'][0])}"), set())
    out = set()
    for h in tgt:
        if h in meta_cols or "=" not in h:
            continue
        base = h.split("=", 1)[0].strip()
        if len(O.tokens(base) & swt) >= YELLOW_TOK_MIN or h in ev:   # == the pilot's own yellow rule (token overlap OR ever-nonzero)
            out.add(h)
    return out


OMAPSET = {}



def evidence(name):
    hits = LINES.get(name) or []
    live = [h for h in hits if not (h[0] == "v12_quick_engine.py" or h[0].startswith("vec_decisions/"))]
    use = (live or hits)[:2]
    if not use:
        return "no non-wiring reader in ez_manage/tradier_manage/modules/v12_quick_engine/vec_decisions (tools/template_code_usage.py)"
    return "; ".join(f"{h[0]}:{h[1]} {h[2]}()" for h in use)


def build_one(cs, wb, usage, ever, ever_out, donors, out, rep):
    F = Fields(cs)
    side = cs.split("_")[1]
    other = "SHORT" if side == "LONG" else "LONG"
    sheets = {t: Sheet(wb[t]) for t in H.TABS}
    by_w = collections.OrderedDict()
    items = []   # (name, is_orange, [(tab, rows)])
    for t in H.TABS:
        g = collections.OrderedDict()
        for row in sheets[t].rows:
            g.setdefault((str(row["rec"]["Switch"][0]).strip(), row["orange"]), []).append(row)
        for (n, org), rows in g.items():
            if org:
                items.append((n, True, [(t, rows)]))      # ORANGE filter rows: per tab, never merged across tabs
            else:
                by_w.setdefault(n, []).append((t, rows))  # WHITE switch rows: one home tab per switch
    # real switches that exist in older backups but not in the base template (tools/template_backup_scan.py)
    restored_names = set()
    decisions = []
    for n, inf in (SCAN.get(cs) or {}).items():
        lv0, vc0 = consumers(usage, n)
        ref0 = F.get(n)
        why0 = None
        if n.upper().endswith("_ALT"):
            why0 = "invented *_ALT"
        elif n not in F.known:
            why0 = "no field in config/config_tradier/QuickConfig"
        elif H.side_of(n) == ("SHORT" if cs.endswith("LONG") else "LONG"):
            why0 = "wrong side for this template"
        elif lv0 == 0 and vc0 == 0:
            why0 = "no consumer in EITHER live (ez_manage/tradier_manage/modules) or vector (v12_quick_engine/vec_decisions)"
        ev0 = evidence(n)
        if why0:
            decisions.append([cs, n, "", "TRASH(not restored)", why0, ev0 + f" | backup {os.path.basename(inf['file'])}"])
            continue
        try:
            wbb = openpyxl.load_workbook(inf["file"])
            shb = Sheet(wbb[inf["tab"]])
        except Exception as e_:
            decisions.append([cs, n, "", "TRASH(not restored)", f"backup unreadable: {e_}", ev0])
            continue
        rows_b = [r_ for r_ in shb.rows if str(r_["rec"]["Switch"][0]).strip() == n and not r_["orange"]]
        for r_ in rows_b:
            r_["restored"] = True
            r_["tab"] = inf["tab"]
        if rows_b:
            by_w.setdefault(n, []).append((inf["tab"], rows_b))
            restored_names.add(n)
            decisions.append([cs, n, "", "RESTORED", f"real switch (live consumer, in config) from backup {os.path.basename(inf['file'])} tab {inf['tab']}", ev0])
    items = [(n, False, c) for n, c in by_w.items()] + items
    by_sw = None
    csv_rows, parked, unclear, opt_log, parked_ids = [], [], [], [], set()
    white_home = {}
    F2 = F2S[cs]
    counts = collections.Counter()
    final = {t: [] for t in H.TABS}
    V = lambda row, h: row["rec"][h][0]

    def park(row, reason, name):
        parked_ids.add(id(row))
        parked.append({"reason": reason, "from_tab": row["tab"], "from_row": row["r"], "Switch": name, "default": nrm(V(row, "default")), "Family": nrm(V(row, "Family")), "is_default": nrm(V(row, "is_default")), "AVG_DELTA": V(row, "AVG_DELTA"), "POS_SYM": V(row, "POS_SYM"), "yellow": len(row["yellow"])})
        csv_rows.append([cs, row["tab"], row["r"], name, nrm(V(row, "default")), "PARKED", reason, "", ""])
        counts["parked:" + reason.split(":")[0]] += 1

    def classify_imp(name, lv, vc, why):
        if name in ("DELTA_GATE_BB_SQUEEZE", "HTF_GATE_D_MANDATORY", "HTF_TREND_VETO_BYPASS_ENABLED", "HTF_TREND_VETO_BYPASS_REASONS", "BTC_ACCEL_RAMP_REQUIRE_POSITIVE") and not (lv or vc):
            return "UNWIRED"
        if "unclear" in why.lower():
            return "UNCLEAR"
        return "HIGH" if (lv and vc) else ("MED" if (lv or vc) else "LOW")

    for name, is_org, copies in items:
        s_side = None if name in NOT_SIDE else H.side_of(name)
        tabs_now = [t for t, _ in copies]
        if s_side == other:
            for t, rows in copies:
                for row in rows:
                    park(row, f"WRONG_SIDE: {name} is a {s_side} switch; this is a {side} template", name)
            continue
        ref = F.get(name)
        in_cfg = name in F.known
        manual = None if is_org else H.MANUAL_HOME.get(name)
        white_same_tab = is_org and white_home.get(name) == tabs_now[0]
        if is_org:
            home, why = tabs_now[0], "orange filter rows (kept in their own tab)"
        elif manual:
            home, why = manual
        elif len(set(tabs_now)) == 1:
            t0 = tabs_now[0]
            L = H.strong_lifecycle(name)
            if H.LIFE[t0] == "ENTRY" and L and L != "ENTRY":
                home, why = H.default_tab_for(L, name), f"name says {L} but filed in ENTRY tab {t0}"
            else:
                home, why = t0, "single-tab: stays"
                if L and L != H.LIFE[t0]:
                    unclear.append([cs, name, t0, f"name looks {L} (-> {H.default_tab_for(L, name)}) but filed in {H.LIFE[t0]} tab {t0}; left in place"])
        else:
            home, why = sorted(set(tabs_now), key=H.TABS.index)[0], "multi-tab, no rule (UNCLEAR)"
            unclear.append([cs, name, ",".join(sorted(set(tabs_now))), "broadcast into several tabs with no home rule; first tab chosen"])
        allrows = [row for _, rows in copies for row in rows]
        homerows = [row for row in allrows if row["tab"] == home]
        if not is_org:
            white_home[name] = home
        oref = F2.get(name)
        cls = O.classify(name, None if ref is MISSING else ref, None if oref is MISSING else oref)
        typed_ok = ref is not MISSING and cls not in ("STRUCT", "UNKNOWN")
        hist = HIST.get(cs, {}).get(name)
        hkeys = set()
        if hist and typed_ok:
            for hv in hist["opts"]:
                okh, vh, _w = O.normalize(cls, ref, hv)
                if okh:
                    hkeys.add(O.key_of(vh))
        opts = collections.OrderedDict()
        bad = []
        n_before = len({nrm(V(r_, 'default')) for r_ in allrows})
        for row in sorted(allrows, key=lambda r: (r["tab"] != home, H.TABS.index(r["tab"]))):
            braw = V(row, "default")
            if typed_ok:
                ok, val, why_ = O.normalize(cls, ref, braw)
            else:
                ok, val, why_ = True, braw, ""
            if not ok:
                bad.append((row, why_))
                continue
            key = O.key_of(val) if typed_ok else "s:" + nrm(braw)
            o = opts.setdefault(key, {"row": row, "yell": set(), "isd": {}, "src": [], "val": val})
            o["yell"] |= row["yellow"]
            o["src"].append(row)
            o["isd"][row["tab"]] = (str(V(row, "is_default") or "").upper() == "YES") or row["bold"]
            if len(o["src"]) > 1:
                opt_log.append([cs, name, row["tab"], repr(braw), type(braw).__name__, repr(o["val"]), type(o["val"]).__name__, cls, "DEDUP", "same option after typing/merge"])
            elif why_.startswith("RETYPED"):
                counts["fix:retyped"] += 1
                opt_log.append([cs, name, row["tab"], repr(braw), type(braw).__name__, repr(val), type(val).__name__, cls, "RETYPED", why_])
        for row, why_ in bad:
            park(row, f"OPTION_TYPE_MISMATCH: {why_} (class {cls}, config {type(ref).__name__} field {name})", name)
            opt_log.append([cs, name, row["tab"], repr(V(row, "default")), type(V(row, "default")).__name__, "", "", cls, "PARKED", why_])
        tmpl_row = (homerows or allrows)[0]
        # ---- default: the venue config value, unless it is a recorded promotion
        cfgv = None
        if typed_ok:
            okc, cfgv, _w = O.normalize(cls, ref, ref)
            cfgkey = O.key_of(cfgv) if okc else None
        else:
            cfgkey = None
        dkey = None
        for t in [home] + [x for x in H.TABS if x != home]:
            for k, o in opts.items():
                if o["isd"].get(t):
                    dkey = k
                    break
            if dkey:
                break
        added = []
        gen_rows = []

        def add_opt(val, how):
            k_ = O.key_of(val)
            if k_ not in opts:
                opts[k_] = {"row": None, "yell": set(tmpl_row["yellow"]), "isd": {}, "src": [], "val": val, "new": val, "gen": how}
                added.append(f"{val!r}[{how}]")
                counts["fix:generated_" + how.lower()] += 1
                opt_log.append([cs, name, home, "", "", repr(val), type(val).__name__, cls, "ADDED_" + how, "option (re)built"])
            return k_

        if typed_ok and cfgkey and not white_same_tab:
            if dkey is None:
                dkey = add_opt(cfgv, "CONFIG_DEFAULT")
            elif dkey != cfgkey:
                pv = PROMOS.get(cs, {}).get(name, {}).get("value")
                pk = None
                if pv is not None:
                    okp, vp, _w = O.normalize(cls, ref, pv)
                    pk = O.key_of(vp) if okp else None
                if pk == dkey:
                    counts["fix:promotion_kept"] += 1
                else:
                    counts["fix:default_reset"] += 1
                    opt_log.append([cs, name, home, repr(opts[dkey]["val"]), type(opts[dkey]["val"]).__name__, repr(cfgv), type(cfgv).__name__, cls, "DEFAULT_RESET", "bold default != venue config and not a recorded promotion"])
                    dkey = add_opt(cfgv, "CONFIG_DEFAULT") if cfgkey not in opts else cfgkey
        if dkey is None:
            dkey = next(iter(opts), None)
        if dkey is None:
            continue
        # ---- foreign options (value belongs to another parameter): numeric domain test vs default, history-confirmed values survive
        if typed_ok and cls in ("INT", "FLOAT"):
            dn = O._num(opts[dkey]["val"])
            for k_ in list(opts):
                if k_ == dkey or k_ in hkeys:
                    continue
                on = O._num(opts[k_]["val"])
                if O.foreign_numeric(dn, on):
                    o_ = opts.pop(k_)
                    for srow in o_["src"]:
                        park(srow, f"FOREIGN_OPTION: {o_['val']!r} is {on / dn:.3g}x the default {opts[dkey]['val']!r} (value belongs to another parameter / wrong unit)" if dn else "FOREIGN_OPTION", name)
                    counts["fix:foreign_option"] += 1
                    opt_log.append([cs, name, home, repr(o_["val"]), type(o_["val"]).__name__, "", "", cls, "PARKED", "FOREIGN_OPTION vs default " + repr(opts[dkey]["val"])])
        # ---- complete the option set when the group lost options
        if typed_ok and not is_org:
            if cls == "BOOL":
                for bv in (True, False):
                    add_opt(bv, "BOOL_PAIR")
            elif cls == "BOOLNUM":
                for bv in (0, 1):
                    add_opt(int(bv) if isinstance(ref, int) else float(bv), "BOOL_PAIR")
            elif cls in ("INT", "FLOAT") and len(opts) < 3 and not O.SIZING.match(name):
                for hv in (hist["opts"] if hist else []):
                    okh, vh, _w = O.normalize(cls, ref, hv)
                    if okh and not O.foreign_numeric(O._num(opts[dkey]["val"]), O._num(vh)) and len(opts) < 4:
                        add_opt(vh, "BACKUP_RESTORE")
                if len(opts) < 3 and n_before > len(opts):   # a ladder only refills a group that LOST options to parking
                    for lv_ in O.ladder(cls, O._num(opts[dkey]["val"]), [o_["val"] for o_ in opts.values()]):
                        add_opt(lv_, "LADDER")
                elif len(opts) < 3:
                    unclear.append([cs, name, home, f"only {len(opts)} numeric option(s) by design/history; no invented ladder (decide whether to sweep it)"])
            elif cls == "TF" and len(opts) < 3:
                for hv in (hist["opts"] if hist else []):
                    okh, vh, _w = O.normalize(cls, ref, hv)
                    if okh:
                        add_opt(vh, "BACKUP_RESTORE")
                for tv in ("OFF", "15m", "1h", "4h", "D"):
                    if len(opts) < 5:
                        add_opt(tv, "TF_DOMAIN")
            elif cls == "ENUM" and len(opts) < 2:
                unclear.append([cs, name, home, f"enum field with a single option {list(opts)}; real alternatives must come from the code (not invented)"])
        if cls in ("INT", "FLOAT", "TF", "ENUM") and typed_ok and len(opts) < 2:
            unclear.append([cs, name, home, f"only {len(opts)} option(s) after cleanup"])
        # family column: one value per switch (history first, else majority of the group's own rows)
        famc = collections.Counter(str(V(r_, "Family")) for r_ in allrows if V(r_, "Family") not in (None, ""))
        hf = {str(x) for x in (hist["fam"] if hist else []) if x not in (None, "")}
        fam = next(iter(hf)) if len(hf) == 1 else (famc.most_common(1)[0][0] if famc else None)
        lv, vc = consumers(usage, name)
        if not in_cfg:
            grey, greyw = True, "NOT_IN_CONFIG"
        elif isinstance(ref, (list, dict, tuple, set)):
            grey, greyw = True, "STRUCTURED_VALUE (config field is a list/dict; template options cannot express it)"
        elif lv == 0 and vc == 0 and not is_org and not name.endswith("_FILTER_TF") and name not in KEEP_CALC:
            grey, greyw = True, "NO_CONSUMER (config/wiring stubs only)"
        else:
            grey, greyw = False, ""
        imp = classify_imp(name, lv, vc, why)
        if greyw.startswith("STRUCTURED"):
            imp = "UNCLEAR"
        orange = (homerows[0] if homerows else tmpl_row)["orange"]
        final[home].append({"name": name, "opts": opts, "dkey": dkey, "orange": orange, "grey": grey, "greyw": greyw, "home": home, "why": why, "ref": ref, "imp": imp, "live": lv, "vec": vc, "from": sorted(set(tabs_now)), "added": added, "tmpl": tmpl_row, "was_grey": all(r["grey"] for r in allrows), "fam": fam, "cls": cls, "name_cs": cs, "is_org": is_org, "no_default": bool(white_same_tab), "ord": min([r_["r"] for r_ in homerows] or [10 ** 6]), "restored": name in restored_names})
        counts["switches"] += 1
        if home not in tabs_now:
            counts["moved_to_home"] += 1
        elif len(set(tabs_now)) > 1:
            counts["dedup_broadcast"] += 1
        if grey:
            counts["grey:" + greyw.split(" ")[0]] += 1
        elif all(r["grey"] for r in allrows):
            counts["ungrey"] += 1
        for t, rows in copies:
            for row in rows:
                if id(row) in parked_ids:
                    continue
                act = "KEEP" if t == home else ("MOVED" if len(set(tabs_now)) == 1 else "DUP_MERGED")
                csv_rows.append([cs, t, row["r"], name, nrm(V(row, "default")), act, why if act != "KEEP" else "", home, imp])
    # ---- missing own-side switches (added from donors; options = donor's valid options, default = config value)
    present = {g["name"] for t in final for g in final[t]}
    for name, per in donors.items():
        if name in present or name in NOT_SIDE or H.side_of(name) != side:
            continue
        ref = F.get(name)
        if ref is MISSING or name not in F.known:
            continue
        ven = cs.split("_")[0]
        ov = "STOCKS" if ven == "CRYPTO" else "CRYPTO"
        drows = next((per[d] for d in (f"{ven}_{other}", f"{ov}_{side}", f"{ov}_{other}") if d in per), None)
        if not drows:
            continue
        home = H.MANUAL_HOME.get(name, (None,))[0] or H.default_tab_for(H.strong_lifecycle(name) or "ENTRY", name)
        oref = F2.get(name)
        cls = O.classify(name, ref, None if oref is MISSING else oref)
        opts = collections.OrderedDict()
        for row in drows:
            ok, val, _w = O.normalize(cls, ref, V(row, "default")) if cls not in ("STRUCT", "UNKNOWN") else (True, V(row, "default"), "")
            k_ = O.key_of(val)
            if ok and k_ not in opts:
                opts[k_] = {"row": row, "yell": set(row["yellow"]), "isd": {}, "src": [], "val": val}
        okc, cfgv, _w = O.normalize(cls, ref, ref) if cls not in ("STRUCT", "UNKNOWN") else (True, ref, "")
        dk = O.key_of(cfgv)
        added = ["(whole switch)"]
        if dk not in opts:
            tmpl0 = next(iter(opts.values()))["row"]
            opts[dk] = {"row": None, "yell": set(tmpl0["yellow"]), "isd": {}, "src": [], "val": cfgv, "new": cfgv, "gen": "CONFIG_DEFAULT"}
            added.append(repr(cfgv))
        want = cfgv
        tmpl = next(o["row"] for o in opts.values() if o["row"])
        lv, vc = consumers(usage, name)
        final[home].append({"name": name, "opts": opts, "dkey": dk, "orange": False, "grey": False, "greyw": "", "home": home, "why": f"ADDED: own-side ({side}) version missing in this template; donor options, venue config default", "ref": ref, "imp": "HIGH" if (lv and vc) else "MED", "live": lv, "vec": vc, "from": [], "added": added, "tmpl": tmpl, "was_grey": False, "fam": next((str(V(r_, "Family")) for r_ in drows if V(r_, "Family") not in (None, "")), None), "cls": cls, "name_cs": cs})
        counts["added_side_switch"] += 1
        csv_rows.append([cs, "", "", name, nrm(want), "ADDED", "missing own-side version", home, "HIGH"])
    # ---- write staged workbook (wb is our private copy; live templates are never written)
    yellow_fill = PatternFill(fill_type="solid", start_color="FFFFFF00", end_color="FFFFFF00")
    nofill = PatternFill(fill_type=None)
    rekey_ever = collections.defaultdict(set)
    hdr_new = {}
    for t in H.TABS:
        ws = wb[t]
        tgt = sheets[t].hdr
        hdr_new[t] = tgt
        plain = next((r for r in sheets[t].rows if not r["foreign"]), None)
        ws.delete_rows(3, ws.max_row)
        r = 3
        grs = sorted(final[t], key=lambda x: (x["orange"], x.get("ord", 10 ** 7)))
        for gi, g in enumerate(grs):
            keys = list(g["opts"])
            if gi == 0 and not g.get("no_default") and g["dkey"] in keys:
                keys = [g["dkey"]] + [k_ for k_ in keys if k_ != g["dkey"]]   # a tab ALWAYS starts with a default row
            for k in keys:
                o = g["opts"][k]
                is_def = k == g["dkey"] and not g.get("no_default")
                srow = o["row"] or g["tmpl"]
                foreign = srow["foreign"]
                styl = plain if foreign else srow
                for h, c in tgt.items():
                    cell = ws.cell(r, c)
                    if styl is not None and h in styl["rec"]:
                        cell._style = copy.copy(styl["rec"][h][1])
                    if h in sheets[t].meta_cols:
                        v = srow["rec"].get(h)
                        cell.value = v[0] if v else None
                    elif not foreign and o["row"] is not None:
                        cell.value = srow["rec"][h][0] if h in srow["rec"] else None
                    else:
                        cell.value = None
                    if h not in sheets[t].meta_cols and (foreign or styl is not srow):
                        cell.fill = copy.copy(nofill)
                # v3a: yellow paint is NEVER changed on existing rows (whole-row copy); only generated rows inherit the group's yellow cells
                if o["row"] is None:
                    for h in o["yell"]:
                        if h in tgt:
                            ws.cell(r, tgt[h]).fill = copy.copy(yellow_fill)
                _y_before = counts["fix:yellow_removed_foreign"]
                if counts["fix:yellow_removed_foreign"] > _y_before:
                    counts["damaged_rows:foreign_yellow_paint"] += 1
                ws.cell(r, 2).value = o["val"]
                if o["row"] is not None and nrm(ws.cell(r, tgt["Family"]).value) != nrm(g.get("fam")) :
                    counts["fix:family_unified"] += 1
                    counts["damaged_rows:family_from_other_switch"] += 1
                ws.cell(r, tgt["Family"]).value = g.get("fam")
                if o["row"] is None:
                    stat = (None, None)
                elif not o["src"]:
                    stat = (None, None)
                elif all(sr_.get("restored") for sr_ in o["src"]):
                    stat = (None, None)
                else:
                    bst = max(o["src"], key=lambda s_: (s_["tab"] == t, s_["rec"]["POS_SYM"][0] if isinstance(s_["rec"]["POS_SYM"][0], (int, float)) else -1))
                    stat = (bst["rec"]["AVG_DELTA"][0], bst["rec"]["POS_SYM"][0])
                ws.cell(r, tgt["AVG_DELTA"]).value, ws.cell(r, tgt["POS_SYM"]).value = stat
                for h in ("override", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA", "LIVE_DELTA", "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS"):
                    if h in tgt:
                        ws.cell(r, tgt[h]).value = None
                bf = ws.cell(r, 2).font
                ws.cell(r, 2).font = Font(name=bf.name or "Arial", size=bf.size or 10, bold=is_def, italic=bf.italic, color=bf.color)
                ws.cell(r, tgt["is_default"]).value = "YES" if is_def else "NO"
                ws.cell(r, tgt["is_default"]).font = Font(name="Arial", size=10, bold=is_def)
                af = ws.cell(r, 1).font
                ws.cell(r, 1).font = Font(name=af.name or "Arial", size=af.size or 10, bold=af.bold, italic=af.italic, color=(GREY if g["grey"] else None))
                ws.cell(r, 1).value = g["name"]
                ws.cell(r, 1).fill = PatternFill(fill_type="solid", start_color="FF" + ORANGE, end_color="FF" + ORANGE) if g["orange"] else copy.copy(nofill)
                if k == keys[0] and (g["added"] or g["home"] not in g["from"] or g["grey"] or g["was_grey"] or g["imp"] in ("UNCLEAR", "UNWIRED")):
                    txt = f"v2 staged: {g['why']}" + (f" | ADDED {g['added']}" if g["added"] else "") + (f" | GREY {g['greyw']}" if g["grey"] else "") + (" | was grey, now calculated" if g["was_grey"] and not g["grey"] else "") + (f" | importance {g['imp']}" if g["imp"] in ("UNCLEAR", "UNWIRED") else "")
                    ws.cell(r, 1).comment = Comment(txt[:500], "template_restructure_v2")
                for sr in o["src"]:
                    rekey_ever[(sr["tab"], g["name"], nrm(sr["rec"]["default"][0]))].add((t, nrm(ws.cell(r, 2).value)))
                r += 1
    old_parked = []
    if "PARKED_ROWS" in wb.sheetnames:
        for rr in wb["PARKED_ROWS"].iter_rows(min_row=2, values_only=True):
            if rr and rr[3] not in (None, ""):
                old_parked.append(list(rr))
        del wb["PARKED_ROWS"]
    pk = wb.create_sheet("PARKED_ROWS")
    cols = ["reason", "from_tab", "from_row", "Switch", "default", "Family", "is_default", "AVG_DELTA", "POS_SYM", "yellow"]
    pk.append(cols)
    for rr in old_parked:
        pk.append(rr)
    counts["parked_carried_over"] = len(old_parked)
    for p_ in parked:
        pk.append([p_[c] for c in cols])
    wb.save(str(out / TEMPLATES[cs]))
    old = ever["cells"].get(cs, [])
    newset, dropped = set(), 0
    hsets = {t: set(hdr_new[t]) for t in H.TABS}
    for key in old:
        tab, sb, hdr = key.split("\t", 2)
        sw, cand = sb.split("=", 1)
        tg = rekey_ever.get((tab, sw, cand.strip()))
        if not tg:
            dropped += 1
            continue
        for nt, nc in tg:
            if hdr in hsets[nt]:
                newset.add(f"{nt}\t{sw}={nc}\t{hdr}")
            else:
                dropped += 1
    ever_out["cells"][cs] = sorted(newset)
    with open(rep / f"rows_{cs}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["template", "tab_now", "row_now", "switch", "option", "action", "reason", "home_tab", "importance"])
        w.writerows(csv_rows)
    with open(rep / f"options_{cs}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["template", "switch", "tab", "before_option", "before_type", "after_option", "after_type", "class", "action", "reason"])
        w.writerows(opt_log)
    counts["damaged_rows:option_parked"] = sum(v for k, v in counts.items() if k in ("parked:FOREIGN_OPTION", "parked:OPTION_TYPE_MISMATCH"))
    counts["hist_backup_used"] = HIST_FILES.get(cs) or ""
    counts.update({"parked_total": len(parked), "ever_yellow_old": len(old), "ever_yellow_new": len(newset), "ever_yellow_dropped": dropped})
    sw_info = [{"name": g["name"], "home": g["home"], "from": g["from"], "grey": g["greyw"], "imp": g["imp"], "live": g["live"], "vec": g["vec"], "added": g["added"], "why": g["why"], "type": ("MISSING" if g["ref"] is MISSING else type(g["ref"]).__name__), "default": nrm(g["opts"][g["dkey"]].get("new") if g["opts"][g["dkey"]]["row"] is None else g["opts"][g["dkey"]]["row"]["rec"]["default"][0]), "n_opts": len(g["opts"]), "n_copies": len(g["from"])} for t in final for g in final[t]]
    agg = collections.OrderedDict()
    for p_ in parked:
        agg.setdefault((p_["Switch"], p_["reason"].split(":")[0]), []).append(p_)
    for (n_, kind), lst in agg.items():
        decisions.append([cs, n_, "|".join(sorted({str(x["default"]) for x in lst}))[:80], f"PARKED({kind}) x{len(lst)}", lst[0]["reason"][:220], evidence(n_)])
    for x in sw_info:
        if x["grey"]:
            decisions.append([cs, x["name"], "", "GREY(" + x["grey"].split(" ")[0] + ")", x["grey"], evidence(x["name"])])
    with open(rep / f"decisions_{cs}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["template", "switch", "options", "decision", "reason", "evidence_file_line"])
        w.writerows(decisions)
    with open(rep / f"switches_{cs}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["switch", "home_tab", "was_in_tabs", "n_options", "default", "cfg_type", "grey", "importance", "live_consumers", "vec_consumers", "added", "note"])
        for x in sw_info:
            w.writerow([x["name"], x["home"], "|".join(x["from"]), x["n_opts"], x["default"], x["type"], x["grey"], x["imp"], x["live"], x["vec"], "|".join(x["added"]), x["why"]])
    return {"counts": dict(counts), "unclear": unclear, "switches": sw_info}


KEEP_CALC = {"HTF_GATE_D_MANDATORY", "HTF_TREND_VETO_BYPASS_ENABLED", "HTF_TREND_VETO_BYPASS_REASONS", "DELTA_GATE_BB_SQUEEZE", "WT_DC_STOCH_THRESHOLD_LONG", "WT_DC_STOCH_THRESHOLD_SHORT", "WT_DC_DC_POS_THRESHOLD_LONG", "WT_DC_DC_POS_THRESHOLD_SHORT", "BTC_ACCEL_RAMP_REQUIRE_POSITIVE"}


if __name__ == "__main__":
    main()
