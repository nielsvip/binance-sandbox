#!/usr/bin/env python3
"""v15_template_staged_verify — structural audit of staged/live TEMPLATE_*.xlsx (report-only).
python tools/v15_template_staged_verify.py --dir SPREADSHEETS/TEMPLATE_STAGED/<ts> [--cat-side X]
Checks per template: every switch in exactly ONE tab; per (tab,switch) exactly one is_default YES == bold B; unique options per switch;
white rows above orange per tab; one bold header per yellow filter per tab; option types fit the config field; VECTOR_DELTA empty;
the pilot's own [DEFAULTS-GATE] (v15_pilot.template_bold_defaults) accepts the file. Exit 1 on any violation."""
import argparse
import collections
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_home_rules as H  # noqa: E402
import v15_template_restructure_v3 as R  # noqa: E402
import v15_template_options as O  # noqa: E402
import template_row_guard as G  # noqa: E402
import json  # noqa: E402

ORANGE = "FFE699"


def kind(v):
    return "bool" if isinstance(v, bool) else ("num" if isinstance(v, (int, float)) else "str")


def loose(v):
    if isinstance(v, bool) or str(v).strip().lower() in ("true", "false"):
        return "n:%r" % (1.0 if (v is True or str(v).strip().lower() == "true") else 0.0)
    n = O._num(v)
    return "n:%r" % n if n is not None and not isinstance(v, bool) else "s:" + str(v).strip().lower()


