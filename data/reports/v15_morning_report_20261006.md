# v15 Morning Report — 20261006

_Generated 2026-10-06T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 42 | 10-06 14:14Z | 10-05 09:27Z | d84863e5d9e4 | OK |
| s1 | s1-int | 48 | 10-06 14:15Z | 10-05 09:15Z | 5506425e192f | OK |
| s2 | s2 | 65 | 10-06 14:16Z | 10-05 09:04Z | 5506425e192f | OK |
| s5 | s5 | 70 | 10-06 14:16Z | 10-05 08:49Z | 5506425e192f | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 160**

> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.
> - mac: `d84863e5d9e4`
> - s1: `5506425e192f`
> - s2: `5506425e192f`
> - s5: `5506425e192f`

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **21**
    - `ZCSH_LONG` [mac] 1230 switches all-zero
    - `MU_SHORT` [mac] 1003 switches all-zero
    - `MSFT_SHORT` [mac] 950 switches all-zero
    - `SNDK_LONG` [mac] 935 switches all-zero
    - `COE_LONG` [s5] 756 switches all-zero
    - `HII_LONG` [s2] 661 switches all-zero
    - `AXTI_LONG` [s2] 556 switches all-zero
    - `IBIT_SHORT` [mac] 463 switches all-zero
    - `LSCC_LONG` [s5] 461 switches all-zero
    - `TTD_LONG` [s2] 211 switches all-zero
    - `RS_LONG` [s2] 158 switches all-zero
    - `AMAT_LONG` [s2] 154 switches all-zero
    - `TSLA_SHORT` [s2] 142 switches all-zero
    - `A_SHORT` [s2] 111 switches all-zero
    - `AMD_LONG` [s2] 88 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **2**
    - `FLNCUSDT_SHORT` [s5] -0.899542 ×33/46
    - `CIBR_LONG` [s2] -1.877092 ×13/26
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **31**
    - `ADAUSDC_SHORT` [s5]
    - `ALB_LONG` [s2]
    - `ALGOUSDT_SHORT` [s5]
    - `ARBUSDC_SHORT` [s5]
    - `AXON_SHORT` [mac]
    - `AXSUSDT_SHORT` [s5]
    - `BCHUSDC_SHORT` [s5]
    - `BTCDOMUSDT_SHORT` [s5]
    - `BWXT_SHORT` [s2]
    - `CHRUSDT_SHORT` [s5]
    - `CLX_LONG` [s5]
    - `CMC_SHORT` [mac]
    - `DASHUSDT_SHORT` [s5]
    - `DOTUSDT_SHORT` [s5]
    - `ENAUSDC_SHORT` [s5]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **4 sym_sides, 4 switch×baseline cases**
    - worst offending switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×1, `CT_CHOP_4H_GATE_ENABLED`×1, `WICK_REJECT_ENTRY_ENABLED`×1, `WT_DIV_ENTRY_GATE_ENABLED`×1
    - `SNXUSDT_LONG` [s5] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `RRC_LONG` [s2] 1 cases — CT_CHOP_4H_GATE_ENABLED T=-7.873/F=+0.073
    - `JASMYUSDT_LONG` [s5] 1 cases — WICK_REJECT_ENTRY_ENABLED T=-2.122/F=+2.574
    - `THETAUSDT_LONG` [s5] 1 cases — WT_DIV_ENTRY_GATE_ENABLED T=+0.196/F=-5.499
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  21 sym_sides
- mean final gain **8.21%** (Δ -20.55 vs prev) · median **3.49%** · mean B&H 22.73%
- positive-gain: **17/21** · beat B&H: **5/21** · mean within-round improvement +11.72% (Δ -17.42 vs prev)
- prev snapshot (20261005): n=49 mean_final=28.76%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COTIUSDT_LONG` | 34.63 | 5.38 | +33.12 | 171 | — | — |
| 2 | `ALGOUSDT_LONG` | 30.93 | 49.08 | +33.08 | 288 | — | — |
| 3 | `AXSUSDT_LONG` | 24.44 | 26.22 | +18.26 | 207 | — | — |
| 4 | `SNXUSDT_LONG` | 19.74 | 22.23 | +23.30 | 205 | — | — |
| 5 | `JASMYUSDT_LONG` | 19.27 | 14.76 | +18.58 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AVAXUSDC_LONG` | -5.88 | 52.47 | +0.00 | — | — | — |
| 2 | `THETAUSDT_LONG` | -4.06 | 31.73 | -4.44 | — | — | — |
| 3 | `ETCUSDT_LONG` | -2.10 | 14.56 | +7.15 | 376 | — | — |
| 4 | `RVNUSDT_LONG` | -1.30 | -24.06 | +13.93 | 315 | — | — |
| 5 | `ADAUSDC_LONG` | 0.37 | 9.75 | +8.58 | — | — | — |

