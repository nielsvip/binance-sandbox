#!/usr/bin/env python3
"""switch_revival_build (Agent G) — DIFF the union of all historical template versions (+ bible coverage, + the 60 BATCH1 switches) against the
LIVE template and stage the REAL missing switches (live OR vector read path exists) into the right template(s)/tab with config-typed options.
Never restores trash: decision per name from data/SWITCH_BIBLE-style tracing (data/switch_revival/<r>/bible_extra.json).
Output: SPREADSHEETS/TEMPLATE_STAGED/<ts>/TEMPLATE_*.xlsx (live templates + inserted rows; existing rows untouched, whole-row insert only),
data/switch_revival/<ts>/decisions_<cat>.csv + summary.md.
  python tools/switch_revival_build.py --r r1 --ts <ts> [--dry]"""
import argparse, collections, copy, csv, glob, json, re, sys, time
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import v15_template_restructure_v3 as R   # noqa: E402
import v15_template_options as O          # noqa: E402
import v15_template_home_rules as H       # noqa: E402
import template_row_guard as G            # noqa: E402

CATS = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
REAL = {"WIRED_BOTH_UNPROVEN", "VEC_ONLY", "LIVE_ONLY"}
TABS = H.TABS
ORANGE = "FFE699"
RECENT = 1790380800   # 2026-09-26
INFRA = re.compile(r"^(.*_FILE|FORCE_MIN_ONE_TRADE|BASE_PATH|BASE_TF|CAT_SIDE_DEFAULTS_ENABLED|.*COMMISSION.*|.*ROUND_TRIP.*|.*ACCOUNT.*|.*API.*|.*SLEEP.*|.*LOG_.*|.*_PATH$|.*_DIR$|.*_URL$|.*TOKEN.*|.*SECRET.*|MODE|.*THROTTL.*|.*TELEGRAM.*|.*EMAIL.*)$")
SEC_RE = re.compile(r"API_KEY|SECRET|TOKEN|PASSWORD|ACCOUNT_ID")


def load_hist(cat):
    hist = {}
    for j in glob.glob(str(ROOT / "data" / "switch_revival" / "scan" / cat / "*.json")):
        d = json.load(open(j))
        if d["file"] == f"SPREADSHEETS/TEMPLATE_{cat}.xlsx":
            continue
        for tab, rows in d["tabs"].items():
            for name, opt, fam, isdef, bold, orange, grey in rows:
                h = hist.setdefault(name, {"white_tabs": collections.Counter(), "orange_tabs": collections.Counter(), "opts": collections.Counter(), "oopts": collections.Counter(), "fam": collections.Counter(), "last": 0, "last_white": 0, "last_orange": 0, "files": set()})
                if orange:
                    h["orange_tabs"][tab] += 1; h["oopts"][str(opt)] += 1; h["last_orange"] = max(h["last_orange"], d["mtime"])
                else:
                    h["white_tabs"][tab] += 1; h["opts"][str(opt)] += 1; h["last_white"] = max(h["last_white"], d["mtime"])
                if fam: h["fam"][str(fam)] += 1
                h["last"] = max(h["last"], d["mtime"]); h["files"].add(d["file"])
    return hist


