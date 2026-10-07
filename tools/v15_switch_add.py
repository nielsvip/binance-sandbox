"""v15_switch_add — THE switch-adding tool (USER 2026-10-06 exception to the template one-script rule).

Adds ONE wired switch (2+ option rows) to the exact right tab of the right
TEMPLATE_FINAL_NORM file(s): whole-row insert after the last WHITE row
(before the orange block), style mirrored, default row first + bold B +
is_default YES. Refuses: unknown tab, <2 candidates, default not in
candidates, config default mismatch, unwired switches, duplicates.

Order of operations (full procedure: SWITCH_ADD_GUIDE.md):
  1. wire code first (config + QuickConfig + live fn + vec predicate + call site)
  2. registry entry + bible rebuild + verify_switch_bible green for the switch
  3. THIS TOOL --dry-run (default), review, then --apply (+ --fleet to sync)

Usage: python3 tools/v15_switch_add.py --switch NAME --tab TAB --candidates a,b
       --default a --venues both --sides both [--apply] [--fleet]
"""
import argparse
import ast
import copy
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_restructure_v3 as R  # noqa: E402
import openpyxl  # noqa: E402
from openpyxl.comments import Comment  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from template_row_guard import fingerprints  # noqa: E402

SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
FILES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
FLEET = ["s1-via-gateway", "s2", "s5"]


def die(msg):
    sys.exit(f"REFUSED: {msg}")


def ast_assigns(path, cls=None):
    tree = ast.parse(Path(path).read_text())
    out = {}
    nodes = tree.body
    if cls:
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name == cls:
                nodes = n.body
                break
        else:
            return {}
    for n in nodes:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            try:
                out[n.target.id] = ast.literal_eval(n.value) if n.value else None
            except Exception:
                out[n.target.id] = None
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    try:
                        out[t.id] = ast.literal_eval(n.value)
                    except Exception:
                        out[t.id] = None
    return out


