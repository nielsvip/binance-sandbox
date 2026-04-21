# SWEEP MISSION — 2026-04-21

## GOAL
- **20:00 UTC**: ≥1000 crypto configs with pool_sharpe > 1.5 + ≥1000 tradier configs with pool_sharpe > 1.5
- **00:00 UTC**: All setups that give 10x B&H accumulated gain
- **Reports**: 20:00 UTC, 00:00 UTC, 09:00 UTC, 12:00 UTC

## CRITICAL FIX LANDED (19:04 UTC)
`PROFIT_TARGET_PCT` **PURGED** from all live files + both servers.
- Was fake exit mechanism in quick engine (not in real trading code)
- Was the reason PPL appeared to work in sweeps but never fired live
- Was inflating Sharpe 8x (99.4% WR = price always eventually hits fixed TP)
- **All previous Sharpe numbers from quick engine ARE SUSPECT and must be re-validated**

## SERVERS
| Server | Role | CPU | RAM | Status |
|--------|------|-----|-----|--------|
| S1 (157.180.125.52) | CRYPTO sweep | 16 cores | 30GB | Sweeping |
| S2 (204.168.181.211) | TRADIER sweep | 8 cores | 30GB | Sweeping |

## SWEEP STRATEGY (Post-PT-removal)
Without PROFIT_TARGET, exits are now HONEST: WT cross, DC reversal, WRONG_SIDE_ABS_KILL, PPL partial.
Best known levers for real Sharpe:
1. **CT_WT_VELOCITY_GATE** — BC_170/172 validated, wt_velocity_1h gate
2. **WRONG_SIDE_ABS_KILL** — confirmed dominant lever from pre_fullsweep ablation
3. **PPL (PARTIAL_PROFIT_LOCK)** — real 50% close at 0.5%, now the only TP mechanism
4. **LOCAL_EXTREMES_SCORER** — filters low-conviction entries
5. **MIN_HOLD_BARS** — forces riding winners (250 bars = 12.5h crypto)
6. **WINNER_PROTECT** — trails stop after gain reached

## ACTIVE SWEEPS
### S1 — CRYPTO
- Tier: `hunt_crypto` (725k configs, early-abort floor 2.5 after 5 syms)
- Tier: `mega_crypto_v8` (full sweep grid)
- Tier: `le_partial_exit_crypto` (PPL-focused, the real exit mechanism)
- Workers: 12
- Start: 2022-01-01, symbols: 50 (backtest_48_symbols.json)
- Min save Sharpe: 1.5
- Output: data/sweep_results/

### S2 — TRADIER
- Tier: `mega_tradier_v8_focused` (focused stock sweep)
- Tier: `le_full_tradier` (LOCAL_EXTREMES full grid)
- Tier: `le_dynamic_tradier_v2` (le_dynamic sweep)
- Workers: 6
- Start: 2024-01-01, symbols: 114 (backtest_tradier_symbols.json)
- Min save Sharpe: 1.5
- Output: data/sweep_results/

## BUY-AND-HOLD BASELINE (to compute 10x target)
- Crypto (50 sym, 2022-2026): ~TBD from measure_bh.py
- Tradier (114 sym, 2024-2026): ~TBD from measure_bh.py
- **10x target = accumulated_gain_pct > 10 × B&H accumulated_gain_pct**

## REPORT TEMPLATE (fill at each deadline)

### 20:00 UTC Report
- Crypto: X configs tested, Y with Sharpe > 1.5, best = Z
- Tradier: X configs tested, Y with Sharpe > 1.5, best = Z
- Top-3 crypto configs: [list]
- Top-3 tradier configs: [list]

### 00:00 UTC Report
- [fill]

### 09:00 UTC Report
- [fill]

### 12:00 UTC Report
- [fill]

## RESULT FILES
- `data/sweep_results/v8_quick_crypto_*.csv` — crypto sweep results
- `data/sweep_results/v8_quick_tradier_*.csv` — tradier sweep results
- `~/logs/sweep_crypto_s1_*.log` — S1 crypto sweep log
- `~/logs/sweep_tradier_s2_*.log` — S2 tradier sweep log

## HOW TO READ RESULTS (after rsync)
```bash
# Best crypto configs
python3 -c "import pandas as pd, glob; f=sorted(glob.glob('data/sweep_results/v8_quick_crypto_*.csv'))[-1]; df=pd.read_csv(f); print(df[df.pool_sharpe>1.5].sort_values('pool_sharpe',ascending=False).head(20).to_string())"
# Best tradier configs  
python3 -c "import pandas as pd, glob; f=sorted(glob.glob('data/sweep_results/v8_quick_tradier_*.csv'))[-1]; df=pd.read_csv(f); print(df[df.pool_sharpe>1.5].sort_values('pool_sharpe',ascending=False).head(20).to_string())"
```
