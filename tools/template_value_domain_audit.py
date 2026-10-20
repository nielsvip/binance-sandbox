#!/usr/bin/env python3
"""TEMPLATE_* is the AUTHORITY for every switch's type and domain. Everything else must adopt it.

USER 2026-10-20: "threshold written into a timeframe field. these need to be fixed (should not exist in
TEMPLATE_* but check anyway, all config files need to adopt the value from the TEMPLATE_".

Found by the wiring audit: `VETUSDT_LONG` stores `WT_DC_TF_ENTRY = 80.0` — a THRESHOLD in a TIMEFRAME field.
`ez_positions_quick.py:3613` does `str(getattr(config, "WT_DC_TF_ENTRY", "1h"))`, which yields `"80.0"`, a
timeframe that matches no array, so the switch silently does nothing for that sym_side. A value of the wrong TYPE
is worse than a wrong value: it fails open, silently, and no gate notices.

This derives each key's allowed DOMAIN from the candidate values the templates themselves sweep (column B over
all rows of every SWITCH tab in all four TEMPLATE_FINAL_NORM files), then checks every consumer against it:
  1. the TEMPLATE_* files           (the operator expects these clean — verified, not assumed)
  2. data/per_sym_full_sets.json    (the per-sym boards live actually resolves)
  3. config.py / config_tradier.py  (the global defaults)

Domain kinds inferred from the swept candidates:
  TF     every candidate is a timeframe token (OFF/NONE/1m/3m/5m/15m/1h/4h/D/W/M or a combo like 1h_4h_D)
  BOOL   every candidate is True/False
  NUM    every candidate is numeric
  ENUM   a small fixed set of non-TF strings
  MIXED  genuinely heterogeneous -> no verdict claimed (never guessed)

A violation is a value whose TYPE cannot belong to the inferred domain — a float in a TF field, a bool in a
string field, a string in a numeric field. A numeric value merely OUTSIDE the swept range is reported separately
as OUT_OF_RANGE, because the pilot may legitimately have promoted a value it did not sweep.

Exit 0 = no type violations, 1 = violations found, 2 = could not audit. READ-ONLY unless --fix-persym is given,
which rewrites ONLY the offending per-sym keys to the template default and backs the file up first.
"""
from __future__ import annotations

import argparse
import ast
import collections
import glob
import json
import os
import pathlib
import re
import shutil
import sys
import time

ROOT = pathlib.Path(os.environ.get("BASE_PATH") or pathlib.Path(__file__).resolve().parents[1])
TF_TOKENS = {"OFF", "NONE", "", "1M", "3M", "5M", "15M", "30M", "1H", "2H", "4H", "6H", "12H", "D", "W", "M", "1D", "1W"}
SKIP_TABS = {"PROTOCOL", "LEGEND_FILTERS", "INSTRUCTIONS", "INSTRUCTIONS_V2", "FILTERS_EXPLAINED",
             "FILTER_DICTIONARY_V2", "Results_Deltas", "Results_30d_Deltas"}
NON_SWITCH = {"Switch", "default", "override", "Family", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA", "LIVE_DELTA",
              "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS", "is_default", "AVG_DELTA", "POS_SYM"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")
CRYPTO_Q = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def is_tf(v) -> bool:
    if isinstance(v, bool) or v is None:
        return False
    s = str(v).strip().upper()
    if s in TF_TOKENS:
        return True
    parts = [p for p in s.split("_") if p]
    return bool(parts) and len(parts) <= 4 and all(p in TF_TOKENS for p in parts)


def is_num(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).strip())
        return True
    except Exception:
        return False


def template_domains(paths: list) -> dict:
    """{key: {kind, candidates, default, templates, tabs}} from column B over every switch row."""
    import openpyxl
    cand = collections.defaultdict(list)
    tabs = collections.defaultdict(set)
    tmpl = collections.defaultdict(set)
    for p in paths:
        name = pathlib.Path(p).name
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        except Exception as exc:
            print(f"[warn] {name}: {exc}", file=sys.stderr)
            continue
        for ws in wb.worksheets:
            if ws.title in SKIP_TABS or "BASELINE" in ws.title:
                continue
            for i, r in enumerate(ws.iter_rows(min_row=3, max_row=min(ws.max_row or 1, 1200), max_col=2, values_only=True), 3):
                if not r or not r[0]:
                    continue
                k = str(r[0]).strip().upper()
                if not KEY_RE.match(k) or k in NON_SWITCH:
                    continue
                cand[k].append(r[1])
                tabs[k].add(ws.title)
                tmpl[k].add(name)
        wb.close()
    out = {}
    for k, vals in cand.items():
        vs = [v for v in vals if v is not None]
        if not vs:
            kind = "MIXED"
        elif all(isinstance(v, bool) or str(v).strip().lower() in ("true", "false") for v in vs):
            kind = "BOOL"
        elif all(is_tf(v) for v in vs):
            kind = "TF"
        elif all(is_num(v) for v in vs):
            kind = "NUM"
        elif all(isinstance(v, str) for v in vs) and len(set(map(str, vs))) <= 12:
            kind = "ENUM"
        else:
            kind = "MIXED"
        nums = [float(v) for v in vs if is_num(v)]
        out[k] = {"kind": kind, "candidates": sorted({str(v) for v in vs}), "default": str(vs[0]) if vs else None,
                  "lo": min(nums) if nums else None, "hi": max(nums) if nums else None,
                  "tabs": sorted(tabs[k]), "templates": sorted(tmpl[k])}
    return out


