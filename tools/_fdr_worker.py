#!/usr/bin/env python3
"""_fdr_worker — persistent eval worker for v15_filter_delta_rebuild. Loads the engine ONCE, then
loops reading one JSON request per stdin line {"sym","ov","win"} and writes one JSON result line
{"gain_pct","trades","valid","invalid_reason"}. The parent kills + respawns this process on a hard
wall-clock timeout (bounds C-level/numpy GIL hangs that a thread timeout cannot preempt)."""
import json, os, pathlib, sys
ROOT = pathlib.Path.home() / "binance-sandbox"
if not ROOT.exists():
    ROOT = pathlib.Path("/Users/niels/Documents/binance")
os.chdir(ROOT); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("BASE_PATH", str(ROOT)); os.environ["V12_NPZ_CACHE"] = "32"
from tools.opt.v12_pilot import evaluate_sanitized as ev
sys.stderr.write("WORKER_READY\n"); sys.stderr.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        r = ev(req["sym"], req["ov"], req["win"])
        out = {"gain_pct": r.get("gain_pct"), "trades": r.get("trades"), "valid": r.get("valid"), "invalid_reason": r.get("invalid_reason")}
    except Exception as e:
        out = {"gain_pct": None, "trades": 0, "valid": False, "invalid_reason": f"WORKER_ERR {e}"[:150]}
    sys.stdout.write(json.dumps(out) + "\n"); sys.stdout.flush()
