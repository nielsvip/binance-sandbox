# v15 Morning Report — 20261005

_Generated 2026-10-05T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 0 | — | — | 123e808cc860 | Command '['/Users/niels/Documents/binance/.venv/bin/python', '-c', '\nimport sys, json, glob, os, statistics, collection |
| s1 | s1-int | 126 | 10-05 14:20Z | 10-04 08:21Z | 92feb8f328d7 | OK |
| s2 | s2 | 73 | 10-05 14:20Z | 10-04 08:35Z | 92feb8f328d7 | OK |
| s5 | s5 | 63 | 10-05 14:15Z | 10-04 08:36Z | 92feb8f328d7 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 191**

> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.
> - mac: `123e808cc860`
> - s1: `92feb8f328d7`
> - s2: `92feb8f328d7`
> - s5: `92feb8f328d7`

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **7**
    - `DOGEUSDC_LONG` [s2] 971 switches all-zero
    - `MANAUSDT_LONG` [s5] 971 switches all-zero
    - `COTIUSDT_LONG` [s5] 971 switches all-zero
    - `ATOMUSDT_LONG` [s2] 971 switches all-zero
    - `DASHUSDT_SHORT` [s5] 953 switches all-zero
    - `QNTUSDT_LONG` [s1] 900 switches all-zero
    - `USAR_LONG` [s2] 31 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **2**
    - `MDT_LONG` [s1] -1.057871 ×20/38
    - `CIBR_LONG` [s2] -1.877092 ×13/26
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **42**
    - `1000FLOKIUSDT_SHORT` [s2]
    - `1INCHUSDT_SHORT` [s1]
    - `ALGOUSDT_SHORT` [s1]
    - `ARBUSDC_SHORT` [s1]
    - `ARUSDT_SHORT` [s2]
    - `ATOMUSDT_SHORT` [s2]
    - `AXSUSDT_SHORT` [s1]
    - `BCHUSDC_SHORT` [s1]
    - `BTCDOMUSDT_SHORT` [s1]
    - `BTCUSDC_SHORT` [s2]
    - `CHRUSDT_SHORT` [s1]
    - `CLX_LONG` [s5]
    - `COMPUSDT_SHORT` [s1]
    - `DASHUSDT_SHORT` [s5]
    - `DOTUSDT_SHORT` [s1]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **9 sym_sides, 9 switch×baseline cases**
    - worst offending switches (by # sym_sides): `HTF_GATE_SIGNALS_SMA200D`×2, `OI_CONFIRM_ENABLED`×2, `WT_15M_BOUNCE_OPEN_ENABLED`×2, `WT_DIV_EXIT_ENABLED`×1, `HARDCODED_RALLY_SMA200_SIDE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1
    - `BNBUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-10.796/F=+0.100
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `ARUSDT_LONG` [s2] 1 cases — OI_CONFIRM_ENABLED T=+0.065/F=-1.941
    - `TRBUSDT_LONG` [s5] 1 cases — OI_CONFIRM_ENABLED T=+0.406/F=-2.290
    - `CRM_SHORT` [s2] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.687/F=-1.301
    - `ENSUSDT_SHORT` [s2] 1 cases — HARDCODED_RALLY_SMA200_SIDE_ENABLED T=+0.170/F=-2.467
    - `XRPUSDC_LONG` [s5] 1 cases — HTF_GATE_SIGNALS_SMA200D T=-11.139/F=+1.590
    - `SLV_SHORT` [s5] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+0.948/F=+0.948
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  49 sym_sides
- mean final gain **28.76%** (Δ +16.28 vs prev) · median **25.96%** · mean B&H 33.78%
- positive-gain: **48/49** · beat B&H: **27/49** · mean within-round improvement +29.14% (Δ +15.84 vs prev)
- prev snapshot (20261004): n=65 mean_final=12.48%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZECUSDC_LONG` | 98.13 | 66.20 | +99.43 | 106 | — | — |
| 2 | `XTZUSDT_LONG` | 71.06 | 47.61 | +73.11 | 354 | — | — |
| 3 | `EGLDUSDT_LONG` | 68.48 | 16.36 | +70.04 | 90 | — | — |
| 4 | `FILUSDC_LONG` | 62.73 | 52.16 | +62.41 | 245 | — | — |
| 5 | `KSMUSDT_LONG` | 61.64 | 46.77 | +60.74 | 194 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `DOGEUSDC_LONG` | -0.06 | 15.98 | +0.00 | — | — | — |
| 2 | `MANAUSDT_LONG` | 0.28 | 43.50 | +0.00 | — | — | — |
| 3 | `BTCDOMUSDT_LONG` | 1.09 | -3.97 | +1.21 | 78 | — | — |
| 4 | `COTIUSDT_LONG` | 1.39 | 5.38 | +0.00 | — | — | — |
| 5 | `ACEUSDT_LONG` | 4.21 | -14.68 | +17.97 | 93 | — | — |

Biggest within-round improvements (baseline → final): `ZECUSDC_LONG` +99.43%, `XTZUSDT_LONG` +73.11%, `EGLDUSDT_LONG` +70.04%, `FILUSDC_LONG` +62.41%, `KSMUSDT_LONG` +60.74%

### CRYPTO_SHORT  ·  53 sym_sides
- mean final gain **0.47%** (Δ +0.83 vs prev) · median **0.00%** · mean B&H -8.49%
- positive-gain: **7/53** · beat B&H: **15/53** · mean within-round improvement +1.34% (Δ +1.31 vs prev)
- prev snapshot (20261004): n=77 mean_final=-0.36%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `FLNCUSDT_SHORT` | 25.77 | 28.89 | +25.77 | 156 | — | — |
| 2 | `DASHUSDT_SHORT` | 15.03 | -38.07 | +15.03 | — | — | — |
| 3 | `ENSUSDT_SHORT` | 11.49 | -23.74 | +24.89 | 119 | — | — |
| 4 | `ENAUSDC_SHORT` | 2.33 | 0.00 | +2.33 | — | — | — |
| 5 | `ACEUSDT_SHORT` | 1.55 | 14.68 | +1.52 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `GRAMUSDT_SHORT` | -8.29 | -8.72 | +0.00 | — | — | — |
| 2 | `BSVUSDT_SHORT` | -6.80 | -26.81 | +0.00 | — | — | — |
| 3 | `FILUSDC_SHORT` | -5.41 | -52.16 | +0.00 | — | — | — |
| 4 | `QNTUSDT_SHORT` | -3.33 | -100.00 | +0.00 | — | — | — |
| 5 | `ETHFIUSDC_SHORT` | -3.10 | -31.22 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `FLNCUSDT_SHORT` +25.77%, `ENSUSDT_SHORT` +24.89%, `DASHUSDT_SHORT` +15.03%, `ENAUSDC_SHORT` +2.33%, `ADAUSDC_SHORT` +1.67%

### STOCKS_LONG  ·  61 sym_sides
- mean final gain **10.51%** (Δ -2.76 vs prev) · median **6.67%** · mean B&H 1.05%
- positive-gain: **51/61** · beat B&H: **44/61** · mean within-round improvement +12.86% (Δ -2.30 vs prev)
- prev snapshot (20261004): n=105 mean_final=13.27%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MRVL_LONG` | 58.81 | 12.64 | +59.42 | 50 | — | — |
| 2 | `INTC_LONG` | 43.00 | 23.07 | +40.74 | — | — | — |
| 3 | `GME_LONG` | 36.61 | 36.87 | +34.35 | 98 | — | — |
| 4 | `CF_LONG` | 32.48 | -3.18 | +45.52 | 100 | — | — |
| 5 | `PBF_LONG` | 31.96 | 24.24 | +32.00 | 20 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `NOC_LONG` | -4.75 | -17.75 | +8.42 | 38 | — | — |
| 2 | `COPX_LONG` | -4.01 | -1.90 | +0.84 | 57 | — | — |
| 3 | `MDT_LONG` | -3.71 | -6.50 | +0.00 | — | — | — |
| 4 | `APO_LONG` | -1.55 | -13.12 | +1.29 | — | — | — |
| 5 | `TSLA_LONG` | -1.02 | 1.74 | +17.64 | 46 | — | — |

Biggest within-round improvements (baseline → final): `MRVL_LONG` +59.42%, `CF_LONG` +45.52%, `INTC_LONG` +40.74%, `GME_LONG` +34.35%, `PBF_LONG` +32.00%

### STOCKS_SHORT  ·  28 sym_sides
- mean final gain **13.18%** (Δ +10.60 vs prev) · median **14.42%** · mean B&H 2.86%
- positive-gain: **23/28** · beat B&H: **24/28** · mean within-round improvement +14.48% (Δ +10.83 vs prev)
- prev snapshot (20261004): n=86 mean_final=2.59%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `LRCX_SHORT` | 37.81 | 0.50 | +32.99 | 17 | — | — |
| 2 | `AXON_SHORT` | 37.00 | 34.40 | +37.00 | 48 | — | — |
| 3 | `BWXT_SHORT` | 29.42 | 12.63 | +29.42 | 134 | — | — |
| 4 | `CLX_SHORT` | 27.44 | 23.34 | +15.24 | — | — | — |
| 5 | `CIEN_SHORT` | 25.06 | 17.63 | +25.06 | 63 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `GOOGL_SHORT` | -10.63 | 1.46 | +0.00 | — | — | — |
| 2 | `MU_SHORT` | -7.32 | -15.05 | +0.00 | — | — | — |
| 3 | `CIBR_SHORT` | -3.68 | -9.79 | +8.58 | 38 | — | — |
| 4 | `MSTR_SHORT` | 0.00 | -30.49 | +0.00 | — | — | — |
| 5 | `SNDK_SHORT` | 0.00 | -8.01 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `AXON_SHORT` +37.00%, `UEC_SHORT` +36.05%, `LRCX_SHORT` +32.99%, `WDAY_SHORT` +31.53%, `BWXT_SHORT` +29.42%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `IOTXUSDT_LONG` | CRYPTO_LONG | 76.08 | 17.61 | -58.47 |
| `BMNR_LONG` | STOCKS_LONG | 53.87 | 10.17 | -43.70 |
| `CRM_LONG` | STOCKS_LONG | 39.58 | 3.03 | -36.55 |
| `MSTR_LONG` | STOCKS_LONG | 34.90 | 5.66 | -29.24 |
| `AMD_LONG` | STOCKS_LONG | 31.17 | 5.34 | -25.83 |
| `CRWD_LONG` | STOCKS_LONG | 34.24 | 8.61 | -25.62 |
| `MU_LONG` | STOCKS_LONG | 28.08 | 2.93 | -25.15 |
| `ADAUSDC_LONG` | CRYPTO_LONG | 21.86 | 4.27 | -17.59 |
| `ZENUSDT_LONG` | CRYPTO_LONG | -2.32 | 33.92 | +36.24 |
| `1000FLOKIUSDT_LONG` | CRYPTO_LONG | 0.13 | 37.13 | +37.01 |
| `NOTUSDT_LONG` | CRYPTO_LONG | -1.18 | 37.73 | +38.91 |
| `MRVL_LONG` | STOCKS_LONG | 19.24 | 58.81 | +39.57 |
| `ETHFIUSDC_LONG` | CRYPTO_LONG | 1.80 | 41.85 | +40.05 |
| `ARUSDT_LONG` | CRYPTO_LONG | -0.36 | 40.26 | +40.62 |
| `WLDUSDC_LONG` | CRYPTO_LONG | 0.51 | 50.40 | +49.89 |
| `ZECUSDC_LONG` | CRYPTO_LONG | 28.87 | 98.13 | +69.26 |

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
_none today_