Biggest within-round improvements (baseline → final): `COTIUSDT_LONG` +33.12%, `ALGOUSDT_LONG` +33.08%, `SNXUSDT_LONG` +23.30%, `ZENUSDT_LONG` +18.76%, `JASMYUSDT_LONG` +18.58%

### CRYPTO_SHORT  ·  33 sym_sides
- mean final gain **-0.21%** (Δ -0.68 vs prev) · median **0.00%** · mean B&H -5.29%
- positive-gain: **6/33** · beat B&H: **13/33** · mean within-round improvement +1.42% (Δ +0.08 vs prev)
- prev snapshot (20261005): n=53 mean_final=0.47%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZENUSDT_SHORT` | 12.05 | -13.04 | +14.95 | — | — | — |
| 2 | `ACEUSDT_SHORT` | 6.49 | 14.68 | +6.46 | 218 | — | — |
| 3 | `ENAUSDC_SHORT` | 2.33 | 0.00 | +2.33 | — | — | — |
| 4 | `FLNCUSDT_SHORT` | 2.17 | 28.89 | +2.17 | 19 | — | — |
| 5 | `ADAUSDC_SHORT` | 0.50 | 0.00 | +0.50 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `RVNUSDT_SHORT` | -6.89 | 24.06 | +0.00 | — | — | — |
| 2 | `IOTAUSDT_SHORT` | -6.55 | -39.98 | +0.00 | — | — | — |
| 3 | `AAVEUSDC_SHORT` | -6.50 | -32.61 | +0.00 | — | — | — |
| 4 | `1000PEPEUSDC_SHORT` | -4.36 | -16.83 | +0.02 | 300 | — | — |
| 5 | `AVAXUSDC_SHORT` | -3.40 | -52.47 | +0.89 | 146 | — | — |

Biggest within-round improvements (baseline → final): `ZENUSDT_SHORT` +14.95%, `NOTUSDT_SHORT` +12.72%, `ACEUSDT_SHORT` +6.46%, `ATOMUSDT_SHORT` +3.79%, `ETCUSDT_SHORT` +2.51%

### STOCKS_LONG  ·  55 sym_sides
- mean final gain **3.82%** (Δ -6.69 vs prev) · median **1.02%** · mean B&H 1.85%
- positive-gain: **30/55** · beat B&H: **28/55** · mean within-round improvement +5.92% (Δ -6.94 vs prev)
- prev snapshot (20261005): n=61 mean_final=10.51%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MSTR_LONG` | 45.62 | 30.49 | +45.62 | 322 | — | — |
| 2 | `PBF_LONG` | 31.96 | 24.24 | +32.00 | 20 | — | — |
| 3 | `MU_LONG` | 28.08 | 11.11 | +25.46 | — | — | — |
| 4 | `DELL_LONG` | 20.39 | 25.53 | +16.86 | 30 | — | — |
| 5 | `NVDA_LONG` | 17.40 | 3.79 | +18.24 | 15 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ZCSH_LONG` | -35.10 | -38.09 | +0.00 | — | — | — |
| 2 | `QBTS_LONG` | -15.68 | -22.39 | +0.00 | — | — | — |
| 3 | `COE_LONG` | -5.44 | -35.65 | +0.00 | — | — | — |
| 4 | `COPX_LONG` | -3.29 | -1.90 | +1.56 | 51 | — | — |
| 5 | `MDT_LONG` | -2.53 | -5.81 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `MSTR_LONG` +45.62%, `PBF_LONG` +32.00%, `SLV_LONG` +30.71%, `MU_LONG` +25.46%, `NVDA_LONG` +18.24%

### STOCKS_SHORT  ·  51 sym_sides
- mean final gain **7.59%** (Δ -5.60 vs prev) · median **5.93%** · mean B&H 1.06%
- positive-gain: **38/51** · beat B&H: **37/51** · mean within-round improvement +9.83% (Δ -4.65 vs prev)
- prev snapshot (20261005): n=28 mean_final=13.18%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AXON_SHORT` | 32.28 | 34.40 | +32.28 | — | — | — |
| 2 | `BABA_SHORT` | 31.37 | 16.69 | +29.52 | 37 | — | — |
| 3 | `UUUU_SHORT` | 27.80 | 27.21 | +5.23 | 14 | — | — |
| 4 | `CLX_SHORT` | 27.44 | 23.34 | +15.24 | — | — | — |
| 5 | `UEC_SHORT` | 24.83 | 18.92 | +36.05 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `BMNR_SHORT` | -16.28 | -17.20 | +1.89 | 10 | — | — |
| 2 | `TSLA_SHORT` | -6.45 | -8.81 | +0.00 | — | — | — |
| 3 | `A_SHORT` | -6.24 | -7.72 | +0.00 | — | — | — |
| 4 | `DINO_SHORT` | -5.85 | -19.27 | +0.00 | — | — | — |
| 5 | `CIBR_SHORT` | -3.68 | -9.79 | +8.58 | 38 | — | — |

