#!/usr/bin/env python3
"""v15_yellow_from_deltas v2 — colour the TEMPLATE filter cells from recorded evidence (USER 2026-09-30).

Per cat_side template (CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT), evidence = every progress JSON of sym_sides OF THAT
CAT_SIDE, any round, any host ('done' records: per row key 'TAB!n:SWITCH=cand', per filter header a value in 'yellows'):
  BRIGHT yellow FFFFFF00 = the cell EVER produced a real non-zero filter effect (valid, not engine-invalid, not non-binding/noop,
                           delta != the row's naked switch delta) for any sym_side of that cat_side
  LIGHT  yellow FFFFF2CC = the cell was NEVER calculated for that cat_side (absent from every 'yellows'/'yellow_reasons'; a numeric delta
                           incl. 0.0 or a recorded INVALID reason counts as calculated) -> the pilot must test it
  uncoloured             = calculated, only zero / noop / invalid
The pilot evaluates BOTH colours. Rows/columns the pilot never evaluates (empty or *_ALT option, row skipped for NOT_IN_CONFIG/DEAD etc.,
filter name in no config) are never coloured. Only fills of filter cells (columns with a 'FILTER=opt' header) that are currently yellow /
light-yellow / empty are changed; other fills (e.g. FFDDEBF7), values, fonts, bold, row order, row count are untouched.
Matching is value-normalised (1 == 1.0, True == true) so progress keys map onto template rows/headers.
Evidence file: data/yellow_ever_nonzero.json {"cells": bright, "light_yellow_untested": light, "symsides", ...}.

  --emit-partial OUT --files LIST   stdlib-only scan of progress JSONs (runs on the hosts)
  --collect                         drive every host in tools/fleet_hosts.json, merge, classify against the templates -> JSON
  --apply                           paint the templates from the JSON (default = dry-run report)
"""
import os as _os, sys as _sys
if _os.path.exists(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "SPREADSHEETS", "TEMPLATES_FROZEN")) and "--dry-run" not in _sys.argv and not _os.environ.get("TEMPLATES_UNFREEZE"):
    _writes = any(a in _sys.argv for a in ("--apply", "--write", "--out-dir")) or "v15_daily_template_update" in __file__ or "restructure" in __file__
    if _writes and ("--apply" in _sys.argv or "v15_daily_template_update" in __file__ or "restructure" in __file__):
        _sys.exit("REFUSED: SPREADSHEETS/TEMPLATES_FROZEN exists (USER 2026-10-01: templates restored to the pre-trainwreck 20:36 version; no writer may touch them until the user approves a proposal). Remove the file only on explicit user approval.")
import argparse, datetime, hashlib, json, os, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
SKIP_REASONS = ("DEAD_VEC", "LIVE_ONLY", "SKIPPED_GREY", "NO_CANDIDATE", "NOT_IN_CONFIG", "INVENTED_ALT", "NOT_WIRED_VEC")
EPS = 1e-9
REUSE = "--reuse" in sys.argv
def _argval(name, default):
    return pathlib.Path(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


OUT_JSON = _argval("--out", ROOT / "data" / "yellow_ever_nonzero.json")  # discovery runs pass --out data/yellow_discovery/<date>/...: NEVER overwrite the pilot-read file by accident
PARTIALS_DIR = _argval("--partials-dir", ROOT / "data" / "yellow_partials")
NEWCODE_ONLY = "--new-code-only" in sys.argv or os.environ.get("YD_NEWCODE_ONLY") == "1"  # only progress JSONs written by the fixed pilot (entries carry 'naked_binding'); legacy files carry fake zeros
BRIGHT, LIGHT = "FFFFFF00", "FFFFF2CC"
SW = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]


def nv(v) -> str:
    s = str(v).strip().lower()
    try:
        return format(float(s), ".10g")
    except ValueError:
        return s


def split_eq(s):
    a, b = str(s).split("=", 1)
    return a.strip(), b.strip()


def triple(tab, sw, val, hdr) -> str:
    f, o = split_eq(hdr)
    # TAB-FREE evidence key (2026-10-01): the delta of (switch=cand x filter=opt) does not depend on which tab the row lives in, so evidence
    # recorded under an OLD tab layout still applies after rows moved tabs. Output keys still use the template's real tab.
    return f"*\t{sw.strip()}={nv(val)}\t{f}={nv(o)}"


