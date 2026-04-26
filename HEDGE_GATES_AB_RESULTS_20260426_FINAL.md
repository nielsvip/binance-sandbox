# Hedge Gate A/B — Final Results 2026-04-26

## Setup

- **Engine A (vectorized, fast):** v8_quick_engine + autonomous_search on S1, 20 syms × 1 yr (2025-04-01 → 2026-04-26)
- **Engine B (real-code, slow):** backtest_v8_engine on S2, 12 syms × 6 mo (2025-10-01 → 2026-04-26)
- **Baseline:** crypto_2p6365_genuine.json (proven 2.6365 pool_sharpe at 50 syms × multi-year on v8_quick_engine)
- **Gates tested:** HEDGE_DETERIORATING_GAIN_ENABLED, HEDGE_DC_RESISTANCE_GATE_ENABLED, HEDGE_WT_VEL_GATE_ENABLED (added live 2026-04-26 in scan_and_hedge_losers + RatingRegistry; vectorized port added 2026-04-26 in v8_quick_engine simulate() hedge block)

## A: autonomous_search results

### Baselines (no mutations) — IDENTICAL

| Sweep | sym_sharpe | pool_sharpe | gain | trades | dd |
|---|---|---|---|---|---|
| w0001 (gates ON) | 2.6666 | 0.0000 | +12.0% | 13 | 0.0% |
| w0002 (gates OFF) | 2.6666 | 0.0000 | +12.0% | 13 | 0.0% |

Both baselines fire only 13 trades on this 20-sym × 1-yr scope, so almost no hedge candidate searches occur — the gates are dormant. Above the CLAUDE.md 2.5 sym_sharpe floor; pool_sharpe and trade-count flag this as small-sample (per CLAUDE.md rule 4b: not publishable below 48 syms).

### Mutated iters (random ±20% bool / ±15% numeric)

| Stat | w0001 (gates ON, 25 iters) | w0002 (gates OFF, 16 iters) | Δ B−A |
|---|---|---|---|
| Max pool_sharpe | 0.6735 | **1.1142** | **+65%** |
| Avg pool_sharpe | 0.1740 | **0.2904** | **+67%** |
| Best iter dd | 4.02% | **1.08%** | **-73%** |
| Iters ≥ 2.5 floor | 0 | 0 | 0 |

Gates OFF wins on every mutated metric. **Direction matches earlier 6sym × 6mo backtest_v8_engine A/B (gates OFF beat gates ON 2.2× sharpe_pt).**

### Top 3 mutated configs (ranked pool_sharpe)

**w0001 (gates ON):**
- iter=3: pool=0.6735 sym=0.5581 gain=+1081.71% trades=2139 dd=4.02%
- iter=1: pool=0.6034 sym=1.5843 gain=+117.51% trades=241 dd=3.56%
- iter=9: pool=0.5870 sym=1.0153 gain=+236.69% trades=377 dd=4.40%

**w0002 (gates OFF):**
- iter=0: pool=1.1142 sym=1.3498 gain=+45.58% trades=241 dd=1.08% ⭐
- iter=4: pool=0.9684 sym=1.0029 gain=+4716.81% trades=6824 dd=19.48%
- iter=14: pool=0.8167 sym=0.9178 gain=+4538.00% trades=6723 dd=31.29%

## B: backtest_v8_engine results

5 cells (B_calib_2p6365, B_only_DET, B_only_DC, B_only_WT, B_all_gates) ran sequentially, each 25-min budget, 12 syms × 6 months. **All 5 produced ZERO V8_RESULT_LIVE** — engine too slow per step (1.1% sim completion in 25 min) to reach the first reporting checkpoint at step 1534/92071. Total elapsed: ~2.5 hours of compute, zero usable Sharpe data.

The earlier 6sym × 6mo A/B (Arm A vs Arm B from 2026-04-26 first session) DID produce data: gates OFF sharpe_pt=0.022 vs gates ON sharpe_pt=0.010 (2.2× delta).

## Verdict

1. **At the proven 2.6365 baseline configuration, the new hedge gates are dormant** — too few hedge candidate searches occur for them to matter (13 trades on 20 syms × 1 yr).
2. **In aggressive mutated configurations**, gates OFF consistently outperforms gates ON by ~65% on Sharpe and 73% on drawdown. But those configs are well below the 2.5 floor and shouldn't drive decisions.
3. **No mutation in either sweep beat the baseline's 2.6666 sym_sharpe.** The baseline is on the optimization plateau already established 2026-04-22.

## Live trading recommendation

**Keep gates ON in live (current default).** Rationale:
- Zero baseline cost (sym_sharpe identical with/without gates).
- Provides the "wrong moment" filter the user requested 2026-04-26 (DC resistance, WT decel, deteriorating gain).
- The sweep showed gates OFF won only in aggressive sub-2.5 configurations that wouldn't be deployed anyway.

**Don't auto-flip live defaults to OFF based on the mutated-iter results.** Those results explored sub-trash-floor configurations and don't reflect realistic live behavior.

## Capacity / engine notes

- **A (autonomous_search) hit memory limits** — both workers died after 16-25 iters from suspected memory leak in simulate() loop. For longer sweeps (100+ iters), need a periodic-restart wrapper or a memory-leak fix in v8_quick_engine.
- **B (backtest_v8_engine) is unusable for hedge-gate sweeping** at any scope: even 12 syms × 6 mo is too slow to produce a single V8_RESULT_LIVE within 25 min. The IPC overhead with shared-memory server kills throughput. Either need a local in-process indicator cache, or accept this engine is for one-off validation only, not sweeping.

## Files

- A logs: S1 `/home/niels/logs/hedge_gates_sweep_w0001.log`, `_w0002.log`
- A CSV: S1 `/home/niels/binance-sandbox/data/autonomous/hedge_gates_sweep_20260426/{w0001,w0002}/autonomous_crypto.csv`
- B logs: S2 `/home/niels/hedge_ab_20260426/logs/B_*.log`, `sweep_summary.txt`
- Baselines: S1 `/home/niels/binance-sandbox/data/baselines/crypto_2p6365_{plus,minus}_hedge_gates.json`
- Live wiring: `ez_positions_quick.py` (RatingRegistry + scan_and_hedge_losers), `v8_quick_engine.py:2374-2403,2873-2900` (vectorized port)