def violates(kind: str, allowed: list, v) -> str:
    """'' when fine, else the violation name. Only TYPE impossibilities are violations."""
    if v is None:
        return ""
    if kind == "TF":
        if isinstance(v, bool):
            return "BOOL_IN_TF_FIELD"
        if is_num(v) and not is_tf(v):
            return "NUMBER_IN_TF_FIELD"
        if not is_tf(v):
            return "NON_TF_IN_TF_FIELD"
        return ""
    if kind == "BOOL":
        if isinstance(v, bool):
            return ""
        return "" if str(v).strip().lower() in ("true", "false") else "NON_BOOL_IN_BOOL_FIELD"
    if kind == "NUM":
        if isinstance(v, bool):
            return "BOOL_IN_NUMERIC_FIELD"
        return "" if is_num(v) else "NON_NUMERIC_IN_NUMERIC_FIELD"
    if kind == "ENUM":
        if isinstance(v, bool):
            return "BOOL_IN_ENUM_FIELD"
        return "" if str(v) in allowed else ""   # an unswept enum member is a range issue, not a type error
    return ""


def config_defaults(path: pathlib.Path, cls: str) -> dict:
    """Class-level literal assignments, read from the AST (no import: config.py has heavy import-time work)."""
    out = {}
    try:
        tree = ast.parse(path.read_text(errors="ignore"), filename=str(path))
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for st in node.body:
                tgt, val = None, None
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    tgt, val = st.target.id, st.value
                elif isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    tgt, val = st.targets[0].id, st.value
                if tgt and KEY_RE.match(tgt) and val is not None:
                    try:
                        out[tgt] = ast.literal_eval(val)
                    except Exception:
                        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_*.xlsx"))
    ap.add_argument("--json", default=str(ROOT / "data" / "reports" / "template_value_domain_audit.json"))
    ap.add_argument("--fix-persym", action="store_true", help="rewrite ONLY type-violating per-sym values to the template default")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()

    paths = sorted(glob.glob(a.templates))
    if not paths:
        print(f"no templates matched {a.templates}")
        return 2
    dom = template_domains(paths)
    kinds = collections.Counter(v["kind"] for v in dom.values())
    print(f"templates {len(paths)}  keys with a swept domain: {len(dom)}  kinds: {dict(kinds)}")

    findings = []

    # 1. the templates themselves
    for k, d in dom.items():
        for c in d["candidates"]:
            why = violates(d["kind"], d["candidates"], c)
            if why:
                findings.append({"where": "TEMPLATE", "sym_side": None, "key": k, "value": c, "kind": d["kind"], "why": why})

    # 2. the per-sym boards
    persym_path = ROOT / "data" / "per_sym_full_sets.json"
    persym = {}
    try:
        raw = json.loads(persym_path.read_text())
        persym = raw.get("sym_sides", raw)
    except Exception as exc:
        print(f"[warn] {persym_path}: {exc}", file=sys.stderr)
    for ss, v in (persym or {}).items():
        cfg = v.get("full_set") or v.get("full_config") or v if isinstance(v, dict) else {}
        if not isinstance(cfg, dict):
            continue
        for k, val in cfg.items():
            d = dom.get(k)
            if not d:
                continue
            why = violates(d["kind"], d["candidates"], val)
            if why:
                findings.append({"where": "PER_SYM", "sym_side": ss, "key": k, "value": val, "kind": d["kind"],
                                 "why": why, "template_default": d["default"]})

    # 3. the global config defaults
    for fn, cls, label in ((ROOT / "config.py", "Config", "config.py"), (ROOT / "config_tradier.py", "TradierConfig", "config_tradier.py")):
        for k, val in config_defaults(fn, cls).items():
            d = dom.get(k)
            if not d:
                continue
            why = violates(d["kind"], d["candidates"], val)
            if why:
                findings.append({"where": label, "sym_side": None, "key": k, "value": val, "kind": d["kind"],
                                 "why": why, "template_default": d["default"]})

    by_where = collections.Counter(f["where"] for f in findings)
    by_why = collections.Counter(f["why"] for f in findings)
    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "templates": paths, "n_keys": len(dom),
           "kinds": dict(kinds), "n_findings": len(findings), "by_where": dict(by_where), "by_why": dict(by_why),
           "findings": findings[:4000], "domains": {k: v for k, v in list(dom.items())}}
    pathlib.Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.json).write_text(json.dumps(rep, indent=1, default=str))

    print(f"\nTYPE VIOLATIONS: {len(findings)}   by location: {dict(by_where)}   by kind: {dict(by_why)}")
    shown = collections.Counter()
    for f in findings:
        key = (f["where"], f["key"], f["why"])
        shown[key] += 1
    print(f"\ntop {a.top} (location | key | violation | n):")
    for (w, k, why), n in shown.most_common(a.top):
        ex = next(f for f in findings if f["where"] == w and f["key"] == k and f["why"] == why)
        print(f"  {w:16s} {k:40s} {why:26s} n={n:4d}  e.g. {ex.get('sym_side') or '-'} = {ex['value']!r} "
              f"(template kind {ex['kind']}, default {ex.get('template_default')})")

    if a.fix_persym and persym:
        bad = [f for f in findings if f["where"] == "PER_SYM"]
        if not bad:
            print("\nnothing to fix in per_sym")
        else:
            bk = ROOT / "backups" / f"per_sym_full_sets_before_domain_fix_{time.strftime('%Y%m%d%H%M', time.gmtime())}.json"
            bk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(persym_path, bk)
            raw = json.loads(persym_path.read_text())
            rows = raw.get("sym_sides", raw)
            n = 0
            # COERCE FIRST, DEFAULT ONLY AS A LAST RESORT.
            # Resetting every violation to the template default would have been destructive: 606 of the 619 are
            # `1.0`/`0.0` sitting in a BOOL field, whose VALUE is unambiguous (1.0 means True). The template
            # defaults for the two worst keys are False, so a blanket reset would have FLIPPED
            # HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H on 297 boards and HTF_GATE_D_MANDATORY on 289 — a silent
            # behaviour change on 586 sym_sides dressed up as a type fix. So: fix the TYPE, keep the VALUE, and
            # fall back to the template default only when the stored value cannot be interpreted at all
            # ('±25%' in a numeric field, True in a timeframe field, '' in a bool field).
            coerced = defaulted = 0
            changes = []
            for f in bad:
                node = rows.get(f["sym_side"])
                cfg = node.get("full_set") or node.get("full_config") or node
                d = dom[f["key"]]
                raw = cfg.get(f["key"])
                newv, how = None, None
                if d["kind"] == "BOOL" and is_num(raw) and float(str(raw)) in (0.0, 1.0):
                    newv, how = bool(float(str(raw))), "coerced"
                elif d["kind"] == "BOOL" and str(raw).strip().lower() in ("true", "false"):
                    newv, how = str(raw).strip().lower() == "true", "coerced"
                elif d["kind"] == "NUM" and is_num(raw):
                    newv, how = float(str(raw)), "coerced"
                if newv is None:
                    lit = d["default"]
                    newv = {"true": True, "false": False}.get(str(lit).strip().lower(), lit)
                    if d["kind"] == "NUM":
                        try:
                            newv = float(lit)
                        except Exception:
                            pass
                    how = "template_default"
                cfg[f["key"]] = newv
                changes.append({"sym_side": f["sym_side"], "key": f["key"], "was": raw, "now": newv, "how": how})
                n += 1
                if how == "coerced":
                    coerced += 1
                else:
                    defaulted += 1
            pathlib.Path(str(a.json).replace(".json", "_fixes.json")).write_text(json.dumps(changes, indent=1, default=str))
            print(f"  coerced (value preserved, type fixed): {coerced}")
            print(f"  adopted TEMPLATE default (value was uninterpretable): {defaulted}")
            for c in [x for x in changes if x["how"] == "template_default"]:
                print(f"     {c['sym_side']:18s} {c['key']:38s} {c['was']!r} -> {c['now']!r}")
            tmp = persym_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(raw, indent=1, default=str))
            os.replace(tmp, persym_path)
            print(f"\nFIXED {n} per-sym values to their TEMPLATE default; backup {bk}")
    print(f"\nwrote {a.json}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
