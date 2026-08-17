#!/usr/bin/env python3
"""Run one isolated Tier-2 WT_DC entry confirmation.

All other entry and exit families are explicitly disabled.  The first WT_DC
entry is marked to market at the end, making this a same-entry timing/control
check rather than an exit-system claim.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

import exposure_ladder as ladder


def build_overrides(args) -> dict:
    side = args.side.upper()
    other = "SHORT" if side == "LONG" else "LONG"
    out = ladder.all_entries_off()
    out.update(ladder.all_exits_off())
    out.update(
        {
            f"WT_DC_{side}_ENABLED": True,
            f"WT_DC_{other}_ENABLED": False,
            "WT_DC_ENTRY_THRESHOLD": args.threshold,
            "TRA_WT_DC_ENTRY_THRESHOLD": args.threshold,
            "WT_DC_HTF_GATE": args.htf_gate,
            "HTF_ALIGN_REQUIRED_TRADIER": args.align,
            "COMBINED_STOCH_GATE_TRADIER": args.stoch,
            "WT_DC_ENTRY_K5M_MAX_LONG": args.k5m_max_long,
            "WT_DC_ENTRY_K5M_MIN_SHORT": args.k5m_min_short,
            "ENTRY_SCORE_THRESHOLD": 0,
            "TRADIER_ENTRY_SCORE_THRESHOLD": 0,
            "GOLDEN_RULE_HTF_MIN_TFS": 0,
            "GR_HTF_DIRECT_ENTRY_ENABLED": False,
            "LIVE_ENTRY_ENGINE_ENABLED": False,
            "TRADIER_LOCAL_EXTREMES_SCORING_ENABLED": False,
            "UVE_LIVE_ENABLED": False,
            "TRADIER_LONG_ONLY_ENTRIES": side == "LONG",
        }
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", choices=("LONG", "SHORT"), required=True)
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--npz-dir", default=str(REPO / "backtest_v8" / "indicators"))
    ap.add_argument("--threshold", type=float, default=45.0)
    ap.add_argument("--htf-gate", default="none")
    ap.add_argument("--align", type=int, default=0)
    ap.add_argument("--stoch", type=float, default=100.0)
    ap.add_argument("--k5m-max-long", type=float, default=100.0)
    ap.add_argument("--k5m-min-short", type=float, default=0.0)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = Path(args.out_dir or REPO / "data" / "reports" / "vec_research" /
               f"wt_dc_exact_{stamp}_{args.symbol.upper()}_{args.side}")
    out.mkdir(parents=True, exist_ok=True)
    override = out / "override.json"
    override.write_text(json.dumps(build_overrides(args), indent=2) + "\n")
    result_file = out / "v8_result.txt"
    trades_dir = out / "trades"
    trades_dir.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "V8_OVERRIDE_FILE": str(override),
            "V8_TRADES_OUT_DIR": str(trades_dir),
            "V8_TRADES_RUN_ID": "wt_dc_exact",
            "V8_SWEEP_MODE": "1",
            "V8_KEEP_ENTRY_GATES": "1",
            "V8_DISABLE_PER_SYM": "1",
            "V8_LADDER_ONLY_SIDE": args.side.lower(),
            "V8_RATE_GUARD_DISABLED": "1",
            "V8_RESULT_FILE": str(result_file),
        }
    )
    env.pop("V8_LADDER_FORCE_INITIAL_SIDE", None)
    env.pop("V8_SIDE_GATE_DISABLED", None)
    py = os.environ.get(
        "BINANCE_PYTHON", "/home/niels/.conda/envs/binance_env/bin/python"
    )
    if not Path(py).exists():
        py = sys.executable
    cmd = [
        py,
        str(REPO / "backtest_v8_engine.py"),
        "--mode",
        "tradier",
        "--account",
        "trb",
        "--start",
        args.start,
        "--capital",
        "10000",
        "--symbols",
        args.symbol.upper(),
        "--npz-dir",
        str(Path(args.npz_dir)),
    ]
    with (out / "engine.log").open("w") as log:
        proc = subprocess.run(
            cmd,
            cwd=REPO,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=args.timeout,
        )
    text = result_file.read_text() if result_file.exists() else ""
    summary = {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "command": cmd,
        "symbol": args.symbol.upper(),
        "side": args.side,
        "start": args.start,
        "overrides": build_overrides(args),
        "v8_result": text.strip(),
        "contract": {
            "entry_family": "WT_DC_ONLY",
            "exit_families": "ALL_OFF_MARK_TO_MARKET",
            "dc_low4_used_as_exit": False,
            "promotion_eligible": False,
        },
    }
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
