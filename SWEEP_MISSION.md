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

## BUY-AND-HOLD BASELINE (measured 2026-04-21)
- Crypto (50 sym, 2022-2026): accumulated = **-3,623%** (avg -72.46%/sym). 10x target = +3,623% gain.
- Tradier (114 sym, 2024-2026): accumulated = **+7,720.55%** (avg +67.72%/sym). 10x target = **+77,205%** gain.
- **10x target = accumulated_gain_pct > 10 × |B&H accumulated_gain_pct|**

## REPORT TEMPLATE (fill at each deadline)

### 20:00 UTC Report
- **Crypto**: ~120+ configs tested, **0** with pool_sharpe > 1.5. Best = 0.21 (1-sym only, not valid). C08 snapshot claims 0.59 on 50-sym — needs retest.
- **Tradier**: 192 configs tested on 114-sym, **0** with pool_sharpe > 1.5. Best valid = **0.5823**.
- **Root cause**: PROFIT_TARGET_PCT removal (19:04 UTC) was correct. Honest exits cap around 0.58 Sharpe currently.
- **Top-3 tradier configs**: See RESULTS_SPREADSHEET.md — WSAK + multiple exit mechanisms
- **Sweeps running**: hunt_crypto + le_partial_exit_crypto + mega_crypto_v8 (S1), le_partial_exit_tradier + mega_tradier_v8_focused + le_full_tradier + autonomous_v2 (S2)
- **Action needed**: Test LOCAL_EXTREMES + MIN_HOLD_BARS + PPL combo to push Sharpe > 1.0

### 00:00 UTC Report (filled at ~21:00 UTC — preliminary, updating through midnight)
- **Crypto**: 789 structured-sweep configs tested (max 24-sym, all invalid). Autonomous 50-sym (crypto_swarm_v2/w1): 4 valid rows, best pool_sharpe = **0.2203** (gain=3,580%, dd=117%, trades=6,727). ZERO configs with pool_sharpe > 1.5 on valid ≥48-sym runs.
- **Crypto 6-sym sub-swarm** (crypto_swarm, 6 sym × 2024-2026, 7 workers): 8 rows, best = **0.6335** (gain=5,510%, dd=2.84%, trades=7,189). NOT VALID per ≥48-sym rule — diagnostic only.
- **10x B&H Crypto target (>3,623% gain)**: iter=0 hit **5,358%** gain BUT sharpe=0.1371, dd=90.87% — terrible risk. iter=3 hit 3,580% (just under target). No config with both gain>3,623% AND sharpe>1.5.
- **Tradier**: 37,408 structured configs + 292 autonomous configs tested on 114+ symbols. Best valid pool_sharpe = **0.5823** (gain=4,181%, dd=9.65%, trades=8,078). ZERO configs with pool_sharpe > 1.5.
- **10x B&H Tradier target (>77,205% gain)**: Not in sight. Best honest gain = 4,614% (16× below target).
- **Status**: Ceiling is ~0.58 tradier / ~0.22 crypto (50-sym, 4yr). 6-sym shows 0.63 — may improve on larger sample. No architectural breakthrough yet.
- **Sweeps still running**: S1 — 7 autonomous workers (6-sym crypto) + 1 autonomous (50-sym) + structured. S2 — 4 autonomous workers (114-sym tradier) + 37 structured-sweep processes.
- **Rate**: S2 tradier autonomous: ~40s/config → ~90 configs/hour per worker (4 workers = 360/hr). S1 crypto: ~40s/config (6-sym much faster than 50-sym, multiple workers).
- **Action needed**: The 6-sym crypto 0.63 result uses different symbol set/period. Need to validate those overrides on 50-sym 4yr. Run c08 winner config without PT on 50-sym.

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
