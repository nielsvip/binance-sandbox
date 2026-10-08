#!/usr/bin/env python3
"""v15_autopsy_first — AUTOPSY-FIRST BASELINE for a sym_side (USER 2026-10-08: "audit and improve the system …
multiplying gains"; system audit data/causal/system_audit_20261008.md).

Before a board is swept — always when its baseline is negative or invalid — run the trade autopsy's engine-verified
surgical combination (tools/v15_trade_autopsy_run.run_one: every candidate switch attributed to the losing trades it
fixes, then applied cumulatively and KEPT only where the engine confirms a real gain) and hand the result to the pilot
as its starting set through the existing V15_START_OVERRIDES path (BIBLE §58 step 5). The sweep then refines a valid,
positive, compact base instead of fighting a negative one (3/14 recent boards ended as 0-trade finals that way).

  .venv/bin/python tools/v15_autopsy_first.py --symsides UNIUSDC_SHORT,KSMUSDT_SHORT --out ~/v15_autopsy_first [--base-json FILE] [--workers 6]

Per sym_side writes {out}/{SS}_autopsy_base.json  = {"final": {"overrides": base ∪ verified combo}, "autopsy": {...}}
and {out}/{SS}_autopsy.{json,md} (the full autopsy). Writes the base ONLY when the verified combo is valid and beats
the base (else status NO_RESCUE, nothing to ingest — never a fabricated set). Launch hint printed per sym_side:
  V15_START_OVERRIDES={out}/{SS}_autopsy_base.json V15_FRESH_RUN=1 v15_pilot.py --sym-side SS ...
Real engine numbers only; the autopsy's attribution is a guide, the engine is the judge (runner contract).
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def run(ss, out, workers, base_json, dirs):
    from tools import v15_trade_autopsy_run as R
    import v15_pilot as P
    t0 = time.time()
    defaults = P.get_defaults_for_symside(ss)
    base_ov0, src = R.base_set(ss, base_json, dirs)
    base_ov = P.sanitize_overrides(base_ov0, defaults)[0]
    rep = R.run_one(ss, out, workers, base_json, dirs)
    if rep.get("error"):
        return {"symside": ss, "status": "ERROR", "error": rep["error"]}
    base_gain = rep["base"]["gain"]
    combo_gain = rep["combo_gain"]
    kept = [v for v in rep["combo_verified"] if v["kept"]]
    final_valid = bool(kept[-1]["valid"]) if kept else bool(rep["base"]["valid"])
    rescued = bool(kept) and final_valid and combo_gain is not None and base_gain is not None and combo_gain > base_gain + 1e-9
    summary = {"symside": ss, "status": "RESCUED" if rescued else "NO_RESCUE", "base_src": src, "base_gain": base_gain, "base_trades": rep["base"]["trades"],
               "base_valid": rep["base"]["valid"], "combo_gain": combo_gain, "combo_trades": kept[-1]["trades"] if kept else rep["base"]["trades"], "combo_valid": final_valid,
               "switches": [v["switch"] for v in kept], "n_base_overrides": len(base_ov), "secs": round(time.time() - t0, 1)}
    if rescued:
        start = {**base_ov, **rep["combo_overrides"]}
        (out / f"{ss}_autopsy_base.json").write_text(json.dumps({"final": {"overrides": start}, "autopsy": summary}, indent=1, default=str))
        summary["start_overrides"] = str(out / f"{ss}_autopsy_base.json")
        summary["launch"] = f"V15_START_OVERRIDES={out / f'{ss}_autopsy_base.json'} V15_FRESH_RUN=1 .venv/bin/python v15_pilot.py --sym-side {ss}"
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--base-json", default=None, help="{switch: value} base set (default: template defaults = {})")
    ap.add_argument("--progress-dirs", default="data/reports/lifecycle_pilot")
    a = ap.parse_args(argv)
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    base_json = a.base_json
    if not base_json:
        base_json = str(out / "_defaults_base.json")
        Path(base_json).write_text("{}")
    dirs = []
    for d in a.progress_dirs.split(","):
        dirs += glob.glob(os.path.expanduser(d.strip())) or [os.path.expanduser(d.strip())]
    results = []
    for ss in [s for s in a.symsides.split(",") if s]:
        try:
            s = run(ss, out, a.workers, base_json, dirs)
        except Exception as e:
            s = {"symside": ss, "status": "ERROR", "error": repr(e)[:300]}
        results.append(s)
        print(f"[autopsy-first] {ss} {s['status']} base={s.get('base_gain')} ({s.get('base_trades')}t valid={s.get('base_valid')}) -> combo={s.get('combo_gain')} ({s.get('combo_trades')}t) switches={s.get('switches')} {s.get('error', '')}", flush=True)
        if s.get("launch"):
            print("   launch:", s["launch"], flush=True)
    (out / "autopsy_first_summary.json").write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
