#!/usr/bin/env python3
"""Simplest calculator: baseline = all defaults, then one switch at a time + each filter, reports delta and trades in CSV.
Row by row, column by column. No magic.
Usage: python tools/simple_switch_filter_calculator.py --symside SNDK_LONG --window-days 30 --out data/reports/simple_calc_SNDK_LONG_30d.csv
"""
from pathlib import Path
import argparse, csv, sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from tools.opt.evaluate_v12 import evaluate

SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

def load_switches(template_path: Path):
    wb = openpyxl.load_workbook(str(template_path), data_only=True, read_only=True)
    switches = []  # list of (sheet, switch, default)
    for sheet in wb.sheetnames:
        # WHITELIST: only real switch sheets – avoids FILTERS_EXPLAINED etc polluting CSV
        if sheet not in SWITCH_SHEETS:
            continue
        ws = wb[sheet]
        # header is row 2, data from row 3
        for r in range(3, ws.max_row+1):
            sw = ws.cell(r,1).value
            default = ws.cell(r,2).value
            # need switch name
            if not sw or str(sw).strip() == "":
                continue
            # skip rows where switch is like "Live Location" etc
            if str(sw).startswith("Live"):
                continue
            # skip placeholder / empty switch rows
            if str(sw).strip().lower() in ("switch", "general", "blanket"):
                continue
            switches.append((sheet, str(sw).strip(), default))
    wb.close()
    return switches

def load_filters(template_path: Path):
    wb = openpyxl.load_workbook(str(template_path), data_only=True, read_only=True)
    fsheet = None
    for cand in ("FILTER_DICTIONARY_V2","FILTER_DICTIONARY_V8","FILTER_DICTIONARY"):
        if cand in wb.sheetnames:
            fsheet = cand
            break
    if not fsheet:
        wb.close()
        return []
    ws = wb[fsheet]
    hdr = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    # find Filter and Option Value columns
    try:
        filt_idx = hdr.index("Filter")
        opt_idx = hdr.index("Option Value")
    except:
        filt_idx, opt_idx = 1, 2
    filters = []
    for r in range(2, ws.max_row+1):
        row = list(ws.iter_rows(min_row=r, max_row=r, values_only=True))[0]
        filt = row[filt_idx]
        opt = row[opt_idx]
        if not filt or opt is None:
            continue
        filters.append((str(filt).strip(), str(opt).strip()))
    wb.close()
    return filters

