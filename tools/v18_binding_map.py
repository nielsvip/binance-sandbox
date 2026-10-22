#!/usr/bin/env python3
"""V18/V19 binding map (spec 22_V19_NETWORK_UPGRADE_SPEC §2.3, family map 22_SWITCH_FAMILY_MAP §c).

Per cat_side, strictly separate: data/learn/binding_map_<CS>.json, contract "binding_map_v1":
  switches {SW: family, stage, masters, vec_reads, dead, anchor}
  filters  {F: class, stages, master, vec_reads, anchor}
  rules    [{family, class, verdict, condition_keys, anchor}]
  rows     {"<TAB>!<SW>=<cand>": {family, dead_row, cells: {"F=opt": {verdict, condition_keys, reason}}}}
  summary  {TAB: {verdict: n}, "_total": {...}}
Cells = the template's shaded yellow headers (fill FFFF00 / FFF2CC, row-2 headers from col O). Verdicts are computed under
the cat_side default set (template bold > SWITCH_BIBLE template_bold > cat_side_defaults_4 > QuickConfig) overlaid with the
row's own switch=cand. condition_keys are ALL-of "SWITCH=value" (a cell with condition_mode "any" needs one of them;
"WIN_TRAIL_EROSION_PCT>0" is the one numeric key). verdict_for() computes any (row, header) pair, shaded or not.
Unknown family/class -> CONDITIONAL reason UNKNOWN_FAMILY (fail-open, never NEVER by ignorance).

  python3 tools/v18_binding_map.py build --cat-side all [--out-dir data/learn]
"""
import argparse
import ast
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CONTRACT = "binding_map_v1"
FAMILY_MAP = ROOT / "data" / "learn" / "switch_family_map_20261022.json"
SWITCH_BIBLE = ROOT / "data" / "SWITCH_BIBLE.json"
ABLATION_GROUPS = ROOT / "vec_decisions" / "filter_ablation_groups.py"
EXEMPTIONS = ROOT / "vec_decisions" / "filter_exemptions.py"
TEMPLATE_DIR = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")
YELLOW = ("FFFFFF00", "FFFFF2CC")
HEADER_COL0 = 14
FAMILIES = ("ENTRY", "REENTRY", "EXIT_STOP", "EXIT_TARGET", "EXIT_VEL", "EXIT_STRUCT", "AUGMENT", "REDUCE", "SIZING", "GLOBAL")
CLASSES = ("ENTRY_TF_MASK", "ENTRY_BOOL_GATE", "ENTRY_PARAM", "EXIT_CONFIRM", "EROSION_CONFIRM", "REDUCE_CONFIRM", "REDUCE_SIGNAL_TF", "REDUCE_PARAM", "EXIT_TF_SELECTOR", "EXIT_PARAM", "REENTRY_PARAM", "NO_ENGINE_READ")
ENTRY_GATES = ("ENTRY_TF_MASK", "ENTRY_BOOL_GATE")
EXIT_CLASSES = ("EXIT_TF_SELECTOR", "EXIT_PARAM")
REDUCE_CLASSES = ("REDUCE_CONFIRM", "REDUCE_SIGNAL_TF", "REDUCE_PARAM")
DEAD_IN_VEC = ("DC_DAYTRADE_STOP_PCT", "DC_DAYTRADE_TARGET_PCT", "TRADIER_DC_DAYTRADE_STOP_PCT", "TRADIER_DC_DAYTRADE_TARGET_PCT")
STOCKS_ONLY = ("MANDATORY_REENTRY_WT_FILTER_TF_MODE",)
CRYPTO_ONLY = ("FAST_RISER_FILTER_TF",)
REENTRY_MASK_KEYS = ("REENTRY_ENTRY_FILTER_ENABLED=True", "REENTRY_APPLY_ENTRY_GATES_ENABLED=True")
DROP_REASONS = ("SELF_PAIR", "NO_ENGINE_READ", "DEAD_ROW", "DC_DAYTRADE_BRANCH")
A_DEAD = "v12_quick_engine.py:17396-17402 (DC_DAYTRADE_*_PCT assigned, never read); data/SWITCH_BIBLE.json vec_read_count.reachable==0"
A_ENTRY = "vec_decisions/generic_filter_tf.py:30; v12_quick_engine.py:16659,17035"
A_REENTRY = "v12_quick_engine.py:17262,19263 (REENTRY_ENTRY_FILTER_ENABLED / REENTRY_APPLY_ENTRY_GATES_ENABLED)"
A_PARAM = "ported_entry.py:117; vec_decisions/twin_yellow_filters.py REGISTRY"
A_AUG = "vec_decisions/filter_exemptions.py:35 AUGMENT_GATE_KEYS"
A_XC = "v12_quick_engine.py:22484-22491 (exit_sig and held_bars>=min_hold and _xc_ok)"
A_XC_NEVER = "v12_quick_engine.py:22484 (loss exits bypass), 21908 (DC branch precedes)"
A_EROSION = "v12_quick_engine.py:21908 (DC daytrade elif), 21960-21964 (WIN_TRAIL_EROSION leg)"
A_REDUCE = "v12_quick_engine.py:22166-22177"
A_EXIT = "vec_decisions/twin_yellow_filters.py:100"
A_RALLY = "vec_decisions/hardcoded_rally_filters.py:29"
A_REXIT = "v12_quick_engine.py:19226 (reentry population follows exits)"
A_INDIRECT = "22_SWITCH_FAMILY_MAP §c rules 6/15 (different decision point; cell = filter standalone)"
A_NOREAD = "vec_decisions/twin_yellow_filters.py docstring; FILTER_WIRING_CENSUS_20260928.md"
A_UNKNOWN = "family or class not in switch_family_map_20261022.json (fail-open)"


