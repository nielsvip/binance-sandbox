#!/usr/bin/env python3
"""Compare finished v15 mega-sweep sym_sides with the old DC64_GREEDY results — and prove the v15 numbers are real.

For every sym_side whose mega log says "[spec-fill] DONE":
  * v15 recorded: initial baseline, final cumulative gain, promotions (progress JSON of the isolated run)
  * v15 VERIFIED: the final cumulative_overrides are re-evaluated from scratch (fresh prepare_batch + evaluate_prepared_sanitized
    with ledger). REAL = recomputed gain == recorded gain (abs diff < 1e-6) AND ledger length == trades.
    Also reports gain_pct recomputed from the ledger (sum pnl_dollars / peak_notional * 100) as a formula check.
  * DC64: BASE + FINAL gain/trades/tim from SPREADSHEETS/DC64_GREEDY/{SS}_*_30d_matrix.xlsx (old system, per_sym BEST base).
  * SIZING flag: promotions of pure position-size switches (integer-share rounding can move gain% without any edge).
Writes data/reports/v15_vs_dc64/compare_{venue}.csv and prints a summary. Run on the server that owns the venue.
"""
import argparse
import csv
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl  # noqa: E402
from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch  # noqa: E402
from tools.opt.evaluate_v12 import _honest_gain_pct, _peak_concurrent  # noqa: E402

FIELDS = ["sym_side", "REAL", "v15_base_gain", "v15_final_recorded", "v15_final_recomputed", "ledger_gain_check", "trades", "ledger_trades", "tim_pct", "pool_sharpe", "max_dd_pct", "bh_pct", "valid", "v15_promotions", "sizing_promotion", "n_rows_done", "dc64_base_gain", "dc64_base_trades", "dc64_final_gain", "dc64_final_trades", "dc64_final_tim", "dc64_applied", "dc64_file", "v15_minus_dc64_final"]
SIZING_SWITCHES = ("START_POSITION_SIZE", "POSITION_SIZE", "MAX_POSITION", "SIZE_MULT", "NOTIONAL")


def dc64_result(sym_side):
    files = sorted(glob.glob(str(ROOT / "SPREADSHEETS" / "DC64_GREEDY" / f"{sym_side}_bh*_30d_matrix.xlsx")))
    if not files:
        return None
    ws = openpyxl.load_workbook(files[-1], read_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(min_row=2, max_col=6, values_only=True) if r[0] not in (None, "")]
    base = next((r for r in rows if r[0] == "BASE"), None)
    last = rows[-1]
    applied = [f"{r[0]}" for r in rows if r[0] != "BASE" and (r[3] or 0) > 1e-9]
    return {"dc64_base_gain": base[2] if base else None, "dc64_base_trades": base[4] if base else None, "dc64_final_gain": last[2], "dc64_final_trades": last[4], "dc64_final_tim": last[5], "dc64_applied": "; ".join(applied), "dc64_file": os.path.basename(files[-1])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=["crypto", "stocks"], required=True)
    ap.add_argument("--run", default=None)
    args = ap.parse_args()
    run = args.run or f"MEGA_{args.venue}_jump"
    log_dir = Path.home() / "v15_mega_logs" / run
    prog_root = Path.home() / "v15_mega_progress" / run
    out_dir = ROOT / "data" / "reports" / "v15_vs_dc64"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"compare_{args.venue}.csv"
    done_before = set()
    if out_csv.exists():
        with open(out_csv) as fh:
            done_before = {r["sym_side"] for r in csv.DictReader(fh)}
    rows = []
    for log in sorted(log_dir.glob("*.log")):
        sym_side = log.stem
        if sym_side in done_before or "[spec-fill] DONE" not in log.read_text(errors="ignore"):
            continue
        prog_files = list((prog_root / sym_side).glob("*_v14_progress.json"))
        if not prog_files:
            continue
        prog = json.loads(prog_files[0].read_text())
        overrides = prog.get("cumulative_overrides") or {}
        recorded = prog.get("cumulative_gain")
        base = prog.get("initial_baseline_gain")
        promos = [k.split(":", 1)[1] for k, v in (prog.get("done") or {}).items() if isinstance(v, dict) and v.get("promoted")]
        fr = (prog.get("final_filter_recheck") or {}).get("promoted")
        if fr:
            promos.append(f"FINAL_RECHECK:{fr[0]}={fr[1]}")
        res = evaluate_prepared_sanitized(prepare_batch(sym_side, 30), overrides, 30, include_ledger=True)
        ledger = res.get("ledger") or []
        # gain_pct definition (evaluate_v12._honest_gain_pct): sum(pnl_dollars) / peak CONCURRENT deployed * 100
        pnl = sum(float(t.get("pnl_dollars") or 0) for t in ledger)
        peak = _peak_concurrent(ledger)
        ledger_gain = pnl / peak * 100 if peak > 0 else None
        real = (recorded is not None and res.get("gain_pct") is not None and abs(float(res["gain_pct"]) - float(recorded)) < 1e-6
                and sum(1 for t in ledger if str(t.get("type", "CLOSE")).upper() == "CLOSE") == int(res.get("trades") or -1) and ledger_gain is not None and abs(ledger_gain - float(res["gain_pct"])) < 1e-6)
        row = {"sym_side": sym_side, "v15_base_gain": base, "v15_final_recorded": recorded, "v15_final_recomputed": res.get("gain_pct"), "REAL": real, "ledger_gain_check": ledger_gain, "trades": res.get("trades"), "ledger_trades": sum(1 for t in ledger if str(t.get("type", "CLOSE")).upper() == "CLOSE"), "tim_pct": res.get("tim_pct"), "pool_sharpe": res.get("pool_sharpe"), "max_dd_pct": res.get("max_dd_pct"), "bh_pct": res.get("bh_pct"), "valid": res.get("valid"), "v15_promotions": "; ".join(promos), "sizing_promotion": any(any(s in p for s in SIZING_SWITCHES) for p in promos), "n_rows_done": len(prog.get("done") or {})}
        row.update(dc64_result(sym_side) or {"dc64_final_gain": None})
        if row.get("dc64_final_gain") is not None and recorded is not None:
            row["v15_minus_dc64_final"] = float(recorded) - float(row["dc64_final_gain"])
        rows.append(row)
        print(f"{sym_side:22} REAL={real!s:5} v15 base={base if base is None else round(base,3)} final={recorded if recorded is None else round(recorded,3)} (recomp {res.get('gain_pct') if res.get('gain_pct') is None else round(res['gain_pct'],3)}, ledger {ledger_gain if ledger_gain is None else round(ledger_gain,3)}) trades={res.get('trades')} | DC64 base={row.get('dc64_base_gain')} final={row.get('dc64_final_gain')} trades={row.get('dc64_final_trades')} | diff={row.get('v15_minus_dc64_final') if row.get('v15_minus_dc64_final') is None else round(row['v15_minus_dc64_final'],3)} sizing={row['sizing_promotion']}", flush=True)
    if rows:
        fields = FIELDS
        new_file = not out_csv.exists()
        with open(out_csv, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(rows)
    print(f"[compare] {len(rows)} new sym_sides -> {out_csv}", flush=True)


if __name__ == "__main__":
    main()
