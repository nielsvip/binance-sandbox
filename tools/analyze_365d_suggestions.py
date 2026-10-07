#!/usr/bin/env python3
"""
Deep analysis of each sym_side's 365D chart vs 30D, suggesting switches for 365D retest.
- For each 365D xlsx in SPREADSHEETS/V15_V16_CELL_BY_CELL/*_365d_matrix.xlsx (with bh+gain in name after publish),
  read Results_Deltas and Results_30d_Deltas vs 30D FINAL to find which filters had negative delta in 365D but positive in 30D.
- Suggests: which WT_DC TF, DC_HARD_STOP_TF, BB 15m, etc. to change for 365D retest.
- Output: SPREADSHEETS/V15_365D_ANALYSIS.md + per-sym JSON in data/365d_suggestions/
"""
import pathlib, glob, json, re, sys
import openpyxl

ROOT = pathlib.Path.home() / "binance-sandbox" if pathlib.Path.home().joinpath("binance-sandbox").exists() else pathlib.Path("/Users/niels/Documents/binance")
OUT_DIR = ROOT / "data/365d_suggestions"
OUT_MD = ROOT / "SPREADSHEETS/V15_365D_ANALYSIS.md"

def parse_bh_gain(name):
    m = re.search(r"_bh(m?)(\d+)p(\d+)_gain(m?)(\d+)p(\d+)_", name)
    if not m: return None
    bh = -float(f"{m.group(2)}.{m.group(3)}") if m.group(1)=="m" else float(f"{m.group(2)}.{m.group(3)}")
    g = -float(f"{m.group(5)}.{m.group(6)}") if m.group(4)=="m" else float(f"{m.group(5)}.{m.group(6)}")
    return bh, g

def analyze_one(xlsx_365, xlsx_30=None):
    try:
        wb = openpyxl.load_workbook(str(xlsx_365), data_only=True, read_only=True)
    except: return None
    # Find Results_Deltas sheet
    suggestions = []
    # Check if 365D gain < 30D gain or < bh, suggest TF changes
    bh_g = parse_bh_gain(xlsx_365.name)
    if not bh_g: 
        wb.close()
        return None
    bh, g365 = bh_g
    # Compare to 30D if available
    g30 = None
    if xlsx_30 and xlsx_30.exists():
        bh30_g30 = parse_bh_gain(xlsx_30.name)
        if bh30_g30: g30 = bh30_g30[1]
    # Simple heuristics for 365D retest
    # If 365D gain < 0 or < 30D gain *0.5, suggest wider stops and different TFs
    if g365 < 0:
        suggestions.append("365D gain negative — try DC_HARD_STOP_TF=D (wider) vs 4h, and WT_DC_TF_HTF=D instead of 4h for longer trend")
    if g30 and g365 < g30 * 0.5:
        suggestions.append(f"365D {g365:.2f} much worse than 30D {g30:.2f} — 30D overfit, try WT_DC_STOCH_TF=15m (was 5m) and DC_HARD_STOP_TF=D for 365D")
    if g365 < bh:
        suggestions.append(f"365D gain {g365:.2f} < bh {bh:.2f} — underperforms buy-hold in 365D, try WT_DC_DC_TF=D and BB 15m thresholds relaxed (WT_CHAN_15m 8 vs 10)")
    # Look at Results_Deltas for worst switches
    try:
        if "Results_Deltas" in wb.sheetnames:
            ws = wb["Results_Deltas"]
            # scan for filters with large negative delta
            for row in ws.iter_rows(min_row=2, max_row=min(30, ws.max_row), values_only=True):
                if not row or not row[0]: continue
                # row[0] switch, row[4] delta?
                try:
                    sw = str(row[0])
                    delta = float(row[4]) if row[4] else 0
                    if delta < -2:
                        # suggest flipping this switch
                        suggestions.append(f"Filter {sw} delta {delta:.2f} very negative in 365D — try alternative value for this switch in retest")
                        if len(suggestions) > 5: break
                except: continue
    except: pass
    wb.close()
    if not suggestions:
        suggestions.append("365D in line with 30D — keep same switches, maybe try DC_HARD_STOP_TF=D for wider stop only")
    return {"bh": bh, "g365": g365, "g30": g30, "suggestions": suggestions[:6]}

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Find 365D files (with 365d in name)
    files_365 = sorted((ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL").glob("*_365d_matrix.xlsx"))
    # also check FINAL dir for 365d
    files_365 += sorted((ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL").glob("*_365d_matrix.xlsx"))
    # deduplicate
    seen=set(); uniq=[]
    for f in files_365:
        if f.name not in seen:
            seen.add(f.name); uniq.append(f)
    print(f"[365D-AN] Found {len(uniq)} 365D files", flush=True)
    results={}
    for f365 in uniq:
        sym_m = re.match(r"(.+_(?:LONG|SHORT))_bh", f365.name)
        if not sym_m: continue
        sym = sym_m.group(1)
        # find 30D counterpart
        f30_cands = list((ROOT / "SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL").glob(f"{sym}_*30d_matrix.xlsx"))
        f30 = f30_cands[0] if f30_cands else None
        res = analyze_one(f365, f30)
        if res:
            results[sym] = {"file": f365.name, "analysis": res}
            # write per-sym json
            (OUT_DIR / f"{sym}_365d_suggestion.json").write_text(json.dumps(res, indent=2))
    # write MD
    md = ["# V15 365D Deep Analysis — per sym_side suggestions for 365D retest", "", f"Generated from {len(results)} 365D files (S1 365D run with found 30D settings)", ""]
    for sym, data in sorted(results.items()):
        a = data["analysis"]
        md.append(f"## {sym} — 30D {a['g30']:.2f} vs 365D {a['g365']:.2f} bh {a['bh']:.2f} ({a['file']})")
        for s in a["suggestions"]:
            md.append(f"- {s}")
        md.append("")
    OUT_MD.write_text("\n".join(md))
    print(f"[365D-AN] Wrote {OUT_MD} and {len(results)} jsons to {OUT_DIR}", flush=True)

if __name__ == "__main__":
    main()