class BindingMapError(Exception):
    pass


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def literal_assign(path: Path, name: str):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise BindingMapError(f"{name} not found in {path}")


def vstr(v) -> str:
    return "" if v is None else str(v)


def truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() not in ("", "off", "false", "0", "0.0", "none", "null")


def bible_default(entry: dict, cs: str):
    d = (entry or {}).get("defaults") or {}
    tb = (d.get("template_bold") or {}).get(cs)
    if isinstance(tb, list) and tb:
        return tb[0], "bible_template_bold"
    c4 = d.get("cat_side_defaults_4") or {}
    if cs in c4:
        return c4[cs], "cat_side_defaults_4"
    if cs.startswith("STOCKS") and "QuickConfig_apply_tradier_defaults" in d:
        return d["QuickConfig_apply_tradier_defaults"], "QuickConfig_tradier"
    if "QuickConfig" in d:
        return d["QuickConfig"], "QuickConfig"
    return None, None


def read_template(path: Path) -> dict:
    """-> {"tabs": {TAB: [{"row", "switch", "cand", "bold", "yellows": [hdr...]}]}, "header_bold": {hdr: bool}}"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    out = {"tabs": {}, "header_bold": {}}
    try:
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            hdrs = []
            rows = []
            for i, row in enumerate(ws.iter_rows(min_row=2)):
                if i == 0:
                    for c in row[HEADER_COL0:]:
                        h = vstr(c.value).strip()
                        hdrs.append(h)
                        if h and "=" in h:
                            out["header_bold"][h] = bool(c.font is not None and c.font.b)
                    continue
                sw = vstr(row[0].value).strip() if row else ""
                if not sw or len(row) < 2:
                    continue
                ys = []
                for j, c in enumerate(row[HEADER_COL0:HEADER_COL0 + len(hdrs)]):
                    fg = c.fill.fgColor if c.fill is not None else None
                    rgb = fg.rgb if fg is not None else None
                    if isinstance(rgb, str) and rgb.upper() in YELLOW and hdrs[j] and "=" in hdrs[j]:
                        ys.append(hdrs[j])
                rows.append({"row": i + 2, "switch": sw, "cand": row[1].value, "bold": bool(row[1].font is not None and row[1].font.b), "yellows": ys})
            out["tabs"][tab] = rows
    finally:
        wb.close()
    return out


class Ctx:
    """Everything the verdict needs for one cat_side."""

    def __init__(self, cs, sw_family, filters, defaults, dead, augment_gate_keys):
        self.cs = cs
        self.sw_family = sw_family
        self.filters = filters
        self.defaults = defaults
        self.dead = dead
        self.augment_gate_keys = set(augment_gate_keys)

    def value(self, key, overlay):
        return overlay[key] if key in overlay else self.defaults.get(key)


def _cell(verdict, reason, keys=None, mode=None):
    out = {"verdict": verdict, "condition_keys": list(keys or []), "reason": reason}
    if mode:
        out["condition_mode"] = mode
    return out


def _master_gate(ctx, fname, overlay, verdict_if_on, reason_on):
    master = (ctx.filters.get(fname) or {}).get("master")
    if not master or truthy(ctx.value(master, overlay)):
        return _cell(verdict_if_on, reason_on)
    return _cell("CONDITIONAL", "MASTER_OFF", [f"{master}=True"])


def verdict_for(ctx: Ctx, switch: str, cand, header: str) -> dict:
    """Verdict of one (row switch=cand, header FILTER=opt) under ctx defaults overlaid with the row's own setting."""
    fname = header.split("=", 1)[0].strip()
    if switch in ctx.dead:
        return _cell("NEVER", "DEAD_ROW")
    if fname == switch:
        return _cell("NEVER", "SELF_PAIR")
    fam = ctx.sw_family.get(switch, "UNKNOWN")
    cls = (ctx.filters.get(fname) or {}).get("class", "UNKNOWN")
    if cls == "NO_ENGINE_READ":
        return _cell("NEVER", "NO_ENGINE_READ")
    if fam not in FAMILIES or cls not in CLASSES:
        return _cell("CONDITIONAL", "UNKNOWN_FAMILY")
    if fname in STOCKS_ONLY and ctx.cs.startswith("CRYPTO"):
        return _cell("NEVER", "STOCKS_ONLY_FILTER")
    if fname in CRYPTO_ONLY and ctx.cs.startswith("STOCKS"):
        return _cell("NEVER", "CRYPTO_ONLY_FILTER")
    ov = {switch: cand}
    if cls == "EROSION_CONFIRM":
        dc_on = truthy(ctx.value("DC_DAYTRADE_ENABLED", ov)) and (truthy(ctx.value("DAYTRADE_DC_TARGET_TF", ov)) or truthy(ctx.value("DAYTRADE_DC_STOP_TF", ov)))
        if dc_on:
            return _cell("NEVER", "DC_DAYTRADE_BRANCH")
        master = (ctx.filters.get(fname) or {}).get("master")
        return _cell("CONDITIONAL", "EROSION_LEG", ["WIN_TRAIL_EROSION_PCT>0"] + ([f"{master}=True"] if master and not truthy(ctx.value(master, ov)) else []))
    if fam == "ENTRY":
        if cls in ENTRY_GATES:
            return _master_gate(ctx, fname, ov, "BINDS", "ENTRY_MASK")
        if cls == "ENTRY_PARAM":
            return _master_gate(ctx, fname, ov, "BINDS", "ENTRY_PARAM_ARMED")
        return _cell("INDIRECT", "DIFFERENT_STAGE")
    if fam == "REENTRY":
        if cls in ENTRY_GATES:
            if any(truthy(ctx.value(k.split("=")[0], ov)) for k in REENTRY_MASK_KEYS):
                return _master_gate(ctx, fname, ov, "BINDS", "REENTRY_MASKS_ON")
            return _cell("CONDITIONAL", "REENTRY_MASKS_OFF", REENTRY_MASK_KEYS, "any")
        if cls == "REENTRY_PARAM":
            return _master_gate(ctx, fname, ov, "BINDS", "REENTRY_PARAM")
        return _cell("INDIRECT", "DIFFERENT_STAGE")
    if fam == "AUGMENT":
        if cls == "ENTRY_BOOL_GATE" and fname in ctx.augment_gate_keys:
            return _cell("BINDS", "AUGMENT_GATE_KEY")
        return _cell("INDIRECT", "DIFFERENT_STAGE")
    if fam in ("EXIT_VEL", "EXIT_STRUCT", "EXIT_STOP", "EXIT_TARGET"):
        if cls == "EXIT_CONFIRM":
            return _cell("BINDS", "EXIT_SIG_CONFIRM") if fam in ("EXIT_VEL", "EXIT_STRUCT") else _cell("NEVER", "EXIT_CONFIRM_BYPASSED")
        if cls in EXIT_CLASSES:
            return _master_gate(ctx, fname, ov, "BINDS", "EXIT_SELECTOR")
        if cls == "REENTRY_PARAM":
            master = (ctx.filters.get(fname) or {}).get("master")
            return _cell("CONDITIONAL", "REENTRY_FOLLOWS_EXIT", [f"{master}=True"] if master else [])
        return _cell("INDIRECT", "DIFFERENT_STAGE")
    if fam == "REDUCE":
        if cls in REDUCE_CLASSES:
            return _master_gate(ctx, fname, ov, "BINDS", "REDUCE_GATE")
        return _cell("INDIRECT", "DIFFERENT_STAGE")
    return _cell("INDIRECT", "DIFFERENT_STAGE")


