#!/usr/bin/env python3
"""
Brute-force every filter × every switch for a few sym_sides, memorize pos-delta combos.
Beating-ideas-to-death: tests all filters on all switches, not only yellow boxes.
Punish with realistic vector costs; record only >1e-9 vs baseline.
Usage: PYTHONPATH=. .venv/bin/python tools/test_all_filters_brute.py --syms AAPL_LONG,ALGOUSDT_LONG --window-days 30 --workers 16
Outputs: data/reports/brute_pos_filters_{symside}_{window}d.json
"""
import argparse
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl

# Use same helpers as v15_pilot
from v15_pilot import get_defaults_for_symside, sanitize_overrides, _load_filter_dictionary, SWITCH_SHEETS, SKIP_SHEETS

def load_switches_from_template(template: Path):
    wb = openpyxl.load_workbook(str(template), data_only=True, read_only=True)
    switches = {}
    for sheet in SWITCH_SHEETS:
        if sheet in SKIP_SHEETS:
            continue
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        lst = []
        for r in range(3, ws.max_row + 1):
            sw = ws.cell(row=r, column=1).value
            cand = ws.cell(row=r, column=2).value
            if not sw or not isinstance(sw, str):
                continue
            sw = sw.strip()
            if not sw or sw.lower() in ("switch", "general", "blanket", "filter", "option value") or sw.startswith("—"):
                continue
            if cand is None:
                continue
            lst.append((sw, str(cand).strip(), sheet))
        switches[sheet] = lst
    wb.close()
    return switches

def get_template_for_symside(symside: str) -> Path:
    s = symside.upper()
    is_crypto = s.endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
    is_long = s.endswith("_LONG")
    if is_crypto:
        cand = ROOT / "SPREADSHEETS" / ("TEMPLATE_CRYPTO_LONG.xlsx" if is_long else "TEMPLATE_CRYPTO_SHORT.xlsx")
    else:
        cand = ROOT / "SPREADSHEETS" / ("TEMPLATE_STOCKS_LONG.xlsx" if is_long else "TEMPLATE_STOCKS_SHORT.xlsx")
    if cand.exists():
        return cand
    return ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"

