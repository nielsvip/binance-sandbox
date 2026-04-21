#!/usr/bin/env python3
"""Generate a large JSONL of hierarchy sweep configs (2026-04-21)."""
import itertools, json, sys
from pathlib import Path

GRID = {
    "SIMPLE_WT15M_EXIT_ONLY_ENABLED": [True, False],
    "HIER_SIGNAL_MODE": ["off", "entry", "exit", "both"],
    "HIER_RZ_TOP_BB": [0.80, 0.85, 0.90, 0.95],
    "HIER_RZ_BOT_BB": [0.05, 0.10, 0.15, 0.20],
    "HIER_DC_BAND_PCT": [0.1, 0.2, 0.5, 1.0],
    "HIER_WT_DELTA_MIN": [0.0, 0.5, 1.0, 2.0],
    "HIER_WT_VEL_MIN": [0.0, 0.5, 1.0, 2.0],
    # also vary these since we confirmed they dominate:
    "WRONG_SIDE_ABS_KILL_ENABLED": [True],  # fixed True — proven critical
    "REENTRY_IF_MOMENTUM_ENABLED": [True, False],
    "PARTIAL_PROFIT_LOCK_ENABLED": [True, False],
    "K3M_FLOOR": [10, 15, 20, 30],
    "STRENGTH_FILTER_ENABLED": [True, False],
}


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/orchestrator/configs/hier_sweep.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(GRID.keys())
    vals = [GRID[k] for k in keys]
    total = 1
    for v in vals: total *= len(v)
    print(f"Full cartesian: {total} configs")
    with open(out_path, "w") as f:
        for combo in itertools.product(*vals):
            cfg = dict(zip(keys, combo))
            if cfg["HIER_SIGNAL_MODE"] == "off":
                # When HIER off, most HIER_* thresholds are irrelevant — dedupe by only emitting one threshold set
                # Check if this is the canonical "off" variant
                if (cfg["HIER_RZ_TOP_BB"] != 0.85 or cfg["HIER_RZ_BOT_BB"] != 0.15
                        or cfg["HIER_DC_BAND_PCT"] != 0.2 or cfg["HIER_WT_DELTA_MIN"] != 0.0
                        or cfg["HIER_WT_VEL_MIN"] != 0.0):
                    continue
            f.write(json.dumps(cfg) + "\n")
    count = sum(1 for _ in open(out_path))
    print(f"Wrote {count} unique configs to {out_path}")


if __name__ == "__main__":
    main()
