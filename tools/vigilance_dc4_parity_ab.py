#!/usr/bin/env python3
"""VIGILANCE_DC4 exit vectorization parity A/B — S1 only (needs crypto NPZ).

Per HANDOFF_EXIT_VECTORIZATION_PARITY.md pipeline step 3: prove the v12_quick
vec twin of the live VIGILANCE_DC4 structural stop reproduces the live-faithful
scalar engine (backtest_v12_engine -> real ez_manage.process_position) on the
SAME frozen 30d NPZ. Switch-attribution test: delta(ON-OFF) per engine must
match, and the stop must actually FIRE somewhere (a 0-fire pass is a null
result, not parity proof — NO-LIES).

Usage (S1): python3 tools/vigilance_dc4_parity_ab.py [SYMSIDE ...]
Output: data/reports/vigilance_dc4_parity_ab.json + stdout table.
"""
import io
import json
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt.evaluate_v12 import evaluate as vec_eval

SYMSIDES = sys.argv[1:] or ["ADAUSDC_LONG", "ZECUSDC_LONG"]
WINDOW = 30
# 4th vigilance mandate (2026-09-28 19:12): guard is a SWITCH, default OFF, 0.25% breach tolerance.
CASES = {"ON": {"VIGILANCE_GUARD_ENABLED": True}, "OFF": {}}


def vec_run(symside, overrides):
    r = vec_eval(symside, dict(overrides), window_days=WINDOW, include_ledger=True)
    ledger = r.get("execution_ledger") or []
    fires = sum(1 for t in ledger if t.get("type") == "CLOSE" and "VIGILANCE_DC4" in str(t.get("exit_reason", "") or t.get("reason", "")))
    return {"gain_pct": float(r.get("gain_pct") or 0.0), "trades": int(r.get("trades") or 0),
            "valid": bool(r.get("valid")), "invalid_reason": r.get("invalid_reason"),
            "vig_fires": fires}


def live_run_counted(symside, overrides):
    from backtest_v12_engine import run_one
    # Isolate the persisted vigilance block state: the sim drives REAL ez_manage code,
    # which loads/saves data/vigilance_blocks_ez.json — without this, the ON run's blocks
    # leak into the OFF run (observed: ADAUSDC live OFF differed from ON by -0.036 with
    # zero fires). Snapshot aside, run, restore.
    blocks = ROOT / "data/vigilance_blocks_ez.json"
    stash = ROOT / "data/vigilance_blocks_ez.json.parity_stash"
    had = blocks.exists()
    if had:
        blocks.replace(stash)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(h)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        r = run_one(symside, overrides=dict(overrides), window_days=WINDOW)
    finally:
        root.removeHandler(h)
        root.setLevel(prev_level)
        try:
            if blocks.exists():
                blocks.unlink()
            if had and stash.exists():
                stash.replace(blocks)
        except Exception:
            pass
    logtext = buf.getvalue()
    fires = logtext.count("VIGILANCE_DC4_") and Counter(
        line.split("[VIGILANCE_DC4_", 1)[1].split("]", 1)[0]
        for line in logtext.splitlines() if "[VIGILANCE_DC4_" in line)
    n_fires = sum(fires.values()) if fires else 0
    blocks = logtext.count("VIGILANCE_BLOCK")
    return {"gain_pct": float(r.get("gain_pct") or 0.0), "trades": int(r.get("trades") or 0),
            "valid": bool(r.get("valid")), "invalid_reason": r.get("invalid_reason"),
            "vig_fires": n_fires, "vig_blocks": blocks}


def main():
    out = {"window_days": WINDOW, "results": {}}
    for symside in SYMSIDES:
        row = {}
        for case, ov in CASES.items():
            row[f"vec_{case}"] = vec_run(symside, ov)
            print(f"[{symside}] vec {case}: {row[f'vec_{case}']}", flush=True)
        for case, ov in CASES.items():
            row[f"live_{case}"] = live_run_counted(symside, ov)
            print(f"[{symside}] live {case}: {row[f'live_{case}']}", flush=True)
        vec_delta = row["vec_ON"]["gain_pct"] - row["vec_OFF"]["gain_pct"]
        live_delta = row["live_ON"]["gain_pct"] - row["live_OFF"]["gain_pct"]
        row["vec_delta_on_minus_off"] = round(vec_delta, 4)
        row["live_delta_on_minus_off"] = round(live_delta, 4)
        row["fires_vec_ON"] = row["vec_ON"]["vig_fires"]
        row["fires_live_ON"] = row["live_ON"]["vig_fires"]
        row["null_test"] = row["fires_vec_ON"] == 0 and row["fires_live_ON"] == 0
        out["results"][symside] = row
        print(f"[{symside}] DELTA vec={vec_delta:+.4f} live={live_delta:+.4f} "
              f"fires vec={row['fires_vec_ON']} live={row['fires_live_ON']} "
              f"{'NULL-TEST (stop never fired — not parity proof)' if row['null_test'] else ''}", flush=True)
    dest = ROOT / "data/reports/vigilance_dc4_parity_ab.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, default=str))
    print(f"WROTE {dest}", flush=True)


if __name__ == "__main__":
    main()
