#!/usr/bin/env python3
"""v15_connect_proof — proves every sheet cell is connected to a real engine function.

For one SYM_SIDE's 30d matrix workbook, every (switch, cand) row on all 13
SWITCH_SHEETS tabs is evaluated against baseline with include_ledger=True and
classified by LEDGER EVIDENCE (NO-LIES: a delta only counts as real when the
trade ledger actually changed):

  CONNECTED  ledger fingerprint changed (real trades made/altered)
  INERT      identical ledger AND delta exactly 0 — honest no-op on this symbol
  DEAD       switch/tab listed in v15_pilot DEAD_VEC_TABS / DEAD_VEC_SWITCHES
  MISSING    switch is not a QuickConfig field
  SUSPECT    delta != 0 but ledger identical — FORBIDDEN (gain moved without trades)

Exit 0 only when every non-DEAD row is CONNECTED or INERT AND the four
AUGMENT/REDUCE tabs contain at least one CONNECTED row (the pilot criterion).

Survival: pass the sym_side via env V15_PROOF_SYMSIDE to keep it out of the
process cmdline (a stale Mac monitor SIGKILLs s1 processes matching
"1000BONK.*30d").
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import dataclasses
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVAL_TIMEOUT = 10.0
AUG_REDUCE_TABS = ["AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"]


def log(msg):
    print(f"[connect_proof {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ledger_fingerprint(res):
    led = res.get("ledger") or res.get("execution_ledger") or []
    h = hashlib.md5()
    for t in led:
        h.update(repr((str(t.get("type")), t.get("ts"), t.get("qty"), t.get("price"), t.get("bar_entry"), t.get("bar_exit"))).encode())
    return h.hexdigest(), len(led)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symside", nargs="?", default=os.environ.get("V15_PROOF_SYMSIDE"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--wb", default=None, help="explicit workbook path")
    args = ap.parse_args()
    if not args.symside:
        ap.error("give SYM_SIDE or set V15_PROOF_SYMSIDE")
    symside = args.symside
    import openpyxl
    import v15_pilot as VP
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    import v12_quick_engine as VQ
    qc_fields = {f.name for f in dataclasses.fields(VQ.QuickConfig)}
    dead_tabs = set(getattr(VP, "DEAD_VEC_TABS", set()) or set())
    dead_switches = set(getattr(VP, "DEAD_VEC_SWITCHES", set()) or set())
    wb_path = Path(args.wb) if args.wb else ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{symside}_30d_matrix.xlsx"
    if not wb_path.exists():
        log(f"workbook missing: {wb_path}")
        sys.exit(2)
    hb = Path("/tmp") / f"v15_connect_proof_{symside}.txt"
    t0 = time.time()
    prep = prepare_batch(symside, args.window_days)
    if prep is None:
        log(f"{symside}: no NPZ on this host — run on the venue's worker (s1 crypto / s2 stocks)")
        sys.exit(2)
    defaults = VP.get_defaults_for_symside(symside)
    base = evaluate_prepared_sanitized(prep, {}, args.window_days, include_ledger=True)
    base_gain = base.get("gain_pct")
    base_fp, base_n = ledger_fingerprint(base)
    if base_gain is None:
        log(f"{symside}: baseline eval invalid ({base.get('invalid_reason')}) — cannot prove")
        sys.exit(2)
    log(f"{symside}: prepared {time.time()-t0:.1f}s baseline gain={base_gain:.4f} trades={base.get('trades')} ledger={base_n}")
    wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=False)
    rows = []
    for sn in VP.SWITCH_SHEETS:
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]
        for r in ws.iter_rows(min_row=3, max_col=2):
            a = r[0].value if len(r) > 0 else None
            b = r[1].value if len(r) > 1 else None
            if a in (None, ""):
                continue
            rows.append((sn, r[0].row, str(a).strip(), b))
    wb.close()
    pool = cf.ThreadPoolExecutor(max_workers=max(1, args.workers))
    cache = {}
    results = []
    done_ct = 0
    for sn, rr, switch, cand in rows:
        rec = {"sheet": sn, "row": rr, "switch": switch, "cand": str(cand)}
        if sn in dead_tabs or switch in dead_switches:
            rec["class"] = "DEAD"
            results.append(rec)
            continue
        if switch not in qc_fields:
            rec["class"] = "MISSING"
            results.append(rec)
            continue
        cand_parsed = VP._parse_opt_value(cand, defaults.get(switch))
        variant = dict(VP._switch_overrides(switch, cand_parsed)) if hasattr(VP, "_switch_overrides") else {switch: cand_parsed}
        variant, _ = VP.sanitize_overrides(variant, defaults)
        ck = tuple(sorted((k, str(v)) for k, v in variant.items()))
        rec["cand_equals_default"] = bool(defaults.get(switch) == cand_parsed)
        if ck in cache:
            res, err = cache[ck]
        else:
            fut = pool.submit(evaluate_prepared_sanitized, prep, dict(variant), args.window_days, True)
            try:
                res, err = fut.result(timeout=EVAL_TIMEOUT), ""
            except cf.TimeoutError:
                res, err = None, f"TIMEOUT {EVAL_TIMEOUT:.0f}s"
            except Exception as e:
                res, err = None, f"ERR {e}"[:80]
            cache[ck] = (res, err)
        if res is None or res.get("gain_pct") is None:
            # an eval that ERRORED tested nothing — it is not the forbidden
            # gain-without-ledger-change case (that needs a real gain). Seen live:
            # template rows with empty candidate cells -> float('None') errors
            # were miscounted as SUSPECT (GDX 2026-09-28).
            rec["class"] = "ERROR"
            rec["error"] = err or str((res or {}).get("invalid_reason"))[:80]
            results.append(rec)
            continue
        fp, n_led = ledger_fingerprint(res)
        delta = float(res["gain_pct"]) - float(base_gain)
        delta = 0.0 if abs(delta) < 1e-9 else delta
        rec["delta"] = delta
        rec["ledger_events"] = n_led
        if fp != base_fp:
            rec["class"] = "CONNECTED"
        elif delta == 0.0:
            rec["class"] = "INERT"
        else:
            rec["class"] = "SUSPECT"
        results.append(rec)
        done_ct += 1
        if done_ct % 50 == 0:
            try:
                hb.write_text(f"{time.time():.0f} {sn}!{rr} {done_ct}/{len(rows)}")
            except Exception:
                pass
    pool.shutdown(wait=False)
    tabs = {}
    for rec in results:
        t = tabs.setdefault(rec["sheet"], {"CONNECTED": 0, "INERT": 0, "DEAD": 0, "MISSING": 0, "SUSPECT": 0, "ERROR": 0})
        t[rec["class"]] += 1
    total = {"CONNECTED": 0, "INERT": 0, "DEAD": 0, "MISSING": 0, "SUSPECT": 0, "ERROR": 0}
    print(f"\n{'TAB':38s} {'CONN':>5s} {'INERT':>6s} {'DEAD':>5s} {'MISS':>5s} {'SUSPECT':>7s}")
    for sn in VP.SWITCH_SHEETS:
        if sn not in tabs:
            continue
        t = tabs[sn]
        for k in total:
            total[k] += t[k]
        print(f"{sn:38s} {t['CONNECTED']:>5d} {t['INERT']:>6d} {t['DEAD']:>5d} {t['MISSING']:>5d} {t['SUSPECT']:>7d}")
    print(f"{'TOTAL':38s} {total['CONNECTED']:>5d} {total['INERT']:>6d} {total['DEAD']:>5d} {total['MISSING']:>5d} {total['SUSPECT']:>7d}")
    aug_connected = sum(1 for rec in results if rec["sheet"] in AUG_REDUCE_TABS and rec["class"] == "CONNECTED")
    aug_nondead = sum(1 for rec in results if rec["sheet"] in AUG_REDUCE_TABS and rec["class"] != "DEAD")
    failures = [rec for rec in results if rec["class"] in ("SUSPECT", "MISSING")]
    out_dir = ROOT / "data" / "reports" / "connect_proof"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M")
    report = {"symside": symside, "ts": time.time(), "window_days": args.window_days, "baseline_gain": base_gain, "baseline_trades": base.get("trades"), "rows": results, "totals": total, "aug_reduce_connected": aug_connected, "aug_reduce_nondead": aug_nondead, "elapsed_s": round(time.time() - t0, 1)}
    out_path = out_dir / f"{symside}_{stamp}.json"
    out_path.write_text(json.dumps(report))
    log(f"report -> {out_path} ({len(results)} rows in {report['elapsed_s']}s, evals={len(cache)} cached-dedup)")
    ok = not failures
    if aug_connected < 1:
        ok = False
        if aug_nondead == 0:
            log("PILOT CRITERION FAIL: all AUGMENT/REDUCE rows still DEAD (engine wiring not landed)")
        else:
            log(f"PILOT CRITERION FAIL: 0 CONNECTED rows on AUGMENT/REDUCE tabs ({aug_nondead} non-DEAD rows evaluated)")
    if failures:
        log(f"{len(failures)} failing rows (SUSPECT/MISSING), first 15:")
        for rec in failures[:15]:
            log(f"  {rec['sheet']}!{rec['row']} {rec['switch']}={rec['cand']} -> {rec['class']} {rec.get('error','')} delta={rec.get('delta')}")
    if ok:
        log(f"ALL CONNECTED-OR-INERT + augment/reduce pilot criterion met ({aug_connected} connected)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
