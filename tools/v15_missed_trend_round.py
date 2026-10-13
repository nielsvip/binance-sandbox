#!/usr/bin/env python3
"""v15_missed_trend_round — daily driver of the §80 MISSED-TREND revision round (2026-10-13, BIBLE §94).

Before: tools/v15_missed_trend.py ran once by hand (AGLDUSDT_SHORT, 10-08). BEST_EFFORT boards stayed terminal and no
board was ever re-checked for the move it missed. Now S1 chain step 4f runs this driver:
  1. select   data/v15_symside_ledger.json rows with verdict BEST_EFFORT and TIM < V15_MT_TIM_MAX (20) — the §81
              TIM_LOW class — not screened in the last V15_MT_RESCREEN_D (7) days, best potential first, at most
              V15_MT_MAX (2) per run;
  2. screen   the REAL `v15_missed_trend.py screen` on the board's own progress JSON (published final set): every delta is
              an evaluate_prepared_sanitized number; a verified combo is kept only when valid AND gain up AND TIM not down;
  3. install  when the screen wrote {SS}_missed_trend_base.json AND combo_gain > base_gain AND combo TIM >= base TIM, the
              base is installed as ~/v15_autopsy_first/{SS}_autopsy_base.json (previous base kept as .prev_<ts>) — the
              scheduler's V15_START_OVERRIDES launch env starts the side's NEXT cadence round from the repaired set and
              V15_AF_PUSH copies it to the workers. Published sheets and progress are NOT touched (no archive/requeue
              unless V15_MT_REQUEUE=1, which calls the tool's own reversible run_requeue);
  4. record   data/reports/missed_trend_round_last.json; PROPOSED_NEW_SWITCH specs land in ~/v15_missed_trend/ where the
              invention loop (step 4e) mines them.

Usage: .venv/bin/python tools/v15_missed_trend_round.py [--dry-run] [--max N] [--workers W]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def select(ledger, out_dir, tim_max, rescreen_d, now, k):
    rows = (ledger or {}).get("sym_sides") or {}
    picks = []
    for ss, r in rows.items():
        if not isinstance(r, dict) or r.get("verdict") != "BEST_EFFORT":
            continue
        tim = r.get("tim")
        if tim is None or float(tim) >= tim_max:
            continue
        if not r.get("path") or not Path(r["path"]).exists():
            continue
        prev = Path(out_dir) / f"{ss}_missed_trend.json"
        if prev.exists() and now - prev.stat().st_mtime < rescreen_d * 86400:
            continue
        picks.append((float(r.get("potential") or 0.0), ss, r))
    picks.sort(key=lambda x: (-x[0], x[1]))
    return [(ss, r) for _, ss, r in picks[: max(0, k)]]


def should_install(rep):
    try:
        b = rep.get("base") or {}
        return bool(rep.get("combo_overrides")) and rep.get("combo_gain") is not None and b.get("gain") is not None and float(rep["combo_gain"]) > float(b["gain"]) + 1e-9 and float(rep.get("combo_tim") or 0.0) >= float(b.get("tim") or 0.0) - 1e-9
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(ROOT / "data" / "v15_symside_ledger.json"))
    ap.add_argument("--out", default=os.path.expanduser("~/v15_missed_trend"))
    ap.add_argument("--autopsy-dir", default=os.path.expanduser("~/v15_autopsy_first"))
    ap.add_argument("--max", type=int, default=int(os.environ.get("V15_MT_MAX", "2")))
    ap.add_argument("--workers", type=int, default=int(os.environ.get("V15_MT_WORKERS", "3")))
    ap.add_argument("--tim-max", type=float, default=float(os.environ.get("V15_MT_TIM_MAX", "20")))
    ap.add_argument("--rescreen-d", type=float, default=float(os.environ.get("V15_MT_RESCREEN_D", "7")))
    ap.add_argument("--timeout-s", type=int, default=1500)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    now = time.time()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    try:
        ledger = json.loads(Path(a.ledger).read_text())
    except Exception as e:
        print(f"[mt-round] no ledger ({e}) — nothing to do", flush=True)
        return 0
    picks = select(ledger, a.out, a.tim_max, a.rescreen_d, now, a.max)
    summary = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "picked": [ss for ss, _ in picks], "results": {}}
    print(f"[mt-round] BEST_EFFORT & TIM<{a.tim_max:g} due for screen: {summary['picked']}", flush=True)
    for ss, r in picks:
        if a.dry_run:
            summary["results"][ss] = {"dry_run": True, "set_json": r["path"]}
            continue
        cmd = ["nice", "-n", "15", sys.executable, "-u", "tools/v15_missed_trend.py", "screen", "--symside", ss, "--set-json", r["path"], "--out", a.out, "--workers", str(a.workers)]
        res = {"set_json": r["path"], "tim": r.get("tim"), "final_gain": r.get("final_gain")}
        try:
            p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=a.timeout_s, env=dict(os.environ, V12_ALLOW_STALE_NPZ="1", V12_NPZ_CACHE=os.environ.get("V12_NPZ_CACHE", "2")))
            res["rc"] = p.returncode
            res["line"] = ([l for l in (p.stdout or "").splitlines() if l.startswith("[missed-trend]")] or [""])[-1]
            if p.returncode != 0:
                res["stderr"] = (p.stderr or "")[-400:]
        except subprocess.TimeoutExpired:
            res["rc"] = "timeout"
        rp, bp = Path(a.out) / f"{ss}_missed_trend.json", Path(a.out) / f"{ss}_missed_trend_base.json"
        rep = {}
        try:
            rep = json.loads(rp.read_text()) if rp.exists() and rp.stat().st_mtime >= now else {}
        except Exception:
            rep = {}
        res["status"] = rep.get("status")
        res["combo_gain"], res["combo_tim"] = rep.get("combo_gain"), rep.get("combo_tim")
        res["missing_spec"] = bool(rep.get("missing"))
        if bp.exists() and bp.stat().st_mtime >= now and should_install(rep):
            dst = Path(a.autopsy_dir) / f"{ss}_autopsy_base.json"
            stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
            if dst.exists():
                shutil.copy2(dst, str(dst) + f".prev_{stamp}")
            tmp = str(dst) + ".mttmp"
            shutil.copy(bp, tmp)
            os.replace(tmp, dst)
            res["installed_base"] = str(dst)
            if os.environ.get("V15_MT_REQUEUE") == "1":
                sys.path.insert(0, str(ROOT / "tools"))
                import v15_missed_trend as MT
                res["requeue"] = MT.run_requeue(ss, r["path"], str(bp), autopsy_dir=None)
        summary["results"][ss] = res
        print(f"[mt-round] {ss} rc={res.get('rc')} status={res.get('status')} combo={res.get('combo_gain')} tim={res.get('combo_tim')} installed={bool(res.get('installed_base'))} {res.get('line', '')[:160]}", flush=True)
    try:
        out = ROOT / "data" / "reports" / "missed_trend_round_last.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=1, default=str))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
