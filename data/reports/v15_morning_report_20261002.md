# v15 Morning Report — 20261002

_Generated 2026-10-02T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 0 | — | — | 4c3f72a4ce9d | no rows in window |
| s1 | s1-int | 241 | 10-02 13:59Z | 10-01 08:40Z | ecacb3be0f20 | OK |
| s2 | s2 | 299 | 10-02 14:04Z | 10-01 08:31Z | ecacb3be0f20 | OK |
| s5 | s5 | 252 | 10-02 12:34Z | 10-01 08:23Z | ecacb3be0f20 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 326**

> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.
> - mac: `4c3f72a4ce9d`
> - s1: `ecacb3be0f20`
> - s2: `ecacb3be0f20`
> - s5: `ecacb3be0f20`

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **6**
    - `GOOGLUSDT_SHORT` [s1] 1086 switches all-zero
    - `MRVLUSDT_LONG` [s2] 737 switches all-zero
    - `QNTUSDT_LONG` [s2] 736 switches all-zero
    - `GOOGLUSDT_LONG` [s1] 733 switches all-zero
    - `MRVLUSDT_SHORT` [s2] 724 switches all-zero
    - `QNTUSDT_SHORT` [s2] 721 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **3**
    - `BABAUSDT_LONG` [s1] -2.916162 ×159/306
    - `AAPLUSDT_LONG` [s5] -0.015091 ×103/197
    - `UUUU_LONG` [s2] -2.505589 ×67/118
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **1**
    - `HII_LONG` [s2]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **8 sym_sides, 8 switch×baseline cases**
    - worst offending switches (by # sym_sides): `HTF_GATE_D_MANDATORY`×8
    - `HYPEUSDT_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-14.093/F=-11.719
    - `GRTUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-10.714/F=-8.402
    - `QTUMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-13.049/F=-5.810
    - `XMRUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-22.278/F=-15.768
    - `THETAUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-17.343/F=-13.172
    - `XLMUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.869/F=+0.129
    - `KASUSDT_LONG` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-29.762/F=-15.745
    - `SANDUSDT_LONG` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-7.397/F=-8.473
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  63 sym_sides
- mean final gain **24.93%** (Δ -8.35 vs prev) · median **20.14%** · mean B&H 35.78%
- positive-gain: **62/63** · beat B&H: **29/63** · mean within-round improvement +10.88% (Δ -3.24 vs prev)
- prev snapshot (20261001): n=110 mean_final=33.27%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZECUSDC_LONG` | 84.11 | 59.83 | +52.60 | 127 | — | — |
| 2 | `ARUSDT_LONG` | 80.79 | 89.39 | +62.57 | 309 | — | — |
| 3 | `ZENUSDT_LONG` | 57.47 | 31.18 | +13.86 | 178 | — | — |
| 4 | `MOVRUSDT_LONG` | 55.84 | 268.19 | +29.57 | 178 | — | — |
| 5 | `DASHUSDT_LONG` | 54.20 | 38.07 | +6.62 | 120 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AAPLUSDT_LONG` | -0.57 | 1.74 | +3.22 | 125 | — | — |
| 2 | `BTCDOMUSDT_LONG` | 0.01 | -4.45 | +0.32 | 11 | — | — |
| 3 | `GRAMUSDT_LONG` | 2.96 | 8.72 | +1.28 | 143 | — | — |
| 4 | `GOOGLUSDT_LONG` | 3.15 | 5.06 | +0.00 | — | — | — |
| 5 | `ADAUSDC_LONG` | 4.01 | 25.66 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `ARUSDT_LONG` +62.57%, `ZECUSDC_LONG` +52.60%, `AXSUSDT_LONG` +33.98%, `AAVEUSDC_LONG` +33.24%, `MOVRUSDT_LONG` +29.57%

### CRYPTO_SHORT  ·  63 sym_sides
- mean final gain **9.43%** (Δ -1.50 vs prev) · median **7.31%** · mean B&H -27.90%
- positive-gain: **59/63** · beat B&H: **60/63** · mean within-round improvement +5.11% (Δ -1.64 vs prev)
- prev snapshot (20261001): n=109 mean_final=10.93%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COTIUSDT_SHORT` | 46.60 | -9.61 | +16.18 | 79 | — | — |
| 2 | `WLDUSDC_SHORT` | 31.74 | -40.00 | +5.48 | 68 | — | — |
| 3 | `NEARUSDC_SHORT` | 27.28 | -100.00 | +0.00 | — | — | — |
| 4 | `KASUSDT_SHORT` | 26.19 | -58.48 | +6.38 | 43 | — | — |
| 5 | `FILUSDC_SHORT` | 23.69 | -52.16 | +11.52 | 174 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `BABAUSDT_SHORT` | -5.17 | 5.29 | +0.00 | — | — | — |
| 2 | `DASHUSDT_SHORT` | -1.45 | -38.07 | +0.00 | — | — | — |
| 3 | `QNTUSDT_SHORT` | -0.42 | -100.00 | +0.00 | — | — | — |
| 4 | `GOOGLUSDT_SHORT` | -0.13 | -5.06 | +0.00 | — | — | — |
| 5 | `IOTAUSDT_SHORT` | 0.04 | -36.74 | +0.02 | 18 | — | — |

Biggest within-round improvements (baseline → final): `GRTUSDT_SHORT` +22.26%, `ADBEUSDT_SHORT` +18.28%, `TRUMPUSDC_SHORT` +17.03%, `COTIUSDT_SHORT` +16.18%, `MOVRUSDT_SHORT` +12.20%

### STOCKS_LONG  ·  100 sym_sides
- mean final gain **15.14%** (Δ +1.41 vs prev) · median **9.75%** · mean B&H -1.52%
- positive-gain: **98/100** · beat B&H: **94/100** · mean within-round improvement +5.63% (Δ -5.51 vs prev)
- prev snapshot (20261001): n=116 mean_final=13.73%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MSTR_LONG` | 74.71 | 67.89 | +9.67 | 186 | — | — |
| 2 | `AXTI_LONG` | 71.43 | -4.86 | +40.63 | 85 | — | — |
| 3 | `MRVL_LONG` | 58.81 | 12.64 | +22.57 | 81 | — | — |
| 4 | `COHR_LONG` | 53.33 | 9.94 | +14.19 | 124 | — | — |
| 5 | `GME_LONG` | 43.59 | 36.87 | +16.38 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `HD_LONG` | -0.01 | -15.75 | +0.30 | 19 | — | — |
| 2 | `HII_LONG` | 0.00 | -14.79 | +0.00 | — | — | — |
| 3 | `LOW_LONG` | 0.58 | -14.61 | +0.08 | 121 | — | — |
| 4 | `MCD_LONG` | 0.89 | -13.14 | +0.01 | 28 | — | — |
| 5 | `JOBY_LONG` | 1.01 | -20.81 | +2.09 | 35 | — | — |

Biggest within-round improvements (baseline → final): `AXTI_LONG` +40.63%, `HOOD_LONG` +34.25%, `MRVL_LONG` +22.57%, `DINO_LONG` +22.22%, `TTD_LONG` +21.88%

### STOCKS_SHORT  ·  100 sym_sides
- mean final gain **18.13%** (Δ +2.79 vs prev) · median **14.46%** · mean B&H 1.50%
- positive-gain: **99/100** · beat B&H: **96/100** · mean within-round improvement +6.92% (Δ -6.53 vs prev)
- prev snapshot (20261001): n=116 mean_final=15.34%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COE_SHORT` | 104.33 | 38.91 | +73.58 | 48 | — | — |
| 2 | `HAO_SHORT` | 82.80 | 34.68 | +48.13 | 232 | — | — |
| 3 | `AXTI_SHORT` | 52.18 | 4.86 | +30.04 | 318 | — | — |
| 4 | `ALB_SHORT` | 43.92 | 20.98 | +0.48 | 94 | — | — |
| 5 | `ASTS_SHORT` | 43.62 | 12.65 | +40.85 | 88 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `SNDK_SHORT` | -0.48 | -3.25 | +0.00 | — | — | — |
| 2 | `LAC_SHORT` | 0.69 | 13.67 | +0.00 | — | — | — |
| 3 | `GLD_SHORT` | 1.36 | 4.83 | +0.00 | — | — | — |
| 4 | `COPX_SHORT` | 1.95 | 1.90 | +0.00 | — | — | — |
| 5 | `VT_SHORT` | 2.58 | 0.94 | +1.73 | 239 | — | — |

Biggest within-round improvements (baseline → final): `COE_SHORT` +73.58%, `HAO_SHORT` +48.13%, `ASTS_SHORT` +40.85%, `USAR_SHORT` +36.52%, `JOBY_SHORT` +34.56%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `UNIUSDC_LONG` | CRYPTO_LONG | 103.71 | 34.69 | -69.02 |
| `XLMUSDT_LONG` | CRYPTO_LONG | 65.81 | 11.55 | -54.26 |
| `WLDUSDC_LONG` | CRYPTO_LONG | 94.32 | 41.93 | -52.39 |
| `DASHUSDT_SHORT` | CRYPTO_SHORT | 50.17 | -1.45 | -51.62 |
| `IOTXUSDT_LONG` | CRYPTO_LONG | 76.08 | 27.12 | -48.96 |
| `XTZUSDT_LONG` | CRYPTO_LONG | 85.27 | 39.34 | -45.93 |
| `1000PEPEUSDC_LONG` | CRYPTO_LONG | 63.13 | 20.23 | -42.90 |
| `ADAUSDC_LONG` | CRYPTO_LONG | 46.73 | 4.01 | -42.72 |
| `AGI_SHORT` | STOCKS_SHORT | 11.71 | 32.54 | +20.83 |
| `BMNR_SHORT` | STOCKS_SHORT | 11.05 | 31.97 | +20.92 |
| `WLDUSDC_SHORT` | CRYPTO_SHORT | 10.02 | 31.74 | +21.72 |
| `CIEN_LONG` | STOCKS_LONG | 15.33 | 38.17 | +22.84 |
| `NEARUSDC_SHORT` | CRYPTO_SHORT | 1.41 | 27.28 | +25.87 |
| `COHR_LONG` | STOCKS_LONG | 18.78 | 53.33 | +34.54 |
| `AXTI_LONG` | STOCKS_LONG | 35.04 | 71.43 | +36.40 |
| `HAO_SHORT` | STOCKS_SHORT | 34.66 | 82.80 | +48.14 |

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **239** produce ≥1 positive value · **0** never tested · **11** DEAD no-op (moves nothing) · **56** never help (honest losers). Total levers seen: **306**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**11 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| switch | `SATOSHIT_ENABLED` | S_SHORT | 2 | 4 | 2 | +0.001 | -0.001 | trace `SATOSHIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SATOSHIT_ENTRY_ENABLED` | C_LONG | 2 | 2 | 1 | +0.009 | -0.009 | trace `SATOSHIT_ENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MFI_FLIP_EXIT_LONG_THRESHOLD` | S_LONG | 2 | 2 | 0 | -0.000 | -0.000 | trace `MFI_FLIP_EXIT_LONG_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_EXIT_MIN_TFS` | S_LONG | 2 | 2 | 0 | +0.000 | +0.000 | trace `WT_EXIT_MIN_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STOCH_CROSS_1H_EXIT_ENABLED` | S_SHORT | 2 | 2 | 1 | +0.001 | -0.001 | trace `STOCH_CROSS_1H_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED` | S_SHORT | 2 | 2 | 1 | +0.000 | +0.000 | trace `FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EMA20_SLOPE_ENTRY_ENABLED` | C_LONG | 1 | 1 | 0 | -0.003 | -0.003 | trace `EMA20_SLOPE_ENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT` | S_SHORT | 1 | 1 | 0 | -0.000 | -0.000 | trace `GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FORMATION_TRIANGLE_EXIT_ENABLED` | S_SHORT | 1 | 1 | 0 | -0.000 | -0.000 | trace `FORMATION_TRIANGLE_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `ABLATION_DISABLE_AGGRESSIVE_HEDGE` | S_SHORT | 1 | 1 | 1 | +0.000 | +0.000 | trace `ABLATION_DISABLE_AGGRESSIVE_HEDGE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY` | S_SHORT | 1 | 1 | 1 | +0.002 | +0.002 | trace `BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **56** levers (54 switches, 2 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (54):** `VIGILANCE_RECOVERY_REENTRY_ENABLED`, `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER`, `ALL_TF_AGAINST_CLOSE_MIN_TFS`, `MOMENTUM_SMA_WATCHDOG_ENABLED`, `CT_CHOP_4H_GATE_ENABLED`, `MTF_DC_REJECT_EXIT_ENABLED`, `LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED`, `BB_PULLBACK_GATE_ENABLED`, `PROFIT_TARGET_ENABLED`, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`, `HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN`, `WT_15M_BOUNCE_REQUIRE_BOTH_HTF`, `CHOP_TRENDING_THRESHOLD`, `CHOP_RANGING_THRESHOLD`, `CT_REL_VOL_MIN`, `TF_HTF1`, `HARD_BREAKEVEN_FLOOR_ENABLED`, `WT_PERCENTILE_ENTRY_GATE_ENABLED`, `PROFIT_TARGET_PCT`, `MI_VELOCITY_EXIT_ENABLED`, `CT_VOLUME_SURGE_GATE_ENABLED`, `REENTRY_ENTRY_FILTER_ENABLED`, `BB_PULLBACK_GATE_SHORT_MIN`, `MTF_GR_EXIT_GATE_ENABLED`, `WT_PERCENTILE_EXIT_OS_D`, `STDEV_REJECT_EXIT_ENABLED`, `MI_WAVE_EXIT_ENABLED`, `WT_DC_ENTRY_THRESHOLD`, `WT_15M_BOUNCE_VOLUME_THRESHOLD`, `MI_STRUCT_EXIT_ENABLED`, `BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE`, `MTF_ATR_TRAIL_ENABLED_TRADIER`, `WT_PERCENTILE_EXIT_OS_4H`, `RZ_BOT_BB_THRESHOLD`, `BB_SQUEEZE_MIN_ALIGNMENT`, `WT_15M_BOUNCE_FILTER_HH_ENABLED`, `RZ_BREAKOUT_ENTRY_ENABLED`, `GAP_MOC_FORCE_MOC_AT_CLOSE`, `HLR_TOP_VEL_1H_THRESH`, `LH_HL_FILTER_REQUIRE_BOTH`, `MFI_FLIP_EXIT_SHORT_THRESHOLD`, `REENTRY_MANDATORY`, `STDEV_SLOPE_SIZING_4H_MAX`, `MI_TF_AGREE_MIN`, `MTF_ATR_TRAIL_TF`, `AUGMENT_BREAKOUT_MIN_GAIN_PCT`, `GAP_MOC_DC_PROXIMITY_PCT`, `GAP_CLOSE_MOC_ONLY_FOR_SHORTS`, `ABLATION_DISABLE_AUGMENTATION`, `ABLATION_DISABLE_HIGH_GAIN_AUGMENT`, `PYRAMID_MIN_DC_POS_15M`, `K_ZONE_ENTRY_ENABLED_TRADIER`, `PYRAMID_SIZE_MULT`, `TECHNICAL_DC_STOP_TF`
- **filters (2):** `BREAKEVEN_GAIN_EROSION_FILTER_TF`, `PEAK_GIVEBACK_BE_EROSION_FILTER_TF`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions

_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_

### live-faithful (backtest_v12_engine) coverage: **0** sym_sides today
- parity gaps (vec vs live-faithful): **0**

### Promotions made today (bold defaults, cat_side_promotions.json)
_none today_

