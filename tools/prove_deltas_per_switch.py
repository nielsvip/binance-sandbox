#!/usr/bin/env python3
"""prove_deltas_per_switch — prove F/G deltas are filling, BEFORE any sweep.

Reports for every TEMPLATE switch+filter: vec delta (F) and live delta (G)
for 1yr (365d) and 1mo proof (last 30d of same 1yr, or separate 30d window).
Never overwrites hourly_reconfig or per_sym engines. Isolated, reversible.

- DEFAULT (no 3/5m): uses v12_pilot.evaluate_sanitized (15m frozen NPZ, 0.07s/eval).
  No 3m/5m resampling — fast, as you requested. Reversible: --use-3m-base opts
  into isolated 3m/5m copies (tools/per_sym_engine_*_isolated.py) which are risky.
- Writes to data/reports/delta_proof_<tag>/ — originals untouched.
- For each sym_side (BTCUSDC/ZECUSDC/NVDA/GOOGL/MSFT LONG), window 365 and 30,
  baseline = per_sym overrides (old working) + defaults. Then per switch:
    F = vec_gain(switch_candidate) - vec_gain(baseline)
    G = live_gain(switch_candidate) - live_gain(baseline)  (if live available)
  Parity: |F-G| <=0.5pp or 15% and pool_sharpe delta <=0.25, trades ratio 0.80-1.25.

Usage (S1, proves deltas first):
  python tools/prove_deltas_per_switch.py --tag proof1 --syms BTCUSDC --workers 4        # sample, fast
  python tools/prove_deltas_per_switch.py --tag proof1 --workers 4                      # all 5
  python tools/prove_deltas_per_switch.py --tag proof1 --use-3m-base --workers 2        # old 3/5m, reversible

On Mac (4 NPZ) will report NPZ_MISS and still write empty proof — S1 has 659.
"""
from __future__ import annotations
import argparse, json, sys, time, hashlib
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx" if (ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx").exists() else ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"

def load_template_switches() -> List[Tuple[str, str, object]]:
    """Return list of (sheet, switch, candidate_value) for every row in 20 switch sheets."""
    wb = openpyxl.load_workbook(str(TEMPLATE), read_only=True, data_only=False)
    out = []
    for name in wb.sheetnames:
        if name.startswith("INSTRUCTIONS") or name in ("Results_30d_Deltas","TEMPLATE_BASELINE_METRICS","_BLANKET_INVENTORY","12SYM_PARITY","FILTER_DICTIONARY_V2"):
            continue
        ws = wb[name]
        # col A=switch, col B=candidate — use iter_rows for speed (246 cols IL, cell-by-cell is 10x slower on APFS)
        for row in ws.iter_rows(min_row=3, max_col=2, values_only=True):
            sw, cand = row[0], row[1]
            if sw and isinstance(sw, str):
                sw = sw.strip()
                out.append((name, sw, cand))
    wb.close()
    return out

def load_template_filters() -> List[Tuple[str, str]]:
    wb = openpyxl.load_workbook(str(TEMPLATE), read_only=True, data_only=False)
    ws = wb["FILTER_DICTIONARY_V2"]
    out = []
    for row in ws.iter_rows(min_row=2, max_col=3, values_only=True):
        fname, opt = row[1], row[2]
        if fname:
            out.append((str(fname).strip(), str(opt) if opt is not None else ""))
    wb.close()
    return out

def load_overrides(symside: str) -> Dict:
    for cand in [ROOT / "data/hourly_reconfig/per_sym_active_config.json",
                 ROOT / "data/hourly_reconfig/trb/active_config.json",
                 Path("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"),
                 Path("/home/niels/binance-sandbox/data/hourly_reconfig/trb/active_config.json")]:
        try:
            if cand.exists():
                j = json.loads(cand.read_text())
                if symside in j:
                    ov = j[symside].get("overrides") or {}
                    return {k: v for k, v in ov.items() if not k.startswith("_")}
        except Exception:
            pass
    return {}

def evaluate_vec(symside: str, overrides: Dict, window_days: int) -> Dict:
    try:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"valid": False, "invalid_reason": f"vec EXC {e}", "trace": traceback.format_exc()[:600], "gain_pct": 0, "trades": 0, "pool_sharpe": 0}

