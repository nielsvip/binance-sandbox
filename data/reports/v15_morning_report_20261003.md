# v15 Morning Report — 20261003

_Generated 2026-10-03T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 100 | 10-03 03:49Z | 10-02 22:13Z | 98bd3e9e8158 | OK |
| s1 | s1-int | 286 | 10-03 14:14Z | 10-02 10:08Z | 98bd3e9e8158 | OK |
| s2 | s2 | 251 | 10-03 14:15Z | 10-02 09:22Z | 98bd3e9e8158 | OK |
| s5 | s5 | 252 | 10-03 11:40Z | 10-02 11:22Z | 98bd3e9e8158 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 335**

Engine md5 parity across boxes: ✅ `98bd3e9e8158` on mac, s1, s2, s5.

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **16**
    - `NKE_SHORT` [s2] 858 switches all-zero
    - `JASMYUSDT_LONG` [s2] 782 switches all-zero
    - `ATOMUSDT_LONG` [s2] 782 switches all-zero
    - `LINKUSDC_LONG` [s5] 776 switches all-zero
    - `EGLDUSDT_LONG` [s2] 776 switches all-zero
    - `MOVRUSDT_SHORT` [s1] 771 switches all-zero
    - `1INCHUSDT_SHORT` [s1] 766 switches all-zero
    - `QNTUSDT_LONG` [s2] 745 switches all-zero
    - `ENSUSDT_LONG` [s1] 745 switches all-zero
    - `MRVLUSDT_LONG` [s2] 737 switches all-zero
    - `KASUSDT_SHORT` [s2] 736 switches all-zero
    - `GOOGLUSDT_LONG` [s1] 733 switches all-zero
    - `QNTUSDT_SHORT` [s2] 727 switches all-zero
    - `MRVLUSDT_SHORT` [s2] 724 switches all-zero
    - `LLY_SHORT` [s1] 478 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **4**
    - `GOOGLUSDT_SHORT` [s1] -12.472691 ×1066/1483
    - `BABAUSDT_LONG` [s1] -2.916162 ×159/306
    - `AAPLUSDT_LONG` [s5] -0.015091 ×103/197
    - `AXON_LONG` [s2] 1.708363 ×90/162
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **41**
    - `1000PEPEUSDC_SHORT` [s1]
    - `1INCHUSDT_SHORT` [s1]
    - `AAVEUSDC_LONG` [s1]
    - `ADBE_SHORT` [s2]
    - `AGI_SHORT` [s1]
    - `ARUSDT_LONG` [s1]
    - `ARUSDT_SHORT` [s1]
    - `ATOMUSDT_SHORT` [s2]
    - `BTCUSDC_SHORT` [s2]
    - `CIEN_SHORT` [s2]
    - `COMPUSDT_LONG` [s1]
    - `COMPUSDT_SHORT` [s1]
    - `CVX_SHORT` [s2]
    - `ETN_SHORT` [s2]
    - `EXEL_SHORT` [s2]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **9 sym_sides, 30 switch×baseline cases**
    - worst offending switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×5, `WT_15M_BOUNCE_OPEN_ENABLED`×3, `BAND_ARROW_ENABLED`×1, `DELTA_REENTRY_FILTER_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `GR_FILTER_VEC_ENABLED`×1, `HA_WICK_QUALITY_ENABLED`×1, `HTF4_CONF`×1, `HTF_DIRECTION_GATE_ENABLED`×1, `HTF_TREND_VETO_BYPASS_ENABLED`×1, `LH_HL_FILTER_ENABLED`×1, `LIVE_VEC_EMERGENCY_BRAKE_ENABLED`×1
    - `GOOGLUSDT_SHORT` [s1] 19 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386
    - `MSTR_SHORT` [s1] 4 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.408/F=+0.843; NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.333/F=-0.521
    - `PYPL_SHORT` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.433/F=-1.128
    - `ETHFIUSDC_SHORT` [s1] 1 cases — MTF_BB_REJECT_EXIT_ENABLED T=+0.461/F=-5.643
    - `BSVUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.006/F=-0.357
    - `RBLX_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.126/F=-1.914
    - `A_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.171/F=-10.828
    - `ARM_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+1.224/F=-0.176
    - `BABA_LONG` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.144/F=-3.218
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  70 sym_sides
- mean final gain **17.07%** (Δ -7.86 vs prev) · median **12.24%** · mean B&H 35.66%
- positive-gain: **56/70** · beat B&H: **21/70** · mean within-round improvement +12.57% (Δ +1.69 vs prev)
- prev snapshot (20261002): n=63 mean_final=24.93%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZECUSDC_LONG` | 84.11 | 59.83 | +52.60 | 127 | — | — |
| 2 | `COTIUSDT_LONG` | 84.11 | 9.61 | +83.12 | 109 | — | — |
| 3 | `MOVRUSDT_LONG` | 82.24 | 268.19 | +24.62 | — | — | — |
| 4 | `QNTUSDT_LONG` | 61.48 | 365.74 | +0.00 | — | — | — |
| 5 | `MINAUSDT_LONG` | 57.39 | 130.30 | +36.58 | 108 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `BNBUSDC_LONG` | -1.93 | 9.00 | -1.23 | — | — | — |
| 2 | `LINKUSDC_LONG` | -0.89 | 27.62 | +0.00 | — | — | — |
| 3 | `AAPLUSDT_LONG` | -0.57 | 1.74 | +3.22 | 125 | — | — |
| 4 | `ENSUSDT_LONG` | -0.19 | 23.74 | +0.00 | — | — | — |
| 5 | `COMPUSDT_LONG` | 0.00 | 41.06 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `COTIUSDT_LONG` +83.12%, `UNIUSDC_LONG` +57.03%, `ZECUSDC_LONG` +52.60%, `WLDUSDC_LONG` +51.51%, `NEARUSDC_LONG` +37.93%

