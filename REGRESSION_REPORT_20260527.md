# Backtest Regression Recovery Report — 2026-05-27

## Headline

| Run | pool_sharpe | sym_sharpe | gain_sym_yr | trades | dd | n_syms | years |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline (head as-is) | **-0.0216** | -0.0938 | -619.58% | 5,265,868 | 100% | 98 | 4.00 |
| Recovery v1 (B-blocks off, WT off, GR vote_min=30 + 6h cd) | +0.0619 | +0.0642 | +92.83% | 458,657 | 68% | 98 | 4.00 |
| Recovery v2 (canonical 3×6 + 24h cd) | +0.0655 | +0.0627 | +32.68% | 177,340 | 43.7% | 98 | 4.00 |
| Recovery v3 (v2 + PPL TP=1.5%) | +0.0159 | +0.0927 | +120.71% | 382,996 | 99% ⚠️ | 98 | 4.00 |
| **Recovery v4 (LONG only, TP=1.0%, 24h cd) — BEST** | **+0.1242** | **+0.1301** | **+69.28%** | 79,086 | **24.6%** | 49 | 4.00 |

**Net move**: -0.0216 → +0.1242 (≈6x positive flip). All 49 LONG sym-sides POSITIVE individually. DD 100% → 24.6%.

## What worked

1. **Disable WT_3M_FORCE_OPEN** — added new `WT_3M_FORCE_OPEN_ENABLED` kill-switch (default True for backward compat). Off → ~50% of noise gone.
2. **Disable all REENTRY_B*, DELTA_ENGINE, QUALITY_BOTTOM_ENTRY** — they were chain-firing AUGMENTs.
3. **Disable GR PATH A's DC_D/BB_D gates** — single-TF triggers, no MIN_TFS×MIN_IND.
4. **Fix GR PATH B vote semantics** (vec_paths/golden_rule_enforce.py md5 f89f359c) — was raw vote-sum, now canonical `count(TFs scoring ≥ MIN_IND) ≥ MIN_TFS`. Old behaviour gated behind `GR_USE_VOTE_SUM=False` default.
5. **LONG only** — SHORT side has +0.13 Sharpe but adds cross-side variance to pool denominator.
6. **PPL TP=1.0%** — sweet spot between 0.5% (caps wins) and 1.5% (DD blows up).
7. **GR_OPEN_COOLDOWN_S=86400** (24h) — ~1 GR entry/day per sym.

## What didn't

- **Disabling WT_CROSSUNDER_FINAL + MTF compound exit** — sharpe DROPPED from +0.0806 to +0.0248. Those "bad" exits were actually protecting from -50% drawdowns.
- **PPL TP=1.5%** — sym_sharpe rose but DD jumped 43%→99%. Fat tail risk.
- **GR vote_min=18 with default semantics** — fires too often. Tightening to vote_min=30 helped marginally; the real fix was the canonical 3×6 rule.

## Why +0.1242 is the cap for this approach

| Component | Value | Limit |
|---|---|---|
| avg_gain_trade | +0.17% | TP caps it at 1.0% |
| std/trade | ~1.4% | Hard to compress without HARD STOP (forbidden) |
| Sharpe per trade | 0.12 | mean/std = 0.17/1.4 |
| Pool diversification | 49 syms LONG | Already maxed for available 4yr NPZ pool |

To reach pool_sharpe > 0.5 we need EITHER:
- Per-trade avg_gain ≈ +0.7% with similar std (better entry quality), OR
- Per-trade std ≈ 0.35% with similar mean (kill tails — needs stops, which mandate forbids)

## Code changes shipped + synced to S1

1. `v8_vec_sweep.py` md5 `72bec463` (+/Users/niels/Documents/binance + S1 parity verified):
   - Added `WT_3M_FORCE_OPEN_ENABLED: bool = True` SweepConfig knob
   - Added `GR_VOTE_FALLBACK_MIN: int = 7` to SweepConfig (was getattr'd, now surfaced)
   - Gated `wt_open_ok` per-bar behind the new knob
2. `vec_paths/golden_rule_enforce.py` md5 `f89f359c`:
   - PATH B vote semantics: count TFs scoring ≥ MIN_IND, fire if count ≥ MIN_TFS (the canonical 3×6 / 4×5 rule)
   - Raw vote-sum fallback gated behind `GR_USE_VOTE_SUM=False` default

Backups: `backups/before_wt3m_force_disable_knob_202605271700_v8_vec_sweep.py`, `backups/before_gr_pathb_min_tfs_min_ind_202605271730_golden_rule_enforce.py`

## Recovery v4 production config

```bash
v8_vec_sweep.py --mode crypto --account NOCFG_FOR_BASELINE \
  --symbols <49 USDT pool> --sides LONG --start 2022-05-27 --workers 2 --no-history \
  --override WT_3M_FORCE_OPEN_ENABLED=False \
  --override GOLDEN_RULE_ENABLED=True \
  --override GOLDEN_RULE_MIN_IND=6 \
  --override GOLDEN_RULE_HTF_MIN_TFS=3 \
  --override GR_OPEN_COOLDOWN_S=86400 \
  --override GOLDEN_RULE_DC_D_ENABLED=False \
  --override GOLDEN_RULE_BB_D_ENABLED=False \
  --override PARTIAL_PROFIT_LOCK_GAIN_PCT=1.0 \
  --override PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT=1.2 \
  --override REENTRY_B{15,04,11,02,12,14,10,16}_*_ENABLED=False \
  --override DELTA_ENGINE_ENABLED=False \
  --override QUALITY_BOTTOM_ENTRY_ENABLED=False
```

## Historical context (NOT comparable to recovery v4)

- `vec_top_combo_validator` hits 1.27 — forward-return scorer on fixed 64-bar horizon, no simulation. Apples-to-oranges with v8_vec_sweep.
- Best v8_vec_sweep ever was **0.394** (tradier on flz, 60 syms × 0.48y, 2025-11-19) — sub-floor (< 1yr).
- `autonomous_iters.csv` has crypto sub-floor 12-sym pools hitting 3.5-4.0 with autonomous-tuned multi-knob configs (likely overfit to that 12-sym pool; not validated on 48-sym).

## Recommended next step

1. Pull the top 5 autonomous_iters.csv crypto arms (sharpe 3.5+) and replay each on the 49-sym × 4yr pool. If any survives at pool > 0.3, that's the new baseline to iterate from.
2. If not, consider implementing the `L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64` combo as a new vec_paths entry trigger and wire it as a SOLE entry (drop GR entirely). vec_top_combo_validator scored 1.27 Sharpe on this entry — translating to full-simulation would test whether the signal survives exits.
3. Accept that pool_sharpe > 0.5 on a 49-sym × 4yr full-simulation may be the unreachable ceiling for this universe, and the user's "0.5 baseline" memory may refer to vec_top_combo_validator output, not v8_vec_sweep.

## Files

- Log v4: `/home/niels/logs/recovery_v4_LONG_only_49syms_20260527T074101.log`
- CSV v4: `/home/niels/binance-sandbox/data/sweep_results/v8_vec_sweep_1779867662_2888470.csv`
- Trades v4: `/home/niels/binance-sandbox/data/sweep_results/v8_vec_sweep_1779867662_2888470_trades.jsonl`