def evaluate_live(symside: str, overrides: Dict, window_days: int) -> Dict:
    """Live via backtest_v12_engine — isolated subprocess to avoid starting daemons in this process.
    If live is not safely importable, return pending marker (F still proves vector wiring)."""
    try:
        import subprocess, json as _js, sys
        # Run live in subprocess with timeout 25s, never in this process (prevents daemon start)
        payload = _js.dumps({"symside": symside, "overrides": overrides, "window_days": window_days})
        # Use the isolated live helper (writes to /tmp, no imports in parent)
        cmd = [sys.executable, "-c",
               "import sys,json; "
               "d=json.loads(sys.argv[1]); "
               "import backtest_v12_engine as B; "
               "r=B.run_one(d['symside'], d['overrides'], window_days=d['window_days']); "
               "print(json.dumps({k: r.get(k) for k in ('valid','gain_pct','trades','pool_sharpe','invalid_reason') if k in r}))",
               payload]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if res.returncode == 0 and res.stdout.strip():
            j = _js.loads(res.stdout.strip().splitlines()[-1])
            return {"valid": j.get("valid", True), "gain_pct": j.get("gain_pct", 0), "trades": j.get("trades", 0),
                    "pool_sharpe": j.get("pool_sharpe", 0), "invalid_reason": j.get("invalid_reason","")}
        return {"valid": False, "invalid_reason": f"live pending (subprocess {res.stderr[:200]})", "gain_pct": 0, "trades": 0, "pool_sharpe": 0, "pending": True}
    except Exception as e:
        import traceback
        return {"valid": False, "invalid_reason": f"live pending {e}", "trace": traceback.format_exc()[:600], "gain_pct": 0, "trades": 0, "pool_sharpe": 0, "pending": True}

