#!/usr/bin/env python3
"""Sweep tradier parameters through REAL process_position() backtest.
Runs each config variation and reports the impact vs baseline.
Uses local 7-symbol test (NVDA,AAPL,META,MSFT,XOM,GLD,USO) — fast, ~60s each."""
import asyncio
import json
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

SYMBOLS = "NVDA,AAPL,META,MSFT,XOM,GLD,USO"
START = "2024-06-01"
CAPITAL = 70000

# Parameter variations to test
PARAMS = {
    "BASELINE": {},
    # T51: NOLOSS_MIN_PROFIT_PCT variations
    "T51_noloss_0.5pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5},
    "T51_noloss_1.0pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 1.0},
    "T51_noloss_3.0pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 3.0},
    # T55: RSI entry period
    "T55_rsi_period_7": {"RSI_ENTRY_PERIOD_TRADIER": 7},
    "T55_rsi_period_10": {"RSI_ENTRY_PERIOD_TRADIER": 10},
    "T55_rsi_period_14": {"RSI_ENTRY_PERIOD_TRADIER": 14},
    # T61: RATIO_MULTIPLIER
    "T61_ratio_2.0": {"RATIO_MULTIPLIER_TRADIER": 2.0},
    "T61_ratio_3.0": {"RATIO_MULTIPLIER_TRADIER": 3.0},
    "T61_ratio_3.5": {"RATIO_MULTIPLIER_TRADIER": 3.5},
    # Position sizing
    "sizing_400": {"START_POSITION_SIZE": 400},
    "sizing_600": {"START_POSITION_SIZE": 600},
    "sizing_800": {"START_POSITION_SIZE": 800},
    # MIN_HOLD_MINUTES
    "hold_15m": {"MIN_HOLD_MINUTES_TRADIER": 15},
    "hold_30m": {"MIN_HOLD_MINUTES_TRADIER": 30},
    "hold_60m": {"MIN_HOLD_MINUTES_TRADIER": 60},
    # BC_151: MIN_GAIN_TO_BUY_AGGRESSIVELY (augment gate)
    "aug_gate_1.5pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.5},
    "aug_gate_3.0pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0},
    "aug_gate_5.0pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 5.0},
}


async def run_one(name, overrides):
    """Run one parameter variation."""
    import backtest_v5_full_tradier as m
    # Reset module state
    import importlib
    importlib.reload(m)
    runner = m.V5FullRunner(SYMBOLS.split(","), START, CAPITAL, "trb", "WT_EXIT")
    runner._load()
    runner._init_strategy()
    # Apply overrides to config
    for key, val in overrides.items():
        if hasattr(runner.tm.strategy.config, key):
            setattr(runner.tm.strategy.config, key, val)
    # Suppress output
    import logging
    logging.getLogger("v5_full").setLevel(logging.WARNING)
    logging.getLogger("tradier_manage").setLevel(logging.CRITICAL)
    # Run
    all_ts = set()
    for sym, store in runner.stores.items():
        for t in store.timestamps:
            t_int = int(t)
            if t_int >= runner.start_ts:
                all_ts.add(t_int)
    market_ts = []
    for ts in sorted(all_ts):
        dt = datetime.utcfromtimestamp(ts)
        if dt.weekday() >= 5:
            continue
        utc_min = dt.hour * 60 + dt.minute
        if 810 <= utc_min <= 1200:
            market_ts.append(ts)
    from unittest.mock import MagicMock
    process_position = runner._tm_mod.process_position
    order_queue = MagicMock()
    t0 = time.time()
    for step, ts in enumerate(market_ts):
        runner._step_to(ts)
        await runner._wt_exit_check(ts)
        for sym in runner.stores:
            if sym not in runner.tm.indicator_cache:
                continue
            for side in ["LONG", "SHORT"]:
                pk = f"trb:{sym}_{side}"
                try:
                    await process_position("trb", pk, order_queue, runner.tm, force=False)
                except Exception:
                    pass
        runner.tm.last_monitored_positions.clear()
    elapsed = time.time() - t0
    # Calculate stats
    closes = [t for t in runner.trade_log if t["action"] == "CLOSE"]
    opens = [t for t in runner.trade_log if t["action"] == "OPEN"]
    wins = [c for c in closes if c["gain"] > 0]
    losses = [c for c in closes if c["gain"] <= 0]
    total_pnl = sum(c["pnl"] for c in closes)
    wr = len(wins) / max(1, len(closes)) * 100
    avg_win = sum(c["pnl"] for c in wins) / max(1, len(wins))
    avg_loss = sum(c["pnl"] for c in losses) / max(1, len(losses))
    pf = abs(sum(c["pnl"] for c in wins)) / max(1, abs(sum(c["pnl"] for c in losses)))
    return {
        "name": name,
        "trades": len(closes),
        "opens": len(opens),
        "pnl": round(total_pnl, 2),
        "wr": round(wr, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "pf": round(pf, 2),
        "elapsed": round(elapsed, 1),
    }


async def main():
    results = []
    for name, overrides in PARAMS.items():
        print(f"Running {name}...", end=" ", flush=True)
        try:
            r = await run_one(name, overrides)
            results.append(r)
            print(f"trades={r['trades']} PnL=${r['pnl']} WR={r['wr']}% PF={r['pf']} ({r['elapsed']}s)")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"name": name, "trades": 0, "pnl": 0, "wr": 0, "error": str(e)})
    # Summary table
    print(f"\n{'='*100}")
    print(f"{'Name':30s} {'Trades':>7s} {'PnL':>10s} {'WR%':>6s} {'AvgWin':>8s} {'AvgLoss':>8s} {'PF':>6s}")
    print(f"{'='*100}")
    baseline_pnl = results[0]["pnl"] if results else 0
    for r in sorted(results, key=lambda x: -x.get("pnl", 0)):
        delta = r.get("pnl", 0) - baseline_pnl
        sign = "+" if delta >= 0 else ""
        print(f"{r['name']:30s} {r.get('trades',0):7d} ${r.get('pnl',0):9.2f} {r.get('wr',0):5.1f}% ${r.get('avg_win',0):7.2f} ${r.get('avg_loss',0):7.2f} {r.get('pf',0):5.2f}  ({sign}{delta:.0f})")
    # Save
    with open("sweep_tradier_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to sweep_tradier_results.json")


if __name__ == "__main__":
    asyncio.run(main())