def rules_table() -> list:
    r = []
    add = lambda f, c, v, k, a: r.append({"family": f, "class": c, "verdict": v, "condition_keys": list(k), "anchor": a})
    add("ANY_DEAD_ROW", "ANY", "NEVER", [], A_DEAD)
    add("ANY", "SELF_PAIR", "NEVER", [], "filter name == row switch name")
    add("ANY", "NO_ENGINE_READ", "NEVER", [], A_NOREAD)
    add("ANY", "EROSION_CONFIRM", "NEVER", ["DC_DAYTRADE_ENABLED=True", "DAYTRADE_DC_TARGET_TF|DAYTRADE_DC_STOP_TF non-empty"], A_EROSION)
    add("ANY", "EROSION_CONFIRM", "CONDITIONAL", ["WIN_TRAIL_EROSION_PCT>0"], A_EROSION)
    add("ENTRY", "ENTRY_TF_MASK", "BINDS", ["<filter master>=True when master-gated"], A_ENTRY)
    add("ENTRY", "ENTRY_BOOL_GATE", "BINDS", [], A_ENTRY)
    add("ENTRY", "ENTRY_PARAM", "CONDITIONAL", ["<filter master>=True"], A_PARAM)
    add("REENTRY", "ENTRY_TF_MASK", "CONDITIONAL", list(REENTRY_MASK_KEYS), A_REENTRY)
    add("REENTRY", "ENTRY_BOOL_GATE", "CONDITIONAL", list(REENTRY_MASK_KEYS), A_REENTRY)
    add("REENTRY", "REENTRY_PARAM", "BINDS", ["HARDCODED_RALLY_REENTRY_ENABLED=True"], A_RALLY)
    add("AUGMENT", "ENTRY_BOOL_GATE", "BINDS", ["filter in AUGMENT_GATE_KEYS"], A_AUG)
    add("AUGMENT", "ENTRY_TF_MASK", "INDIRECT", [], A_INDIRECT)
    add("EXIT_VEL", "EXIT_CONFIRM", "BINDS", [], A_XC)
    add("EXIT_STRUCT", "EXIT_CONFIRM", "BINDS", [], A_XC)
    add("EXIT_STOP", "EXIT_CONFIRM", "NEVER", [], A_XC_NEVER)
    add("EXIT_TARGET", "EXIT_CONFIRM", "NEVER", [], A_XC_NEVER)
    for f in ("EXIT_VEL", "EXIT_STRUCT", "EXIT_STOP", "EXIT_TARGET"):
        add(f, "EXIT_TF_SELECTOR", "BINDS", ["<filter master>=True"], A_EXIT)
        add(f, "EXIT_PARAM", "BINDS", ["<filter master>=True"], A_EXIT)
        add(f, "REENTRY_PARAM", "CONDITIONAL", ["<filter master>=True"], A_REXIT)
    for c in REDUCE_CLASSES:
        add("REDUCE", c, "BINDS", ["<filter master>=True"], A_REDUCE)
    add("SIZING", "ANY", "INDIRECT", [], A_INDIRECT)
    add("GLOBAL", "ANY", "INDIRECT", [], A_INDIRECT)
    add("ANY", "OTHER", "INDIRECT", [], A_INDIRECT)
    add("UNKNOWN", "UNKNOWN", "CONDITIONAL", [], A_UNKNOWN)
    return r