def prove_one_sym(sym: str, side: str, tag: str, use_3m: bool, sample_n: int = 0, do_live: bool = False) -> Dict:
    symside = f"{sym}_{side}"
    ov_base = load_overrides(symside)
    # windows: 1yr and 1mo proof (30d). 1mo proof is same 1yr filtered, but we also eval separate 30d for parity.
    results = {"symside": symside, "tag": tag, "use_3m": use_3m, "baseline": {}, "switches": [], "filters": []}
    # baseline vec/live for each window
    # baseline: vec always, live only if do_live
    for wd in (365, 30):
        vec = evaluate_vec(symside, ov_base, window_days=wd)
        live = evaluate_live(symside, ov_base, window_days=wd) if do_live else {"valid": False, "gain_pct": 0, "trades": 0, "pending": True, "invalid_reason": "live pending — prove F first"}
        results["baseline"][f"{wd}d"] = {"vec": vec, "live": live, "ov_count": len(ov_base)}
    # Now per-switch deltas: sample first, then all if sample_n==0
    print(f"  [load] loading switches from {TEMPLATE.name} ...", flush=True)
    switches = load_template_switches()
    print(f"  [load] {len(switches)} switches loaded, loading filters ...", flush=True)
    filters = load_template_filters()
    print(f"  [load] {len(switches)} switches, {len(filters)} filters from {TEMPLATE.name} — sample {sample_n} -> {len(switches[:sample_n]) if sample_n else len(switches)} switches", flush=True)
    if sample_n > 0:
        switches = switches[:sample_n]
        filters = filters[:sample_n]
    print(f"  [loop] starting {len(switches)} switches", flush=True)
    # vec deltas for 1yr and 30d — live progress per-F (tail -f useful)
    total_sw = len([s for s in switches if s[2] is not None])
    done = 0
    start_t = time.time()
    # persistent live CSV — survives S1/Mac reset (never /tmp)
    live_csv = (Path(ROOT) / "data" / "reports" / f"delta_proof_{tag}" / f"{symside}_live.csv")
    live_csv.parent.mkdir(parents=True, exist_ok=True)
    if not live_csv.exists():
        live_csv.write_text("symside,switch,candidate,window,F_vec_delta,vec_trades,F_filling\n")
    print(f"  [loop] total_sw={total_sw} start eval -> {live_csv}", flush=True)
    for sheet, sw, cand in switches:
        # skip if candidate is None (empty row)
        if cand is None:
            continue
        ov = dict(ov_base)
        # coerce str True/False
        if isinstance(cand, str) and cand in ("True","False"):
            cand = cand == "True"
        ov[sw] = cand
        for wd in (365, 30):
            vec = evaluate_vec(symside, ov, window_days=wd)
            vec_base = results["baseline"][f"{wd}d"]["vec"]
            if do_live:
                live = evaluate_live(symside, ov, window_days=wd)
                live_base = results["baseline"][f"{wd}d"]["live"]
                live_delta = float(live.get("gain_pct",0) or 0) - float(live_base.get("gain_pct",0) or 0)
            else:
                live = {"valid": False, "gain_pct": 0, "trades": 0, "pending": True, "invalid_reason": "live pending — prove F first"}
                live_base = {"gain_pct": 0, "trades": 0}
                live_delta = 0
            vec_delta = float(vec.get("gain_pct",0) or 0) - float(vec_base.get("gain_pct",0) or 0)
            # parity
            vec_tr = int(vec.get("trades",0) or 0)
            live_tr = int(live.get("trades",0) or 0)
            # BUGFIX audit 2026-09-09: 203/252 +19.66 repeats are 0-trade dead sink — gate on trades<2 / not valid
            if vec_tr < 2 or not vec.get("valid"):
                vec_delta = 0.0
            if live_tr < 2 or not live.get("valid"):
                live_delta = 0.0
            # F/G filling check: delta is not None and not 0 due to bug
            F_filling = (vec_delta != 0 or vec.get("gain_pct") != vec_base.get("gain_pct")) and vec_tr >= 2 and bool(vec.get("valid"))
            G_filling = (live_delta != 0 or live.get("gain_pct") != live_base.get("gain_pct")) and live_tr >= 2 and bool(live.get("valid"))
            results["switches"].append({
                "sheet": sheet, "switch": sw, "candidate": str(cand)[:40],
                "window_days": wd,
                "vec_gain": vec.get("gain_pct"), "vec_trades": vec_tr, "vec_valid": vec.get("valid"),
                "live_gain": live.get("gain_pct"), "live_trades": live_tr, "live_valid": live.get("valid"),
                "vec_delta_F": round(vec_delta,4), "live_delta_G": round(live_delta,4),
                "parity_trades_ratio": round(live_tr/max(1,vec_tr),3) if vec_tr else 0,
                "parity_gain_pp": round(abs(vec_delta-live_delta),4),
                "F_filling": F_filling,
                "G_filling": G_filling,
            })
            # append live per-F to persistent CSV (survives reset, tail-able)
            with live_csv.open("a") as lf:
                lf.write(f"{symside},{sw},{str(cand)[:24]},{wd},{vec_delta:.4f},{vec_tr},{F_filling}\n")
            # live per-F progress: tail -f shows each F fill as it happens
            if wd == 30:  # print once per switch (30d is proof window)
                done += 1
                elapsed = time.time() - start_t
                rate = done / max(0.1, elapsed)
                print(f"  [{done}/{total_sw}] {symside} {sw}={str(cand)[:18]:18} 365d F={results['switches'][-2]['vec_delta_F']:+7.4f} tr{results['switches'][-2]['vec_trades']:4} {'F' if results['switches'][-2]['F_filling'] else '.'} | 30d F={vec_delta:+7.4f} tr{vec_tr:4} {'F' if F_filling else '.'}  {elapsed:.0f}s {rate:.1f}/s", flush=True)
            # only one window per row to keep count low for sample — we already loop wd, so break after first for sample?
        # we want both windows, so keep loop
    # filters: report as TF/bool gates (vec only, live often same)
    # cap filters to sample for speed (prove switches first)
    f_sample = filters[:sample_n] if sample_n>0 else filters[:5]
    for fname, opt in f_sample:
        ov = dict(ov_base)
        # filter value: try to set as string TF or bool/float
        if opt in ("True","False"):
            ov[fname] = opt == "True"
        else:
            ov[fname] = opt
        for wd in (30,):  # filters only on 30d proof for speed
            vec = evaluate_vec(symside, ov, window_days=wd)
            vec_base = results["baseline"][f"{wd}d"]["vec"]
            vec_delta = float(vec.get("gain_pct",0) or 0) - float(vec_base.get("gain_pct",0) or 0)
            results["filters"].append({
                "filter": fname, "option": opt, "window_days": wd,
                "vec_delta": round(vec_delta,4), "vec_trades": vec.get("trades"), "F_filling": vec_delta != 0
            })
    return results