def h8(s) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:16]


def cat_side_of(symside):
    s = symside.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    if not side:
        return None
    base = s[: -(len(side) + 1)]
    return ("CRYPTO" if base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "STOCKS") + "_" + side


def scan(files):
    bright = {cs: set() for cs in CAT_SIDES}
    calc = {cs: set() for cs in CAT_SIDES}
    skiprows = {cs: set() for cs in CAT_SIDES}
    syms = {cs: set() for cs in CAT_SIDES}
    for p in files:
        ss = os.path.basename(p)[: -len("_v14_progress.json")]
        cs = cat_side_of(ss)
        if cs is None:
            continue
        try:
            done = (json.load(open(p)) or {}).get("done", {})
        except Exception:
            continue
        if not isinstance(done, dict):
            continue
        if NEWCODE_ONLY and not any(isinstance(_e, dict) and "naked_binding" in _e for _e in done.values()):
            continue  # legacy (pre-47794652) progress file: fabricated zeros / copied values, not evidence
        syms[cs].add(ss)
        # DUP RULE (v15_zero_audit): a non-zero yellow repeated (same value to 1e-9) over rows whose switch is non-binding (running row /
        # naked delta 0) within one baseline epoch is the filter's STANDALONE effect copied onto unrelated switches, not a cell-specific
        # effect -> never BRIGHT (modern pilots blank such cells and list them in 'yellow_dups'; legacy files are collapsed here).
        _seen_dup = set()
        for key, e in done.items():
            if not isinstance(e, dict):
                continue
            try:
                tab = key.split("!", 1)[0].strip()
                sw, val = key.split(":", 1)[1].split("=", 1)
            except Exception:
                continue
            if str(e.get("reason") or "").startswith(SKIP_REASONS):
                skiprows[cs].add(f"*\t{sw.strip()}={nv(val)}")
                continue
            bad = e.get("yellow_reasons") or {}
            noop = set(e.get("noop_yellows") or [])
            naked = e.get("naked_delta")
            naked = float(naked) if isinstance(naked, (int, float)) else 0.0
            for hdr in bad:
                if "=" in hdr:
                    calc[cs].add(h8(triple(tab, sw, val, hdr)))
            for hdr, d in (e.get("yellows") or {}).items():
                if "=" not in hdr or not isinstance(d, (int, float)):
                    continue
                t = triple(tab, sw, val, hdr)
                calc[cs].add(h8(t))
                if hdr in bad or hdr in noop or abs(d) <= EPS or abs(d - naked) <= EPS:
                    continue
                if bool(e.get("is_running")) or e.get("naked_delta") == 0 or e.get("naked_binding") is False:
                    _dk = (round(float(e.get("cumulative_before") or 0.0), 6), hdr, round(float(d), 9))
                    if _dk in _seen_dup:
                        continue
                    _seen_dup.add(_dk)
                    if not e.get("is_running") and e.get("naked_binding") is not None:
                        pass
                    continue  # first occurrence of a standalone effect is a measurement of the FILTER, not of this switch row: not bright either
                bright[cs].add(t)
    return bright, calc, skiprows, syms


def emit_partial(listfile, out):
    files = [l.strip() for l in open(listfile) if l.strip()]
    b, c, s, y = scan(files)
    json.dump({"bright": {k: sorted(v) for k, v in b.items()}, "calc": {k: sorted(v) for k, v in c.items()}, "skiprows": {k: sorted(v) for k, v in s.items()},
               "symsides": {k: sorted(v) for k, v in y.items()}, "files": len(files)}, open(out, "w"))
    print(f"[partial] {out} files={len(files)} bright={sum(len(v) for v in b.values())} calc={sum(len(v) for v in c.values())}")


def template_index(cs):
    import openpyxl
    wb = openpyxl.load_workbook(str(ROOT / "SPREADSHEETS" / f"TEMPLATE_{cs}.xlsx"))
    return wb


