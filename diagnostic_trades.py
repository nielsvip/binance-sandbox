#!/usr/bin/env python3
import sys
import datetime as dt
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from v8_vec_sweep import simulate_one_symbol, SweepConfig
from forward_test_vec_vs_live import load_active_overrides, events_to_trades

def main():
    cfg = SweepConfig()
    overrides = load_active_overrides("ZECUSDC", "LONG")
    print(f"Loaded overrides count: {len(overrides)}")
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    # Lock emergency gates
    for locked in ("UNIVERSAL_NOLOSS_GATE", "VEC_NOLOSS_GATE_ENABLED", "HEDGE_SCAN_ENABLED", "HEDGE_MODE", "OBLIGATORY_HEDGE_ENABLED", "NOLOSS_ENABLED"):
        if hasattr(cfg, locked):
            setattr(cfg, locked, False)

    start_ts = int(dt.datetime(2026, 5, 19, tzinfo=dt.timezone.utc).timestamp())
    events, rets, n_bars = simulate_one_symbol("ZECUSDC", "LONG", "crypto", cfg, start_ts=start_ts)
    trades = events_to_trades(events, rets, "LONG")
    print(f"Total simulated trades: {len(trades)}")
    
    print("\n--- FIRST 20 SIMULATED TRADES ---")
    for i, t in enumerate(trades[:20]):
        ent = dt.datetime.fromtimestamp(t['entry_ts'], dt.timezone.utc)
        ext = dt.datetime.fromtimestamp(t['exit_ts'], dt.timezone.utc)
        print(f"#{i+1}: {ent.strftime('%Y-%m-%d %H:%M')} -> {ext.strftime('%Y-%m-%d %H-%M')} pnl={t['pnl_pct']:.2f}% reason={t['origin']}")

if __name__ == "__main__":
    main()
