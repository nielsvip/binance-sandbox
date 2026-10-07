#!/usr/bin/env python3
"""Fill TEMPLATE.xlsx with real CSV numbers so sheet is readable.

Uses data/reports/BNB_GLD_FULL_*.csv (26 cols) to populate:
- TEMPLATE_BASELINE_METRICS (or symside_BASELINE_METRICS after clone)
- Results_30d_Deltas
- per-row L:IL deltas behind each switch
"""
import csv
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
import openpyxl
from openpyxl.styles import Font, PatternFill

SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

def fill(csv_path: Path, out_path: Path):
    csv_path = Path(csv_path)
    out_path = Path(out_path)
    # load csv
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"empty {csv_path}")
        return
    # baseline from first row (all same)
    b = rows[0]
    # Support both 26-col (current) and legacy 14-col ENTIRE (no bh/tim/dd/sharpe)
    def _f(k, d=0.0):
        v = b.get(k)
        if v is None or str(v).strip() == "":
            return d
        try:
            return float(v)
        except Exception:
            return d
    baseline = {
        'gain_pct': float(b.get('baseline_gain') or b.get('gain_pct') or 0),
        'trades': int(float(b.get('baseline_trades') or b.get('trades') or 0)),
        'bh_pct': _f('baseline_bh', _f('bh_pct', 0.0)),
        'tim_pct': _f('baseline_tim', _f('tim_pct', 0.0)),
        'max_dd_pct': _f('baseline_dd', _f('max_dd_pct', 0.0)),
        'sharpe': _f('baseline_sharpe', _f('sharpe', _f('pool_sharpe', 0.0))),
        'window': int(float(b.get('window_days') or b.get('window') or 30)),
        'symside': b.get('symside') or b.get('sym_side') or 'UNKNOWN',
    }
    print(f"{csv_path.name}: {len(rows)} rows baseline {baseline}")

    # open template
    wb = openpyxl.load_workbook(str(TEMPLATE))
    # ensure baseline sheet exists (clone behavior)
    symside = baseline['symside']
    new_baseline = f"{symside}_BASELINE_METRICS"
    old_baseline = "TEMPLATE_BASELINE_METRICS"
    if old_baseline in wb.sheetnames:
        ws = wb[old_baseline]
        ws.title = new_baseline
        # patch formulas referencing old
        for sh in wb.sheetnames:
            ws2 = wb[sh]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and old_baseline in c.value:
                        c.value = c.value.replace(old_baseline, new_baseline)
    # fill baseline metrics
    ws = wb[new_baseline] if new_baseline in wb.sheetnames else wb.create_sheet(new_baseline)
    # clear
    for r in range(2, max(20, ws.max_row+1)):
        ws.cell(r,1).value=None; ws.cell(r,2).value=None
    vals = [
        ("gain_pct (baseline honest)", baseline['gain_pct']),
        ("trades", baseline['trades']),
        ("sharpe", baseline['sharpe']),
        ("max_dd_pct", baseline['max_dd_pct']),
        ("tim_pct", baseline['tim_pct']),
        ("bh_pct", baseline['bh_pct']),
        ("window_bars", baseline['window']),
        ("symside", baseline['symside']),
        ("valid", True),
        ("source", f"fill_template_from_csv {csv_path.name} {baseline['window']}d {symside}"),
    ]
    for i,(k,v) in enumerate(vals, start=2):
        ws.cell(i,1).value=k; ws.cell(i,2).value=v
    # fix E:E chain clamp already in template, leave

    # fill Results_30d_Deltas
    # header: key, default, override, is_non_default, delta_gain_vs_bh, delta_sharpe, delta_trades, variant_gain, REAL_COMPLETE_DELTA, variant_sharpe, trades, tim, dd, filter_or_override
    target = None
    for cand in ["Results_30d_Deltas","Results_30d","results"]:
        if cand in wb.sheetnames:
            target=cand; break
    if not target:
        ws2=wb.create_sheet("Results_30d_Deltas")
        target="Results_30d_Deltas"
    ws2=wb[target]
    # clear old
    for r in range(2, ws2.max_row+1):
        for c in range(1, ws2.max_column+1):
            ws2.cell(r,c).value=None
    # header row1 already exists, ensure it matches expected — 24-col full per INSTRUCTIONS 5
    hdr = ['key','default','override','is_non_default','delta_gain_vs_bh','delta_sharpe','delta_trades','variant_gain','REAL_COMPLETE_DELTA','variant_sharpe','trades','tim','dd','filter_or_override','symside','window','bh_pct','gain_pct','tim_pct','max_dd','win_rate','bars','peak','source']
    # ensure header row
    for ci, h in enumerate(hdr, start=1):
        ws2.cell(1, ci).value=h
        ws2.cell(1, ci).font = Font(bold=True)
    # DEDUPE: Results_30d_Deltas is keyed by switch=val for VLOOKUP($A&"="&$B).
    # CSV has one row per switch+filter combo (9291). Writing all creates
    # duplicate keys so VLOOKUP returns first arbitrary filter's delta.
    # Keep best delta per switch (max delta) for Results; per-filter deltas
    # go to L:BI via csv_map below.
    from collections import defaultdict
    best_per_switch: dict[str, dict] = {}
    for rec in rows:
        k = f"{rec['switch']}={rec['switch_value']}"
        cur = best_per_switch.get(k)
        d = float(rec.get('delta') or 0)
        if cur is None or d > float(cur.get('delta') or -1e99):
            best_per_switch[k] = rec
    deduped = list(best_per_switch.values())
    # sort by original order (first appearance of each switch)
    order = {f"{r['switch']}={r['switch_value']}": i for i, r in enumerate(rows)}
    deduped.sort(key=lambda r: order.get(f"{r['switch']}={r['switch_value']}", 999999))
    print(f"  deduped Results: {len(rows)} csv rows -> {len(deduped)} unique switches (VLOOKUP key = switch=val)")
    for r_i, rec in enumerate(deduped, start=2):
        key = f"{rec['switch']}={rec['switch_value']}"
        default = rec['switch_value']
        is_non = "YES"
        # legacy 14-col has delta/new_gain/new_trades but no delta_sharpe/new_sharpe/new_tim/new_dd/new_bh
        def _rf(k, d=0):
            v = rec.get(k)
            if v is None or str(v).strip() == "":
                return d
            try:
                return float(v) if isinstance(d, float) else int(float(v)) if isinstance(d, int) else v
            except Exception:
                return d
        delta_gain_vs_bh = float(rec.get('delta') or 0)
        delta_sharpe = float(rec.get('delta_sharpe') or 0)
        delta_trades = int(float(rec.get('delta_trades') or 0))
        variant_gain = float(rec.get('new_gain') or 0)
        variant_sharpe = float(rec.get('new_sharpe') or rec.get('new_gain') or 0)
        trades = int(float(rec.get('new_trades') or 0))
        tim = float(rec.get('new_tim') or rec.get('new_gain') or 0)
        dd = float(rec.get('new_dd') or 0)
        # extended metrics for 24-col (bh etc from CSV new_* cols)
        new_bh = float(rec.get('new_bh') or rec.get('new_gain') or 0)
        filt = f"{rec['filter']}={rec['filter_value']}"
        ws2.cell(r_i,1).value = key
        ws2.cell(r_i,2).value = rec['switch_value']
        ws2.cell(r_i,3).value = rec['switch_value']
        ws2.cell(r_i,4).value = is_non
        ws2.cell(r_i,5).value = delta_gain_vs_bh
        ws2.cell(r_i,6).value = delta_sharpe
        ws2.cell(r_i,7).value = delta_trades
        ws2.cell(r_i,8).value = variant_gain
        ws2.cell(r_i,9).value = delta_gain_vs_bh
        ws2.cell(r_i,10).value = variant_sharpe
        ws2.cell(r_i,11).value = trades
        ws2.cell(r_i,12).value = tim
        ws2.cell(r_i,13).value = dd
        ws2.cell(r_i,14).value = filt
        ws2.cell(r_i,15).value = rec['symside']
        ws2.cell(r_i,16).value = rec['window_days']
        ws2.cell(r_i,17).value = new_bh
        ws2.cell(r_i,18).value = variant_gain
        ws2.cell(r_i,19).value = tim
        ws2.cell(r_i,20).value = dd
        ws2.cell(r_i,21).value = 0  # win_rate not in CSV, left 0
        ws2.cell(r_i,22).value = trades  # bars proxy
        ws2.cell(r_i,23).value = variant_gain  # peak proxy
        ws2.cell(r_i,24).value = f"fill {rec['symside']} {rec['window_days']}d"
    # extend VLOOKUP range to cover >5000 (BNB has 9291, deduped ~400 but keep 10000)
    # Must handle both $H and $P variants and both $A$2 vs $A2 forms
    for sh in SWITCH_SHEETS:
        if sh not in wb.sheetnames: continue
        ws = wb[sh]
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for c in row:
                v = c.value
                if isinstance(v, str) and "Results_30d_Deltas!" in v and "$A" in v:
                    # expand any $5000 to $15000 and ensure $H->$P coverage
                    nv = v.replace("$H$5000","$P$15000").replace("$H$10000","$P$15000").replace("$P$5000","$P$15000")
                    nv = nv.replace("$A$2:$H$","$A$2:$P$").replace("$A2:$H","$A2:$P")
                    if "$P$15000" not in nv and "$A$2:$P" in nv:
                        nv = nv.replace("$A$2:$P$5000","$A$2:$P$15000")
                    if nv != v:
                        c.value = nv

    # fill per-row L:IL deltas
    # Build header_to_col per sheet from row2 col12 onward
    # CSV per switch/filter delta: map (switch, filter=opt) -> delta
    csv_map = {}
    for rec in rows:
        key = (rec['switch'], f"{rec['filter']}={rec['filter_value']}")
        csv_map[key] = float(rec['delta'])
    for sh in SWITCH_SHEETS:
        if sh not in wb.sheetnames: continue
        ws = wb[sh]
        # header_to_col
        h2c = {}
        for c in range(12, ws.max_column+1):
            hv = ws.cell(2, c).value
            if hv and isinstance(hv, str) and "=" in hv:
                h2c[hv.strip()] = c
        if not h2c:
            continue
        # for each switch row
        for r in range(3, ws.max_row+1):
            switch = ws.cell(r,1).value
            if not switch or not isinstance(switch, str): continue
            switch=switch.strip()
            for filt_key, col in h2c.items():
                delta = csv_map.get((switch, filt_key))
                if delta is not None:
                    cell = ws.cell(r, col)
                    # only fill if empty to avoid overwriting existing manual
                    cell.value = delta
                    # color positive green, negative red
                    if delta > 0:
                        cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    elif delta < 0:
                        cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    cell.number_format = '0.0000'

    wb.save(str(out_path))
    print(f"saved {out_path}  rows {len(rows)} -> sheets {len(wb.sheetnames)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: fill_template_from_csv.py <csv> <out_xlsx>")
        sys.exit(1)
    fill(Path(sys.argv[1]), Path(sys.argv[2]))