def audit(path: Path, cs: str, baseline=None, ever=None, orange_base=None) -> list:
    F = R.Fields(cs)
    F2 = R.Fields(("STOCKS_" if cs.startswith("CRYPTO") else "CRYPTO_") + cs.split("_")[1])
    try:
        promos = json.loads((ROOT / "data" / "cat_side_promotions.json").read_text()).get(cs) or {}
    except Exception:
        promos = {}
    wb = openpyxl.load_workbook(str(path))
    bad = []
    seen = collections.defaultdict(set)
    seen_white = collections.defaultdict(set)
    for t in H.TABS:
        ws = wb[t]
        hd = {str(ws.cell(2, c).value): c for c in range(1, ws.max_column + 1) if ws.cell(2, c).value}
        seen_orange = False
        first = next((r_ for r_ in range(3, ws.max_row + 1) if ws.cell(r_, 1).value not in (None, "")), None)
        if first is not None:
            if str(ws.cell(first, hd["is_default"]).value or "").upper() != "YES" or not ws.cell(first, 2).font.b:
                bad.append(f"{t}: FIRST DATA ROW ({first}: {ws.cell(first, 1).value}={ws.cell(first, 2).value}) is not an is_default=YES/bold row — a tab must start with a default")
        grp = collections.defaultdict(list)
        grpc = collections.defaultdict(list)
        opts = collections.defaultdict(set)
        for r in range(3, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if a in (None, ""):
                continue
            a = str(a).strip()
            seen[a].add(t)
            if not (ORANGE in str(ws.cell(r, 1).fill.fgColor.rgb or "")):
                seen_white[a].add(t)
            org = ORANGE in str(ws.cell(r, 1).fill.fgColor.rgb or "")
            if org:
                seen_orange = True
            elif seen_orange:
                bad.append(f"{t}!{r} {a}: white row below an orange row")
            grp[a].append(r)
            grpc[(a, org)].append(r)
            k = R.nrm(ws.cell(r, 2).value)
            if k in opts[(a, org)]:
                bad.append(f"{t}!{r} {a}: duplicate option {k}")
            opts[(a, org)].add(k)
            if ws.cell(r, hd["VECTOR_DELTA"]).value not in (None, ""):
                bad.append(f"{t}!{r} {a}: VECTOR_DELTA not empty")
            ref = F.get(a)
            if ref is not R.MISSING:
                o2 = F2.get(a)
                cls = O.classify(a, ref, None if o2 is R.MISSING else o2)
                if cls not in ("STRUCT", "UNKNOWN"):
                    okv, val, why = O.normalize(cls, ref, ws.cell(r, 2).value)
                    if not okv:
                        bad.append(f"{t}!{r} {a}: option {k!r} invalid for {cls}: {why}")
                    elif kind(val) != kind(ws.cell(r, 2).value) or (not isinstance(val, str) and loose(val) != loose(ws.cell(r, 2).value)):
                        bad.append(f"{t}!{r} {a}: option {k!r} is {type(ws.cell(r, 2).value).__name__}, expected {kind(val)} ({cls})")
        for a, rows in grp.items():
            ref = F.get(a)
            if ref is not R.MISSING and not isinstance(ref, (list, dict, tuple, set)):
                o2 = F2.get(a)
                cls = O.classify(a, ref, None if o2 is R.MISSING else o2)
                dr = [r for r in rows if ws.cell(r, 2).font.b]
                if dr and cls != "UNKNOWN":
                    okd, dv, _w = O.normalize(cls, ref, ws.cell(dr[0], 2).value)
                    okc, cv, _w2 = O.normalize(cls, ref, ref)
                    pv = promos.get(a, {}).get("value")
                    okp, pvv, _w3 = O.normalize(cls, ref, pv) if pv is not None else (False, None, "")
                    if okd and okc and O.key_of(dv) != O.key_of(cv) and not (okp and O.key_of(pvv) == O.key_of(dv)):
                        bad.append(f"{t}!{a}: bold default {dv!r} != venue config {cv!r} and not a recorded promotion")
        for (a, _o), rows in grpc.items():
            fams = {R.nrm(ws.cell(r, hd["Family"]).value) for r in rows}
            if len(fams) > 1:
                bad.append(f"{t}!{a}: Family differs between the rows of one switch {sorted(fams)[:3]}")
        for a, rows in grp.items():
            yes = [r for r in rows if str(ws.cell(r, hd["is_default"]).value or "").upper() == "YES"]
            bold = [r for r in rows if ws.cell(r, 2).font.b]
            if len(yes) != 1 or yes != bold:
                bad.append(f"{t}!{a}: YES {yes} bold {bold}")
        by_f = collections.defaultdict(list)
        for h, c in hd.items():
            if "=" in h and c > 14:
                by_f[h.split("=", 1)[0]].append(c)
        for f, cs_ in by_f.items():
            if sum(1 for c in cs_ if ws.cell(2, c).font.b) != 1:
                bad.append(f"{t}: filter {f} bold headers != 1")
    bl = [Path(x) for x in (baseline if isinstance(baseline, (list, tuple)) else [baseline] if baseline else []) if x and Path(x).exists()]
    if bl:
        imgs = set()
        for bp in bl:
          wbb = openpyxl.load_workbook(str(bp), read_only=False)
          for t in H.TABS:
            ws = wbb[t]
            hb = {str(ws.cell(2, c).value): c for c in range(1, ws.max_column + 1) if ws.cell(2, c).value}
            for r in range(3, ws.max_row + 1):
                if ws.cell(r, 1).value not in (None, ""):
                    imgs.add((str(ws.cell(r, 1).value).strip(), loose(ws.cell(r, 2).value), G._cell_sig(ws.cell(r, hb["AVG_DELTA"])), G._cell_sig(ws.cell(r, hb["POS_SYM"]))))
        moved = 0
        for t in H.TABS:
            ws = wb[t]
            hb = {str(ws.cell(2, c).value): c for c in range(1, ws.max_column + 1) if ws.cell(2, c).value}
            for r in range(3, ws.max_row + 1):
                if ws.cell(r, 1).value in (None, ""):
                    continue
                im = (str(ws.cell(r, 1).value).strip(), loose(ws.cell(r, 2).value), G._cell_sig(ws.cell(r, hb["AVG_DELTA"])), G._cell_sig(ws.cell(r, hb["POS_SYM"])))
                if im not in imgs and (ws.cell(r, hb["AVG_DELTA"]).value is not None or ws.cell(r, hb["POS_SYM"]).value is not None):
                    moved += 1
                    if moved <= 5:
                        bad.append(f"ROW-INTEGRITY {t}!{r} {im[0]}={ws.cell(r, 2).value!r}: AVG/POS do not belong to any baseline row with this switch+option (cell moved without its row)")
        if moved > 5:
            bad.append(f"ROW-INTEGRITY: {moved} rows in total")
    if orange_base and Path(orange_base).exists():
        wob = openpyxl.load_workbook(str(orange_base), read_only=True)
        for t in H.TABS:
            def n_orange(wbx):
                n = 0
                for row in wbx[t].iter_rows(min_row=3, max_col=1):
                    c = row[0]
                    if c.value not in (None, "") and c.fill is not None and c.fill.fill_type == "solid" and R.ORANGE in str(c.fill.fgColor.rgb or ""):
                        n += 1
                return n
            nb, ns = n_orange(wob), n_orange(wb)
            if nb and ns < 0.85 * nb:
                bad.append(f"{t}: ORANGE filter rows lost: base {nb} -> staged {ns} (< 85%)")
    for a, ts in seen_white.items():
        if len(ts) > 1:
            bad.append(f"WHITE switch {a}: in {len(ts)} tabs {sorted(ts)} (white switch rows must be unique; orange filter rows may repeat per tab)")
    try:
        import v15_pilot as P
        typed = {k: F.get(k) for k in seen if F.get(k) is not R.MISSING}
        rows, gbad = P.template_bold_defaults(path, typed, typed, set())
        for g in gbad[:20]:
            bad.append(f"DEFAULTS-GATE: {g}")
    except Exception as e:
        bad.append(f"DEFAULTS-GATE could not run: {str(e)[:150]}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cat-side", default="ALL")
    ap.add_argument("--baseline-dir", default=str(ROOT / "SPREADSHEETS"), help="templates the staged rows must descend from (row-integrity image check)")
    ap.add_argument("--orange-base-dir", default=None, help="dir with the pre-restructure TEMPLATE_*.xlsx: orange rows per tab and extra row-integrity baseline")
    a = ap.parse_args()
    rc = 0
    for cs, name in R.TEMPLATES.items():
        if a.cat_side not in ("ALL", cs):
            continue
        p = Path(a.dir) / name
        if not p.exists():
            continue
        bl_ = [Path(a.baseline_dir) / name] + ([Path(a.orange_base_dir) / name] if a.orange_base_dir else [])
        bad = audit(p, cs, bl_, None, (Path(a.orange_base_dir) / name) if a.orange_base_dir else None)
        print(f"[{cs}] violations={len(bad)}")
        for b in bad[:25]:
            print("   ", b)
        rc |= 1 if bad else 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