def classify(cs, wb, bright, calc, skiprows, known):
    """-> {(tab,row,col): 'B'|'L'|None} for every filter cell of every evaluable row, + template-term lists + diagnostics."""
    cells, blist, llist = {}, [], []
    diag = {"rows_skipped_template": 0, "cols_unknown_filter": 0, "bright_unmatched": 0}
    bmatched = set()
    for tab in SW:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        hdrs = {c: str(ws.cell(row=2, column=c).value).strip() for c in range(1, ws.max_column + 1) if isinstance(ws.cell(row=2, column=c).value, str) and "=" in ws.cell(row=2, column=c).value}
        okcols = {}
        for c, h in hdrs.items():
            f, o = split_eq(h)
            if f not in known or o.endswith("_ALT"):
                diag["cols_unknown_filter"] += 1
                continue
            okcols[c] = h
        for r in range(3, ws.max_row + 1):
            a = ws.cell(row=r, column=1).value
            b = ws.cell(row=r, column=2).value
            if a in (None, ""):
                continue
            rawkey = f"{str(a).strip()}={str(b).strip()}"
            if b in (None, "") or str(b).strip().lower() in ("none",) or str(b).strip().endswith("_ALT") or str(a).strip() not in known or f"*\t{str(a).strip()}={nv(b)}" in skiprows:
                diag["rows_skipped_template"] += 1
                continue
            for c, h in okcols.items():
                t = triple(tab, str(a).strip(), b, h)
                if t in bright:
                    st = "B"
                    bmatched.add(t)
                    blist.append(f"{tab}\t{rawkey}\t{h}")
                elif h8(t) in calc:
                    st = None
                else:
                    # USER-SAFETY 2026-10-01: 'never calculated' for EVERY filter column of every (new) row = 300k cells/sheet (infeasible). Light only where the switch
                    # and the filter share >= 1 name token (related filters); the rest stays plain until evidence exists.
                    _st_tok = {x for x in str(a).upper().split("_") if len(x) > 1}
                    _ft_tok = {x for x in split_eq(h)[0].upper().split("_") if len(x) > 1}
                    if not (_st_tok & _ft_tok):
                        cells[(tab, r, c)] = None
                        continue
                    st = "L"
                    llist.append(f"{tab}\t{rawkey}\t{h}")
                cells[(tab, r, c)] = st
    diag["bright_unmatched"] = len(bright - bmatched)
    return cells, blist, llist, diag, sorted(bright - bmatched)[:2000]


def collect():
    import concurrent.futures as cf, subprocess, tempfile
    import v15_avg_delta_rebuild as A
    tmp = PARTIALS_DIR
    tmp.mkdir(parents=True, exist_ok=True)

    def one(h):
        if REUSE and (tmp / f"{h['name']}.json").exists():
            return json.load(open(tmp / f"{h['name']}.json"))
        if A.ssh(h, "true", 30) is None:
            return None
        via = h["_via"]
        subprocess.run(["scp", "-q", str(pathlib.Path(__file__).resolve()), f"{via}:/tmp/v15_yel.py"], check=True)
        A.ssh(h, f"{A.LIST_CMD} > /tmp/v15_yel_files.txt; YD_NEWCODE_ONLY={1 if NEWCODE_ONLY else 0} python3 /tmp/v15_yel.py --emit-partial /tmp/v15_yel_part.json --files /tmp/v15_yel_files.txt")
        dst = tmp / f"{h['name']}.json"
        subprocess.run(["scp", "-q", f"{via}:/tmp/v15_yel_part.json", str(dst)], check=True)
        return json.load(open(dst))

    with cf.ThreadPoolExecutor(len(A.HOSTS)) as ex:
        parts = [p for p in ex.map(one, A.HOSTS) if p]
    bright = {cs: set() for cs in CAT_SIDES}
    calc = {cs: set() for cs in CAT_SIDES}
    skiprows = {cs: set() for cs in CAT_SIDES}
    syms = {cs: set() for cs in CAT_SIDES}
    for p in parts:
        for cs in CAT_SIDES:
            bright[cs] |= set(p["bright"].get(cs, []))
            calc[cs] |= set(p["calc"].get(cs, []))
            skiprows[cs] |= set(p["skiprows"].get(cs, []))
            syms[cs] |= set(p["symsides"].get(cs, []))
    # a row skipped by one sym_side but evaluated by another is evaluable: only rows with NO evaluated evidence stay skipped
    import v15_pilot as P
    known = P.known_config_fields()
    res = {"built_at": datetime.datetime.utcnow().isoformat() + "Z", "rule": "bright=ever non-zero real filter effect; light=never calculated for this cat_side; per cat_side only",
           "symsides": {cs: len(syms[cs]) for cs in CAT_SIDES}, "cells": {}, "light_yellow_untested": {}, "diag": {}, "bright_unmatched_sample": {}}
    for cs in CAT_SIDES:
        wb = template_index(cs)
        cells, bl, ll, diag, un = classify(cs, wb, bright[cs], calc[cs], skiprows[cs], known)
        res["cells"][cs], res["light_yellow_untested"][cs], res["diag"][cs], res["bright_unmatched_sample"][cs] = sorted(bl), sorted(ll), diag, un
        print(f"[{cs}] symsides={len(syms[cs])} bright={len(bl)} light={len(ll)} plain={len(cells) - len(bl) - len(ll)} grid_evaluable={len(cells)} diag={diag}")
    OUT_JSON.write_text(json.dumps(res, indent=0))
    print("[collect] ->", OUT_JSON)