### CRYPTO_SHORT  ·  72 sym_sides
- mean final gain **5.69%** (Δ -3.74 vs prev) · median **1.01%** · mean B&H -26.17%
- positive-gain: **41/72** · beat B&H: **67/72** · mean within-round improvement +6.63% (Δ +1.52 vs prev)
- prev snapshot (20261002): n=63 mean_final=9.43%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `RVNUSDT_SHORT` | 30.68 | 23.05 | +30.66 | — | — | — |
| 2 | `SANDUSDT_SHORT` | 24.50 | -19.04 | +25.10 | 45 | — | — |
| 3 | `NEARUSDC_SHORT` | 23.89 | -100.00 | +45.06 | 245 | — | — |
| 4 | `COTIUSDT_SHORT` | 23.62 | -9.61 | +24.79 | 317 | — | — |
| 5 | `ASTSUSDT_SHORT` | 22.86 | -1.17 | +0.73 | 118 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZECUSDC_SHORT` | -9.02 | -69.28 | -2.17 | — | — | — |
| 2 | `BABAUSDT_SHORT` | -5.17 | 5.29 | +0.00 | — | — | — |
| 3 | `KASUSDT_SHORT` | -1.23 | -58.48 | -0.35 | — | — | — |
| 4 | `QNTUSDT_SHORT` | -0.40 | -100.00 | +0.00 | — | — | — |
| 5 | `MASKUSDT_SHORT` | -0.18 | -8.30 | +0.63 | 25 | — | — |

Biggest within-round improvements (baseline → final): `NEARUSDC_SHORT` +45.06%, `RVNUSDT_SHORT` +30.66%, `ICPUSDT_SHORT` +28.76%, `SANDUSDT_SHORT` +25.10%, `COTIUSDT_SHORT` +24.79%

### STOCKS_LONG  ·  103 sym_sides
- mean final gain **16.30%** (Δ +1.16 vs prev) · median **10.73%** · mean B&H 0.75%
- positive-gain: **100/103** · beat B&H: **93/103** · mean within-round improvement +8.05% (Δ +2.42 vs prev)
- prev snapshot (20261002): n=100 mean_final=15.14%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AXTI_LONG` | 71.43 | -4.86 | +72.55 | 114 | — | — |
| 2 | `COHR_LONG` | 64.98 | 9.94 | +38.83 | 135 | — | — |
| 3 | `MRVL_LONG` | 47.13 | 12.64 | +5.61 | 71 | — | — |
| 4 | `COIN_LONG` | 46.52 | 28.07 | +45.98 | 36 | — | — |
| 5 | `GME_LONG` | 46.32 | 36.87 | +0.01 | 163 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `OLED_LONG` | -0.68 | -10.32 | -4.96 | — | — | — |
| 2 | `JPM_LONG` | -0.16 | -8.79 | -1.39 | 124 | — | — |
| 3 | `HII_LONG` | 0.00 | -10.89 | +0.00 | — | — | — |
| 4 | `CLX_LONG` | 0.04 | -23.93 | -3.60 | 53 | — | — |
| 5 | `HD_LONG` | 0.18 | -15.75 | +0.19 | 20 | — | — |