def live_rows(path):
    wb = openpyxl.load_workbook(path)
    white, orange = {}, collections.defaultdict(set)
    for t in TABS + ["STDEV_SLOPE_SIZING"]:
        if t not in wb.sheetnames: continue
        ws = wb[t]
        for r in range(3, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if a in (None, ""): continue
            fa = ws.cell(r, 1).fill
            if fa is not None and fa.fill_type == "solid" and ORANGE in str(fa.fgColor.rgb): orange[t].add(str(a).strip())
            else: white.setdefault(str(a).strip(), t)
    return wb, white, orange


ENTRYISH = {"THRESHOLD", "MIN", "REQUIRED", "SATOSHIT", "ALIGN", "ALIGNMENT", "ALIGNED", "TREND", "EMA", "RSI", "RSI2", "MFI", "STOCH", "CHOP", "ADX", "CLENOW", "CONFLUENCE", "CONNORS", "SQUEEZE", "STRENGTH", "REGIME", "SMA200", "VWAP", "ENTRY", "BB", "WT", "DC", "K", "FH", "MOMENTUM", "ZONE"}
EXITISH = {"EXIT", "PROFIT", "TARGET", "STOP", "HOLD", "TRAIL", "TAKE", "HOPELESS", "TECHNICAL", "DAYTRADE"}
MANUAL_EXTRA = {"COOLDOWN_BARS_TRADIER": H.RW, "SIMPLE_PRICE_GT0_ENABLED": H.GR, "WT_VEL_DECAY_THRESHOLD": H.EV, "MIN_HOLD_BARS": H.ES, "DELTA_MAX_HOLD_BARS": H.ES,
                "PROFIT_TARGET_ENABLED": H.ES, "PROFIT_TARGET_PCT": H.ES, "TECHNICAL_DC_TARGET_BUFFER_PCT": H.ES}
STDEV_TAB = "STDEV_SLOPE_SIZING"


def home_tab(name, h, be):
    if name in H.MANUAL_HOME: return H.MANUAL_HOME[name][0], "MANUAL_HOME"
    if name in MANUAL_EXTRA: return MANUAL_EXTRA[name], "MANUAL_EXTRA"
    if name.startswith("STDEV_SLOPE_SIZING"): return STDEV_TAB, "STDEV_FAMILY"
    t = H._TOK(name)
    if "SIZING" in t or t & {"DEPTH"} and "SLOPE" in t: return H.AR, "NAME_SIZING"
    life = H.strong_lifecycle(name)
    if life: return H.default_tab_for(life, name), "NAME_LIFECYCLE"
    if "ABLATION" in t and "ENTRY" in t: return H.CG, "ABLATION_ENTRY"
    if "ABLATION" in t: return H.GR, "ABLATION_OTHER"
    reg = (be.get("agent_c_registry") or {}).get("suggested_home_tab")
    k = be.get("kind")
    if (reg in (H.GR, None) or k in ("global", "unclassified")) and (t & ENTRYISH) and not (t & EXITISH) and "GAP" not in t:
        return H.CG, "TOKEN_ENTRYISH"
    if t & EXITISH and k in ("global", "unclassified"): return H.ES, "TOKEN_EXITISH"
    if reg in TABS: return reg, "AGENT_C_REGISTRY"
    ht = [x for x, _ in h["white_tabs"].most_common() if x in TABS] if h else []
    if ht: return ht[0], "HISTORY_MAJORITY"
    m = {"entry": H.CG, "exit": H.ES, "reentry": H.RA, "augment": H.AT, "reduce": H.RP, "global": H.GR, "sizing": H.AR}
    return m.get(k, H.GR), "KIND_DEFAULT"


def venue_eligible(venue, name, be):
    ic = be.get("in_config") or {}
    t = H._TOK(name)
    if venue == "crypto":
        if "TRADIER" in t or "SATOSHIT" in t and "TRADIER" in name: return False, "stocks-only name (TRADIER)"
        if ic.get("config.py"): return True, ""
        if ic.get("config_tradier.py"): return False, "config_tradier.py field without config.py field (stocks-only)"
        return bool(ic.get("QuickConfig")), "QuickConfig-only (vec)"
    if ic.get("config_tradier.py"): return True, ""
    if ic.get("config.py"): return False, "config.py field without config_tradier.py field (crypto-only)"
    return bool(ic.get("QuickConfig")), "QuickConfig-only (vec)"


def build_options(cs, name, F, F2, h, be):
    ref = F.get(name)
    if ref is R.MISSING:
        return None, "NO_CONFIG_VALUE", []
    o2 = F2.get(name)
    cls = O.classify(name, ref, None if o2 is R.MISSING else o2)
    if cls in ("STRUCT", "UNKNOWN"):
        return cls, "STRUCT_OR_UNKNOWN", []
    dflt_raw = F.cs4.get(name, ref)
    okd, dval, _ = O.normalize(cls, ref, dflt_raw)
    if not okd:
        okd, dval, _ = O.normalize(cls, ref, ref)
    cand = [dval]
    cand += list((be.get("agent_c_registry") or {}).get("valid_options") or [])
    try:
        cand += [getattr(F.qc, name)] if hasattr(F.qc, name) else []
    except Exception:
        pass
    if o2 is not R.MISSING and not isinstance(o2, (list, dict, tuple, set)):
        cand += [o2]
    cand += [o for o, _c in (h["opts"].most_common() if h else [])]
    cand += [o for o, _c in (h["oopts"].most_common() if h else [])]
    if cls in ("BOOL", "BOOLNUM"):
        cand += [True, False]
    if cls == "TF" and (name.upper().endswith("_TF") or "TF" in name.upper().split("_")):
        cand += ["OFF", "15m", "1h", "4h", "D"]
    out, seen = [], set()
    dn = O._num(dval) if cls in ("INT", "FLOAT") else None
    for c in cand:
        ok, v, why = O.normalize(cls, ref, c)
        if not ok or str(c).strip() == "None":
            continue
        if cls in ("INT", "FLOAT") and dn is not None and O.foreign_numeric(dn, O._num(v)):
            continue
        k = O.key_of(v)
        if k in seen: continue
        seen.add(k); out.append(v)
    note = ""
    if cls in ("INT", "FLOAT"):
        if len(out) < 3:
            for v in O.ladder(cls, dval, out, want=4):
                out.append(v); note = "GENERATED_LADDER"
        if dn not in (None, 0.0) and re.search(r"THRESHOLD|_MIN$|MIN_|SCORE", name) and "n:0.0" not in {O.key_of(x) for x in out} and dn > 0:
            out.append(0 if cls == "INT" else 0.0); note = (note + " NEUTRAL_0").strip()
    if cls in ("INT", "FLOAT", "TF", "ENUM") and len(out) < 3:
        note = (note + " FEW_OPTIONS").strip()
    out = out[:8]
    if O.key_of(dval) not in {O.key_of(x) for x in out}:
        out.insert(0, dval)
    return cls, note, [(v, O.key_of(v) == O.key_of(dval)) for v in out]


def copy_style_row(ws, src_r, dst_r, ncol, yellow_from=15, clear_fill_from=15):
    for c in range(1, ncol + 1):
        s, d = ws.cell(src_r, c), ws.cell(dst_r, c)
        d._style = copy.copy(s._style)
        if c >= clear_fill_from:
            d.fill = PatternFill(fill_type=None)
        d.value = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--r", default="r1")
    ap.add_argument("--ts", default=time.strftime("%Y%m%d%H%M"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--cat", default="ALL")
    a = ap.parse_args()
    rd = ROOT / "data" / "switch_revival" / a.r
    bible = json.load(open(rd / "bible_extra.json"))["switches"]
    batch1 = set(json.load(open(rd / "batch1_names.json")))
    out_dir = ROOT / "data" / "switch_revival" / a.ts
    out_dir.mkdir(parents=True, exist_ok=True)
    stg = ROOT / "SPREADSHEETS" / "TEMPLATE_STAGED" / a.ts
    stg.mkdir(parents=True, exist_ok=True)
    summary = {}
    gaps = {}
    for cs in CATS:
        if a.cat not in ("ALL", cs): continue
        venue = "crypto" if cs.startswith("CRYPTO") else "stocks"
        side = cs.split("_")[1]
        path = ROOT / "SPREADSHEETS" / f"TEMPLATE_{cs}.xlsx"
        wb, live_white, live_orange = live_rows(str(path))
        live_names = set(live_white) | set().union(*live_orange.values()) if live_orange else set(live_white)
        hist = load_hist(cs)
        F = R.Fields(cs)
        F2 = R.Fields(("STOCKS_" if cs.startswith("CRYPTO") else "CRYPTO_") + side)
        decisions = []
        cands = set(hist) | batch1
        # coverage names: real read in this venue, never in a template: only with a registry judgement (suggested_home_tab) or entry/exit/reentry/augment/reduce kind
        for n, be in bible.items():
            if n in cands: continue
            if be["status"][venue] in REAL and not be.get("template"):
                reg = (be.get("agent_c_registry") or {}).get("suggested_home_tab")
                if reg in TABS or be.get("kind") in ("entry", "exit", "reentry", "augment", "reduce"):
                    cands.add(n)
        add_white = collections.defaultdict(list)    # tab -> [(name, family, [(val,isdef)])]
        add_orange = collections.defaultdict(list)
        for n in sorted(cands):
            be = bible.get(n)
            h = hist.get(n)
            rec = {"cat": cs, "name": n, "bible_status": be["status"][venue] if be else "NOT_TRACED", "kind": (be or {}).get("kind"), "in_live_template": n in live_names,
                   "hist_files": len(h["files"]) if h else 0, "hist_last": time.strftime("%m-%d", time.gmtime(h["last"])) if h else "", "batch1": n in batch1, "decision": "", "reason": "", "tab": "", "orange_tabs": "", "options": "", "live_reads": "", "vec_reads": ""}
            if be:
                rec["live_reads"] = "; ".join((be["live_reads"].get(venue) or [])[:3]) if isinstance(be.get("live_reads"), dict) else ""
                rec["vec_reads"] = "; ".join((be.get("vec_reads") or [])[:3])
            decisions.append(rec)
            if n in live_names:
                rec["decision"], rec["reason"] = "PRESENT", "already in live template"; continue
            if be is None:
                rec["decision"], rec["reason"] = "TRASH", "not traced"; continue
            if SEC_RE.search(n):
                rec["decision"], rec["reason"] = "TRASH", "secret/credential"; continue
            elig, elig_why = venue_eligible(venue, n, be)
            if not elig:
                rec["decision"], rec["reason"] = "TRASH", f"not a {venue} switch: {elig_why}"; continue
            tok = H.side_of(n)
            if tok and tok != side and n not in R.NOT_SIDE:
                rec["decision"], rec["reason"] = "TRASH", f"wrong side ({tok} name in {side} template)"; continue
            if n.upper().endswith("_ALT"):
                rec["decision"], rec["reason"] = "TRASH", "invented _ALT"; continue
            st = be["status"][venue]
            real = st in REAL
            if not real and n not in batch1:
                rec["decision"], rec["reason"] = "TRASH", f"no real live/vec path in {venue} ({st})"; continue
            if INFRA.match(n) and n not in batch1:
                rec["decision"], rec["reason"] = "AMBIGUOUS", "infra-looking config field with a read site (not a template switch?)"; continue
            cls, note, opts = build_options(cs, n, F, F2, h, be)
            if not opts:
                rec["decision"], rec["reason"] = ("TRASH" if cls == "NO_CONFIG_VALUE" or n not in batch1 else "AMBIGUOUS"), f"no typed options ({cls}/{note})"
                if cls in ("STRUCT", "UNKNOWN"): rec["decision"], rec["reason"] = "STRUCT_SKIPPED", "list/dict/unknown-typed config field cannot be swept as scalar"
                continue
            tab, why = home_tab(n, h, be)
            fam = (h["fam"].most_common(1)[0][0] if h and h["fam"] else None)
            ot = [t for t, c in (h["orange_tabs"].items() if h else []) if t in TABS and h["last_orange"] >= RECENT]
            universal = len(ot) >= 6
            rec.update(decision="REVIVE" if real else "REVIVE_BATCH1", reason=f"{st}; tab via {why}; class {cls} {note}".strip(), tab=tab, options=json.dumps([v for v, _ in opts]), orange_tabs=("ALL" if universal else ""))
            if universal:   # universal gate = orange rows in EVERY tab, no white row (same pattern as ADX_RANGING_THRESHOLD in the live templates)
                for t in TABS:
                    add_orange[t].append((n, fam, opts))
                rec["tab"] = "ALL (orange)"
            else:
                add_white[tab].append((n, fam, opts))
            if st in ("VEC_ONLY",):
                gaps.setdefault(cs, []).append({"name": n, "venue": venue, "vec_reads": (be.get("vec_reads") or [])[:2]})
        # ---------------------------------------------------------------- insert rows (whole-row inserts; existing rows untouched)
        before = {t: G.fingerprints(wb[t]) for t in [STDEV_TAB] + TABS if t in wb.sheetnames}
        nrows = collections.Counter()
        for tab in [STDEV_TAB] + TABS:
            if tab not in wb.sheetnames: continue
            ws = wb[tab]
            ncol = ws.max_column
            hd = {str(ws.cell(2, c).value): c for c in range(1, ncol + 1) if ws.cell(2, c).value}
            c_fam, c_def = hd.get("Family", 4), hd.get("is_default", 12)
            first_orange = next((r for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value not in (None, "") and ORANGE in str(ws.cell(r, 1).fill.fgColor.rgb or "")), None)
            donor_w = next((r for r in range(ws.max_row, 2, -1) if ws.cell(r, 1).value not in (None, "") and ORANGE not in str(ws.cell(r, 1).fill.fgColor.rgb or "")), 3)
            donor_o = first_orange
            wr = [(n, fam, v, d) for (n, fam, opts) in add_white.get(tab, []) for v, d in opts]
            orr = [(n, fam, v, d) for (n, fam, opts) in add_orange.get(tab, []) for v, d in opts]
            if wr:
                at = first_orange or ws.max_row + 1
                ws.insert_rows(at, amount=len(wr))
                for i, (n, fam, v, d) in enumerate(wr):
                    r = at + i
                    copy_style_row(ws, donor_w if donor_w < at else donor_w, r, ncol)
                    ws.cell(r, 1).value = n; ws.cell(r, 2).value = v; ws.cell(r, c_fam).value = fam
                    ws.cell(r, c_def).value = "YES" if d else "NO"
                    f = ws.cell(r, 2).font; ws.cell(r, 2).font = Font(name=f.name or "Arial", size=f.size or 10, bold=bool(d), italic=f.italic, color=f.color)
                    f = ws.cell(r, c_def).font; ws.cell(r, c_def).font = Font(name=f.name or "Arial", size=f.size or 10, bold=bool(d), color=f.color)
                    ws.cell(r, 1).fill = PatternFill(fill_type=None)
                nrows[("white", tab)] += len(wr)
                if first_orange: first_orange += len(wr); donor_o = first_orange
            if orr and donor_o:
                at = ws.max_row + 1
                for i, (n, fam, v, d) in enumerate(orr):
                    r = at + i
                    copy_style_row(ws, donor_o, r, ncol)
                    ws.cell(r, 1).value = n; ws.cell(r, 2).value = v; ws.cell(r, c_fam).value = fam
                    ws.cell(r, c_def).value = "YES" if d else "NO"
                    f = ws.cell(r, 2).font; ws.cell(r, 2).font = Font(name=f.name or "Arial", size=f.size or 10, bold=bool(d), italic=f.italic, color=f.color)
                    f = ws.cell(r, c_def).font; ws.cell(r, c_def).font = Font(name=f.name or "Arial", size=f.size or 10, bold=bool(d), color=f.color)
                nrows[("orange", tab)] += len(orr)
        # integrity: every ORIGINAL row image must still exist (superset)
        bad = []
        for tab, fp in before.items():
            now = G.fingerprints(wb[tab])
            lost = sum((fp - now).values())
            if lost:
                bad.append(f"{tab}: {lost} original rows changed/lost")
        summary[cs] = {"revive": sum(1 for d in decisions if d["decision"] in ("REVIVE", "REVIVE_BATCH1")), "present": sum(1 for d in decisions if d["decision"] == "PRESENT"),
                       "trash": sum(1 for d in decisions if d["decision"] == "TRASH"), "ambiguous": sum(1 for d in decisions if d["decision"] == "AMBIGUOUS"),
                       "struct": sum(1 for d in decisions if d["decision"] == "STRUCT_SKIPPED"), "rows_added": {f"{k[0]}:{k[1]}": v for k, v in nrows.items()}, "integrity_problems": bad}
        with open(out_dir / f"decisions_{cs}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(decisions[0].keys())); w.writeheader(); w.writerows(decisions)
        print(cs, json.dumps({k: v for k, v in summary[cs].items() if k != "rows_added"}), "white+orange rows added:", sum(nrows.values()), flush=True)
        if not a.dry and not bad:
            tmp = stg / f"TEMPLATE_{cs}.tmp.xlsx"
            wb.save(str(tmp)); openpyxl.load_workbook(str(tmp)); tmp.replace(stg / f"TEMPLATE_{cs}.xlsx")
    json.dump(summary, open(out_dir / "summary.json", "w"), indent=1)
    if gaps and not a.dry:
        p = ROOT / "data" / "wiring" / "live_gaps.json"
        try: lg = json.loads(p.read_text())
        except Exception: lg = {}
        if isinstance(lg, dict):
            lg["vec_only_revived"] = gaps
            p.write_text(json.dumps(lg, indent=1))
    print("staged:", stg)


if __name__ == "__main__":
    main()
