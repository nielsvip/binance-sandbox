# v15 Morning Report — 20260930

_Generated 2026-09-30T19:55Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 157 | 09-30 03:07Z | 09-29 14:06Z | 52df25676fc2 | OK |
| s1 | s1-pub | 462 | 09-30 16:03Z | 09-29 15:28Z | 52df25676fc2 | OK |
| s2 | s2 | 371 | 09-30 09:14Z | 09-29 15:28Z | 52df25676fc2 | OK |
| s5 | s5 | 151 | 09-30 05:36Z | 09-29 14:06Z | 52df25676fc2 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 478**

Engine md5 parity across boxes: ✅ `52df25676fc2` on mac, s1, s2, s5.

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **17**
    - `LRCUSDT_LONG` [s1] 1373 switches all-zero
    - `NKNUSDT_LONG` [s1] 1373 switches all-zero
    - `NKNUSDT_SHORT` [s1] 1314 switches all-zero
    - `LRCUSDT_SHORT` [s1] 1314 switches all-zero
    - `NUKZ_LONG` [s1] 958 switches all-zero
    - `ROBO_LONG` [s1] 957 switches all-zero
    - `OXY_SHORT` [s2] 814 switches all-zero
    - `QBTS_SHORT` [s2] 703 switches all-zero
    - `TTD_SHORT` [s2] 573 switches all-zero
    - `NKE_SHORT` [s2] 568 switches all-zero
    - `MPC_LONG` [s2] 556 switches all-zero
    - `DELL_LONG` [s2] 539 switches all-zero
    - `ROKU_LONG` [s2] 488 switches all-zero
    - `HOOD_LONG` [s2] 442 switches all-zero
    - `OXY_LONG` [s2] 437 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **8**
    - `TRXUSDT_SHORT` [s1] -0.168082 ×67/103
    - `1000FLOKIUSDT_SHORT` [s1] -0.671519 ×61/91
    - `ACTUSDT_SHORT` [s1] -0.074964 ×45/90
    - `GTCUSDT_SHORT` [s1] -0.345047 ×23/34
    - `APEUSDT_LONG` [s1] -3.235419 ×22/30
    - `RGLD_SHORT` [s2] -0.12409 ×14/24
    - `OKE_LONG` [s1] -3.590969 ×12/21
    - `TXN_LONG` [s2] -6.111334 ×11/21
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **15**
    - `AMZN_SHORT` [s2]
    - `BATUSDT_SHORT` [s1]
    - `DELL_LONG` [s2]
    - `HOOD_LONG` [s2]
    - `LRCUSDT_LONG` [s1]
    - `LRCUSDT_SHORT` [s1]
    - `MPC_LONG` [s2]
    - `NKE_SHORT` [s2]
    - `NKNUSDT_LONG` [s1]
    - `NKNUSDT_SHORT` [s1]
    - `OXY_SHORT` [s2]
    - `QBTS_SHORT` [s2]
    - `ROKU_LONG` [s2]
    - `TTD_SHORT` [s2]
    - `XLE_LONG` [s2]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **3 sym_sides, 3 switch×baseline cases**
    - worst offending switches (by # sym_sides): `EMA_BLANKET_FILTER_ENABLED`×3
    - `WDAY_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=+0.355/F=-0.047
    - `PLTR_LONG` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-5.907/F=+0.177
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  109 sym_sides
- mean final gain **23.81%** · median **22.10%** · mean B&H 25.11%
- positive-gain: **106/109** · beat B&H: **60/109** · mean within-round improvement +8.27%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MINAUSDT_LONG` | 70.15 | 130.30 | +34.25 | 171 | — | — |
| 2 | `NEARUSDC_LONG` | 66.01 | 164.70 | +33.14 | 57 | — | — |
| 3 | `ARUSDT_LONG` | 60.04 | 105.59 | +5.04 | 162 | — | — |
| 4 | `COTIUSDT_LONG` | 58.12 | -1.36 | +17.52 | 203 | — | — |
| 5 | `ARBUSDC_LONG` | 53.98 | 134.97 | +28.30 | 142 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MSFTUSDT_LONG` | -0.28 | 6.14 | +3.67 | 284 | — | — |
| 2 | `NKNUSDT_LONG` | 0.00 | 0.00 | +0.00 | — | — | — |
| 3 | `LRCUSDT_LONG` | 0.00 | 0.00 | +0.00 | — | — | — |
| 4 | `BTCDOMUSDT_LONG` | 1.23 | -5.45 | +0.02 | 24 | — | — |
| 5 | `BNBUSDC_LONG` | 1.40 | 9.00 | +1.24 | — | — | — |

Biggest within-round improvements (baseline → final): `MINAUSDT_LONG` +34.25%, `RAYSOLUSDT_LONG` +34.03%, `NEARUSDC_LONG` +33.14%, `ZENUSDT_LONG` +30.59%, `ALGOUSDT_LONG` +29.49%

### CRYPTO_SHORT  ·  108 sym_sides
- mean final gain **5.47%** · median **3.30%** · mean B&H -23.68%
- positive-gain: **87/108** · beat B&H: **104/108** · mean within-round improvement +3.65%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COTIUSDT_SHORT` | 39.30 | 1.36 | +7.19 | — | — | — |
| 2 | `PTBUSDT_SHORT` | 30.89 | -15.09 | +15.35 | 127 | — | — |
| 3 | `EGLDUSDT_SHORT` | 26.23 | -17.49 | +10.04 | 149 | — | — |
| 4 | `CELRUSDT_SHORT` | 23.61 | -32.67 | +10.95 | 213 | — | — |
| 5 | `FARTCOINUSDT_SHORT` | 22.65 | 8.49 | +14.48 | 186 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COMPUSDT_SHORT` | -0.45 | -41.06 | +0.24 | 74 | — | — |
| 2 | `LTCUSDC_SHORT` | -0.45 | -37.20 | +1.94 | 10 | — | — |
| 3 | `XTZUSDT_SHORT` | -0.41 | -49.79 | +2.92 | 12 | — | — |
| 4 | `RSRUSDT_SHORT` | -0.33 | -24.09 | +0.52 | 150 | — | — |
| 5 | `1000SATSUSDT_SHORT` | -0.31 | -11.86 | +0.05 | 99 | — | — |

Biggest within-round improvements (baseline → final): `STORJUSDT_SHORT` +16.26%, `PTBUSDT_SHORT` +15.35%, `FARTCOINUSDT_SHORT` +14.48%, `QTUMUSDT_SHORT` +14.28%, `1000BONKUSDC_SHORT` +11.87%

### STOCKS_LONG  ·  129 sym_sides
- mean final gain **10.77%** · median **9.24%** · mean B&H 4.17%
- positive-gain: **117/129** · beat B&H: **96/129** · mean within-round improvement +5.85%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ALMU_LONG` | 48.26 | -11.55 | +31.56 | 99 | — | — |
| 2 | `MRVL_LONG` | 37.53 | 20.69 | +16.91 | 28 | — | — |
| 3 | `HAO_LONG` | 35.77 | -20.15 | +35.66 | 49 | — | — |
| 4 | `PBF_LONG` | 31.96 | 24.24 | +14.51 | 129 | — | — |
| 5 | `NEM_LONG` | 30.79 | 38.55 | +2.24 | 20 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ROBO_LONG` | -5.68 | -7.28 | +0.00 | — | — | — |
| 2 | `NUKZ_LONG` | -5.63 | -7.30 | +0.00 | — | — | — |
| 3 | `APO_LONG` | -3.85 | -5.81 | +0.00 | — | — | — |
| 4 | `MSFT_LONG` | -1.38 | 6.14 | -3.11 | — | — | — |
| 5 | `OKE_LONG` | -1.31 | -7.48 | -2.12 | — | — | — |

Biggest within-round improvements (baseline → final): `HAO_LONG` +35.66%, `ALMU_LONG` +31.56%, `COHR_LONG` +26.03%, `AMD_LONG` +20.97%, `CF_LONG` +18.90%

### STOCKS_SHORT  ·  132 sym_sides
- mean final gain **8.54%** · median **7.02%** · mean B&H -4.05%
- positive-gain: **122/132** · beat B&H: **112/132** · mean within-round improvement +5.27%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COE_SHORT` | 39.07 | 40.15 | +17.91 | 32 | — | — |
| 2 | `ALMU_SHORT` | 38.41 | 11.55 | +18.05 | 195 | — | — |
| 3 | `UEC_SHORT` | 33.68 | 17.07 | +0.84 | 251 | — | — |
| 4 | `COHR_SHORT` | 30.19 | 4.17 | +32.85 | 46 | — | — |
| 5 | `INTC_SHORT` | 26.48 | 13.26 | +23.21 | 103 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `PYPL_SHORT` | -0.28 | -11.88 | +2.61 | 37 | — | — |
| 2 | `QRVO_SHORT` | -0.10 | -30.67 | +0.99 | 28 | — | — |
| 3 | `BG_SHORT` | -0.03 | -8.27 | +0.00 | — | — | — |
| 4 | `XOM_SHORT` | -0.02 | -9.55 | +5.60 | 14 | — | — |
| 5 | `AMZN_SHORT` | 0.00 | 6.94 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `COHR_SHORT` +32.85%, `HAO_SHORT` +27.66%, `INTC_SHORT` +23.21%, `LAC_SHORT` +21.80%, `XLE_SHORT` +21.57%

### Day-over-day movers
_First snapshot written this run — per-sym_side day-over-day comparison starts tomorrow. (Switch/filter day-over-day is available below from the avg_delta archive.)_

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **331** produce ≥1 positive value · **0** never tested · **29** DEAD no-op (moves nothing) · **170** never help (honest losers). Total levers seen: **530**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**29 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| switch | `INTRADAY_RATIO_DEVIATION_THR` | S_LONG,S_SHORT | 4 | 772 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_DEVIATION_THR` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BB_PULLBACK_GATE_LONG_MAX` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 610 | 0 | +0.000 | +0.000 | trace `BB_PULLBACK_GATE_LONG_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BB_PULLBACK_GATE_SHORT_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 608 | 1 | +0.004 | +0.000 | trace `BB_PULLBACK_GATE_SHORT_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_CHECK_INTERVAL_MIN` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_CHECK_INTERVAL_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_TRIM_FRAC` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_TRIM_FRAC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_COOLDOWN_MIN` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_COOLDOWN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_MAX_TRIMS_PER_DAY` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_MAX_TRIMS_PER_DAY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SENTIMENT_REBAL_REDUCE_DEVIATION_THR` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `SENTIMENT_REBAL_REDUCE_DEVIATION_THR` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SENTIMENT_REBAL_COOLDOWN_MIN` | S_LONG,S_SHORT | 3 | 579 | 0 | +0.000 | +0.000 | trace `SENTIMENT_REBAL_COOLDOWN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_REBALANCE_ENABLED` | S_LONG,S_SHORT | 2 | 386 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_REBALANCE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_REQUIRE_TOP` | S_LONG,S_SHORT | 2 | 386 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_REQUIRE_TOP` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EOD_SLIM_RATIO_ENABLED` | S_LONG,S_SHORT | 2 | 386 | 0 | +0.000 | +0.000 | trace `EOD_SLIM_RATIO_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `PER_SYM_GATE_FLAT_OPEN_ENFORCE` | S_LONG,S_SHORT | 2 | 344 | 0 | +0.000 | +0.000 | trace `PER_SYM_GATE_FLAT_OPEN_ENFORCE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BOTTOM_EXIT_HTF_WT_VETO_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 302 | 0 | +0.000 | +0.000 | trace `BOTTOM_EXIT_HTF_WT_VETO_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_WT_CHURN_REENTRY_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 302 | 0 | +0.000 | +0.000 | trace `HTF_WT_CHURN_REENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_WT_CHURN_REENTRY_MAX_AGE_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 302 | 0 | +0.000 | +0.000 | trace `HTF_WT_CHURN_REENTRY_MAX_AGE_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BALANCE_FLOOR_USD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 285 | 0 | +0.000 | +0.000 | trace `BALANCE_FLOOR_USD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `CIRCUIT_BREAKER_THRESHOLD_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 285 | 0 | +0.000 | +0.000 | trace `CIRCUIT_BREAKER_THRESHOLD_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `ABSOLUTE_OPEN_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 271 | 0 | +0.000 | +0.000 | trace `ABSOLUTE_OPEN_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARD_AUGMENT_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 271 | 0 | +0.000 | +0.000 | trace `HARD_AUGMENT_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARD_REDUCE_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 271 | 0 | +0.000 | +0.000 | trace `HARD_REDUCE_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STALE_HOLD_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 271 | 0 | +0.000 | +0.000 | trace `STALE_HOLD_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `UNIVERSAL_NOLOSS_BYPASS_REASONS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 271 | 0 | +0.000 | +0.000 | trace `UNIVERSAL_NOLOSS_BYPASS_REASONS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTF_BB_REJECT_EXIT_LOOKBACK` | C_LONG,C_SHORT | 2 | 218 | 0 | +0.000 | +0.000 | trace `MTF_BB_REJECT_EXIT_LOOKBACK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GAP_RISK_EXIT_ENABLED` | S_LONG,S_SHORT | 2 | 116 | 0 | +0.000 | +0.000 | trace `GAP_RISK_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXIT_STDEV_BREAKOUT_FAIL_ENABLED` | C_LONG | 1 | 49 | 0 | +0.000 | +0.000 | trace `EXIT_STDEV_BREAKOUT_FAIL_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_VEL_DECAY_EXIT_ENABLED` | C_LONG | 1 | 49 | 0 | +0.000 | +0.000 | trace `WT_VEL_DECAY_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_REENTRY_MIN_TF` | C_LONG | 1 | 49 | 0 | +0.000 | +0.000 | trace `DELTA_REENTRY_MIN_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `VIGILANCE_MAX_LOSS_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 36 | 0 | +0.000 | +0.000 | trace `VIGILANCE_MAX_LOSS_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **170** levers (167 switches, 3 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (167):** `LR_BAND_LADDER_STOCH_EXTREME`, `DD_BOUNCE_ENABLED`, `LR_BAND_LADDER_TF_BOTTOM`, `HA_WICK_QUALITY_ENABLED`, `LH_HL_FILTER_ENABLED`, `OI_CONFIRM_ENABLED`, `BAND_ARROW_ENABLED`, `LH_HL_FILTER_REQUIRE_BOTH`, `EZ_MANAGE_THROTTLER_RATE`, `MARKET_QUALITY_SCORE_ENABLED`, `MTS_GATE_ENABLED`, `MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY`, `BB_PROFIT_TAKE_TF`, `GR_FILTER_VEC_FILTER_TF`, `BB_EXIT_AT_LOSS_TF`, `EXIT_VELOCITY_WT_TFS`, `REENTRY_POST_CONSOL_TFS_REQUIRED`, `AUGMENT_BREAKOUT_MIN_GAIN_PCT`, `HTF_GATE_APPLY_TO_AUGMENT`, `MANDATORY_REENTRY_K_HIGH_BLOCK`, `REENTRY_TIER2_MAX_MINUTES`, `AUGMENT_ONLY_WHEN_PROFITABLE`, `TREND_MIN_GAIN_EXIT`, `SCALP_V3_K_OB_EXIT_K3M_HI`, `OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED`, `WT_CROSS_EXIT_APPLIES_TO_WINNERS`, `REENTRY_B16_SMA200_PROX_PCT`, `BOUNCE_REENTRY_K_RESET_LONG`, `BTC_ROUND_BANDS_EACH_SIDE`, `DELTA_PYRAMID_PRICE_TOL`, `DELTA_REENTRY_Z_THRESHOLD`, `OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED`, `LEGACY_REENTRY_PSR_QUICK_RECOVERY`, `HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H`, `REENTRY_TIER1_SIZE_MULT`, `BTC_ACCEL_RAMP_REQUIRE_POSITIVE`, `REENTRY_WT15M_SIZE_MULT`, `SCALP_V3_K_OB_EXIT_K15M_HI`, `SCALP_V3_K_OB_EXIT_K15M_LO`, `STDEV_SUPPRESS_EARLY_EXIT`, `BTC_RZ_WT_DC_MULTIFACTOR`, `E_1_EXIT_DELTA_THR`, `MI_MIN_GAIN_EXIT`, `DELTA_PYRAMID_MAX`, `HTF_GATE_APPLY_TO_OPEN`, `WT_PERCENTILE_EXIT_OB_D`, `MACD_EXIT_MIN_GAIN`, `DC_HOPELESS_EXIT_MIN_AGE_S`, `ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC`, `HTF_EXIT_VETO_MIN_ALIGNED`, `BTC_HARD_BLOCK_OTHER_ACCOUNTS`, `WT_EXHAUST_EXIT_REQUIRE_GAIN`, `REENTRY_B16_SIZE_MULT_STRONG`, `REENTRY_EXIT_RECLAIM_BUFFER_PCT`, `WT_PERCENTILE_EXIT_OB_4H`, `AUGMENT_FALLBACK_GAIN_PCT`, `WT_PERCENTILE_EXIT_OS_4H`, `AUGMENT_BOUNCE_MIN_GAIN_PCT`, `AUGMENT_WT_4H_BOUNCE_ENABLED`, `MTF_GR_MIN_IND` …
- **filters (3):** `BB_RECOVERY_FILTER_TF`, `BB_RECOVERY_ENTRY_FILTER_TF`, `MTF_ATR_TRAIL_FILTER_TF`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