def coerce_value(v: str):
    # Convert Option Value string to python type for overrides
    vs = str(v).strip()
    if vs.upper() == "TRUE":
        return True
    if vs.upper() == "FALSE":
        return False
    if vs.upper() == "OFF":
        return "OFF"
    # Try float/int
    try:
        if "," in vs and vs.replace(",","").replace(".","",1).isdigit():
            vs = vs.replace(",", ".")
        if vs.replace(".","",1).replace("-","",1).isdigit():
            if "." in vs:
                return float(vs)
            return int(vs)
    except:
        pass
    return vs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symside", required=True, help="e.g., SNDK_LONG or ZECUSDC_LONG")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--template", default="SPREADSHEETS/TEMPLATE.xlsx")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-filters", type=int, default=0, help="0 = all")
    args = ap.parse_args()

    template_path = ROOT / args.template
    # Prefer quarantine good if current is crippled empty? Use good if exists
    # But allow explicit template arg
    if not template_path.exists():
        template_path = ROOT / "SPREADSHEETS/quarantine_good_20260910/TEMPLATE_V2_UNIQUE_BTCUSDC_LONG_30D.xlsx"

    switches = load_switches(template_path)
    filters = load_filters(template_path)
    # --- RELEVANT FILTERS ONLY (per INSTRUCTIONS 2): not every filter for every switch ---
    # For GLD (stock) many crypto filters are irrelevant → previously produced 7881/9290 zeros (gross).
    # Use FILTER_DICTIONARY_V2 sheets_app + token_overlap like v14 does.
    try:
        from tools.opt.v14_sequential_filler import get_opportune_filters as _get_opp
        # Build map switch -> relevant filters (apply max-filters after)
        _relevant_cache = {}
        def _relevant_for(switch, sheet):
            key = (switch, sheet)
            if key not in _relevant_cache:
                _relevant_cache[key] = _get_opp(switch, sheet)
            return _relevant_cache[key]
    except Exception:
        _relevant_cache = None
        def _relevant_for(switch, sheet):
            return [{"filter": f, "opt": v} for f, v in filters]

    # Note: max-filters is applied per-switch after relevance filtering, not globally
    if args.max_filters and _relevant_cache is None:
        filters = filters[:args.max_filters]

    print(f"Template {template_path.name}: {len(switches)} switches, {len(filters)} filters (relevant per-switch filtered, was 7881 zeros for GLD)")
    print(f"Symside {args.symside} window {args.window_days} -> baseline all defaults")
    # baseline = all defaults (empty overrides)
    baseline = evaluate(args.symside, {}, window_days=args.window_days)
    bg = float(baseline.get("gain_pct") or 0.0)
    bt = int(baseline.get("trades") or 0)
    bbh = float(baseline.get("bh_pct") or 0.0)
    btim = float(baseline.get("tim_pct") or 0.0)
    bdd = float(baseline.get("max_dd_pct") or 0.0)
    bsharpe = float(baseline.get("pool_sharpe") or baseline.get("sharpe") or 0.0)
    print(f"Baseline gain {bg:.4f} trades {bt} valid {baseline.get('valid')} bh {bbh:.4f} tim {btim:.4f} dd {bdd:.4f} sharpe {bsharpe:.4f}")

    out_path = Path(args.out) if args.out else ROOT / f"data/reports/simple_calc_{args.symside}_{args.window_days}d.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # === RESUMABLE CELL-BY-CELL (OOM/reboot safe) ===
    # If out_path exists, load done keys (switch_sheet|switch|alt|filter|fval) and resume where left off.
    # File fills cell by cell; if interrupted, next run continues from last flushed row (no rework).
    done = set()
    header = ["switch_sheet","switch","switch_value","filter","filter_value","baseline_gain","new_gain","delta","baseline_trades","new_trades","delta_trades","baseline_bh","new_bh","delta_bh","baseline_tim","new_tim","delta_tim","baseline_dd","new_dd","delta_dd","baseline_sharpe","new_sharpe","delta_sharpe","window_days","symside","valid"]
    needs_header = True
    if out_path.exists():
        try:
            with open(out_path, "r", newline="") as rf:
                r = csv.DictReader(rf)
                if r.fieldnames and r.fieldnames[0] == "switch_sheet":
                    needs_header = False
                    for row in r:
                        try:
                            done.add((row["switch_sheet"], row["switch"], row["switch_value"], row["filter"], row["filter_value"]))
                        except Exception:
                            continue
            print(f"Resume: {len(done)} rows already in {out_path.name}, continuing where left off")
        except Exception as e:
            print(f"Resume check failed {e}, starting fresh")
            done = set()
            needs_header = True
    def _alt_for_progress(default):
        if default is None:
            return 1
        if isinstance(default, bool):
            return not default
        if isinstance(default, (int, float)):
            return 1 if default == 0 else float(default) * 1.5
        if isinstance(default, str):
            vs = default.strip()
            if vs == "":
                return "1"
            up = vs.upper()
            if up == "TRUE":
                return False
            if up == "FALSE":
                return True
            if up == "OFF":
                return "15m"
            if up in ("15M", "1H", "4H", "D"):
                return "OFF"
            try:
                vsc = vs.replace(",", ".").replace("%", "").strip()
                num = float(vsc)
                if "." not in vsc:
                    return int(num * 1.5) if num != 0 else 1
                return float(num * 1.5) if num != 0 else 1.0
            except Exception:
                pass
            return vs + "_ALT"
        return str(default) + "_ALT"

    # Precompute total expected for progress (relevant per-switch)
    total_expected = 0
    for sheet, switch, default in switches:
        alt_tmp = _alt_for_progress(default)
        if alt_tmp != default:
            try:
                rel = _relevant_for(switch, sheet)
                # _get_opp returns list of dicts with 'filter'/'opt', need to expand to (filter, opt) pairs per FILTER_DICTIONARY rows
                # For count, count each dict as one filter-opt combo; if max-filters set, cap per-switch
                cnt = len(rel)
                if args.max_filters and cnt > args.max_filters:
                    cnt = args.max_filters
                total_expected += cnt
            except Exception:
                total_expected += len(filters) if not args.max_filters else min(len(filters), args.max_filters)
    print(f"Total expected {total_expected}, remaining {total_expected - len(done)} (relevant filtered)")

    # Sequence of sym_sides is durable in symbols_flz/trb_long/trb_short (see v14_pilot.py ~200 deduplicated)
    # Per-symside file is the persistence — no global pointer needed beyond file existence.

    with open(out_path, "a", newline="") as f:
        w = csv.writer(f)
        if needs_header:
            w.writerow(header)
            f.flush()
        # For each switch, try flipping it, and for each filter apply it
        # Simplest: for boolean switches flip, for TF try OFF, for others try the template's alternative
        # Here we just set switch to its *non-default* by toggling boolean or trying OFF if TF, else try alternative from template's override col logic
        # For filters, we set filter to its Option Value
        count = 0
        for sheet, switch, default in switches:
            alt = _alt_for_progress(default)
            # If alt == default, skip (should not happen now — every row has alt)
            if alt == default:
                continue
            # For each filter, evaluate switch+filter together, and also switch alone
            # First, switch alone (no filter)
            # But user wants one switch at a time and applies each filter to it, so we do switch+filter
            skipped = 0
            # Build relevant list for this switch/sheet
            try:
                _rel = _relevant_for(switch, sheet)
                # Convert dicts to (filt, fval) tuples; _get_opp returns dict with 'filter' and 'opt'
                _iter = [(d.get("filter"), str(d.get("opt"))) for d in _rel if d.get("filter")]
                if args.max_filters and len(_iter) > args.max_filters:
                    _iter = _iter[:args.max_filters]
            except Exception:
                _iter = filters[:args.max_filters] if args.max_filters else filters
            for filt, fval in _iter:
                key = (sheet, switch, str(alt), filt, fval)
                if key in done:
                    skipped += 1
                    continue
                overrides = {}
                overrides[switch] = alt
                overrides[filt] = coerce_value(fval)
                res = evaluate(args.symside, overrides, window_days=args.window_days)
                ng = float(res.get("gain_pct") or 0.0)
                nt = int(res.get("trades") or 0)
                delta = ng - bg
                dt = nt - bt
                nbh = float(res.get("bh_pct") or 0.0)
                ntim = float(res.get("tim_pct") or 0.0)
                ndd = float(res.get("max_dd_pct") or 0.0)
                nsharpe = float(res.get("pool_sharpe") or res.get("sharpe") or 0.0)
                w.writerow([sheet, switch, str(alt), filt, fval, f"{bg:.6f}", f"{ng:.6f}", f"{delta:.6f}", bt, nt, dt, f"{bbh:.6f}", f"{nbh:.6f}", f"{nbh-bbh:.6f}", f"{btim:.6f}", f"{ntim:.6f}", f"{ntim-btim:.6f}", f"{bdd:.6f}", f"{ndd:.6f}", f"{ndd-bdd:.6f}", f"{bsharpe:.6f}", f"{nsharpe:.6f}", f"{nsharpe-bsharpe:.6f}", args.window_days, args.symside, res.get("valid")])
                f.flush()
                try:
                    import os
                    os.fsync(f.fileno())
                except Exception:
                    pass
                count += 1
                if count % 500 == 0 or count == 1:
                    remaining = total_expected - len(done) - count
                    print(f"  ... {count} new rows ({skipped} skipped), last {switch}+{filt} delta {delta:.4f} trades {nt} remaining ~{remaining}", flush=True)
        print(f"Done {count} new rows (skipped {len(done)} already done) -> {out_path} total {len(done)+count+1} lines incl header")

if __name__ == "__main__":
    main()