def parse_opt(raw, want):
    s = str(raw).strip()
    if want == bool:
        if s in ("True", "1", "1.0"):
            return True
        if s in ("False", "0", "0.0"):
            return False
        die(f"candidate {raw!r} is not a bool")
    if want == int:
        try:
            return int(float(s))
        except Exception:
            die(f"candidate {raw!r} is not an int")
    if want == float:
        try:
            return float(s)
        except Exception:
            die(f"candidate {raw!r} is not a float")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--default", required=True)
    ap.add_argument("--venues", default="both", choices=["crypto", "stocks", "both"])
    ap.add_argument("--sides", default="both", choices=["long", "short", "both"])
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    a = ap.parse_args()
    sw = a.switch.strip().upper()
    if a.tab not in SWITCH_SHEETS:
        die(f"unknown tab {a.tab} (must be one of the 13 SWITCH_SHEETS)")
    cands = [c.strip() for c in a.candidates.split(",") if c.strip() != ""]
    if len(set(cands)) < 2:
        die("need at least 2 distinct candidates")
    if a.default.strip() not in cands:
        die("default must be one of the candidates")
    venues = ["CRYPTO", "STOCKS"] if a.venues == "both" else [a.venues.upper()]
    sides = ["LONG", "SHORT"] if a.sides == "both" else [a.sides.upper()]
    if "_LONG" in sw:
        sides = ["LONG"]
    if "_SHORT" in sw:
        sides = ["SHORT"]
    want = None
    for v in venues:
        cfg = ast_assigns(ROOT / ("config.py" if v == "CRYPTO" else "config_tradier.py"), "Config" if v == "CRYPTO" else "TradierConfig")
        if sw not in cfg:
            die(f"{sw} is not a field of {'config.py' if v == 'CRYPTO' else 'config_tradier.py'} — add the config field first")
        dv = cfg[sw]
        t = bool if isinstance(dv, bool) else (int if isinstance(dv, int) and not isinstance(dv, bool) else (float if isinstance(dv, float) else str))
        want = t if want is None else want
        if parse_opt(a.default, t) != (dv if not isinstance(dv, float) else float(dv)):
            die(f"default {a.default!r} != {v} config default {dv!r} — align config first")
    qc = ast_assigns(ROOT / "v12_quick_engine.py", "QuickConfig")
    if sw not in qc:
        die(f"{sw} is not a QuickConfig field — wire v12_quick_engine first")
    try:
        bible = json.loads((ROOT / "data" / "SWITCH_BIBLE.json").read_text())["switches"]
    except Exception as e:
        die(f"cannot read data/SWITCH_BIBLE.json ({e}) — rebuild the bible first")
    e = bible.get(sw)
    if not e:
        die(f"{sw} has no SWITCH_BIBLE entry — rebuild the bible first")
    if str(e.get("status") or "").upper().startswith("DEAD"):
        die(f"{sw} bible status is dead ({e.get('status')})")
    if not (e.get("vec_reads") or e.get("vec_read_count")):
        die(f"{sw} has no vec reads — wire v12_quick_engine/vec_decisions first")
    if not any((e.get("live_read_count") or {}).values()):
        die(f"{sw} has no live reads — wire ez_manage/tradier_manage first")
    reg = (e.get("agent_c_registry") or {})
    if reg.get("suggested_home_tab") and reg["suggested_home_tab"] != a.tab:
        die(f"registry suggested_home_tab is {reg['suggested_home_tab']}, not {a.tab}")
    files = [(f"{v}_{s}", FILES[f"{v}_{s}"]) for v in venues for s in sides]
    typed = [parse_opt(c, want) for c in cands]
    dflt = parse_opt(a.default, want)
    ordered = [dflt] + [t for t in typed if t != dflt]
    plan = []
    for cs, fn in files:
        p = Path(a.templates_dir) / fn
        if not p.exists():
            die(f"missing {p}")
        wb = openpyxl.load_workbook(str(p))
        try:
            ws = wb[a.tab]
            if any(ws.cell(r, 1).value == sw for r in range(3, ws.max_row + 1)):
                die(f"{sw} already has rows in {fn}!{a.tab}")
            last_w = 2
            for r in range(3, ws.max_row + 1):
                if ws.cell(r, 1).value in (None, ""):
                    continue
                if not (ws.cell(r, 1).fill.fill_type == "solid" and R.ORANGE in str(ws.cell(r, 1).fill.fgColor.rgb or "")):
                    last_w = r
            plan.append((cs, fn, p, last_w, ordered))
        finally:
            wb.close()
    for cs, fn, p, last_w, opts in plan:
        print(f"[{cs}] {fn}!{a.tab}: insert {len(opts)} rows at {last_w + 1} (after last white row) default={dflt!r}", flush=True)
    if not a.apply:
        print("dry-run OK — re-run with --apply to write (backs up each file first)", flush=True)
        return 0
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    for cs, fn, p, last_w, opts in plan:
        shutil.copy2(p, ROOT / "backups" / f"before_switch_add_{sw}_{ts}_{fn}")
        wb = openpyxl.load_workbook(str(p))
        try:
            ws = wb[a.tab]
            before = fingerprints(ws)
            ws.insert_rows(last_w + 1, len(opts))
            hdrs = [ws.cell(2, c).value for c in range(1, ws.max_column + 1)]
            isd = hdrs.index("is_default") + 1
            ref = last_w
            for i, o in enumerate(opts):
                rr = last_w + 1 + i
                for c in range(1, ws.max_column + 1):
                    tgt, src = ws.cell(rr, c), ws.cell(ref, c)
                    tgt.font, tgt.fill, tgt.alignment, tgt.border, tgt.number_format = copy.copy(src.font), copy.copy(src.fill), copy.copy(src.alignment), copy.copy(src.border), src.number_format
                ws.cell(rr, 1).value = sw
                ws.cell(rr, 2).value = o
                ws.cell(rr, 2).font = Font(bold=(o == dflt))
                ws.cell(rr, isd).value = "YES" if o == dflt else "NO"
                ws.cell(rr, 1).comment = Comment(f"switch-add {ts}: wired switch staged by tools/v15_switch_add.py", "switch_add")
            after = fingerprints(ws)
            lost = sum((before - after).values())
            if lost:
                die(f"{fn}!{a.tab}: {lost} original rows changed — aborting, restore from backups/before_switch_add_{sw}_{ts}_{fn}")
            grp = [(ws.cell(r, 2).font.bold, ws.cell(r, isd).value) for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value == sw]
            if sum(1 for b, y in grp if b and y == "YES") != 1 or sum(1 for b, y in grp if (b and y != "YES") or (y == "YES" and not b)):
                die(f"{fn}!{a.tab}: DEFAULTS-GATE failed for {sw} (need exactly one bold B == YES)")
            tmp = str(p) + ".addtmp"
            wb.save(tmp)
            import zipfile
            with zipfile.ZipFile(tmp) as z:
                if len(z.namelist()) < 10:
                    die(f"refusing truncated save {fn}")
            os.replace(tmp, str(p))
            print(f"[{cs}] installed {len(opts)} rows -> {fn}!{a.tab}", flush=True)
        finally:
            wb.close()
    if a.fleet:
        for h in FLEET:
            for _, fn in files:
                subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new", str(Path(a.templates_dir) / fn), f"{h}:~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/{fn}"], check=False)
            r = subprocess.run(["ssh", "-S", "none", "-o", "StrictHostKeyChecking=accept-new", h, "cd ~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM && md5sum " + " ".join(fn for _, fn in files)], capture_output=True, text=True)
            print(f"[{h}] {r.stdout.strip()}", flush=True)
    else:
        print("fleet NOT synced — re-run with --fleet (or rsync + md5 by hand)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
