# v15 Morning Report — 20261007

_Generated 2026-10-07T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 33 | 10-07 06:17Z | 10-06 08:21Z | 0c621913b1ce | OK |
| s1 | s1-int | 33 | 10-07 06:17Z | 10-06 08:21Z | 70b30c6bb3b9 | OK |
| s2 | s2 | 44 | 10-07 04:02Z | 10-06 08:26Z | ce15973ab640 | OK |
| s5 | s5 | 43 | 10-07 04:02Z | 10-06 09:03Z | ce15973ab640 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 114**

> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.
> - mac: `0c621913b1ce`
> - s1: `70b30c6bb3b9`
> - s2: `ce15973ab640`
> - s5: `ce15973ab640`

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **16**
    - `WLDUSDC_SHORT` [s5] 1136 switches all-zero
    - `COE_LONG` [s5] 756 switches all-zero
    - `IBIT_SHORT` [mac] 463 switches all-zero
    - `TTD_LONG` [s2] 211 switches all-zero
    - `RS_LONG` [s2] 158 switches all-zero
    - `AMAT_LONG` [s2] 154 switches all-zero
    - `LSCC_SHORT` [s5] 145 switches all-zero
    - `TSLA_SHORT` [s2] 142 switches all-zero
    - `LSCC_LONG` [s5] 117 switches all-zero
    - `A_SHORT` [s2] 111 switches all-zero
    - `SNDK_LONG` [mac] 55 switches all-zero
    - `MDT_SHORT` [s2] 52 switches all-zero
    - `MDT_LONG` [s2] 46 switches all-zero
    - `TSLA_LONG` [s2] 44 switches all-zero
    - `DINO_SHORT` [s2] 41 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **0**
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **19**
    - `1INCHUSDT_SHORT` [mac]
    - `ADAUSDC_SHORT` [s5]
    - `ALB_LONG` [s2]
    - `ALGOUSDT_SHORT` [s5]
    - `ARBUSDC_SHORT` [s5]
    - `AXON_SHORT` [mac]
    - `AXSUSDT_SHORT` [s5]
    - `BNBUSDC_SHORT` [mac]
    - `BTCDOMUSDT_SHORT` [s5]
    - `BWXT_SHORT` [s2]
    - `CHRUSDT_SHORT` [s5]
    - `DASHUSDT_SHORT` [s5]
    - `HD_LONG` [s5]
    - `MSTR_SHORT` [mac]
    - `SNDK_SHORT` [mac]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **9 sym_sides, 9 switch×baseline cases**
    - worst offending switches (by # sym_sides): `RZ_BREAKOUT_ENTRY_ENABLED`×1, `OI_CONFIRM_ENABLED`×1, `FUNDING_CROWD_ENTRY_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `EMA_9_21_FILTER_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `EXIT_VELOCITY_WT_ENABLED`×1, `HTF_GATE_SIGNALS_SMA200D`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `BNBUSDC_LONG` [mac] 1 cases — RZ_BREAKOUT_ENTRY_ENABLED T=+1.049/F=+1.642
    - `WLDUSDC_LONG` [mac] 1 cases — OI_CONFIRM_ENABLED T=+3.958/F=+0.617
    - `RLCUSDT_LONG` [s2] 1 cases — FUNDING_CROWD_ENTRY_ENABLED T=-8.272/F=+12.442
    - `XRPUSDC_LONG` [mac] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.383/F=+1.381
    - `BTCUSDC_LONG` [mac] 1 cases — EMA_9_21_FILTER_ENABLED T=+0.004/F=-0.066
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `UNIUSDC_LONG` [s2] 1 cases — EXIT_VELOCITY_WT_ENABLED T=+0.885/F=-4.426
    - `BSVUSDT_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-4.101/F=+2.691
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  20 sym_sides
- mean final gain **15.84%** (Δ +7.63 vs prev) · median **11.26%** · mean B&H 20.36%
- positive-gain: **17/20** · beat B&H: **7/20** · mean within-round improvement +20.81% (Δ +9.09 vs prev)
- prev snapshot (20261006): n=21 mean_final=8.21%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `XTZUSDT_LONG` | 71.06 | 47.61 | +73.11 | 531 | — | — |
| 2 | `RLCUSDT_LONG` | 53.99 | 18.52 | +63.32 | 463 | — | — |
| 3 | `ALGOUSDT_LONG` | 30.93 | 49.08 | +33.08 | 288 | — | — |
| 4 | `THETAUSDT_LONG` | 29.18 | 31.73 | +28.79 | 510 | — | — |
| 5 | `AAVEUSDC_LONG` | 27.00 | 36.92 | +19.33 | 294 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AVAXUSDC_LONG` | -5.88 | 52.47 | +0.00 | — | — | — |
| 2 | `BSVUSDT_LONG` | -3.83 | 26.81 | +9.71 | 346 | — | — |
| 3 | `ETCUSDT_LONG` | -2.04 | 14.56 | +7.21 | 381 | — | — |
| 4 | `BNBUSDC_LONG` | 0.34 | 3.80 | +3.62 | 254 | — | — |
| 5 | `YFIUSDT_LONG` | 1.12 | 9.81 | +7.77 | 331 | — | — |

Biggest within-round improvements (baseline → final): `XTZUSDT_LONG` +73.11%, `RLCUSDT_LONG` +63.32%, `ALGOUSDT_LONG` +33.08%, `UNIUSDC_LONG` +30.98%, `THETAUSDT_LONG` +28.79%

### CRYPTO_SHORT  ·  25 sym_sides
- mean final gain **1.61%** (Δ +1.82 vs prev) · median **0.00%** · mean B&H -16.68%
- positive-gain: **8/25** · beat B&H: **17/25** · mean within-round improvement +4.15% (Δ +2.72 vs prev)
- prev snapshot (20261006): n=33 mean_final=-0.21%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZENUSDT_SHORT` | 30.80 | -35.69 | +33.70 | 245 | — | — |
| 2 | `RVNUSDT_SHORT` | 8.10 | 24.06 | +14.99 | — | — | — |
| 3 | `WLDUSDC_SHORT` | 7.24 | -40.00 | +7.24 | — | — | — |
| 4 | `1000PEPEUSDC_SHORT` | 5.72 | -16.83 | +10.09 | 206 | — | — |
| 5 | `DOTUSDT_SHORT` | 3.73 | -14.26 | +3.73 | 230 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `YFIUSDT_SHORT` | -7.53 | -9.81 | +0.05 | 281 | — | — |
| 2 | `RENDERUSDT_SHORT` | -6.12 | -35.19 | +0.00 | — | — | — |
| 3 | `AVAXUSDC_SHORT` | -3.38 | -52.47 | +0.90 | 148 | — | — |
| 4 | `ETCUSDT_SHORT` | -0.70 | -14.56 | +4.68 | 501 | — | — |
| 5 | `BSVUSDT_SHORT` | -0.25 | -26.81 | +4.49 | 288 | — | — |

Biggest within-round improvements (baseline → final): `ZENUSDT_SHORT` +33.70%, `RVNUSDT_SHORT` +14.99%, `RLCUSDT_SHORT` +14.01%, `1000PEPEUSDC_SHORT` +10.09%, `IOTAUSDT_SHORT` +8.65%

### STOCKS_LONG  ·  32 sym_sides
- mean final gain **3.00%** (Δ -0.82 vs prev) · median **0.05%** · mean B&H 3.24%
- positive-gain: **16/32** · beat B&H: **14/32** · mean within-round improvement +4.66% (Δ -1.26 vs prev)
- prev snapshot (20261006): n=55 mean_final=3.82%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AMD_LONG` | 20.62 | 31.75 | +20.27 | 25 | — | — |
| 2 | `DELL_LONG` | 20.39 | 25.53 | +16.86 | 30 | — | — |
| 3 | `IBIT_LONG` | 18.06 | 30.25 | +14.19 | 31 | — | — |
| 4 | `NVDA_LONG` | 17.40 | 3.79 | +18.24 | — | — | — |
| 5 | `MSFT_LONG` | 7.13 | 7.36 | +10.50 | 11 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `QBTS_LONG` | -15.68 | -22.39 | +0.00 | — | — | — |
| 2 | `COE_LONG` | -5.44 | -35.65 | +0.00 | — | — | — |
| 3 | `MDT_LONG` | -2.53 | -5.81 | +0.00 | — | — | — |
| 4 | `RDDT_LONG` | -2.15 | -1.53 | +2.66 | 74 | — | — |
| 5 | `BABA_LONG` | -1.34 | -3.45 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `AMD_LONG` +20.27%, `NVDA_LONG` +18.24%, `DELL_LONG` +16.86%, `SLV_LONG` +14.58%, `IBIT_LONG` +14.19%

### STOCKS_SHORT  ·  37 sym_sides
- mean final gain **6.40%** (Δ -1.19 vs prev) · median **4.26%** · mean B&H -1.39%
- positive-gain: **24/37** · beat B&H: **28/37** · mean within-round improvement +9.48% (Δ -0.36 vs prev)
- prev snapshot (20261006): n=51 mean_final=7.59%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AXON_SHORT` | 31.83 | 34.40 | +31.83 | — | — | — |
| 2 | `BABA_SHORT` | 31.37 | 16.69 | +29.52 | 37 | — | — |
| 3 | `UUUU_SHORT` | 27.80 | 27.21 | +5.23 | 14 | — | — |
| 4 | `ALB_SHORT` | 23.04 | 26.73 | +0.00 | — | — | — |
| 5 | `HD_SHORT` | 17.46 | 15.75 | +6.45 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `BMNR_SHORT` | -16.28 | -17.20 | +1.89 | 10 | — | — |
| 2 | `TSLA_SHORT` | -6.45 | -8.81 | +0.00 | — | — | — |
| 3 | `LSCC_SHORT` | -6.41 | -15.52 | +0.00 | — | — | — |
| 4 | `A_SHORT` | -6.24 | -7.72 | +0.00 | — | — | — |
| 5 | `DINO_SHORT` | -5.85 | -19.27 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `COIN_SHORT` +37.13%, `AXON_SHORT` +31.83%, `CRWD_SHORT` +30.80%, `BABA_SHORT` +29.52%, `SNOW_SHORT` +22.16%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `MU_LONG` | STOCKS_LONG | 28.08 | 4.64 | -23.44 |
| `SLV_LONG` | STOCKS_LONG | 16.13 | 0.00 | -16.13 |
| `LSCC_SHORT` | STOCKS_SHORT | 3.47 | -6.41 | -9.88 |
| `VLO_LONG` | STOCKS_LONG | 9.05 | 2.24 | -6.80 |
| `SNOW_SHORT` | STOCKS_SHORT | 2.05 | -0.42 | -2.47 |
| `AXON_SHORT` | STOCKS_SHORT | 32.28 | 31.83 | -0.46 |
| `ALGOUSDT_LONG` | CRYPTO_LONG | 30.93 | 30.93 | +0.00 |
| `AVAXUSDC_LONG` | CRYPTO_LONG | -5.88 | -5.88 | +0.00 |
| `1000PEPEUSDC_LONG` | CRYPTO_LONG | 1.21 | 11.98 | +10.77 |
| `RVNUSDT_SHORT` | CRYPTO_SHORT | -6.89 | 8.10 | +14.99 |
| `DASHUSDT_LONG` | CRYPTO_LONG | 0.66 | 15.87 | +15.21 |
| `ZENUSDT_SHORT` | CRYPTO_SHORT | 12.05 | 30.80 | +18.75 |
| `AMD_LONG` | STOCKS_LONG | -0.13 | 20.62 | +20.75 |
| `AAVEUSDC_LONG` | CRYPTO_LONG | 4.41 | 27.00 | +22.60 |
| `THETAUSDT_LONG` | CRYPTO_LONG | -4.06 | 29.18 | +33.24 |
| `XTZUSDT_LONG` | CRYPTO_LONG | 3.27 | 71.06 | +67.79 |

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **514** produce ≥1 positive value · **0** never tested · **2** DEAD no-op (moves nothing) · **201** never help (honest losers). Total levers seen: **717**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**2 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| switch | `STOCH_CROSS_1H_EXIT_ENABLED` | S_SHORT | 2 | 2 | 1 | +0.001 | -0.001 | trace `STOCH_CROSS_1H_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FORMATION_TRIANGLE_EXIT_ENABLED` | S_SHORT | 1 | 1 | 0 | -0.000 | -0.000 | trace `FORMATION_TRIANGLE_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **201** levers (198 switches, 3 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (198):** `BBKC_ENTRY_ENABLED`, `OI_SURGE_ENTRY_ENABLED`, `BOUNCE_AUGMENT_ENABLED`, `MOMENTUM_SMA_WATCHDOG_ENABLED`, `ALL_TF_AGAINST_CLOSE_MIN_TFS`, `AUGMENT_BULL_KILL_ENABLED`, `SHORT_DC_LOW_BREAK_SIZE_MULT`, `CLENOW_ENABLED`, `MODE`, `LR_BAND_REGIME_ENABLED`, `HTF_TREND_VETO_BYPASS_REASONS`, `COOLDOWN_LOCKS_FILTER_TF`, `BTC_ROUND_BANDS_EACH_SIDE`, `BTC_ACCEL_RAMP_REQUIRE_POSITIVE`, `MTS_GATE_ENABLED`, `EXECUTE_NOW_SINGLE_GATE_ENFORCE`, `HA_WICK_QUALITY_TF`, `FUNDING_GATE_FILTER_TF`, `LR_BAND_LADDER_TF_TOP`, `HTF_TREND_VETO_BYPASS_ENABLED`, `DUP_GUARD_FILTER_TF`, `BTC_HARD_BLOCK_OTHER_ACCOUNTS`, `GOLDEN_RULE_BASE_USD`, `FUNDING_GATE_LONG_MAX`, `LEADERBOARD_FILTER`, `MTF_FILTER_STRONG_BUY_QUICK_BYPASS`, `BAND_ARROW_ENABLED`, `MTS_BOTTOM_BONUS_THRESHOLD`, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`, `FUNDING_GATE_MTF_REQUIRED`, `HA_WICK_QUALITY_ENABLED`, `CHOP_RANGING_THRESHOLD`, `DELTA_REENTRY_FILTER_ENABLED`, `WT_DC_LONG_ENABLED`, `WT_D_EXHAUST_GATE_ENABLED`, `AUGMENTED_POSITIONS_GUARD_FLOOR_MULT`, `AUGMENT_WT_4H_BOUNCE_ENABLED`, `DYNAMIC_SCORE_AUGMENT_ENABLED`, `DELTA_PYRAMID_MAX`, `SCALP_V3_AUG_BE_STOP_ENABLED`, `AUGMENT_FALLBACK_REDUCE_PCT`, `AUGMENT_FALLBACK_REDUCE_ENABLED`, `ENTRY_SCORE_THRESHOLD`, `SCALP_V3_K_OB_EXIT_WALL_PCT`, `TREND_MIN_GAIN_EXIT`, `SCALP_V3_K_OB_EXIT_K3M_HI`, `STDEV_SUPPRESS_EARLY_EXIT`, `ENTRY_PRIMARY_TF`, `SCALP_V3_K_OB_EXIT_K3M_LO`, `PARTIAL_EXIT_FRAC`, `SCALP_V3_K_OB_EXIT_K15M_LO`, `REGIME_TRENDING_EXIT_GAIN_MIN`, `SCALP_V3_K_OB_EXIT_K15M_HI`, `WT_15M_CROSS_ENTRY_ENABLED`, `V8_ENTRY_ENGINE_WT_ENABLED`, `BTC_BREAKOUT_ENTRY_ENABLED`, `V8_ENTRY_ENGINE_DC_ENABLED`, `REVERSE_ON_EXIT_ENABLED`, `MACD_ZERO_CROSS_ENABLED`, `HAIKU_ENTRY_GATE_ENABLED` …
- **filters (3):** `REENTRY_15M_DC_BASIS_CROSS_HTF`, `REENTRY_15M_LRL_PULLBACK_HTF`, `REENTRY_15M_BB1H_LOW_BOUNCE_HTF`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions

_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_

### live-faithful (backtest_v12_engine) coverage: **0** sym_sides today
- parity gaps (vec vs live-faithful): **0**

### Promotions made today (bold defaults, cat_side_promotions.json)
_none today_