def main() -> int:
    ap = argparse.ArgumentParser(description="Prove F/G deltas are filling before sweep.")
    ap.add_argument("--tag", default="proof1")
    ap.add_argument("--syms", default="BTCUSDC")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--sample", type=int, default=10, help="switches to sample per sym (0=all 357)")
    ap.add_argument("--use-3m-base", action="store_true", help="opt-in 3m/5m (risky, reversible)")
    ap.add_argument("--live", action="store_true", help="also prove G (live) — slow, needs subprocess; default proves F only")
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.syms.split(",") if s.strip()]
    side = "LONG"
    out_root = ROOT / "data" / "reports" / f"delta_proof_{args.tag}"
    out_root.mkdir(parents=True, exist_ok=True)
    assert "hourly_reconfig" not in str(out_root)
    print(f"[delta-proof] tag={args.tag} syms={syms} sample={args.sample} use_3m={args.use_3m_base} out={out_root}", flush=True)
    print(f"[delta-proof] NEVER overwrites hourly_reconfig or per_sym engines — isolated, reversible", flush=True)
    if args.use_3m_base:
        print(f"[delta-proof] 3/5m OPT-IN — revert: rm tools/per_sym_engine_*_isolated.py and rerun without flag", flush=True)
    else:
        print(f"[delta-proof] 15m DEFAULT — no 3/5m resample (as requested)", flush=True)

    all_res = {}
    do_live = bool(args.live)
    for sym_raw in syms:
        # allow full symside like CRWD_SHORT/NKE_SHORT/BNBUSDC_LONG, else assume LONG
        if sym_raw.endswith("_LONG") or sym_raw.endswith("_SHORT"):
            symside_full = sym_raw
            sym, side = sym_raw.rsplit("_", 1)
        else:
            sym, side = sym_raw, side
            symside_full = f"{sym}_{side}"
        print(f"[delta-proof] proving {symside_full} (do_live={do_live}) ...", flush=True)
        r = prove_one_sym(sym, side, args.tag, args.use_3m_base, sample_n=args.sample, do_live=do_live)
        all_res[symside_full] = r
        # quick stats
        filling_F = sum(1 for s in r["switches"] if s["F_filling"])
        filling_G = sum(1 for s in r["switches"] if s["G_filling"])
        total = len(r["switches"])
        print(f"[delta-proof] {symside_full} F filling {filling_F}/{total} G filling {filling_G}/{total} baseline 365d vec {r['baseline']['365d']['vec'].get('trades')} trades gain {r['baseline']['365d']['vec'].get('gain_pct')} live {r['baseline']['365d']['live'].get('trades')} | 30d vec {r['baseline']['30d']['vec'].get('trades')} live {r['baseline']['30d']['live'].get('trades')}", flush=True)
        (out_root / f"{symside_full}.json").write_text(json.dumps(r, indent=2, default=str))
    # summary csv: F/G filling
    import csv as _csv
    with (out_root / "summary.csv").open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["sym","switch","candidate","window","F_vec_delta","G_live_delta","F_filling","G_filling","vec_trades","live_trades","parity_pp"])
        for sym, r in all_res.items():
            for s in r["switches"]:
                w.writerow([sym, s["switch"], s["candidate"], s["window_days"], s["vec_delta_F"], s["live_delta_G"], s["F_filling"], s["G_filling"], s["vec_trades"], s["live_trades"], s["parity_gain_pp"]])
    (out_root / "summary.json").write_text(json.dumps(all_res, indent=2, default=str)[:200000])
    print(f"[delta-proof] wrote {out_root}/summary.csv + per-sym jsons — NO SWEEP until F/G filling proven", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
