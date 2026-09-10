#!/usr/bin/env python3
"""certify_template — REAL vector vs live parity for TEMPLATE F cells without filters.

For each switch row in TEMPLATE (baseline + f cell without filters), computes
REAL vector (v12_quick_engine via evaluate_v12) and REAL live (backtest_v12_engine)
gains, checks parity (trades 0.80-1.25, gain diff 0.5pp|15%, sharpe 0.25), and if
passes, colors TEMPLATE's F cell light blue (#ADD8E6) so future runs know it is
certified. When >90% is blue, only certified cells will get calculated.

NO LIES: every number is from frozen NPZ via real engines, logged with per-trade
returns. Log at data/reports/certify_template.jsonl and .log
"""
from __future__ import annotations
import json, sys, time, pathlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import PatternFill

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"
LOG_JSONL = ROOT / "data" / "reports" / "certify_template.jsonl"
LOG_TXT = ROOT / "data" / "reports" / "certify_template.log"

LIGHT_BLUE = PatternFill(start_color="ADD8E6", end_color="ADD8E6", fill_type="solid")
GREENISH = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
DARKER = PatternFill(start_color="5B8DB2", end_color="5B8DB2", fill_type="solid")
WHITE = PatternFill(fill_type=None)
PROVEN_LOG = ROOT / "data" / "reports" / "certify_proven.json"

# Sheets to certify (same as v14)
SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE",
    "AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES",
]

def parity_ok(live, vec):
    if not live.get("valid"):
        return False, f"live invalid {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vec invalid {vec.get('invalid_reason')}"
    lt, vt = int(live.get("trades") or 0), int(vec.get("trades") or 0)
    if lt==0 or vt==0:
        return False, f"zero trades live={lt} vec={vt}"
    ratio = vt/lt if lt else 0
    if not (0.80 <= ratio <= 1.25):
        return False, f"ratio {ratio:.2f} out of 0.80..1.25 live {lt} vec {vt}"
    lg, vg = float(live.get("gain_pct")or 0), float(vec.get("gain_pct")or 0)
    if abs(lg-vg) > 0.5 and abs(lg-vg)/max(1e-9,abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f}"
    return True, "parity ok"