Biggest within-round improvements (baseline → final): `AXTI_LONG` +72.55%, `COIN_LONG` +45.98%, `CIEN_LONG` +39.66%, `COHR_LONG` +38.83%, `META_LONG` +36.74%

### STOCKS_SHORT  ·  90 sym_sides
- mean final gain **12.63%** (Δ -5.50 vs prev) · median **13.02%** · mean B&H -0.47%
- positive-gain: **66/90** · beat B&H: **73/90** · mean within-round improvement +5.87% (Δ -1.05 vs prev)
- prev snapshot (20261002): n=100 mean_final=18.13%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ASTS_SHORT` | 43.62 | 12.65 | +40.85 | 88 | — | — |
| 2 | `QBTS_SHORT` | 41.79 | 20.53 | +16.98 | 222 | — | — |
| 3 | `BWXT_SHORT` | 36.98 | 19.01 | +24.44 | 117 | — | — |
| 4 | `AU_SHORT` | 34.88 | 10.94 | +23.81 | 79 | — | — |
| 5 | `BMNR_SHORT` | 32.27 | -22.23 | +32.95 | 155 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `PSX_SHORT` | -6.56 | -7.71 | +0.00 | — | — | — |
| 2 | `GDX_SHORT` | -5.03 | -13.18 | -5.88 | 35 | — | — |
| 3 | `COIN_SHORT` | -0.12 | -28.07 | +0.00 | — | — | — |
| 4 | `HON_SHORT` | 0.00 | 7.40 | +0.00 | — | — | — |
| 5 | `GME_SHORT` | 0.00 | -36.87 | -4.81 | — | — | — |

Biggest within-round improvements (baseline → final): `ASTS_SHORT` +40.85%, `BMNR_SHORT` +32.95%, `UUUU_SHORT` +30.08%, `PYPL_SHORT` +29.12%, `BWXT_SHORT` +24.44%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `ARUSDT_LONG` | CRYPTO_LONG | 80.79 | 0.00 | -80.79 |
| `GRTUSDT_LONG` | CRYPTO_LONG | 44.29 | 0.00 | -44.29 |
| `MSTR_LONG` | STOCKS_LONG | 74.71 | 31.22 | -43.49 |
| `ENSUSDT_LONG` | CRYPTO_LONG | 39.68 | -0.19 | -39.87 |
| `LINKUSDC_LONG` | CRYPTO_LONG | 37.15 | -0.89 | -38.04 |
| `ALB_SHORT` | STOCKS_SHORT | 43.92 | 6.79 | -37.13 |
| `DASHUSDT_LONG` | CRYPTO_LONG | 54.20 | 18.44 | -35.76 |
| `EGLDUSDT_LONG` | CRYPTO_LONG | 37.99 | 2.29 | -35.70 |
| `UNIUSDC_LONG` | CRYPTO_LONG | 34.69 | 57.03 | +22.34 |
| `META_LONG` | STOCKS_LONG | 13.94 | 37.17 | +23.23 |
| `SNDK_SHORT` | STOCKS_SHORT | -0.48 | 23.74 | +24.22 |
| `MOVRUSDT_LONG` | CRYPTO_LONG | 55.84 | 82.24 | +26.40 |
| `QBTS_SHORT` | STOCKS_SHORT | 12.32 | 41.79 | +29.47 |
| `COTIUSDT_LONG` | CRYPTO_LONG | 50.77 | 84.11 | +33.34 |
| `INTC_LONG` | STOCKS_LONG | 9.01 | 43.74 | +34.73 |
| `QNTUSDT_LONG` | CRYPTO_LONG | 17.69 | 61.48 | +43.79 |

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **451** produce ≥1 positive value · **0** never tested · **4** DEAD no-op (moves nothing) · **204** never help (honest losers). Total levers seen: **659**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**4 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| switch | `FORMATION_TRIANGLE_EXIT_ENABLED` | S_SHORT | 2 | 2 | 1 | +0.002 | -0.002 | trace `FORMATION_TRIANGLE_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `K_ZONE_LONG_THRESHOLD` | C_LONG | 1 | 1 | 0 | +0.000 | +0.000 | trace `K_ZONE_LONG_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `CT_WT_VELOCITY_1H_MIN` | S_LONG | 1 | 1 | 1 | +0.002 | +0.002 | trace `CT_WT_VELOCITY_1H_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `ABLATION_DISABLE_AGGRESSIVE_HEDGE` | S_SHORT | 1 | 1 | 0 | -0.000 | -0.000 | trace `ABLATION_DISABLE_AGGRESSIVE_HEDGE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **204** levers (200 switches, 4 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (200):** `OI_SURGE_ENTRY_ENABLED`, `VWAP_STRETCH_ENTRY_ENABLED`, `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER`, `REENTRY_SMA200_GR_CONTINUATION_ENABLED`, `CLENOW_ENABLED`, `MOMENTUM_SMA_WATCHDOG_ENABLED`, `ULTIMATE_DC_4H_STOP_ENABLED`, `REENTRY_15M_LRL_PULLBACK_HTF_ENABLED`, `EZ_MANAGE_THROTTLER_RATE`, `HTF_TREND_VETO_BYPASS_REASONS`, `MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY`, `EXECUTE_NOW_SINGLE_GATE_ENFORCE`, `BTC_ROUND_BANDS_EACH_SIDE`, `FUNDING_GATE_LONG_MAX`, `GOLDEN_RULE_BASE_USD`, `LEADERBOARD_FILTER`, `ATR_LONG_WINDOW`, `BTC_RZ_WT_DC_MULTIFACTOR`, `MTF_FILTER_STRONG_BUY_QUICK_BYPASS`, `LEGACY_REENTRY_PSR_QUICK_RECOVERY`, `CT_REL_VOL_MIN`, `CHOP_TRENDING_THRESHOLD`, `CHOP_RANGING_THRESHOLD`, `RZ_TOP_BB_THRESHOLD`, `FUNDING_GATE_SHORT_MIN`, `FUNDING_GATE_MTF_REQUIRED`, `PROFIT_TARGET_ENABLED`, `AUGMENT_FALLBACK_REDUCE_PCT`, `AUGMENT_FALLBACK_REDUCE_ENABLED`, `SCALP_V3_K_OB_EXIT_WALL_PCT`, `ATR_ADAPTIVE_STOP_MULT`, `SCALP_V3_K_OB_EXIT_K3M_LO`, `PARTIAL_EXIT_FRAC`, `REGIME_TRENDING_EXIT_GAIN_MIN`, `SCALP_V3_K_OB_EXIT_K15M_HI`, `WT_15M_CROSS_ENTRY_ENABLED`, `V8_ENTRY_ENGINE_WT_ENABLED`, `WT_DIV_ENTRY_GATE_ENABLED`, `BB_PULLBACK_GATE_LONG_MAX`, `SCALP_V3_K_OB_EXIT_ENABLED`, `SCALP_V3_PROTECTIVE_EXIT_ENABLED`, `OBLIGATORY_REENTRY_SMA_FIELD`, `OBLIGATORY_REENTRY_SCORE_TIER2`, `GUARANTEED_REENTRY_K_LOW_BLOCK`, `REENTRY_WT15M_SIZE_MULT`, `GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S`, `REENTRY_POST_CONSOL_MULT`, `REENTRY_EXIT_RECLAIM_BUFFER_PCT`, `REENTRY_B16_SMA200_PULLBACK_ENABLED`, `GUARANTEED_REENTRY_TIGHT_STOP_PCT`, `MANDATORY_REENTRY_K_LOW_BLOCK`, `OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED`, `REENTRY_B16_SIZE_MULT_WEAK`, `REENTRY_POST_CONSOL_ENABLED`, `OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC`, `REENTRY_EXIT_RECLAIM_ENABLED`, `AUGMENT_AT_LOSS_ENABLED`, `DELTA_EXIT_MANDATORY_REENTRY_ENABLED`, `SIMPLE_TP_EXIT_ENABLED`, `DELTA_PYRAMID_MAX` …
- **filters (4):** `LIVE_ENTRY_ENGINE_FILTER_TF`, `EMERGENCY_BRAKE_FILTER_TF`, `REENTRY_15M_DC_BASIS_CROSS_HTF`, `REENTRY_15M_LRL_PULLBACK_HTF`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions

_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_

### live-faithful (backtest_v12_engine) coverage: **0** sym_sides today
- parity gaps (vec vs live-faithful): **0**

### Promotions made today (bold defaults, cat_side_promotions.json)
- STOCKS_SHORT `REENTRY_TIER1_SIZE_MULT_TRADIER` → 3 (avg_delta 0.326036, REENTRY_WINDOWED)
- STOCKS_SHORT `MAX_AUGMENTS_PER_POSITION` → 0 (avg_delta 0.788001, AUGMENT_RISK_SIZING)
- STOCKS_SHORT `FAST_RISER_FILTER_TF` → OFF (avg_delta 0.010614, GLOBAL_RISK_GATES)
- STOCKS_SHORT `WT_15M_BOUNCE_LOW_1H_GT_PREV` → True (avg_delta 0.180007, GLOBAL_RISK_GATES)
- STOCKS_SHORT `EMA_9_21_FILTER_FILTER_TF` → 15m (avg_delta 0.448554, GLOBAL_RISK_GATES)
- STOCKS_SHORT `MANDATORY_REENTRY_WT_FILTER_TF_MODE` → 0 (avg_delta 1.272554, ENTRY_REVERSAL_BOUNCE)
- STOCKS_SHORT `WT_CROSSUNDER_FINAL_ENABLED` → False (avg_delta 0.023833, ENTRY_CONFIRMATION_GATES)
- STOCKS_SHORT `LH_HL_FILTER_DC_THRESHOLD_PCT` → 0.25 (avg_delta 0.21154, ENTRY_CONFIRMATION_GATES)
- STOCKS_SHORT `TECHNICAL_DC_STOP_TF` → 1h (avg_delta 0.317834, EXIT_STRUCTURAL)
- STOCKS_SHORT `FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED` → True (avg_delta 0.713175, EXIT_STRUCTURAL)
- STOCKS_SHORT `MTF_BB_REJECT_EXIT_TF` → OFF (avg_delta 0.544805, EXIT_STRUCTURAL)
_11 promotions today_

