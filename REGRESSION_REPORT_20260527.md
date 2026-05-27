# Backtest Regression Report — 2026-05-27 04:35 UTC

## Current state

**4yr × 49-sym crypto baseline (just completed, 50.8 min run)**:

```
pool_sharpe   = -0.0216   (Discard tier)
sym_sharpe    = -0.0938
avg_gain_trade= -0.0461 % / trade
gain_per_yr   = -60,719.2 % / yr   (sum across 49 syms × 2 sides)
gain_sym_yr   = -619.58  % / sym / yr
trades        = 5,265,868
max_dd_pct    = 100.0    (every account zeroed)
n_syms        = 98       (49 syms × 2 sides)
years         = 4.00
```

Engine: `v8_vec_sweep.py` md5 `5d4cbff0` (post-revert head).
Universe: 49 legacy USDT syms (only NPZ pool with 4yr+ depth).
Pool: `1INCH,ALGO,ANKR,ATOM,AXS,BAND,BAT,BEL,BTCDOM,C98,CELR,CHR,COMP,COTI,DASH,DOT,EGLD,ENJ,ETC,GRT,GTC,HOT,IOST,IOTA,IOTX,KAVA,KNC,KSM,LRC,MANA,MTL,NKN,QTUM,RSR,RVN,SAND,SKL,SNX,STORJ,SUSHI,SXP,THETA,TRX,VET,XLM,XMR,XTZ,YFI,ZEN` × USDT.

CSV: `/home/niels/binance-sandbox/data/sweep_results/v8_vec_sweep_1779854176_596256.csv`
Log: `/home/niels/logs/baseline_4yr_49syms_20260527T035615.log`

## Diagnosis

### 1. Hyperactivity (root cause)
5.27M trades / 49 syms / 2 sides / 4 yr = **13,400 trades / sym / side / yr ≈ 36 / day per side**. Average per-trade gain -0.046%, summed across 5.27M trades = -242,000% cumulative = wiped to 100% DD.

### 2. Default-ON entry triggers firing simultaneously
`SweepConfig` defaults that ALL fire by default:
- `REENTRY_B15_STRONG_TREND_ENABLED=True`
- `REENTRY_B04_DC_RETEST_ENABLED=True`
- `REENTRY_B11_DC_BREAK_ENABLED=True`
- `REENTRY_B02_BC156_BOTTOM_ENABLED=True`
- `REENTRY_B12_WT_MOM_ENABLED=True`
- `REENTRY_B14_HA_TREND_ENABLED=True`
- `REENTRY_B10_STOCH_REV_ENABLED=True`
- `REENTRY_B16_MIDRANGE_ENABLED=True`
- `DELTA_ENGINE_ENABLED=True`
- `QUALITY_BOTTOM_ENTRY_ENABLED=True`
- `GOLDEN_RULE_ENABLED=True` (PATH A + PATH B)
- `WT_3M_FORCE_OPEN` — no _ENABLED guard, fires whenever 3m alignment present

### 3. Override pipeline drops 90 of 199 per-sym knobs
Baseline log: `V8_VEC_OVERRIDES: BTCDOMUSDT_LONG applied 109 / unknown 90`
Final summary: `V8_VEC_UNKNOWN_KNOBS: 90 keys not on SweepConfig` — includes load-bearing flags like `BTC_DEDICATED_ENABLED`, `BB_SQUEEZE_ENABLED`, `FUNDING_GATE_ENABLED`, `NOLOSS_ENABLED`, `V8_ENTRY_ENGINE_DC_ENABLED`, `V8_ENTRY_ENGINE_WT_ENABLED`. These are silently no-op'd because `SweepConfig` dataclass doesn't declare them. (Memory's "setattr-all" fix from 2026-05-25 only applies to per_sym_vec_engine_crypto, NOT to v8_vec_sweep's CLI override path.)

### 4. The "1.13-1.27 PUBLISHABLE" memory entries are NOT v8_vec_sweep
They come from `vec_top_combo_validator.py` — a forward-return scorer that takes a signal mask + fixed horizon (e.g. 64 bars) and computes the Sharpe of returns assuming you HOLD every signal for exactly that horizon. No exits, no augments, no hedges, no stops. Best arm right now: `L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64` → `pool_sharpe=1.2685` (56 syms × 1.22y, 3910 trades, 98.8% WR). Apples-to-oranges with v8_vec_sweep.

## Recovery plan

1. **Smoke (in flight)**: 3 syms × 4yr, all REENTRY_B* + DELTA + QUALITY_BOTTOM disabled, account name with no active_config — only `GOLDEN_RULE_ENABLED` + WT_3M_FORCE_OPEN active. ETA ~3 min. If pool_sharpe > 0 → run full 49-sym × 4yr.

2. **If smoke positive**: tighten GR PATH B vote semantics from raw vote-sum (`GR_VOTE_FALLBACK_MIN=7`) to `count(TFs where score>=GOLDEN_RULE_MIN_IND) >= GOLDEN_RULE_HTF_MIN_TFS` (the canonical 3×5 / 4×5 / 3×6 rule).

3. **If still negative**: also disable WT_3M_FORCE_OPEN by gating to base TF + at least 1 HTF (cheapest cut to ~50% of the noise).

4. **Stretch**: convert MOMENTUM_BREAKOUT / BB_BREAKOUT / BREAKOUT_RETEST / DC_BREAK from entry triggers to gate-flags (per user mandate: breakouts FLAG entries/exits, never fire them).

## Open gaps not yet addressed

- 90 unknown knobs silently dropped — needs SweepConfig schema expansion or override pipeline refactor.
- Active_config.json overrides may amplify hyperactivity (16 syms have per-(sym,side) overrides for flz; 109 knobs applied per sym). Not yet quantified.
- GR PATH B HTF vote threshold `GR_VOTE_FALLBACK_MIN=7` is too low for the per-TF score function — should bind to MIN_TFS × MIN_IND product.
