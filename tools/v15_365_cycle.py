#!/usr/bin/env python3
"""BIBLE §58 cycle driver: verify a finished 30D sheet on 365D; if 30D or 365D is not (valid AND positive), run
tools/v15_365_repair.py, re-run the 30D sheet from the repaired set (V15_START_OVERRIDES), verify again — repeat
until both are positive or --rounds is exhausted. One JSON verdict per sym_side.

Usage: python3 tools/v15_365_cycle.py --sym-side SS --progress <SS>_v14_progress.json --template SPREADSHEETS/TEMPLATE_X.xlsx
       --work /home/niels/v15_365cycle_20260929 [--rounds 3] [--workers 4]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PY = sys.executable


def both_windows(overrides, ss):
    from tools.opt.v12_pilot import evaluate_sanitized as ES
    out = {}
    for wd in (30, 365):
        r = ES(ss, dict(overrides), wd) or {}
        out[wd] = {k: r.get(k) for k in ("gain_pct", "trades", "valid", "invalid_reason", "tim_pct", "max_dd_pct", "bh_pct")}
    return out


MIN_365_SPAN_DAYS = 330


def span_365(ss):
    """Real history covered by the 365D slice. Short NPZs (stocks with ~6 weeks of 15m) silently return the SAME
    window as 30D — a fake 365D pass (CLS_LONG 42.6d, ALMU_SHORT 106.7d on 2026-09-29)."""
    from tools.opt import evaluate_v12 as E
    try:
        t = E.prepare(ss, 365)["npz_prepared"]["timestamps"]
        t = t / 1000 if t[-1] > 1e11 else t
        return float((t[-1] - t[0]) / 86400)
    except Exception:
        return 0.0


FLOOR30, FLOOR365, TARGET30 = 10, 80, 30  # USER 2026-09-29: >=10 trades/mo AND >=80/yr; aim >=30/mo


def good(w, floor=FLOOR30):
    return bool(w.get("valid")) and (w.get("gain_pct") or 0) > 0 and int(w.get("trades") or 0) >= floor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    ss, work = a.sym_side, Path(a.work)
    (work / "repair").mkdir(parents=True, exist_ok=True)
    verdict = {"sym_side": ss, "rounds": []}
    prog_path = Path(a.progress)
    for rnd in range(a.rounds + 1):
        prog = json.load(open(prog_path))
        ov = dict(prog.get("cumulative_overrides") or {})
        w = both_windows(ov, ss)
        rec = {"round": rnd, "progress": str(prog_path), "w30": w[30], "w365": w[365], "both_ok": good(w[30], FLOOR30) and good(w[365], FLOOR365)}
        verdict["rounds"].append(rec)
        if rnd == 0:
            verdict["span_365_days"] = round(span_365(ss), 1)
            if verdict["span_365_days"] < MIN_365_SPAN_DAYS:
                rec["both_ok"] = False
                verdict["unverifiable"] = f"365D slice covers only {verdict['span_365_days']}d of NPZ history (< {MIN_365_SPAN_DAYS}d) — waiting for full-history NPZ"
                print(f"[365-CYCLE] {ss} UNVERIFIABLE: {verdict['unverifiable']}", flush=True)
                break
        print(f"[365-CYCLE] {ss} round {rnd}: 30D {w[30]['gain_pct']} ({w[30]['trades']}tr TIM {w[30]['tim_pct']} valid {w[30]['valid']}) | 365D {w[365]['gain_pct']} ({w[365]['trades']}tr TIM {w[365]['tim_pct']} valid {w[365]['valid']} {w[365]['invalid_reason']}) both_ok={rec['both_ok']}", flush=True)
        if rec["both_ok"] and int(w[30].get("trades") or 0) < TARGET30 and not verdict.get("boosted"):
            # USER: < 30 trades/mo -> soften filters / open entry+reentry paths; keep only if results are not worse
            verdict["boosted"] = True
            bdir = work / "repair" / "boost"
            bdir.mkdir(parents=True, exist_ok=True)
            subprocess.run([PY, "-u", str(ROOT / "tools" / "v15_365_repair.py"), "--progress", str(prog_path), "--template", a.template, "--out", str(bdir), "--workers", str(a.workers)], cwd=str(ROOT), check=False)
            bf = bdir / f"{ss}_365_repair.json"
            if bf.exists():
                bj = json.load(open(bf))
                if bj.get("final", {}).get("both_positive_valid") and any(b.get("applied") for b in bj.get("boost", [])):
                    boosted = work / f"{ss}_boosted_progress.json"
                    boosted.write_text(json.dumps({"symside": ss, "cumulative_overrides": bj["final"]["overrides"], "source": str(bf)}, default=str))
                    prog_path = boosted
                    continue  # re-verify the boosted set as the next round
            break
        if rec["both_ok"] or rnd == a.rounds:
            break
        rep_dir = work / "repair" / f"r{rnd}"
        rep_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([PY, "-u", str(ROOT / "tools" / "v15_365_repair.py"), "--progress", str(prog_path), "--template", a.template, "--out", str(rep_dir), "--workers", str(a.workers)], cwd=str(ROOT), check=False)
        rep_file = rep_dir / f"{ss}_365_repair.json"
        if not rep_file.exists():
            rec["error"] = "repair produced no output"
            break
        sheet_dir = work / f"sheet_r{rnd}"
        (sheet_dir / "progress").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, V15_START_OVERRIDES=str(rep_file), V15_FRESH_RUN="1", V15_PROGRESS_DIR=str(sheet_dir / "progress"), V15_SKIP_LIVE_AT_DONE="1")
        out_x = ROOT / "SPREADSHEETS" / "V15_365CYCLE_20260929" / f"{ss}_r{rnd}_30d_matrix.xlsx"
        out_x.parent.mkdir(parents=True, exist_ok=True)
        with open(sheet_dir / f"{ss}.log", "w") as lf:
            subprocess.run([PY, "-u", str(ROOT / "v15_pilot.py"), "--sym-side", ss, "--template", a.template, "--seq-mode", "worst2best", "--window-days", "30", "--vector-only", "--workers", str(a.workers), "--out", str(out_x)], cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT, check=False)
        nxt = sheet_dir / "progress" / f"{ss}_v14_progress.json"
        if not nxt.exists():
            rec["error"] = f"sheet re-run produced no progress ({sheet_dir / (ss + '.log')})"
            break
        prog_path = nxt
    verdict["final_both_ok"] = bool(verdict["rounds"][-1].get("both_ok", False)) and not verdict.get("unverifiable")
    verdict["final_progress"] = str(prog_path)
    verdict["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (work / f"{ss}_365_cycle.json").write_text(json.dumps(verdict, indent=1, default=str))
    print(f"[365-CYCLE] {ss} FINAL both_ok={verdict['final_both_ok']} -> {work / (ss + '_365_cycle.json')}", flush=True)


if __name__ == "__main__":
    main()