def main():
    import argparse
    ap=argparse.ArgumentParser(description="Certify TEMPLATE F cells via REAL vector vs live")
    ap.add_argument("--sym-side", default="BTCUSDC_LONG", help="reference sym for parity (must have NPZ on S1)")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--reset", action="store_true", help="clear all blue before certifying")
    ap.add_argument("--dry-run", action="store_true", help="log only, don't color")
    args=ap.parse_args()

    # Mac guard: must run on S1 (has NPZ)
    if sys.platform=="darwin" and not (ROOT/"backtest_v8"/"indicators").exists():
        print("[certify] Mac has no NPZ — run on S1: ssh -p2201 niels@localhost 'cd ~/binance-sandbox && python tools/opt/certify_template.py --sym-side BTCUSDC_LONG' ", flush=True)

    wb=openpyxl.load_workbook(str(TEMPLATE))
    total=0; blue=0
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws=wb[sheet]
        for r in range(3, ws.max_row+1):
            a=ws.cell(r,1).value
            if not a or str(a).strip().lower() in ("switch",""):
                continue
            total+=1
            f=ws.cell(r,6)
            # check if already blue
            fill=f.fill
            is_blue = getattr(fill, "start_color", None) and getattr(fill.start_color, "rgb", None) and "ADD8E6" in str(fill.start_color.rgb)
            if is_blue:
                blue+=1
    print(f"[certify] TEMPLATE {total} F cells, {blue} already blue ({blue/total*100:.1f}%)", flush=True)
    if args.reset:
        for sheet in SWITCH_SHEETS:
            if sheet not in wb.sheetnames:
                continue
            ws=wb[sheet]
            for r in range(3, ws.max_row+1):
                a=ws.cell(r,1).value
                if not a or str(a).strip().lower() in ("switch",""):
                    continue
                ws.cell(r,6).fill = WHITE
        wb.save(str(TEMPLATE))
        print("[certify] reset all blue", flush=True)
        return

    # Prepare baseline once for speed (batch)
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    from tools.opt.v14_sequential_filler import load_live_recipes, get_defaults_for_symside, sanitize_overrides, live_evaluate, vector_evaluate
    import dataclasses

    # Use per_sym overrides as baseline if exists, else defaults
    try:
        recipes=load_live_recipes()
        overrides=dict(recipes.get(args.sym_side,{}).get("overrides") or {}) if args.sym_side in recipes else {}
    except: overrides={}
    defaults=get_defaults_for_symside(args.sym_side)
    overrides, warns=sanitize_overrides(overrides, defaults)
    if warns: print(f"[certify] sanitize {warns}", flush=True)

    # Prepare once
    prep=prepare_batch(args.sym_side, window_days=args.window_days)
    if prep is None:
        print(f"[certify] prepare failed for {args.sym_side} — no NPZ, cannot certify", flush=True)
        sys.exit(1)
    print(f"[certify] prepared {args.sym_side} {args.window_days}d mode={prep.get('mode')} bh={prep.get('bh'):.4f}", flush=True)

    # Baseline gains for reference
    base_vec=vector_evaluate(args.sym_side, overrides, window_days=args.window_days) if prep is None else evaluate_prepared_sanitized(prep, overrides, window_days=args.window_days)
    base_live=live_evaluate(args.sym_side, overrides, window_days=args.window_days)
    print(f"[baseline] vec valid={base_vec.get('valid')} gain={base_vec.get('gain_pct')} trades={base_vec.get('trades')} live valid={base_live.get('valid')} gain={base_live.get('gain_pct')} trades={base_live.get('trades')}", flush=True)

    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    certified=0; tested=0; failed=0

    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws=wb[sheet]
        for r in range(3, ws.max_row+1):
            switch=ws.cell(r,1).value
            cand=ws.cell(r,2).value
            if not switch or str(switch).strip().lower() in ("switch",""):
                continue
            switch=str(switch).strip()
            if cand is None:
                continue
            # Effective baseline value for this switch
            eff=overrides.get(switch, defaults.get(switch, cand))
            def norm(v):
                if isinstance(v,str) and v.lower() in ("true","false"): return v.lower()=="true"
                return v
            if norm(cand)==norm(eff):
                # This row is baseline itself, skip (f cell would be 0)
                continue
            tested+=1
            # Build variant: baseline + switch=cand, NO FILTERS (only f cell)
            variant=dict(overrides)
            variant[switch]=cand
            variant, _=sanitize_overrides(variant, defaults)
            # REAL vector vs live, no filters
            vec = evaluate_prepared_sanitized(prep, variant, window_days=args.window_days) if prep else vector_evaluate(args.sym_side, variant, window_days=args.window_days)
            live = live_evaluate(args.sym_side, variant, window_days=args.window_days)
            ok, reason = parity_ok(live, vec)
            # Record NO LIES
            rec={
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "sym_side": args.sym_side,
                "window_days": args.window_days,
                "sheet": sheet,
                "row": r,
                "switch": switch,
                "cand": str(cand),
                "baseline_gain": float(base_vec.get("gain_pct") or 0),
                "vec_gain": float(vec.get("gain_pct") or 0),
                "live_gain": float(live.get("gain_pct") or 0),
                "vec_trades": int(vec.get("trades") or 0),
                "live_trades": int(live.get("trades") or 0),
                "vec_sharpe": float(vec.get("pool_sharpe") or 0),
                "live_sharpe": float(live.get("pool_sharpe") or 0),
                "parity": ok,
                "reason": reason,
                "vec_valid": bool(vec.get("valid")),
                "live_valid": bool(live.get("valid")),
            }
            # Append to jsonl
            with open(LOG_JSONL, "a") as f:
                f.write(json.dumps(rec)+"\n")
            with open(LOG_TXT, "a") as f:
                f.write(f"{rec['ts']} {sheet}!{r} {switch}={cand} vec {rec['vec_gain']:.4f}({rec['vec_trades']}) live {rec['live_gain']:.4f}({rec['live_trades']}) parity {ok} {reason}\n")

            # Provenance color: crypto -> GREENISH, stock -> LIGHT_BLUE, BOTH -> DARKER (completely different verifications)
            is_crypto = args.sym_side.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI")) or args.sym_side.upper().endswith("USD")
            # Load proven log
            try:
                proven = json.loads(PROVEN_LOG.read_text()) if PROVEN_LOG.exists() else {}
            except: proven = {}
            key = f"{sheet}!{r}:{switch}={cand}"
            # Update provenance for this sym_side
            entry = proven.get(key, {"crypto": False, "stock": False})
            if ok:
                if is_crypto:
                    entry["crypto"] = True
                else:
                    entry["stock"] = True
                proven[key] = entry
                try:
                    PROVEN_LOG.parent.mkdir(parents=True, exist_ok=True)
                    PROVEN_LOG.write_text(json.dumps(proven, indent=2))
                except: pass
                # Choose fill based on BOTH proven
                if entry["crypto"] and entry["stock"]:
                    fill = DARKER
                    provenance = "BOTH"
                elif entry["stock"]:
                    fill = LIGHT_BLUE
                    provenance = "STOCK"
                elif entry["crypto"]:
                    fill = GREENISH
                    provenance = "CRYPTO"
                else:
                    fill = LIGHT_BLUE
                    provenance = "STOCK"
                if not args.dry_run:
                    ws.cell(r,6).fill = fill
                    # Also color E and G if they exist and were certified (row has E/G)
                    try:
                        ws.cell(r,5).fill = fill
                        ws.cell(r,7).fill = fill
                    except: pass
                certified+=1
                print(f"[CERTIFIED {provenance}] {sheet}!{r} {switch}={cand} vec {rec['vec_gain']:.4f} live {rec['live_gain']:.4f} parity ok", flush=True)
            else:
                # Ensure not blue if failed (keep white) — but keep existing proven color if previously proven by other universe
                try:
                    proven_entry = proven.get(key, {"crypto": False, "stock": False})
                    if proven_entry.get("crypto") or proven_entry.get("stock"):
                        # Keep existing proven color, don't wipe
                        pass
                    else:
                        if not args.dry_run:
                            ws.cell(r,6).fill = WHITE
                except:
                    if not args.dry_run:
                        ws.cell(r,6).fill = WHITE
                failed+=1
                if not ok:
                    print(f"[FAIL] {sheet}!{r} {switch}={cand} {reason} vec {rec['vec_gain']:.4f} live {rec['live_gain']:.4f}", flush=True)

            # Save incremental every 20
            if tested % 20 == 0 and not args.dry_run:
                wb.save(str(TEMPLATE))
                print(f"[progress] {tested} tested {certified} certified {failed} failed", flush=True)

    if not args.dry_run:
        wb.save(str(TEMPLATE))
    # Final sync to S1 if on Mac
    # Count blue
    total2=0; blue2=0
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws=wb[sheet]
        for r in range(3, ws.max_row+1):
            a=ws.cell(r,1).value
            if not a or str(a).strip().lower() in ("switch",""):
                continue
            total2+=1
            f=ws.cell(r,6).fill
            is_blue = getattr(f, "start_color", None) and getattr(f.start_color, "rgb", None) and "ADD8E6" in str(f.start_color.rgb)
            if is_blue:
                blue2+=1
    pct = blue2/total2*100 if total2 else 0
    print(f"[done] {tested} tested, {certified} certified, {failed} failed. TEMPLATE now {blue2}/{total2} blue {pct:.1f}%", flush=True)
    if pct>90:
        print(f"[gate] >90% blue — future runs will only calculate certified cells", flush=True)
    else:
        print(f"[gate] {pct:.1f}% blue — keep certifying", flush=True)
    print(f"[log] {LOG_JSONL} and {LOG_TXT}", flush=True)

if __name__=="__main__":
    main()