Biggest within-round improvements (baseline → final): `COIN_SHORT` +37.13%, `UEC_SHORT` +36.05%, `AXON_SHORT` +32.28%, `CRWD_SHORT` +30.80%, `BABA_SHORT` +29.52%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `XTZUSDT_LONG` | CRYPTO_LONG | 71.06 | 3.27 | -67.79 |
| `IOTAUSDT_LONG` | CRYPTO_LONG | 56.10 | 3.49 | -52.61 |
| `ETCUSDT_LONG` | CRYPTO_LONG | 49.49 | -2.10 | -51.59 |
| `INTC_LONG` | STOCKS_LONG | 43.00 | 5.40 | -37.60 |
| `AVAXUSDC_LONG` | CRYPTO_LONG | 28.15 | -5.88 | -34.03 |
| `THETAUSDT_LONG` | CRYPTO_LONG | 29.18 | -4.06 | -33.24 |
| `CHRUSDT_LONG` | CRYPTO_LONG | 31.24 | 2.67 | -28.57 |
| `CLS_LONG` | STOCKS_LONG | 31.12 | 2.75 | -28.37 |
| `AGI_LONG` | STOCKS_LONG | -0.75 | 4.50 | +5.25 |
| `MU_SHORT` | STOCKS_SHORT | -7.32 | 0.00 | +7.32 |
| `APO_SHORT` | STOCKS_SHORT | 7.40 | 16.53 | +9.13 |
| `DELL_LONG` | STOCKS_LONG | 9.16 | 20.39 | +11.23 |
| `GOOGL_SHORT` | STOCKS_SHORT | -10.63 | 4.26 | +14.90 |
| `MU_LONG` | STOCKS_LONG | 2.93 | 28.08 | +25.15 |
| `COTIUSDT_LONG` | CRYPTO_LONG | 1.39 | 34.63 | +33.24 |
| `MSTR_LONG` | STOCKS_LONG | 5.66 | 45.62 | +39.96 |

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

