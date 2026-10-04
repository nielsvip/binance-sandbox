# v15 Morning Report — 20261004

_Generated 2026-10-04T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 200 | 10-04 09:46Z | 10-03 14:14Z | 5db47685ec6a | OK |
| s1 | s1-pub | 204 | 10-04 14:15Z | 10-04 02:17Z | 9a4fa00b0120 | OK |
| s2 | s2 | 118 | 10-04 14:15Z | 10-04 02:17Z | 9a4fa00b0120 | OK |
| s5 | s5 | 96 | 10-04 10:40Z | 10-04 02:48Z | 9a4fa00b0120 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 333**

> ⚠️ **ENGINE MD5 MISMATCH across boxes — MIXED BASELINE. Deltas from different engines are not comparable (NO-LIES).** Re-sync `v12_quick_engine.py`, rerun the mismatched box.
> - mac: `5db47685ec6a`
> - s1: `9a4fa00b0120`
> - s2: `9a4fa00b0120`
> - s5: `9a4fa00b0120`

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **25**
    - `AAVEUSDC_LONG` [s2] 971 switches all-zero
    - `JASMYUSDT_LONG` [s5] 971 switches all-zero
    - `DOGEUSDC_LONG` [s2] 971 switches all-zero
    - `CHRUSDT_LONG` [s5] 971 switches all-zero
    - `COTIUSDT_LONG` [s5] 971 switches all-zero
    - `AXSUSDT_LONG` [s5] 971 switches all-zero
    - `XRPUSDC_LONG` [s5] 971 switches all-zero
    - `BTCUSDC_LONG` [s2] 971 switches all-zero
    - `MANAUSDT_LONG` [s5] 971 switches all-zero
    - `ETHFIUSDC_LONG` [s2] 971 switches all-zero
    - `ATOMUSDT_LONG` [s2] 971 switches all-zero
    - `LINKUSDC_LONG` [s2] 971 switches all-zero
    - `RLCUSDT_LONG` [s2] 971 switches all-zero
    - `ARUSDT_LONG` [s2] 971 switches all-zero
    - `AVAXUSDC_LONG` [s5] 971 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **0**
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **121**
    - `1000BONKUSDC_LONG` [mac]
    - `1000BONKUSDC_SHORT` [mac]
    - `1000FLOKIUSDT_SHORT` [s2]
    - `1000PEPEUSDC_LONG` [s1]
    - `1000PEPEUSDC_SHORT` [s1]
    - `1INCHUSDT_SHORT` [s1]
    - `AAPL_SHORT` [s1]
    - `ABT_SHORT` [s2]
    - `ACEUSDT_LONG` [mac]
    - `ADAUSDC_SHORT` [mac]
    - `ADBE_SHORT` [s2]
    - `AIAUSDT_LONG` [mac]
    - `ALGOUSDT_SHORT` [mac]
    - `AMZN_SHORT` [s5]
    - `APO_SHORT` [s5]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **5 sym_sides, 5 switch×baseline cases**
    - worst offending switches (by # sym_sides): `WT_DIV_EXIT_ENABLED`×2, `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED`×1, `WT_15M_BOUNCE_OPEN_ENABLED`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1
    - `SNXUSDT_LONG` [s1] 1 cases — WT_DIV_EXIT_ENABLED T=-7.886/F=+0.154
    - `QBTS_LONG` [s1] 1 cases — EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED T=-4.340/F=+0.128
    - `PBF_SHORT` [s1] 1 cases — WT_15M_BOUNCE_OPEN_ENABLED T=+3.121/F=+0.957
    - `ENSUSDT_LONG` [s2] 1 cases — WT_DIV_EXIT_ENABLED T=-9.062/F=+0.028
    - `CLX_LONG` [s2] 1 cases — WT_15M_LH_WAIT_EXIT_ENABLED T=+0.365/F=+0.217
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  65 sym_sides
- mean final gain **12.48%** (Δ -4.59 vs prev) · median **0.28%** · mean B&H 38.89%
- positive-gain: **38/65** · beat B&H: **16/65** · mean within-round improvement +13.30% (Δ +0.72 vs prev)
- prev snapshot (20261003): n=70 mean_final=17.07%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `IOTXUSDT_LONG` | 76.08 | 21.47 | +83.30 | 98 | — | — |
| 2 | `XTZUSDT_LONG` | 71.06 | 47.61 | +73.11 | 217 | — | — |
| 3 | `EGLDUSDT_LONG` | 68.48 | 16.36 | +70.04 | 90 | — | — |
| 4 | `FILUSDC_LONG` | 62.73 | 52.16 | +62.41 | 245 | — | — |
| 5 | `KSMUSDT_LONG` | 61.64 | 46.77 | +60.74 | 194 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COMPUSDT_LONG` | -2.35 | 22.53 | +13.18 | 66 | — | — |
| 2 | `ZENUSDT_LONG` | -2.32 | 31.18 | +0.00 | — | — | — |
| 3 | `XMRUSDT_LONG` | -1.22 | 6.30 | +0.12 | 202 | — | — |
| 4 | `NOTUSDT_LONG` | -1.18 | 15.67 | +6.62 | 139 | — | — |
| 5 | `GRAMUSDT_LONG` | -1.09 | 8.72 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `IOTXUSDT_LONG` +83.30%, `XTZUSDT_LONG` +73.11%, `EGLDUSDT_LONG` +70.04%, `FILUSDC_LONG` +62.41%, `KSMUSDT_LONG` +60.74%

### CRYPTO_SHORT  ·  77 sym_sides
- mean final gain **-0.36%** (Δ -6.06 vs prev) · median **0.00%** · mean B&H -7.07%
- positive-gain: **6/77** · beat B&H: **18/77** · mean within-round improvement +0.03% (Δ -6.60 vs prev)
- prev snapshot (20261003): n=72 mean_final=5.69%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ENAUSDC_SHORT` | 2.33 | 0.00 | +2.33 | — | — | — |
| 2 | `XRPUSDC_SHORT` | 0.74 | -11.77 | +0.00 | — | — | — |
| 3 | `JASMYUSDT_SHORT` | 0.74 | -14.76 | +0.00 | — | — | — |
| 4 | `AVAXUSDC_SHORT` | 0.69 | -52.47 | +0.00 | — | — | — |
| 5 | `BNBUSDC_SHORT` | 0.27 | -12.80 | +0.00 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `GRAMUSDT_SHORT` | -8.29 | -8.72 | +0.00 | — | — | — |
| 2 | `BSVUSDT_SHORT` | -6.80 | -26.81 | +0.00 | — | — | — |
| 3 | `FILUSDC_SHORT` | -5.41 | -52.16 | +0.00 | — | — | — |
| 4 | `QNTUSDT_SHORT` | -3.33 | -100.00 | +0.00 | — | — | — |
| 5 | `ETHFIUSDC_SHORT` | -3.10 | -31.22 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `ENAUSDC_SHORT` +2.33%, `1INCHUSDT_SHORT` +0.00%, `ATOMUSDT_SHORT` +0.00%, `GOOGLUSDT_SHORT` +0.00%, `ZROUSDT_SHORT` +0.00%

### STOCKS_LONG  ·  105 sym_sides
- mean final gain **13.27%** (Δ -3.03 vs prev) · median **9.21%** · mean B&H 1.70%
- positive-gain: **96/105** · beat B&H: **87/105** · mean within-round improvement +15.16% (Δ +7.11 vs prev)
- prev snapshot (20261003): n=103 mean_final=16.30%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AXTI_LONG` | 71.43 | -4.86 | +72.49 | 68 | — | — |
| 2 | `BMNR_LONG` | 53.87 | 45.93 | +53.29 | 108 | — | — |
| 3 | `COIN_LONG` | 46.60 | 25.52 | +46.99 | 61 | — | — |
| 4 | `INTC_LONG` | 43.74 | 23.07 | +43.28 | 45 | — | — |
| 5 | `HOOD_LONG` | 40.98 | 12.68 | +40.48 | 31 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COE_LONG` | -11.51 | -37.33 | +0.00 | — | — | — |
| 2 | `NOC_LONG` | -4.75 | -17.75 | +8.42 | 38 | — | — |
| 3 | `AMZN_LONG` | -3.87 | -11.51 | -3.29 | 39 | — | — |
| 4 | `CLX_LONG` | -1.05 | -23.93 | +12.15 | 79 | — | — |
| 5 | `MNTS_LONG` | -0.49 | -10.54 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `AXTI_LONG` +72.49%, `BMNR_LONG` +53.29%, `COIN_LONG` +46.99%, `INTC_LONG` +43.28%, `HOOD_LONG` +40.48%

### STOCKS_SHORT  ·  86 sym_sides
- mean final gain **2.59%** (Δ -10.04 vs prev) · median **0.00%** · mean B&H -0.93%
- positive-gain: **24/86** · beat B&H: **48/86** · mean within-round improvement +3.65% (Δ -2.21 vs prev)
- prev snapshot (20261003): n=90 mean_final=12.63%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AXTI_SHORT` | 52.18 | 4.86 | +44.83 | 25 | — | — |
| 2 | `AXON_SHORT` | 37.58 | 34.40 | +37.58 | 51 | — | — |
| 3 | `UEC_SHORT` | 31.47 | 18.92 | +39.04 | 58 | — | — |
| 4 | `CIEN_SHORT` | 25.06 | 17.63 | +42.92 | 20 | — | — |
| 5 | `WDAY_SHORT` | 24.63 | 2.34 | +31.53 | 35 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AMD_SHORT` | -14.12 | -33.29 | +0.00 | — | — | — |
| 2 | `PSX_SHORT` | -10.83 | -7.71 | +0.00 | — | — | — |
| 3 | `EXEL_SHORT` | -8.78 | -7.76 | +0.00 | — | — | — |
| 4 | `MU_SHORT` | -7.67 | -15.05 | +0.00 | — | — | — |
| 5 | `SNDK_SHORT` | -7.54 | -8.01 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `AXTI_SHORT` +44.83%, `CIEN_SHORT` +42.92%, `UEC_SHORT` +39.04%, `AXON_SHORT` +37.58%, `WDAY_SHORT` +31.53%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `COTIUSDT_LONG` | CRYPTO_LONG | 84.11 | 1.39 | -82.72 |
| `MOVRUSDT_LONG` | CRYPTO_LONG | 82.24 | 0.00 | -82.24 |
| `QNTUSDT_LONG` | CRYPTO_LONG | 61.48 | 0.04 | -61.43 |
| `MINAUSDT_LONG` | CRYPTO_LONG | 57.39 | 0.00 | -57.39 |
| `UNIUSDC_LONG` | CRYPTO_LONG | 57.03 | 0.00 | -57.03 |
| `ZECUSDC_LONG` | CRYPTO_LONG | 84.11 | 28.87 | -55.25 |
| `WLDUSDC_LONG` | CRYPTO_LONG | 50.40 | 0.51 | -49.89 |
| `QBTS_SHORT` | STOCKS_SHORT | 41.79 | -5.78 | -47.57 |
| `ETCUSDT_LONG` | CRYPTO_LONG | 14.69 | 49.49 | +34.80 |
| `AXON_SHORT` | STOCKS_SHORT | 1.12 | 37.58 | +36.45 |
| `MASKUSDT_LONG` | CRYPTO_LONG | 0.00 | 46.64 | +46.64 |
| `XTZUSDT_LONG` | CRYPTO_LONG | 21.57 | 71.06 | +49.49 |
| `IOTAUSDT_LONG` | CRYPTO_LONG | 0.00 | 56.10 | +56.10 |
| `KSMUSDT_LONG` | CRYPTO_LONG | 0.00 | 61.64 | +61.64 |
| `EGLDUSDT_LONG` | CRYPTO_LONG | 2.29 | 68.48 | +66.19 |
| `IOTXUSDT_LONG` | CRYPTO_LONG | 5.54 | 76.08 | +70.54 |

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
- CRYPTO_LONG `MIN_GAIN_TO_BUY_AGGRESSIVELY` → 4.5 (avg_delta 0.502307, AUGMENT_TREND)
- CRYPTO_LONG `MTF_ATR_TRAIL_FILTER_TF` → 1h (avg_delta 0.269929, GLOBAL_RISK_GATES)
- CRYPTO_LONG `WT_15M_BOUNCE_OPEN_ENABLED` → False (avg_delta 1.781462, ENTRY_REVERSAL_BOUNCE)
- CRYPTO_LONG `WT_DIV_EXIT_ENABLED` → False (avg_delta 1.143354, ENTRY_REVERSAL_BOUNCE)
- CRYPTO_LONG `WT_DC_ENTRY_THRESHOLD` → 45 (avg_delta 0.921479, ENTRY_CONFIRMATION_GATES)
- CRYPTO_LONG `ATR_ADAPTIVE_SIZING_TARGET_PCT` → 3.0 (avg_delta 0.286107, ENTRY_BREAKOUT_CHANNEL)
- CRYPTO_LONG `MAX_ORDER_VALUE` → 90 (avg_delta 1.22976, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `REGIME_RANGING_WT_EXIT_VEL` → -3 (avg_delta 0.025725, EXIT_STRUCTURAL)
- CRYPTO_SHORT `BB_SQUEEZE_ENTRY_ENABLED` → True (avg_delta 4.035313, ENTRY_REVERSAL_BOUNCE)
- CRYPTO_SHORT `DC_BREAK_FILTER_TF` → D (avg_delta 0.816002, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `EMA_9_21_FILTER_FILTER_TF` → 15m (avg_delta 1.640991, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `SCALP_V3_AUG_BE_STOP_PCT` → 0.1 (avg_delta 0.321321, AUGMENT_RISK_SIZING)
- CRYPTO_SHORT `DC_EDGE_SIZING_MAX_MULT` → 6 (avg_delta 0.099503, AUGMENT_RISK_SIZING)
- STOCKS_LONG `REENTRY2_DC_BREAK_FILTER_TF` → 1h (avg_delta 0.405794, REENTRY_WINDOWED)
- STOCKS_LONG `MANDATORY_REENTRY_WT_FILTER_TF_MODE` → 0 (avg_delta 0.929826, ENTRY_REVERSAL_BOUNCE)
- STOCKS_LONG `BB_BOUNCE_ENTRY_TF` → OFF (avg_delta 1.887604, ENTRY_REVERSAL_BOUNCE)
- STOCKS_LONG `DC_BREAKOUT_TF` → OFF (avg_delta 1.812236, ENTRY_BREAKOUT_CHANNEL)
- STOCKS_LONG `RZ_BOT_BB_THRESHOLD` → 0.225 (avg_delta 0.135453, ENTRY_BREAKOUT_CHANNEL)
- STOCKS_LONG `LH_HL_FILTER_DC_THRESHOLD_PCT` → 1 (avg_delta 0.346175, ENTRY_CONFIRMATION_GATES)
- STOCKS_LONG `BB_EXIT_AT_LOSS_TF` → 15m (avg_delta 0.005605, EXIT_STRUCTURAL)
- STOCKS_LONG `MIN_GAIN_TO_BUY_AGGRESSIVELY` → 6 (avg_delta 1.362522, AUGMENT_TREND)
- STOCKS_SHORT `REENTRY_TIER1_SIZE_MULT_TRADIER` → 3 (avg_delta 0.279136, REENTRY_WINDOWED)
- STOCKS_SHORT `MAX_AUGMENTS_PER_POSITION` → 0 (avg_delta 0.295101, AUGMENT_RISK_SIZING)
- STOCKS_SHORT `MANDATORY_REENTRY_WT_FILTER_TF_MODE` → 0 (avg_delta 1.272554, ENTRY_REVERSAL_BOUNCE)
- STOCKS_SHORT `LH_HL_FILTER_DC_THRESHOLD_PCT` → 0.25 (avg_delta 0.498628, ENTRY_CONFIRMATION_GATES)
- STOCKS_SHORT `BB_PULLBACK_GATE_FILTER_TF` → OFF (avg_delta 1.19653, ENTRY_REVERSAL_BOUNCE)
- STOCKS_SHORT `BREAKOUT_RETEST_FILTER_TF` → OFF (avg_delta 0.024051, ENTRY_BREAKOUT_CHANNEL)
- STOCKS_SHORT `GAP_CLOSE_MOC_EXIT_ENABLED` → False (avg_delta 0.046138, EXIT_STRUCTURAL)
- STOCKS_SHORT `REENTRY2_DC_BREAK_FILTER_TF` → OFF (avg_delta 0.235834, REENTRY_WINDOWED)
_29 promotions today_

