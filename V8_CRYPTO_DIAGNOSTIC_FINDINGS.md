# V8 Crypto No-Result — Parallel Diagnostic Findings (2026-04-16)

Main thread diagnostic while agent a59a078f works the primary fix.

## 1. Actual failing config fingerprint (from S2)
File: `/home/niels/binance-sandbox/backtest_v8/sweeps/v8_sweep_crypto_t30_20260416_0034.csv`

Tier-30 baseline (`crypto_t30_00000`): NOLOSS_MIN_PROFIT_PCT=-999, STRICT_NO_LOSS=[], **ALL 5 CT gates OFF** (CT_WT_VEL, CT_15M_MOM, CT_DC_CROSS, CT_CHOP_4H, CT_VOL_SURGE). Result: `sharpe=0 pnl=0 trades=0 status=no_result elapsed=157.4s`.

## 2. Tier-30 is NOT using live defaults
Live config.py (BC_170, BC_172 applied):
- `CT_WT_VELOCITY_GATE_ENABLED = True` ← sweep forces False
- `CT_DC_CROSSOVER_SKIP_ENABLED = True` ← sweep forces False

Sweep tier-30 deliberately disables BC_170+BC_172 to test them individually. Baseline = "live minus CT filters" — should produce MORE trades, not fewer.

Other live defaults passthrough (sweep doesn't override):
- `DELTA_ENGINE_ENABLED = True`
- `STRUCTURAL_RANGE_SHIFT_EXIT = True`
- `RZ_EXIT_ENABLED = True`
- `SATOSHIT_ENABLED = True`

Local "working override" flipped RZ_EXIT + SATOSHIT OFF. Tier-30 leaves them ON. **That's the delta between working-67-trades and failing-0-trades.**

## 3. NPZ data is COMPLETE (BTCUSDT verified on S2)
Keys present: 504 total. `_W` timeframes all present (63 fields). `wt_velocity_1h` ✓, `dc_basis_crossover_1h` ✓, `relative_volume_1h/4h` ✓, `stoch_k_15m` ✓, `mfi_15m` ✓.

**Missing:**
- `adx_*` (all TFs) — would affect ADX_REGIME_FILTER_ENABLED (default False, not active)
- `choppiness_*` (all TFs) — would affect CT_CHOP_4H_GATE (default False in sweep baseline)
- `sentiment_*`, `conviction_*` — memory noted these missing; need to verify which live gates read them

NPZ is NOT the blocker for tier-30 baseline (no CT gates active).

## 4. Hardcoded ungated entry guards (ez_manage.py:180–184)
- `K3M_CAP_SHORT`: if k_3m ≤ (100 - k3m_cap)
- `K3M_FLOOR_SHORT`: if k_3m ≤ 30 (hardcoded)
- `K3M_FLOOR_LONG`: if k_3m ≥ 70 (hardcoded)

These are counter-trend protections. Most bars have k_3m in 30–70 range → entries still allowable. NOT blocking everything.

## 5. PRIME SUSPECT — conflict between SRS + RZ_EXIT + SATOSHIT + DELTA_ENGINE all simultaneously True

The "working override" was:
- STRUCTURAL_RANGE_SHIFT_EXIT = True
- DELTA_ENGINE_ENABLED = True
- RZ_EXIT_ENABLED = **False**
- SATOSHIT_ENABLED = **False**

= 67 trades / 38s / 1 symbol.

Sweep tier-30 leaves ALL FOUR True. Four exit engines competing to close the same position may interact badly — possibly one rejects every new entry pre-emptively (e.g., SATOSHIT requires an override state, or RZ_EXIT's setup prevents DELTA_ENGINE from opening).

## 6. Secondary suspect — HEDGE_MODE=True on account `inf` + single-symbol backtest

Sweep: `--mode crypto --tier 30 --account inf --start 2024-01-01`.
`HEDGE_MODE=True` is enabled for inf per CLAUDE.md. Single-symbol backtests cannot form hedge partner positions. If entry gate requires a hedge partner to exist → all entries blocked.

Agent should check: does V8 engine's `apply_patches()` disable HEDGE_MODE or stub out hedge-partner requirements for single-symbol mode?

## 7. Recommended investigation order for agent
1. Verify k_3m distribution on real bars is not pathologically outside 30–70. Sanity check.
2. Test tier-30 baseline with `RZ_EXIT_ENABLED=False` + `SATOSHIT_ENABLED=False` injected via override. If trades appear → confirms the four-exits conflict as root cause.
3. If still 0 trades, check `HEDGE_MODE` gate in V8 engine for single-symbol mode.
4. If still 0 trades, check `NEWBORN_PROTECT_ENABLED` — per backtest_v8_engine.py:1098 comment, `NEWBORN_PROTECT uses time.time()` which V8 patches. If patch is incomplete, NEWBORN_PROTECT could reject all "new" positions as age=0.
5. Last resort: add debug log inside the entry gate chain to print the FIRST rejection reason per symbol-per-bar for first 100 bars. Identify the most common rejection.

## 8. Quick validation snippet for agent

```bash
ssh s2-int "/home/niels/miniconda3/envs/binance_env/bin/python -c \"
import numpy as np
d = np.load('/home/niels/binance-sandbox/backtest_v8/indicators/BTCUSDT.npz', allow_pickle=True)
k = d['stoch_k_3m']
print('k_3m total bars:', len(k))
print('k_3m pct in 30-70:', ((k >= 30) & (k <= 70)).mean())
print('k_3m <= 30:', (k <= 30).mean(), '  >= 70:', (k >= 70).mean())
\""
```

If "pct in 30-70" is high (say 60%+), most bars allow entries → K3M gates not the root cause. If low (<30%), gates are the cause.

## 9. k_3m distribution VERIFIED on BTCUSDT (219,841 bars)

- k_3m in 30–70: **28.5%** of bars
- k_3m ≤ 30: 35.5% (SHORTS blocked by K3M_FLOOR these bars)
- k_3m ≥ 70: 36.0% (LONGS blocked by K3M_FLOOR these bars)
- wt_velocity_1h populated: 99.9% non-zero
- wt1_W populated: 99.5% non-zero

**K3M_FLOOR reduces but does not eliminate entries.** At least one direction is always allowable (when k≤30: LONG allowed; when k≥70: SHORT allowed; mid-zone: both allowed). NOT a 0-trade cause on its own.

**NPZ field population is healthy.** W-timeframe + wt_velocity_1h both populated >99%. Rules out missing-data hypothesis.

## 10. Conclusion — #1 prime suspect

With NPZ proven healthy and K3M_FLOOR partial (not total) — **the sweep configs leaving RZ_EXIT_ENABLED + SATOSHIT_ENABLED + SRS + DELTA_ENGINE all True simultaneously** is the most likely root cause. The working local override specifically flipped RZ_EXIT and SATOSHIT off. Agent should test injecting those overrides into one tier-30 config and see if trades emerge.

If that confirms → the true bug is multi-exit-engine conflict. Fix options:
- Disable RZ_EXIT + SATOSHIT in apply_patches() for backtest mode, OR
- Fix the conflict in the exit engines themselves (live code change, needs user approval), OR
- Add a sweep override that makes tier-30 explicit about which exit engine to test.

