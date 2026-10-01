# v15 Morning Report — 20261001

_Generated 2026-10-01T14:15Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 0 | — | — | 5fe299c878c6 | no rows in window |
| s1 | s1-int | 249 | 10-01 14:15Z | 09-30 12:59Z | 5fe299c878c6 | OK |
| s2 | s2 | 286 | 10-01 14:06Z | 09-30 08:17Z | 5fe299c878c6 | OK |
| s5 | s5 | 116 | 10-01 13:57Z | 09-30 23:51Z | 5fe299c878c6 | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 451**

Engine md5 parity across boxes: ✅ `5fe299c878c6` on mac, s1, s2, s5.

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **8**
    - `LRCUSDT_LONG` [s1] 1373 switches all-zero
    - `NKNUSDT_LONG` [s1] 1373 switches all-zero
    - `NKNUSDT_SHORT` [s1] 1314 switches all-zero
    - `LRCUSDT_SHORT` [s1] 1314 switches all-zero
    - `OXY_SHORT` [s2] 814 switches all-zero
    - `MRVLUSDT_LONG` [s5] 726 switches all-zero
    - `MRVLUSDT_SHORT` [s5] 712 switches all-zero
    - `OXY_LONG` [s2] 437 switches all-zero
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **4**
    - `TRXUSDT_SHORT` [s1] -0.168082 ×67/103
    - `ACTUSDT_SHORT` [s1] -0.074964 ×45/90
    - `GTCUSDT_SHORT` [s1] -0.345047 ×23/34
    - `APEUSDT_LONG` [s1] -3.235419 ×22/30
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **9**
    - `AVGO_LONG` [s2]
    - `BATUSDT_SHORT` [s1]
    - `JOBY_LONG` [s5]
    - `LRCUSDT_LONG` [s1]
    - `LRCUSDT_SHORT` [s1]
    - `MDT_LONG` [s1]
    - `NKNUSDT_LONG` [s1]
    - `NKNUSDT_SHORT` [s1]
    - `OXY_SHORT` [s2]

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **9 sym_sides, 10 switch×baseline cases**
    - worst offending switches (by # sym_sides): `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST`×3, `HTF_GATE_D_MANDATORY`×3, `LH_HL_FILTER_REQUIRE_BOTH`×1, `WT_15M_LH_WAIT_EXIT_ENABLED`×1, `EMA_BLANKET_FILTER_ENABLED`×1, `NEWBORN_LOSS_KILL_ENABLED`×1
    - `XMRUSDT_LONG` [s1] 2 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.164/F=-9.436; WT_15M_LH_WAIT_EXIT_ENABLED T=-1.720/F=+0.300
    - `ZENUSDT_LONG` [s1] 1 cases — LH_HL_FILTER_REQUIRE_BOTH T=-1.311/F=-0.429
    - `TRBUSDT_SHORT` [s1] 1 cases — HTF_GATE_D_MANDATORY T=-1.228/F=-1.228
    - `UNIUSDC_LONG` [s2] 1 cases — HTF_GATE_D_MANDATORY T=-1.922/F=-2.156
    - `XLMUSDT_SHORT` [s5] 1 cases — HTF_GATE_D_MANDATORY T=-0.063/F=-0.060
    - `MANAUSDT_LONG` [s2] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+0.634/F=-17.051
    - `TRBUSDT_LONG` [s1] 1 cases — NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST T=+5.373/F=-4.717
    - `BNO_SHORT` [s2] 1 cases — EMA_BLANKET_FILTER_ENABLED T=-0.001/F=+0.003
    - `NVDA_SHORT` [s2] 1 cases — NEWBORN_LOSS_KILL_ENABLED T=+0.216/F=-2.013
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  110 sym_sides
- mean final gain **33.27%** (Δ +21.17 vs prev) · median **26.82%** · mean B&H 25.73%
- positive-gain: **107/110** · beat B&H: **79/110** · mean within-round improvement +14.12% (Δ +3.08 vs prev)
- prev snapshot (20260930): n=43 mean_final=12.10%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `ARUSDT_LONG` | 103.76 | 93.59 | +0.01 | 317 | — | — |
| 2 | `UNIUSDC_LONG` | 103.71 | 68.21 | +87.33 | 184 | — | — |
| 3 | `ZECUSDC_LONG` | 101.42 | 66.20 | +3.30 | 80 | — | — |
| 4 | `WLDUSDC_LONG` | 94.32 | 47.39 | +18.73 | 139 | — | — |
| 5 | `GRTUSDT_LONG` | 86.41 | 78.13 | +1.16 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MSFTUSDT_LONG` | -0.28 | 6.14 | +3.67 | 284 | — | — |
| 2 | `NKNUSDT_LONG` | 0.00 | 0.00 | +0.00 | — | — | — |
| 3 | `LRCUSDT_LONG` | 0.00 | 0.00 | +0.00 | — | — | — |
| 4 | `BTCDOMUSDT_LONG` | 0.63 | -5.10 | +0.39 | 20 | — | — |
| 5 | `NVDAUSDT_LONG` | 2.58 | -0.60 | +0.21 | 61 | — | — |

Biggest within-round improvements (baseline → final): `UNIUSDC_LONG` +87.33%, `XTZUSDT_LONG` +75.55%, `IOTXUSDT_LONG` +73.29%, `1000PEPEUSDC_LONG` +61.45%, `KSMUSDT_LONG` +53.03%

### CRYPTO_SHORT  ·  109 sym_sides
- mean final gain **10.93%** (Δ +11.28 vs prev) · median **7.32%** · mean B&H -24.36%
- positive-gain: **93/109** · beat B&H: **105/109** · mean within-round improvement +6.75% (Δ +0.85 vs prev)
- prev snapshot (20260930): n=30 mean_final=-0.36%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `DASHUSDT_SHORT` | 50.17 | -31.24 | +52.59 | 167 | — | — |
| 2 | `ARUSDT_SHORT` | 43.66 | -93.59 | +5.64 | 86 | — | — |
| 3 | `UNIUSDC_SHORT` | 42.05 | -68.21 | +42.52 | 164 | — | — |
| 4 | `COTIUSDT_SHORT` | 39.30 | 1.36 | +7.19 | — | — | — |
| 5 | `XRPUSDC_SHORT` | 39.18 | -8.18 | +12.96 | 134 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `TRBUSDT_SHORT` | -0.72 | -14.82 | +1.08 | 45 | — | — |
| 2 | `COMPUSDT_SHORT` | -0.45 | -41.06 | +0.24 | 74 | — | — |
| 3 | `LTCUSDC_SHORT` | -0.45 | -37.20 | +1.94 | 10 | — | — |
| 4 | `RSRUSDT_SHORT` | -0.33 | -24.09 | +0.52 | 150 | — | — |
| 5 | `1000SATSUSDT_SHORT` | -0.31 | -11.86 | +0.05 | 99 | — | — |

Biggest within-round improvements (baseline → final): `DASHUSDT_SHORT` +52.59%, `UNIUSDC_SHORT` +42.52%, `FILUSDC_SHORT` +38.64%, `XTZUSDT_SHORT` +33.21%, `NOTUSDT_SHORT` +28.43%

### STOCKS_LONG  ·  116 sym_sides
- mean final gain **13.73%** (Δ +7.90 vs prev) · median **10.91%** · mean B&H 0.86%
- positive-gain: **111/116** · beat B&H: **103/116** · mean within-round improvement +11.14% (Δ +7.80 vs prev)
- prev snapshot (20260930): n=29 mean_final=5.82%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MSTR_LONG` | 62.07 | 62.72 | +3.84 | 153 | — | — |
| 2 | `BMNR_LONG` | 51.29 | 43.45 | +29.94 | 19 | — | — |
| 3 | `COIN_LONG` | 46.60 | 25.52 | +46.68 | 108 | — | — |
| 4 | `GME_LONG` | 38.34 | 36.87 | +34.46 | 173 | — | — |
| 5 | `MRVL_LONG` | 38.25 | 21.21 | +39.35 | 345 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `AVGO_LONG` | -4.13 | -8.66 | -4.13 | — | — | — |
| 2 | `MDT_LONG` | -2.78 | -6.32 | -2.78 | — | — | — |
| 3 | `JOBY_LONG` | -1.20 | -20.81 | -1.20 | — | — | — |
| 4 | `HD_LONG` | -0.40 | -15.75 | +4.51 | 24 | — | — |
| 5 | `LAC_LONG` | -0.33 | -12.00 | +2.59 | 66 | — | — |

Biggest within-round improvements (baseline → final): `COIN_LONG` +46.68%, `MRVL_LONG` +39.35%, `ARM_LONG` +36.09%, `AXTI_LONG` +34.52%, `GME_LONG` +34.46%

### STOCKS_SHORT  ·  116 sym_sides
- mean final gain **15.34%** (Δ +12.08 vs prev) · median **12.73%** · mean B&H -0.39%
- positive-gain: **111/116** · beat B&H: **108/116** · mean within-round improvement +13.44% (Δ +9.54 vs prev)
- prev snapshot (20260930): n=26 mean_final=3.26%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `COE_SHORT` | 84.53 | 38.91 | +82.70 | 47 | — | — |
| 2 | `AXTI_SHORT` | 49.39 | 11.69 | +43.02 | 50 | — | — |
| 3 | `USAR_SHORT` | 41.48 | 23.73 | +29.29 | 111 | — | — |
| 4 | `ASTS_SHORT` | 39.14 | 13.61 | +44.94 | 267 | — | — |
| 5 | `JOBY_SHORT` | 36.67 | 20.81 | +30.30 | 239 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MPC_SHORT` | -0.38 | -7.84 | +2.21 | 14 | — | — |
| 2 | `QRVO_SHORT` | -0.10 | -30.67 | +0.99 | 28 | — | — |
| 3 | `BG_SHORT` | -0.03 | -8.27 | +0.00 | — | — | — |
| 4 | `XOM_SHORT` | -0.02 | -9.55 | +5.60 | 14 | — | — |
| 5 | `OXY_SHORT` | 0.00 | -15.26 | +0.00 | — | — | — |

Biggest within-round improvements (baseline → final): `COE_SHORT` +82.70%, `ASTS_SHORT` +44.94%, `AXTI_SHORT` +43.02%, `HAO_SHORT` +37.50%, `ALB_SHORT` +35.58%

### Day-over-day movers (final gain vs previous snapshot)
| sym_side | cat_side | prev% | now% | Δ |
|---|---|---|---|---|
| `RVNUSDT_SHORT` | CRYPTO_SHORT | 30.68 | 3.17 | -27.51 |
| `LAC_LONG` | STOCKS_LONG | 13.33 | -0.33 | -13.66 |
| `NEM_LONG` | STOCKS_LONG | 24.04 | 10.43 | -13.62 |
| `UEC_SHORT` | STOCKS_SHORT | 41.01 | 34.89 | -6.12 |
| `GOOGL_SHORT` | STOCKS_SHORT | 13.06 | 11.73 | -1.33 |
| `GLD_LONG` | STOCKS_LONG | 5.30 | 4.24 | -1.06 |
| `RGLD_LONG` | STOCKS_LONG | 17.03 | 16.08 | -0.95 |
| `THETAUSDT_SHORT` | CRYPTO_SHORT | 0.00 | -0.26 | -0.26 |
| `1000PEPEUSDC_LONG` | CRYPTO_LONG | 3.90 | 63.13 | +59.23 |
| `AXSUSDT_LONG` | CRYPTO_LONG | 0.46 | 64.24 | +63.77 |
| `DOTUSDT_LONG` | CRYPTO_LONG | 1.34 | 69.87 | +68.53 |
| `XTZUSDT_LONG` | CRYPTO_LONG | 13.46 | 85.27 | +71.81 |
| `IOTXUSDT_LONG` | CRYPTO_LONG | -1.13 | 76.08 | +77.20 |
| `UNIUSDC_LONG` | CRYPTO_LONG | 20.57 | 103.71 | +83.14 |
| `ZECUSDC_LONG` | CRYPTO_LONG | 12.77 | 101.42 | +88.66 |
| `WLDUSDC_LONG` | CRYPTO_LONG | 0.00 | 94.32 | +94.32 |

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **232** produce ≥1 positive value · **0** never tested · **2** DEAD no-op (moves nothing) · **56** never help (honest losers). Total levers seen: **290**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**2 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| switch | `GAP_RISK_EXIT_LONG_ENABLED` | S_LONG | 1 | 2 | 0 | -0.006 | -0.006 | trace `GAP_RISK_EXIT_LONG_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SATOSHIT_ENABLED` | S_SHORT | 1 | 1 | 0 | -0.004 | -0.004 | trace `SATOSHIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **56** levers (56 switches, 0 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (56):** `BOUNCE_AUGMENT_K_D_THRESHOLD`, `PYRAMID_ENABLED`, `WATCHDOG_DC_FORCE_OPEN_ENABLED`, `MOMENTUM_SMA_WATCHDOG_ENABLED`, `ALL_TF_AGAINST_CLOSE_MIN_TFS`, `BOUNCE_AUGMENT_ENABLED`, `BOUNCE_AUGMENT_MIN_LOSS_PCT`, `WT_PERCENTILE_EXIT_OB_D`, `WT_DC_EXIT_ENABLED`, `CHOP_TRENDING_THRESHOLD`, `BB_PULLBACK_GATE_TF`, `CLENOW_ENABLED`, `LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED`, `D_TREND_REQUIRED`, `TF_HTF1`, `TF_HTF3`, `HTF_MIN_ALIGNED`, `VIGILANCE_RECOVERY_REENTRY_ENABLED`, `RZ_TOP_BB_THRESHOLD`, `MTF_GR_EXIT_GATE_ENABLED`, `HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H`, `WT_4H_VEL_EXIT_REQUIRE_PROFIT`, `CT_CHOP_4H_GATE_ENABLED`, `BB_PULLBACK_GATE_ENABLED`, `STOCH_CROSS_1H_EXIT_ENABLED`, `RZ_BREAKOUT_ENTRY_ENABLED`, `BB_SQUEEZE_WIDTH_PERCENTILE`, `WT_15M_BOUNCE_REQUIRE_BOTH_HTF`, `WT_15M_BOUNCE_FILTER_HH_ENABLED`, `WT_DC_DETAILED_ENTRY_THRESHOLD`, `MTF_ATR_TRAIL_ENABLED_TRADIER`, `WT_D_BOUNCE_DD_STOP_ENABLED`, `ENTRY_ZONE_LONG`, `MOM3_LONG_THRESHOLD`, `WT_15M_BOUNCE_FILTER_HL_ENABLED`, `WT_15M_BOUNCE_VOLUME_FILTER_ENABLED`, `RZ_BOT_BB_THRESHOLD`, `MTF_DC_REJECT_EXIT_ENABLED`, `BB_RECOVERY_FILTER_TF`, `VIGILANCE_RECOVERY_BOUNCE_OK`, `BB_SQUEEZE_MIN_ALIGNMENT`, `FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED`, `FORMATION_FLAG_PENNANT_EXIT_ENABLED`, `FORMATION_HEAD_SHOULDERS_EXIT_ENABLED`, `FORMATION_TREND_STRUCTURE_EXIT_ENABLED`, `FORMATION_TRIANGLE_EXIT_ENABLED`, `FORMATION_WEDGE_EXIT_ENABLED`, `GAP_MOC_DC_PROXIMITY_PCT`, `RZ_EXIT_ENABLED`, `ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME`, `MI_ENTRY_ENABLED_TRADIER`, `ALL_TF_AGAINST_CLOSE_ENABLED`, `GAP_PER_SYMBOL_AVG_THRESH_PCT`, `K_ZONE_LONG_THRESHOLD`, `MFI_FLIP_EXIT_LONG_THRESHOLD`, `REENTRY_MANDATORY`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions

_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_

### live-faithful (backtest_v12_engine) coverage: **0** sym_sides today
- parity gaps (vec vs live-faithful): **0**

### Promotions made today (bold defaults, cat_side_promotions.json)
_none today_