def apply(do_write):
    import openpyxl
    from openpyxl.styles import PatternFill
    data = json.loads(OUT_JSON.read_text())
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    summary = {}
    for cs in CAT_SIDES:
        path = ROOT / "SPREADSHEETS" / f"TEMPLATE_{cs}.xlsx"
        wb = openpyxl.load_workbook(str(path))
        bset = {tuple(x.split("\t")) for x in data["cells"][cs]}
        lset = {tuple(x.split("\t")) for x in data["light_yellow_untested"][cs]}
        cnt = {"bright": 0, "light": 0, "plain": 0, "changed": 0, "blue_or_other_fill_kept": 0}
        for tab in SW:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            hdrs = {c: str(ws.cell(row=2, column=c).value).strip() for c in range(1, ws.max_column + 1) if isinstance(ws.cell(row=2, column=c).value, str) and "=" in ws.cell(row=2, column=c).value}
            for r in range(3, ws.max_row + 1):
                a = ws.cell(row=r, column=1).value
                if a in (None, ""):
                    continue
                rawkey = f"{str(a).strip()}={str(ws.cell(row=r, column=2).value).strip()}"
                for c, h in hdrs.items():
                    k = (tab, rawkey, h)
                    want = BRIGHT if k in bset else LIGHT if k in lset else None
                    cell = ws.cell(row=r, column=c)
                    cur = str(cell.fill.fgColor.rgb or "").upper() if cell.fill is not None and cell.fill.fill_type == "solid" else None
                    cnt["bright" if want == BRIGHT else "light" if want == LIGHT else "plain"] += 1
                    if cur not in (None, BRIGHT, LIGHT):
                        cnt["blue_or_other_fill_kept"] += 1
                        continue
                    if cur == want:
                        continue
                    cell.fill = PatternFill(start_color=want, end_color=want, fill_type="solid") if want else PatternFill(fill_type=None)
                    cnt["changed"] += 1
        summary[cs] = cnt
        print(f"[{cs}] {cnt}")
        if do_write and cnt["changed"]:
            shutil.copy2(path, ROOT / "backups" / f"before_yellow_v2_{ts}_{path.name}")
            tmp = path.with_suffix(".tmp.xlsx")
            wb.save(str(tmp))
            openpyxl.load_workbook(str(tmp))
            tmp.replace(path)
            print(f"[{cs}] saved {path}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-partial")
    ap.add_argument("--files")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", help="output JSON (default data/yellow_ever_nonzero.json = pilot-read; discovery uses data/yellow_discovery/...)")
    ap.add_argument("--partials-dir")
    ap.add_argument("--new-code-only", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="reuse data/yellow_partials/*.json instead of rescanning the hosts")
    a = ap.parse_args()
    if a.emit_partial:
        return emit_partial(a.files, a.emit_partial)
    if a.collect:
        collect()
    if a.apply or a.dry_run or not a.collect:
        apply(a.apply)


if __name__ == "__main__":
    main()
