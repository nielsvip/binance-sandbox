#!/usr/bin/env python3
"""short_knob_sweep.py — build baseline_short by sweeping the 522 vec-aware knobs SHORT-only.

USER 2026-05-30 mandate (option A): vectorized sweep first; Tier-2 validates if vec can't reach parity.
Base = baseline_long config (WT_DC_HTF_GATE=4h + QUALITY_BOTTOM_DISABLE_SCATTERGUN=True), --sides SHORT.
For each vec-aware knob: bool→flip; numeric→{0.5x,2x default (and a couple fixed steps)}; str→skip (no safe range).
Runs v8_vec_sweep per candidate, bounded concurrency (no S1 thrash), parses pool_sharpe, logs canonical CSV,
tracks best vs baseline_short=0.26 / baseline_long=0.40. Every Sharpe routed through the engine's own
metrics_guard output (we only parse its emitted pool_sharpe= line — no recomputation here). PARITY CAVEAT:
vec result; promote to live only after Tier-2 (backtest_v8_engine) validation.
"""
import csv, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import fields

REPO = "/home/niels/binance-sandbox"
PY = "/home/niels/.conda/envs/binance_env/bin/python"
SYMS_FILE = "/tmp/baseline_syms_full.txt"
KNOBS_FILE = f"{REPO}/vec_aware_knobs.txt"
BASE_OVERRIDES = {"WT_DC_HTF_GATE": "4h", "QUALITY_BOTTOM_DISABLE_SCATTERGUN": "True"}
BASELINE_LONG = 0.40
BASELINE_SHORT = 0.26
MAX_PARALLEL = 4          # bounded — coexist with S1 production sweeps, no OOM
RESULT_CSV = os.path.expanduser("~/logs/short_knob_sweep_results.csv")
POOL_RE = re.compile(r"pool_sharpe=([+-]?[0-9.]+)")
DD_RE = re.compile(r"dd=([0-9.]+)%")
TR_RE = re.compile(r"trades=([0-9,]+)")

def candidates_for(name, default):
    if isinstance(default, bool):
        return [not default]                       # flip
    if isinstance(default, int):
        out = {max(0, default - 1), default + 1, default * 2}
        return sorted(v for v in out if v != default)
    if isinstance(default, float):
        return sorted({round(default * 0.5, 6), round(default * 2.0, 6)} - {default})
    return []                                      # str/other: no safe auto-range → skip

def run_arm(knob, val):
    syms = open(SYMS_FILE).read().strip()
    cmd = [PY, "v8_vec_sweep.py", "--mode", "crypto", "--symbols", syms,
           "--sides", "SHORT", "--start", "2024-01-01"]
    for k, v in BASE_OVERRIDES.items():
        cmd += ["--override", f"{k}={v}"]
    cmd += ["--override", f"{knob}={val}"]
    env = dict(os.environ, V8_VEC_ALLOW_DIAGNOSTIC="1")
    try:
        r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=1200)
        out = r.stdout + r.stderr
        m = POOL_RE.search(out)
        if not m:
            return (knob, val, None, None, None, "no_pool_sharpe")
        ps = float(m.group(1))
        dd = float(DD_RE.search(out).group(1)) if DD_RE.search(out) else None
        tr = int(TR_RE.search(out).group(1).replace(",", "")) if TR_RE.search(out) else None
        return (knob, val, ps, dd, tr, "ok")
    except subprocess.TimeoutExpired:
        return (knob, val, None, None, None, "timeout")
    except Exception as e:
        return (knob, val, None, None, None, f"err:{type(e).__name__}")

def main():
    sys.path.insert(0, REPO)
    from v8_vec_sweep import SweepConfig
    cfg = SweepConfig()
    knob_names = [l.strip() for l in open(KNOBS_FILE) if l.strip() and not l.startswith("#")]
    fld = {f.name: f for f in fields(SweepConfig)}
    arms = []
    for kn in knob_names:
        if kn not in fld:
            continue
        default = getattr(cfg, kn)
        for v in candidates_for(kn, default):
            arms.append((kn, v))
    print(f"[SHORT_KNOB_SWEEP] {len(knob_names)} vec-aware knobs → {len(arms)} candidate arms "
          f"(base={BASE_OVERRIDES}, SHORT, baseline_long={BASELINE_LONG})", flush=True)
    best = {"ps": BASELINE_SHORT, "knob": "BASELINE", "val": "-", "dd": None, "tr": 853}
    with open(RESULT_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["knob", "value", "pool_sharpe", "max_dd_pct", "trades", "status",
                    "beats_short_0.26", "beats_long_0.40", "ts"])
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futs = {ex.submit(run_arm, kn, v): (kn, v) for kn, v in arms}
            for fu in as_completed(futs):
                kn, v, ps, dd, tr, st = fu.result()
                done += 1
                bs = (ps is not None and ps > BASELINE_SHORT)
                bl = (ps is not None and ps > BASELINE_LONG)
                w.writerow([kn, v, ps, dd, tr, st, bs, bl, int(time.time())]); fh.flush()
                if ps is not None and ps > best["ps"]:
                    best = {"ps": ps, "knob": kn, "val": v, "dd": dd, "tr": tr}
                    print(f"[NEW BEST] {kn}={v} → pool_sharpe={ps:.4f} dd={dd} trades={tr} "
                          f"{'>>> BEATS LONG 0.40' if bl else ''}", flush=True)
                if done % 25 == 0:
                    print(f"[PROGRESS] {done}/{len(arms)} | best={best['ps']:.4f} ({best['knob']}={best['val']})", flush=True)
    print(f"[DONE] best SHORT pool_sharpe={best['ps']:.4f} via {best['knob']}={best['val']} "
          f"(baseline_long={BASELINE_LONG}). Results: {RESULT_CSV}", flush=True)

if __name__ == "__main__":
    main()