def load_sources(cs: str, family_map_path=FAMILY_MAP, bible_path=SWITCH_BIBLE, template_dir=TEMPLATE_DIR):
    fm = json.loads(Path(family_map_path).read_text())
    bible = json.loads(Path(bible_path).read_text()).get("switches") or {}
    groups = literal_assign(ABLATION_GROUPS, "GROUPS")
    agk = literal_assign(EXEMPTIONS, "AUGMENT_GATE_KEYS")
    tpath = Path(template_dir) / f"TEMPLATE_{cs}.xlsx"
    tpl = read_template(tpath)
    srcs = {str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else str(p): md5_of(p) for p in (Path(family_map_path), Path(bible_path), ABLATION_GROUPS, EXEMPTIONS, tpath)}
    return fm, bible, groups, agk, tpl, srcs


def build_one(cs: str, fm: dict, bible: dict, groups: dict, agk, tpl: dict, srcs: dict) -> dict:
    if cs not in CAT_SIDES:
        raise BindingMapError(f"bad cat_side {cs}")
    sw_family = {}
    fam_stage = {}
    for fam, v in (fm.get("families") or {}).items():
        fam_stage[fam] = v.get("stage", "")
        for s in v.get("switches") or []:
            sw_family.setdefault(s, fam)
    group_of = {}
    for g, members in groups.items():
        for m in members:
            group_of.setdefault(m, g.replace("ABLATION_DISABLE_FILTER_", ""))
    defaults, dsrc = {}, {}
    for name, e in bible.items():
        v, s = bible_default(e, cs)
        if s is not None:
            defaults[name], dsrc[name] = v, s
    for tab_rows in tpl["tabs"].values():
        for r in tab_rows:
            if r["bold"]:
                defaults[r["switch"]], dsrc[r["switch"]] = r["cand"], "template_bold"
    for h, b in tpl["header_bold"].items():
        f, o = h.split("=", 1)
        if b and dsrc.get(f) != "template_bold":
            defaults[f], dsrc[f] = o, "template_header_bold"
    fmf = fm.get("filters") or {}
    names = sorted({r["switch"] for t in tpl["tabs"].values() for r in t})
    dead = set()
    switches = {}
    filter_names = {f for f in fmf}
    for name in names:
        e = bible.get(name)
        reach = int(((e or {}).get("vec_read_count") or {}).get("reachable", 0)) if e else -1
        is_dead = name in DEAD_IN_VEC or reach == 0
        if is_dead:
            dead.add(name)
        masters = []
        if name in fmf and fmf[name].get("master"):
            masters.append(fmf[name]["master"])
        else:
            parts = name.split("_")
            for i in range(len(parts) - 1, 0, -1):
                cand = "_".join(parts[:i]) + "_ENABLED"
                if cand != name and (cand in bible or cand in sw_family):
                    masters.append(cand)
                    break
        fam = sw_family.get(name, "UNKNOWN")
        anchor = (e or {}).get("vec_reads") or []
        switches[name] = {"family": fam, "stage": fam_stage.get(fam, ""), "masters": masters, "vec_reads": reach, "dead": is_dead, "anchor": (anchor[0] if anchor else ("DEAD_IN_VEC v12:17396-17402" if name in DEAD_IN_VEC else "")), "ablation_group": group_of.get(name)}
    hdr_bases = {h.split("=", 1)[0] for h in tpl["header_bold"]}
    filters = {}
    for f in sorted(filter_names | hdr_bases):
        v = fmf.get(f) or {}
        e = bible.get(f)
        filters[f] = {"class": v.get("class", "UNKNOWN"), "stages": list(v.get("stages") or []), "master": v.get("master"), "vec_reads": int(((e or {}).get("vec_read_count") or {}).get("reachable", 0)) if e else -1, "anchor": v.get("anchor", A_UNKNOWN), "ablation_group": group_of.get(f)}
    ctx = Ctx(cs, sw_family, filters, defaults, dead, agk)
    rows = {}
    summary = {"_total": {"BINDS": 0, "CONDITIONAL": 0, "INDIRECT": 0, "NEVER": 0}}
    reasons = {}
    dup = 0
    for tab in SWITCH_SHEETS:
        summ = summary.setdefault(tab, {"BINDS": 0, "CONDITIONAL": 0, "INDIRECT": 0, "NEVER": 0})
        for r in tpl["tabs"].get(tab) or []:
            key = f"{tab}!{r['switch']}={vstr(r['cand'])}"
            if key in rows:
                dup += 1
                continue
            cells = {}
            for h in r["yellows"]:
                c = verdict_for(ctx, r["switch"], r["cand"], h)
                cells[h] = c
                summ[c["verdict"]] += 1
                summary["_total"][c["verdict"]] += 1
                reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
            rows[key] = {"family": sw_family.get(r["switch"], "UNKNOWN"), "dead_row": r["switch"] in dead, "row": r["row"], "default_row": r["bold"], "cells": cells}
    masters_used = sorted({v["master"] for v in filters.values() if v.get("master")} | {"DC_DAYTRADE_ENABLED", "DAYTRADE_DC_TARGET_TF", "DAYTRADE_DC_STOP_TF", "WIN_TRAIL_EROSION_PCT", "REENTRY_ENTRY_FILTER_ENABLED", "REENTRY_APPLY_ENTRY_GATES_ENABLED"})
    meta = {"cat_side": cs, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": srcs, "contract": CONTRACT, "cells": "template shaded yellow headers (FFFF00/FFF2CC) per row", "defaults_order": "template_bold > template_header_bold > bible_template_bold > cat_side_defaults_4 > QuickConfig(_tradier)", "masters_default": {m: {"value": defaults.get(m), "source": dsrc.get(m)} for m in masters_used}, "augment_gate_keys": list(agk), "n_rows": len(rows), "n_dead_switches": len(dead & set(names)), "duplicate_row_keys": dup, "reasons": reasons, "condition_keys_semantics": "ALL-of SWITCH=value; condition_mode 'any' = ANY-of; 'WIN_TRAIL_EROSION_PCT>0' numeric"}
    return {"meta": meta, "switches": switches, "filters": filters, "rules": rules_table(), "rows": rows, "summary": summary}


def write_map(bm: dict, out_dir: Path) -> Path:
    bm = {k: v for k, v in bm.items() if not k.startswith("_")}
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"binding_map_{bm['meta']['cat_side']}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bm, default=str, separators=(",", ":")))
    os.replace(tmp, p)
    return p


def load_binding_map(path, cat_side: str) -> dict:
    bm = json.loads(Path(path).read_text())
    meta = bm.get("meta") or {}
    if meta.get("cat_side") != cat_side:
        raise BindingMapError(f"CAT_SIDE_MISMATCH want={cat_side} got={meta.get('cat_side')} path={path}")
    if meta.get("contract") != CONTRACT:
        raise BindingMapError(f"CONTRACT_MISMATCH want={CONTRACT} got={meta.get('contract')} path={path}")
    return bm


def pair_verdict(bm: dict, switch: str, cand, header: str, sheets=None) -> dict:
    """Verdict of a (switch=cand, header) pair from a loaded map: the row cell if shaded, else recomputed from switches/filters."""
    c = vstr(cand)
    keys = [f"{t}!{switch}={c}" for t in (sheets or SWITCH_SHEETS)]
    for k in keys:
        row = (bm.get("rows") or {}).get(k)
        if row and header in row["cells"]:
            return row["cells"][header]
    sw = (bm.get("switches") or {}).get(switch) or {}
    fams = {switch: sw.get("family", "UNKNOWN")}
    dead = {switch} if sw.get("dead") else set()
    defaults = {m: (v or {}).get("value") for m, v in ((bm.get("meta") or {}).get("masters_default") or {}).items()}
    ctx = Ctx((bm.get("meta") or {}).get("cat_side", ""), fams, bm.get("filters") or {}, defaults, dead, (bm.get("meta") or {}).get("augment_gate_keys") or ())
    return verdict_for(ctx, switch, cand, header)


def build(cat_side: str, out_dir: Path) -> dict:
    sides = CAT_SIDES if cat_side == "all" else (cat_side,)
    res = {}
    for cs in sides:
        fm, bible, groups, agk, tpl, srcs = load_sources(cs)
        bm = build_one(cs, fm, bible, groups, agk, tpl, srcs)
        p = write_map(bm, out_dir)
        res[cs] = {"path": str(p), "rows": bm["meta"]["n_rows"], "summary_total": bm["summary"]["_total"], "reasons": bm["meta"]["reasons"]}
    return res


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--cat-side", required=True, choices=CAT_SIDES + ("all",))
    b.add_argument("--out-dir", default=str(ROOT / "data" / "learn"))
    a = ap.parse_args()
    print(json.dumps(build(a.cat_side, Path(a.out_dir)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