def brute_for_sym(symside: str, window_days: int, workers: int):
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    print(f"[brute] {symside} window={window_days} workers={workers}", flush=True)
    defaults = get_defaults_for_symside(symside)
    # baseline = defaults only (no BEST overrides for pure brute)
    baseline_overrides = {}
    # Try to preload
    prep = None
    try:
        prep = prepare_batch(symside, window_days)
        print(f"[brute] {symside} prep {len(prep.get('npz_prepared', {}).get('close', []))} bars" if prep and prep.get('npz_prepared') else f"[brute] {symside} no prep, fallback to live", flush=True)
    except Exception as e:
        print(f"[brute-warn] {symside} prep failed {e}", flush=True)
        prep = None

    def eval_variant(overrides):
        sanitized, _ = sanitize_overrides(dict(overrides), defaults)
        if prep is not None:
            return evaluate_prepared_sanitized(prep, sanitized, window_days=window_days)
        else:
            from tools.opt.v12_pilot import evaluate_sanitized
            return evaluate_sanitized(symside, sanitized, window_days=window_days)

    base_vec = eval_variant(baseline_overrides)
    if not base_vec.get("valid"):
        print(f"[brute] {symside} baseline invalid {base_vec.get('invalid_reason')} gain {base_vec.get('gain_pct')} trades {base_vec.get('trades')}", flush=True)
        # still proceed, baseline_gain may be 0
    base_gain = float(base_vec.get("gain_pct") or 0)
    base_trades = int(base_vec.get("trades") or 0)
    print(f"[brute] {symside} baseline gain {base_gain:.4f} trades {base_trades} bh {base_vec.get('bh_pct')}", flush=True)

    # Switches and filters
    template = get_template_for_symside(symside)
    switches_by_sheet = load_switches_from_template(template)
    all_switches = []
    for sheet, lst in switches_by_sheet.items():
        for sw, cand, _ in lst:
            all_switches.append((sw, cand, sheet))
    # Deduplicate switches (same switch may appear multiple places? Keep first)
    seen_sw = set()
    uniq_switches = []
    for sw, cand, sheet in all_switches:
        if sw not in seen_sw:
            seen_sw.add(sw)
            uniq_switches.append((sw, cand, sheet))
    print(f"[brute] {symside} {len(uniq_switches)} unique switches from {len(all_switches)} rows", flush=True)

    fd = _load_filter_dictionary()
    print(f"[brute] {symside} {len(fd)} filter entries loaded", flush=True)
    # Expand each filter to its option values
    filter_options = []
    for entry in fd:
        filt = entry.get("filter")
        vals = entry.get("vals") or ([entry.get("opt")] if entry.get("opt") else [])
        # filter out empty/UNLIKELY
        vals = [v for v in vals if v and str(v).strip().upper() != "UNLIKELY"]
        if not vals:
            continue
        for v in vals:
            filter_options.append((filt, v, entry))

    print(f"[brute] {symside} testing {len(uniq_switches)} switches × {len(filter_options)} filter-vals = {len(uniq_switches)*len(filter_options)} combos (every filter on every switch)", flush=True)

    # For speed, test switch+filter combos in batches via ThreadPool
    import concurrent.futures as cf

    # To avoid punishing with too many combos, limit to maybe 20k per symside for this brute
    # But user said every filter on every switch for a couple syms — expect ~300 switches × 200 filters = 60k combos
    # We'll do all, but with workers 16, each 0.07s = ~70min per sym worst case. So we cap workers and batch.

    pos_map = {}  # switch -> list of {filter, fval, delta, gain, trades}
    total = 0
    pos_total = 0
    start = time.time()

    # Batch evaluation: for each switch, create variants for all filter options + baseline switch alone?
    # We'll test: baseline + switch=cand (no filter) vs baseline, then switch=cand + filter=val vs baseline
    # But user wants every filter on every switch, so we test switch=cand + filter=val vs baseline

    for sw, cand, sheet in uniq_switches:
        variants = []
        keys = []
        for filt, fval, entry in filter_options:
            # Skip if filter is same as switch? Still test — switch already set to cand, filter is extra override
            # If filter name equals switch, then it's not extra, it's same — skip duplicate
            if filt == sw:
                continue
            var = dict(baseline_overrides)
            var[sw] = cand
            # filter value: try to coerce bool/string as needed via sanitize
            var[filt] = fval
            variants.append(var)
            keys.append((filt, fval))

        # Evaluate in parallel
        results = []
        if variants:
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                def do_eval(v):
                    return eval_variant(v)
                futures = list(ex.map(do_eval, variants))
                results = futures
        else:
            continue

        for (filt, fval), vec in zip(keys, results):
            total += 1
            if not vec.get("valid"):
                continue
            gain = float(vec.get("gain_pct") or 0)
            delta = gain - base_gain
            if delta > 1e-9:
                pos_total += 1
                pos_map.setdefault(sw, []).append({"filter": filt, "fval": str(fval), "delta": float(delta), "gain": float(gain), "trades": int(vec.get("trades") or 0), "sharpe": float(vec.get("pool_sharpe") or 0)})
        # Also test naked switch (no filter) for reference
        var_naked = dict(baseline_overrides)
        var_naked[sw] = cand
        vec_naked = eval_variant(var_naked)
        if vec_naked.get("valid"):
            gain_n = float(vec_naked.get("gain_pct") or 0)
            delta_n = gain_n - base_gain
            if delta_n > 1e-9:
                pos_map.setdefault(sw, []).append({"filter": None, "fval": None, "delta": float(delta_n), "gain": float(gain_n), "trades": int(vec_naked.get("trades") or 0), "sharpe": float(vec_naked.get("pool_sharpe") or 0), "naked": True})
                pos_total += 1
                total += 1

        # Periodic heartbeat
        if (len(pos_map) % 20 == 0 or total % 5000 == 0):
            elapsed = time.time() - start
            print(f"[brute] {symside} progress switches {len(pos_map)}/{len(uniq_switches)} combos {total} pos {pos_total} elapsed {elapsed:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"[brute] {symside} DONE total combos {total} pos {pos_total} elapsed {elapsed:.0f}s unique switches with pos {len(pos_map)}", flush=True)

    out = {
        "symside": symside,
        "window_days": window_days,
        "baseline_gain": base_gain,
        "baseline_trades": base_trades,
        "baseline_bh": float(base_vec.get("bh_pct") or 0),
        "total_combos": total,
        "pos_combos": pos_total,
        "switches_tested": len(uniq_switches),
        "filters_tested": len(filter_options),
        "pos_map": pos_map,
        "elapsed_sec": elapsed,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_dir = ROOT / "data" / "reports" / "brute_pos_filters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symside}_{window_days}d_brute_pos_filters.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[brute] wrote {out_path} {out_path.stat().st_size/1024:.0f}K", flush=True)
    return out_path

def main():
    ap = argparse.ArgumentParser(description="Brute every filter × every switch, memorize pos deltas")
    ap.add_argument("--syms", default="AAPL_LONG,AAPL_SHORT,ALGOUSDT_LONG,ALGOUSDT_SHORT", help="comma-separated sym_sides")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    syms = [s.strip() for s in args.syms.split(",") if s.strip()]
    for sym in syms:
        try:
            brute_for_sym(sym, args.window_days, args.workers)
        except Exception as e:
            import traceback
            print(f"[brute-ERR] {sym} {e} {traceback.format_exc()[:1000]}", flush=True)

if __name__ == "__main__":
    main()
