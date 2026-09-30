# v15 Morning Report — 20260930

_Generated 2026-09-30T21:50Z · window = last 30.0h · NO-LIES: only real recorded deltas, no annualisation._

## 1 · Round verification

| source | alias | sym_sides in window | freshest | oldest | engine md5 | status |
|---|---|---|---|---|---|---|
| mac | local | 128 | 09-30 03:07Z | 09-29 15:51Z | 8fbc7ffbdf0b | OK |

**Deduped sym_sides in window (newest-per-sym_side across all boxes): 128**

Engine md5 parity across boxes: ✅ `8fbc7ffbdf0b` on mac.

- **ZERO-DELTA sym_sides** (≥8 switches, every delta ~0 → dead NPZ / no-op stubs): **0**
- **REPEATED-DELTA sym_sides** (one value repeated across many switches → v12 synthetic-distinctness fabrication, NEVER promote): **7**
    - `BNBUSDC_SHORT` [mac] -5.970394 ×1466/2272
    - `GOOGLUSDT_SHORT` [mac] -12.472691 ×1398/1891
    - `AVAXUSDC_LONG` [mac] -0.329211 ×951/1381
    - `NEM_LONG` [mac] -3.887013 ×846/1452
    - `BHP_LONG` [mac] -1.008925 ×293/379
    - `DELL_SHORT` [mac] -0.661465 ×26/48
    - `EXEL_LONG` [mac] -2.574379 ×16/30
- **DATA_ERROR / zero-trade sym_sides** (no baseline trades — NPZ gap): **0**

- **DEFAULT-BASELINE INCONSISTENCIES** (a bool switch where both True & False scored non-zero vs the same baseline — one MUST be the default → 0; NO-LIES): **17 sym_sides, 233 switch×baseline cases**
    - worst offending switches (by # sym_sides): `GR_FILTER_VEC_ENABLED`×7, `BAND_ARROW_ENABLED`×7, `DELTA_REENTRY_FILTER_ENABLED`×7, `HTF_BULL_ENTRY_FILTER_ENABLED`×7, `HTF_DIRECTION_GATE_ENABLED`×6, `EMA_BLANKET_FILTER_ENABLED`×6, `BB_SQUEEZE_EXIT_ENABLED`×5, `COUNTER_TREND_ADD_BLOCK_ENABLED`×5, `ALL_TF_AGAINST_CLOSE_ENABLED`×5, `HA_WICK_QUALITY_ENABLED`×5, `HTF_TREND_VETO_BYPASS_ENABLED`×5, `LH_HL_FILTER_ENABLED`×5
    - `1INCHUSDT_SHORT` [mac] 43 cases — OPEN_RATE_BREAKER_ENABLED T=-4.258/F=-4.258; HTF_DIRECTION_GATE_ENABLED T=-4.258/F=-4.258
    - `ADAUSDC_SHORT` [mac] 43 cases — UNIVERSAL_NOLOSS_GATE T=-7.279/F=-4.468; COUNTER_TREND_ADD_BLOCK_ENABLED T=-4.468/F=-7.279
    - `XTZUSDT_LONG` [mac] 21 cases — HTF_DIRECTION_GATE_ENABLED T=-1.097/F=-1.097; MTS_GATE_ENABLED T=-1.097/F=-1.097
    - `ETHUSDC_SHORT` [mac] 21 cases — OI_CONFIRM_ENABLED T=-20.658/F=-21.196; UNIVERSAL_NOLOSS_GATE T=-20.596/F=-20.596
    - `GOOGLUSDT_SHORT` [mac] 20 cases — LH_HL_FILTER_ENABLED T=-15.386/F=-15.386; MTS_GATE_ENABLED T=-15.386/F=-15.386
    - `MSTR_LONG` [mac] 18 cases — HAIKU_ENTRY_GATE_ENABLED T=+7.403/F=+7.403; WT_ACCEL_EXIT_ENABLED T=+1.989/F=+1.989
    - `BHP_LONG` [mac] 18 cases — BB_SQUEEZE_ENTRY_ENABLED T=-1.009/F=-1.009; ALL_TF_AGAINST_CLOSE_ENABLED T=-1.009/F=-1.009
    - `BNBUSDC_SHORT` [mac] 17 cases — OI_CONFIRM_ENABLED T=-5.970/F=-5.970; BAND_ARROW_ENABLED T=-5.970/F=-5.970
    - `NKE_SHORT` [mac] 7 cases — WT_DIV_EXIT_ENABLED T=-13.093/F=-13.093; ALL_TF_AGAINST_CLOSE_ENABLED T=-13.093/F=-13.093
    - `AIAUSDT_LONG` [mac] 7 cases — SHORT_DC_LOW_BREAK_ENABLED T=-10.782/F=-10.782; WT_15M_BOUNCE_REL_VOL_GT_1 T=-10.782/F=-10.782
    - `COMPUSDT_LONG` [mac] 4 cases — WT_AGAINST_FILTER_ENABLED T=-15.630/F=-15.630; ALL_TF_AGAINST_CLOSE_ENABLED T=-15.630/F=-15.630
    - `NEM_LONG` [mac] 3 cases — WT_ACCEL_EXIT_ENABLED T=+14.011/F=+14.011; HTF_BULL_ENTRY_FILTER_ENABLED T=+14.011/F=+14.011
    - _Fix: the engine must return the frozen-baseline gain (delta 0) for the value that equals the sym_side's running config. A non-zero there means the baseline snapshot and the candidate eval used different configs/NPZ, or the eval is non-deterministic — trace `evaluate_prepared_sanitized` baseline handling. These deltas are unsafe to promote._

## 2 · Per cat_side performance

### CRYPTO_LONG  ·  43 sym_sides
- mean final gain **12.10%** · median **8.77%** · mean B&H 29.45%
- positive-gain: **33/43** · beat B&H: **9/43** · mean within-round improvement +11.04%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `MINAUSDT_LONG` | 57.39 | 130.30 | +36.58 | 108 | — | — |
| 2 | `COTIUSDT_LONG` | 34.84 | -2.01 | +2.33 | 148 | — | — |
| 3 | `GRTUSDT_LONG` | 34.74 | 79.63 | +21.21 | — | — | — |
| 4 | `NEARUSDC_LONG` | 34.62 | 164.70 | +33.82 | 173 | — | — |
| 5 | `ENAUSDC_LONG` | 31.38 | 58.17 | +25.39 | — | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `BTCDOMUSDT_LONG` | -3.81 | -5.03 | -0.08 | 45 | — | — |
| 2 | `IOTXUSDT_LONG` | -1.13 | 19.03 | +1.19 | 176 | — | — |
| 3 | `AIAUSDT_LONG` | -0.01 | -8.05 | +2.09 | 39 | 0.074 | 10.62 |
| 4 | `XRPUSDC_LONG` | 0.00 | 10.02 | +1.35 | 24 | — | — |
| 5 | `SANDUSDT_LONG` | 0.00 | 10.77 | +7.44 | 13 | — | — |

Biggest within-round improvements (baseline → final): `MINAUSDT_LONG` +36.58%, `NEARUSDC_LONG` +33.82%, `CHRUSDT_LONG` +29.05%, `ATOMUSDT_LONG` +25.52%, `ENAUSDC_LONG` +25.39%

### CRYPTO_SHORT  ·  30 sym_sides
- mean final gain **-0.36%** · median **-0.06%** · mean B&H -24.66%
- positive-gain: **5/30** · beat B&H: **28/30** · mean within-round improvement +5.89%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `RVNUSDT_SHORT` | 30.68 | 23.05 | +30.66 | — | — | — |
| 2 | `GOOGLUSDT_SHORT` | 15.39 | 7.82 | +13.22 | — | — | — |
| 3 | `BNBUSDC_SHORT` | 4.53 | -9.00 | +16.44 | 15 | -0.253 | 6.34 |
| 4 | `WLDUSDC_SHORT` | 1.67 | -30.28 | +2.10 | 16 | — | — |
| 5 | `APPUSDT_SHORT` | 0.16 | 2.03 | +6.89 | 22 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `VETUSDT_SHORT` | -16.17 | -28.31 | -10.01 | 90 | — | — |
| 2 | `GALAUSDT_SHORT` | -13.00 | -30.06 | +1.81 | 134 | — | — |
| 3 | `ZECUSDC_SHORT` | -9.02 | -69.28 | -2.17 | — | — | — |
| 4 | `ETHUSDC_SHORT` | -6.75 | -9.68 | +3.01 | 13 | -0.277 | 21.31 |
| 5 | `ADAUSDC_SHORT` | -4.94 | -22.23 | -3.22 | — | — | — |

Biggest within-round improvements (baseline → final): `WIFUSDC_SHORT` +48.06%, `RVNUSDT_SHORT` +30.66%, `BNBUSDC_SHORT` +16.44%, `GOOGLUSDT_SHORT` +13.22%, `ALGOUSDT_SHORT` +12.26%

### STOCKS_LONG  ·  29 sym_sides
- mean final gain **5.82%** · median **4.25%** · mean B&H 6.42%
- positive-gain: **20/29** · beat B&H: **16/29** · mean within-round improvement +3.34%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `NEM_LONG` | 24.04 | 38.55 | +17.10 | — | — | — |
| 2 | `MSTR_LONG` | 17.06 | 27.39 | +9.50 | 236 | 0.167 | 5.59 |
| 3 | `RGLD_LONG` | 17.03 | 13.78 | +5.39 | 88 | — | — |
| 4 | `PR_LONG` | 13.93 | 12.85 | +2.23 | 81 | — | — |
| 5 | `LAC_LONG` | 13.33 | -6.31 | +11.84 | 111 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `TXN_LONG` | -1.04 | -4.46 | +2.19 | 10 | — | — |
| 2 | `BWXT_LONG` | -0.31 | -14.25 | +11.00 | 15 | — | — |
| 3 | `RRC_LONG` | 0.00 | 0.23 | -12.20 | 23 | — | — |
| 4 | `NUKZ_LONG` | 0.00 | -7.30 | +5.44 | — | — | — |
| 5 | `DELL_LONG` | 0.00 | 27.73 | -4.12 | 38 | — | — |

Biggest within-round improvements (baseline → final): `NEM_LONG` +17.10%, `LAC_LONG` +11.84%, `BWXT_LONG` +11.00%, `SCCO_LONG` +10.11%, `MSTR_LONG` +9.50%

### STOCKS_SHORT  ·  26 sym_sides
- mean final gain **3.26%** · median **0.12%** · mean B&H -5.05%
- positive-gain: **14/26** · beat B&H: **20/26** · mean within-round improvement +3.90%

| rank | TOP by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `UEC_SHORT` | 41.01 | 17.07 | +20.88 | — | — | — |
| 2 | `JOBY_SHORT` | 18.39 | 7.35 | +4.00 | 79 | — | — |
| 3 | `GOOGL_SHORT` | 13.06 | 7.82 | +8.89 | 49 | — | — |
| 4 | `NLR_SHORT` | 6.74 | 5.37 | +0.00 | — | — | — |
| 5 | `NOC_SHORT` | 4.07 | -4.59 | +4.06 | 32 | — | — |

| rank | BOTTOM by final gain | final% | B&H% | Δ improve | trades | sharpe | dd% |
|---|---|---|---|---|---|---|---|
| 1 | `GDX_SHORT` | -5.03 | -13.18 | -5.88 | 35 | — | — |
| 2 | `CF_SHORT` | -0.50 | -7.26 | +7.19 | 16 | — | — |
| 3 | `CRM_SHORT` | -0.41 | -37.99 | +13.96 | 94 | — | — |
| 4 | `PYPL_SHORT` | -0.23 | -11.88 | +0.90 | 17 | — | — |
| 5 | `RDDT_SHORT` | 0.00 | 21.59 | +2.47 | 49 | — | — |

Biggest within-round improvements (baseline → final): `UEC_SHORT` +20.88%, `CRM_SHORT` +13.96%, `GOOGL_SHORT` +8.89%, `IBM_SHORT` +8.74%, `DELL_SHORT` +7.31%

### Day-over-day movers
_First snapshot written this run — per-sym_side day-over-day comparison starts tomorrow. (Switch/filter day-over-day is available below from the avg_delta archive.)_

---

## 3 · Switches & filters that produce no value — agent fix worklist

_Aggregated to the LEVER level (base switch / filter, across all its values and all 4 cat_sides) from `SPREADSHEETS/v15_avg_delta_latest.xlsx` — the authoritative NO-LIES aggregate rebuilt from every recorded delta. A lever that moves nothing anywhere is a wiring gap or a no-op stub: both are real coverage/NO-LIES defects. Action every one in 3a/3b. 3c (honest losers) is condensed._

Lever summary: **176** produce ≥1 positive value · **0** never tested · **249** DEAD no-op (moves nothing) · **100** never help (honest losers). Total levers seen: **525**.

### 3a · NEVER TESTED — no candidate delta in any cat_side (wiring / whitelist gap)

_None._

### 3b · DEAD NO-OP — tested but EVERY value moves gain < 0.01% (dead engine key / stub)

**249 levers are exercised by the sweep but change nothing** — a switch the optimiser can never use is wasted compute and a silent wiring bug. _Fix each: trace the real code path for the lever in `ez_manage.py`/`tradier_manage.py` (crypto vs stocks) AND `v12_quick_engine.py` — a live-wired lever MUST change trades. If it is a known stub farm (memory `filter_stub_farms_and_ghost_push`), wire it on all 4 surfaces (vec+ez+tradier+config), never a proxy (memory `switch_wiring_pattern_20260930`)._

| kind | lever | cat_sides | values | n | pos | best avg | worst avg | fix |
|---|---|---|---|---|---|---|---|---|
| filter | `WT_DC_DC_POS_THRESHOLD_SHORT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 5599 | 0 | +0.000 | +0.000 | trace `WT_DC_DC_POS_THRESHOLD_SHORT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `WT_DC_STOCH_THRESHOLD_LONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 4913 | 0 | +0.000 | +0.000 | trace `WT_DC_STOCH_THRESHOLD_LONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `WT_DC_STOCH_THRESHOLD_SHORT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 4911 | 0 | +0.000 | +0.000 | trace `WT_DC_STOCH_THRESHOLD_SHORT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `DC_BREAKOUT_TF_EXPANDED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 1836 | 5 | +0.004 | +0.000 | trace `DC_BREAKOUT_TF_EXPANDED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `WT_DC_DC_POS_THRESHOLD_LONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 1729 | 0 | +0.000 | +0.000 | trace `WT_DC_DC_POS_THRESHOLD_LONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `WT_DC_DIRECT_TF_ENTRY` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1041 | 2 | +0.001 | +0.000 | trace `WT_DC_DIRECT_TF_ENTRY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `REENTRY_15M_LRL_PULLBACK_HTF` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `REENTRY_15M_LRL_PULLBACK_HTF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| filter | `REENTRY_15M_DC_BASIS_CROSS_HTF` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `REENTRY_15M_DC_BASIS_CROSS_HTF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `COOLDOWN_LOCKS_FILTER_TF` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 26741 | 0 | +0.000 | -0.010 | trace `COOLDOWN_LOCKS_FILTER_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead (FILTER_TF stub? check opportune_filter_map) |
| switch | `DUP_GUARD_FILTER_TF` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 26726 | 0 | +0.000 | -0.010 | trace `DUP_GUARD_FILTER_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead (FILTER_TF stub? check opportune_filter_map) |
| switch | `BANDAID_OFF_LOSER_RECOVER_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 23304 | 0 | +0.000 | -0.006 | trace `BANDAID_OFF_LOSER_RECOVER_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HLR_SMA_BAND_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 23293 | 0 | +0.000 | -0.004 | trace `HLR_SMA_BAND_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `NEW_POSITION_MAX_LOSS_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 23212 | 0 | +0.000 | -0.003 | trace `NEW_POSITION_MAX_LOSS_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTS_BOTTOM_BONUS_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 22667 | 0 | +0.000 | -0.003 | trace `MTS_BOTTOM_BONUS_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTS_BOTTOM_STRONG_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 22350 | 0 | +0.000 | -0.004 | trace `MTS_BOTTOM_STRONG_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 21842 | 2 | +0.000 | -0.003 | trace `NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EMA_9_21_FILTER_MIN_TFS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 20817 | 7 | +0.001 | -0.001 | trace `EMA_9_21_FILTER_MIN_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GR_FILTER_VEC_MIN_TFS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 17027 | 0 | +0.000 | -0.001 | trace `GR_FILTER_VEC_MIN_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FUNDING_GATE_SHORT_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 12080 | 0 | +0.000 | +0.000 | trace `FUNDING_GATE_SHORT_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FUNDING_GATE_LONG_MAX` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 12056 | 0 | +0.000 | +0.000 | trace `FUNDING_GATE_LONG_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FUNDING_GATE_MTF_REQUIRED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11955 | 0 | +0.000 | +0.000 | trace `FUNDING_GATE_MTF_REQUIRED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXECUTE_NOW_SINGLE_GATE_ENFORCE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11834 | 2 | +0.001 | +0.000 | trace `EXECUTE_NOW_SINGLE_GATE_ENFORCE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_GATE_BYPASS_RZ` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11808 | 0 | +0.000 | +0.000 | trace `HTF_GATE_BYPASS_RZ` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GOLDEN_RULE_BASE_USD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11803 | 0 | +0.000 | +0.000 | trace `GOLDEN_RULE_BASE_USD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 11727 | 0 | +0.000 | +0.000 | trace `NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTF_FILTER_STRONG_BUY_QUICK_BYPASS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11652 | 0 | +0.000 | +0.000 | trace `MTF_FILTER_STRONG_BUY_QUICK_BYPASS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_TREND_VETO_BYPASS_REASONS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 11614 | 0 | +0.000 | +0.000 | trace `HTF_TREND_VETO_BYPASS_REASONS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_TREND_VETO_BYPASS_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11547 | 0 | +0.000 | -0.001 | trace `HTF_TREND_VETO_BYPASS_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTS_GATE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11544 | 0 | +0.000 | -0.005 | trace `MTS_GATE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BAND_ARROW_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11542 | 0 | +0.000 | -0.001 | trace `BAND_ARROW_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LIVE_VEC_EMERGENCY_BRAKE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11540 | 0 | +0.000 | -0.001 | trace `LIVE_VEC_EMERGENCY_BRAKE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEADERBOARD_FILTER` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 11510 | 0 | +0.000 | +0.000 | trace `LEADERBOARD_FILTER` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11500 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LR_BAND_LADDER_STOCH_EXTREME` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 11498 | 0 | +0.000 | -0.006 | trace `LR_BAND_LADDER_STOCH_EXTREME` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EZ_MANAGE_THROTTLER_RATE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 11431 | 0 | +0.000 | +0.000 | trace `EZ_MANAGE_THROTTLER_RATE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LR_BAND_LADDER_TF_TOP` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11366 | 3 | +0.001 | -0.003 | trace `LR_BAND_LADDER_TF_TOP` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LR_BAND_LADDER_TF_BOTTOM` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11366 | 0 | +0.000 | -0.001 | trace `LR_BAND_LADDER_TF_BOTTOM` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_REENTRY_FILTER_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 11069 | 0 | +0.000 | -0.001 | trace `DELTA_REENTRY_FILTER_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MARKET_QUALITY_SCORE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 10967 | 1 | +0.000 | -0.007 | trace `MARKET_QUALITY_SCORE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GR_FILTER_VEC_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 10817 | 0 | +0.000 | -0.010 | trace `GR_FILTER_VEC_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_HARD_BLOCK_OTHER_ACCOUNTS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 10773 | 0 | +0.000 | +0.000 | trace `BTC_HARD_BLOCK_OTHER_ACCOUNTS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HA_WICK_QUALITY_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 10665 | 0 | +0.000 | -0.001 | trace `HA_WICK_QUALITY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_ROUND_BANDS_EACH_SIDE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 10337 | 0 | +0.000 | +0.000 | trace `BTC_ROUND_BANDS_EACH_SIDE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_ACCEL_RAMP_REQUIRE_POSITIVE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 10317 | 0 | +0.000 | +0.000 | trace `BTC_ACCEL_RAMP_REQUIRE_POSITIVE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BB_PROFIT_TAKE_TF` | C_LONG,C_SHORT,S_LONG | 5 | 7996 | 0 | +0.000 | +0.000 | trace `BB_PROFIT_TAKE_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_HTF_GATE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 7147 | 0 | +0.000 | -0.005 | trace `DELTA_HTF_GATE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BB_EXIT_AT_LOSS_TF` | C_LONG,C_SHORT,S_LONG | 5 | 5499 | 0 | +0.000 | +0.000 | trace `BB_EXIT_AT_LOSS_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DC_BREAKOUT_SCORE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 8 | 2692 | 0 | +0.000 | +0.000 | trace `DC_BREAKOUT_SCORE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BB_BREAKOUT_SCORE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 2648 | 0 | +0.000 | +0.000 | trace `BB_BREAKOUT_SCORE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP` | C_LONG,S_SHORT | 4 | 2566 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_RALLY_K15M_MAX` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 2543 | 0 | +0.000 | +0.000 | trace `REENTRY_RALLY_K15M_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_RSI2_EXIT_THRESHOLD_SHORT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2518 | 1 | +0.009 | +0.000 | trace `TRADIER_RSI2_EXIT_THRESHOLD_SHORT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXIT_SCORER_DC_EXTREME` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2518 | 0 | +0.000 | +0.000 | trace `EXIT_SCORER_DC_EXTREME` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_RSI2_EXIT_THRESHOLD_LONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2518 | 0 | +0.000 | +0.000 | trace `TRADIER_RSI2_EXIT_THRESHOLD_LONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_COMPOSITE_ENTRY_OK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 2494 | 0 | +0.000 | +0.000 | trace `WT_COMPOSITE_ENTRY_OK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_COMPOSITE_ENTRY_GOOD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 2490 | 0 | +0.000 | +0.000 | trace `WT_COMPOSITE_ENTRY_GOOD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_COMPOSITE_ENTRY_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 7 | 2489 | 0 | +0.000 | +0.000 | trace `WT_COMPOSITE_ENTRY_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REGIME_TRENDING_EXIT_GAIN_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2463 | 0 | +0.000 | +0.000 | trace `REGIME_TRENDING_EXIT_GAIN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MACD_ZERO_CROSS_SCORE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2463 | 1 | +0.000 | +0.000 | trace `MACD_ZERO_CROSS_SCORE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARD_BREAKEVEN_MIN_PEAK_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2396 | 0 | +0.000 | +0.000 | trace `HARD_BREAKEVEN_MIN_PEAK_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_GUARANTEED_REENTRY_MIN_GAP_BARS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2395 | 0 | +0.000 | +0.000 | trace `BTC_GUARANTEED_REENTRY_MIN_GAP_BARS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_K_LOW_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2394 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_K_LOW_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_K_FAVORABLE_LOW` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2393 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_K_FAVORABLE_LOW` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_K_HIGH_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2392 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_K_HIGH_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2391 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2391 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_TIGHT_STOP_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2389 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_TIGHT_STOP_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_REENTRY_Z_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2387 | 0 | +0.000 | +0.000 | trace `DELTA_REENTRY_Z_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `AUGMENT_FALLBACK_REDUCE_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2376 | 0 | +0.000 | +0.000 | trace `AUGMENT_FALLBACK_REDUCE_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_COMPOSITE_ENTRY_STRONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2360 | 0 | +0.000 | +0.000 | trace `WT_COMPOSITE_ENTRY_STRONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2320 | 0 | +0.000 | +0.000 | trace `SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_PERCENTILE_ENTRY_OS_D` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2314 | 0 | +0.000 | +0.000 | trace `WT_PERCENTILE_ENTRY_OS_D` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MI_ENTRY_EXHAUST_BONUS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2310 | 0 | +0.000 | +0.000 | trace `MI_ENTRY_EXHAUST_BONUS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MI_ENTRY_STRUCT_BONUS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2310 | 0 | +0.000 | +0.000 | trace `MI_ENTRY_STRUCT_BONUS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REGIME_RANGING_EXIT_GAIN_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2292 | 1 | +0.002 | +0.000 | trace `REGIME_RANGING_EXIT_GAIN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OPEN_RATE_MAX` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2280 | 0 | +0.000 | +0.000 | trace `OPEN_RATE_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HIGH_GAIN_AUGMENTATION_MIN_SIZE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2110 | 1 | +0.002 | +0.000 | trace `HIGH_GAIN_AUGMENTATION_MIN_SIZE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SUPPRESS_EARLY_EXIT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2039 | 1 | +0.000 | +0.000 | trace `STDEV_SUPPRESS_EARLY_EXIT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_BREAKOUT_PCTB_SHORT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2039 | 0 | +0.000 | +0.000 | trace `STDEV_BREAKOUT_PCTB_SHORT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_BREAKOUT_PCTB_LONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2039 | 0 | +0.000 | +0.000 | trace `STDEV_BREAKOUT_PCTB_LONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_TIER1_SIZE_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 2016 | 0 | +0.000 | +0.000 | trace `REENTRY_TIER1_SIZE_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TREND_MIN_GAIN_EXIT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `TREND_MIN_GAIN_EXIT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_K_OB_EXIT_K3M_HI` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `SCALP_V3_K_OB_EXIT_K3M_HI` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_K_OB_EXIT_K3M_LO` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `SCALP_V3_K_OB_EXIT_K3M_LO` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `PARTIAL_EXIT_FRAC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `PARTIAL_EXIT_FRAC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_K_OB_EXIT_K15M_LO` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `SCALP_V3_K_OB_EXIT_K15M_LO` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_K_OB_EXIT_K15M_HI` | C_LONG,C_SHORT,S_LONG,S_SHORT | 6 | 2009 | 0 | +0.000 | +0.000 | trace `SCALP_V3_K_OB_EXIT_K15M_HI` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_DIVERGENCE_EXIT_AGAINST` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1962 | 0 | +0.000 | +0.000 | trace `BTC_DIVERGENCE_EXIT_AGAINST` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_CROSS_EXIT_APPLIES_TO_WINNERS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1962 | 0 | +0.000 | +0.000 | trace `WT_CROSS_EXIT_APPLIES_TO_WINNERS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_4H_VEL_EXIT_REQUIRE_K_EXTREME` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1962 | 0 | +0.000 | +0.000 | trace `WT_4H_VEL_EXIT_REQUIRE_K_EXTREME` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1962 | 0 | +0.000 | +0.000 | trace `LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_4H_VEL_EXIT_REQUIRE_PROFIT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1961 | 2 | +0.003 | +0.000 | trace `WT_4H_VEL_EXIT_REQUIRE_PROFIT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_CROSS_EXIT_REQUIRE_15M_CONFIRM` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1961 | 1 | +0.003 | +0.000 | trace `WT_CROSS_EXIT_REQUIRE_15M_CONFIRM` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEGACY_REENTRY_PSR_DC_BOUNCE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1961 | 0 | +0.000 | +0.000 | trace `LEGACY_REENTRY_PSR_DC_BOUNCE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1960 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY2_DC_BREAK_REQUIRE_K_FILTER` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1960 | 0 | +0.000 | +0.000 | trace `REENTRY2_DC_BREAK_REQUIRE_K_FILTER` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEGACY_REENTRY_PSR_FULL_DC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1960 | 0 | +0.000 | +0.000 | trace `LEGACY_REENTRY_PSR_FULL_DC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY2_DC_BREAK_ALLOW_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1959 | 0 | +0.000 | +0.000 | trace `REENTRY2_DC_BREAK_ALLOW_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_PRICE_IMPROVE_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1958 | 0 | +0.000 | +0.000 | trace `REENTRY_PRICE_IMPROVE_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_SIZE_DIP_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1958 | 0 | +0.000 | +0.000 | trace `REENTRY_SIZE_DIP_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_SIZE_EXTENDED_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1958 | 0 | +0.000 | +0.000 | trace `REENTRY_SIZE_EXTENDED_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEGACY_REENTRY_PSR_QUICK_RECOVERY` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1958 | 0 | +0.000 | +0.000 | trace `LEGACY_REENTRY_PSR_QUICK_RECOVERY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_SIZE_EXTENDED_K1H` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1958 | 0 | +0.000 | +0.000 | trace `REENTRY_SIZE_EXTENDED_K1H` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1942 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEGACY_PROC_SINGLE_REENTRY` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1941 | 0 | +0.000 | +0.000 | trace `LEGACY_PROC_SINGLE_REENTRY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LEGACY_REENTRY_PSR_K_DC_CROSSOVER` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1941 | 0 | +0.000 | +0.000 | trace `LEGACY_REENTRY_PSR_K_DC_CROSSOVER` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_POST_CONSOL_TFS_REQUIRED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1940 | 0 | +0.000 | +0.000 | trace `REENTRY_POST_CONSOL_TFS_REQUIRED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1940 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1939 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_POST_CONSOL_ATR_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1939 | 0 | +0.000 | +0.000 | trace `REENTRY_POST_CONSOL_ATR_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_GUARANTEED_REENTRY_MAX_AGE_BARS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1939 | 0 | +0.000 | +0.000 | trace `BTC_GUARANTEED_REENTRY_MAX_AGE_BARS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1939 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1938 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_POST_CONSOL_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1938 | 0 | +0.000 | +0.000 | trace `REENTRY_POST_CONSOL_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_K_LOW_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_K_LOW_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SMA_FIELD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SMA_FIELD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_TIER2_MAX_MINUTES` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `REENTRY_TIER2_MAX_MINUTES` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SCORE_TIER1` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SCORE_TIER1` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BOUNCE_REENTRY_K_RESET_SHORT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `BOUNCE_REENTRY_K_RESET_SHORT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BOUNCE_REENTRY_K_RESET_LONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `BOUNCE_REENTRY_K_RESET_LONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SCORE_TIER3` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1937 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SCORE_TIER3` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SCORE_TIER2` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SCORE_TIER2` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MANDATORY_REENTRY_K_HIGH_BLOCK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `MANDATORY_REENTRY_K_HIGH_BLOCK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_WT15M_SIZE_MULT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `REENTRY_WT15M_SIZE_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_EXIT_RECLAIM_BUFFER_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `REENTRY_EXIT_RECLAIM_BUFFER_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_B16_SIZE_MULT_WEAK` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `REENTRY_B16_SIZE_MULT_WEAK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_B16_SIZE_MULT_STRONG` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1936 | 0 | +0.000 | +0.000 | trace `REENTRY_B16_SIZE_MULT_STRONG` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_CROSS_EXIT_MIN_AGE_MINUTES` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1918 | 0 | +0.000 | +0.000 | trace `WT_CROSS_EXIT_MIN_AGE_MINUTES` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1914 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1914 | 0 | +0.000 | +0.000 | trace `OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `QUICK_REDUCE_TECHNICAL_ONLY` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1908 | 1 | +0.005 | +0.000 | trace `QUICK_REDUCE_TECHNICAL_ONLY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `QUICK_REENTRY_60MIN_MIN_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1905 | 0 | +0.000 | +0.000 | trace `QUICK_REENTRY_60MIN_MIN_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_PYRAMID_MAX` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1902 | 0 | +0.000 | +0.000 | trace `DELTA_PYRAMID_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_PYRAMID_PRICE_TOL` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1902 | 0 | +0.000 | +0.000 | trace `DELTA_PYRAMID_PRICE_TOL` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_OB_WALL_TOO_CLOSE_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1891 | 0 | +0.000 | +0.000 | trace `SCALP_V3_OB_WALL_TOO_CLOSE_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_K_OB_EXIT_WALL_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1887 | 0 | +0.000 | +0.000 | trace `SCALP_V3_K_OB_EXIT_WALL_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TECHNICAL_DC_STOP_TF` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1887 | 3 | +0.005 | -0.004 | trace `TECHNICAL_DC_STOP_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_AUG_BE_STOP_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1884 | 0 | +0.000 | +0.000 | trace `SCALP_V3_AUG_BE_STOP_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MACD_EXIT_MIN_GAIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1865 | 0 | +0.000 | +0.000 | trace `MACD_EXIT_MIN_GAIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_PERCENTILE_EXIT_OS_4H` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1861 | 1 | +0.002 | +0.000 | trace `WT_PERCENTILE_EXIT_OS_4H` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_EXHAUST_EXIT_REQUIRE_GAIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1860 | 2 | +0.001 | -0.001 | trace `WT_EXHAUST_EXIT_REQUIRE_GAIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_TF_HTF` | C_LONG,C_SHORT,S_LONG | 6 | 1857 | 0 | +0.000 | +0.000 | trace `WT_DC_TF_HTF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_REDUCE_FRAC_MED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1856 | 0 | +0.000 | +0.000 | trace `WT_REDUCE_FRAC_MED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TREND_EXIT_SCORE_FLIP` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1837 | 0 | +0.000 | +0.000 | trace `TREND_EXIT_SCORE_FLIP` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_EXIT_VETO_MAX_LOSS_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1836 | 0 | +0.000 | +0.000 | trace `HTF_EXIT_VETO_MAX_LOSS_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_TF_COMBO` | C_LONG,C_SHORT,S_LONG | 6 | 1835 | 0 | +0.000 | +0.000 | trace `WT_DC_TF_COMBO` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXIT_VELOCITY_WT_TFS` | C_LONG,C_SHORT,S_LONG | 5 | 1739 | 0 | +0.000 | +0.000 | trace `EXIT_VELOCITY_WT_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_B16_SMA200_PROX_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 5 | 1739 | 0 | +0.000 | +0.000 | trace `REENTRY_B16_SMA200_PROX_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_HTF_GATE` | C_LONG,C_SHORT,S_LONG | 6 | 1728 | 1 | +0.001 | +0.000 | trace `WT_DC_HTF_GATE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_TF_HTF2` | C_LONG,C_SHORT,S_LONG | 7 | 1622 | 0 | +0.000 | +0.000 | trace `WT_DC_TF_HTF2` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_DIRECT_DC_TF` | C_LONG,C_SHORT,S_LONG | 5 | 1491 | 0 | +0.000 | +0.000 | trace `WT_DC_DIRECT_DC_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_DC_TF` | C_LONG,C_SHORT,S_LONG | 5 | 1491 | 0 | +0.000 | +0.000 | trace `WT_DC_DC_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_BULL_ENTRY_FILTER_ENABLED` | C_LONG,C_SHORT,S_LONG | 3 | 1453 | 0 | +0.000 | +0.000 | trace `HTF_BULL_ENTRY_FILTER_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_MOMENTUM_EXIT_THRESHOLD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 1440 | 0 | +0.000 | +0.000 | trace `WT_MOMENTUM_EXIT_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GAP_PER_SYMBOL_AVG_THRESH_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 1388 | 0 | +0.000 | +0.000 | trace `GAP_PER_SYMBOL_AVG_THRESH_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DC_DIRECT_THRESHOLD` | C_LONG,C_SHORT,S_LONG | 4 | 1383 | 0 | +0.000 | +0.000 | trace `WT_DC_DIRECT_THRESHOLD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BULL_HOLD_EXIT_DELAY_BARS` | C_LONG,C_SHORT,S_LONG | 2 | 1319 | 0 | +0.000 | +0.000 | trace `BULL_HOLD_EXIT_DELAY_BARS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `AUGMENT_BULL_KILL_ENABLED` | C_LONG,C_SHORT,S_LONG | 2 | 1309 | 0 | +0.000 | +0.000 | trace `AUGMENT_BULL_KILL_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_BREAKOUT_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 4 | 1186 | 0 | +0.000 | +0.000 | trace `STDEV_BREAKOUT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DIV_ENTRY_GATE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 1060 | 0 | +0.000 | +0.000 | trace `WT_DIV_ENTRY_GATE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `V8_ENTRY_ENGINE_DC_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 3 | 1060 | 0 | +0.000 | +0.000 | trace `V8_ENTRY_ENGINE_DC_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARDCODED_RALLY_REENTRY_REQUIRE_WT` | C_LONG,C_SHORT,S_LONG | 1 | 1023 | 0 | +0.000 | +0.000 | trace `HARDCODED_RALLY_REENTRY_REQUIRE_WT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN` | C_LONG,C_SHORT,S_LONG | 1 | 1023 | 0 | +0.000 | +0.000 | trace `HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARDCODED_RALLY_REENTRY_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 1022 | 1 | +0.001 | +0.000 | trace `HARDCODED_RALLY_REENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `ULTIMATE_DC_4H_STOP_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 1006 | 0 | +0.000 | +0.000 | trace `ULTIMATE_DC_4H_STOP_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY2_DIR_FAV_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 961 | 0 | +0.000 | +0.000 | trace `REENTRY2_DIR_FAV_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SCALP_V3_PROTECTIVE_EXIT_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 960 | 0 | +0.000 | +0.000 | trace `SCALP_V3_PROTECTIVE_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BTC_GUARANTEED_REENTRY_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 960 | 0 | +0.000 | +0.000 | trace `BTC_GUARANTEED_REENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GUARANTEED_REENTRY_DELTA_GATE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 960 | 0 | +0.000 | +0.000 | trace `GUARANTEED_REENTRY_DELTA_GATE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 942 | 1 | +0.005 | +0.000 | trace `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `RECENT_REDUCTION_GUARD_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 935 | 0 | +0.000 | +0.000 | trace `RECENT_REDUCTION_GUARD_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `COUNTER_TREND_SMA200_BYPASS_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 935 | 0 | +0.000 | +0.000 | trace `COUNTER_TREND_SMA200_BYPASS_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `UNIVERSAL_NOLOSS_GATE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 935 | 0 | +0.000 | +0.000 | trace `UNIVERSAL_NOLOSS_GATE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_EXHAUST_ENTRY_GATE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 927 | 1 | +0.002 | +0.000 | trace `WT_EXHAUST_ENTRY_GATE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MI_ENTRY_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 927 | 0 | +0.000 | +0.000 | trace `MI_ENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `OPEN_RATE_BREAKER_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 915 | 0 | +0.000 | +0.000 | trace `OPEN_RATE_BREAKER_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_CROSS_FRESHNESS_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 879 | 0 | +0.000 | +0.000 | trace `REENTRY_CROSS_FRESHNESS_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `V8_ENTRY_ENGINE_WT_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 870 | 0 | +0.000 | +0.000 | trace `V8_ENTRY_ENGINE_WT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_DEVIATION_THR` | S_LONG,S_SHORT | 4 | 863 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_DEVIATION_THR` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 854 | 0 | +0.000 | +0.000 | trace `BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MACD_EXIT_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 844 | 0 | +0.000 | +0.000 | trace `MACD_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 843 | 0 | +0.000 | +0.000 | trace `LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED` | C_LONG,C_SHORT,S_LONG | 2 | 698 | 0 | +0.000 | +0.000 | trace `WT_DIVERGENCE_VV_SHORT_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED` | C_LONG,C_SHORT,S_LONG | 2 | 698 | 0 | +0.000 | -0.000 | trace `EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DC_BREAK_WAIT_WT15_CLOSE_ENABLED` | C_LONG,C_SHORT,S_LONG | 2 | 697 | 0 | +0.000 | +0.000 | trace `DC_BREAK_WAIT_WT15_CLOSE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BEAR_HOLD_EXIT_DELAY_BARS` | C_LONG,C_SHORT,S_LONG | 2 | 680 | 2 | +0.009 | +0.000 | trace `BEAR_HOLD_EXIT_DELAY_BARS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 672 | 0 | +0.000 | +0.000 | trace `LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SHORT_DC_LOW_BREAK_SIZE_MULT` | C_LONG,C_SHORT,S_LONG | 2 | 656 | 0 | +0.000 | +0.000 | trace `SHORT_DC_LOW_BREAK_SIZE_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SHORT_PARTIAL_RECOVERY_ENABLED` | C_LONG,C_SHORT,S_LONG | 2 | 654 | 0 | +0.000 | +0.000 | trace `SHORT_PARTIAL_RECOVERY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SHORT_PARTIAL_RECOVERY_THRESHOLD_PCT` | C_LONG,C_SHORT,S_LONG | 2 | 654 | 0 | +0.000 | +0.000 | trace `SHORT_PARTIAL_RECOVERY_THRESHOLD_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SHORT_PARTIAL_RECOVERY_SIZE_MULT` | C_LONG,C_SHORT,S_LONG | 2 | 654 | 0 | +0.000 | +0.000 | trace `SHORT_PARTIAL_RECOVERY_SIZE_MULT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BEAR_MARKET_MODE_TRADIER` | C_LONG,C_SHORT,S_LONG | 2 | 651 | 0 | +0.000 | +0.000 | trace `BEAR_MARKET_MODE_TRADIER` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_CHECK_INTERVAL_MIN` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_CHECK_INTERVAL_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_TRIM_FRAC` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_TRIM_FRAC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_COOLDOWN_MIN` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_COOLDOWN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_MAX_TRIMS_PER_DAY` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_MAX_TRIMS_PER_DAY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SENTIMENT_REBAL_REDUCE_DEVIATION_THR` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `SENTIMENT_REBAL_REDUCE_DEVIATION_THR` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `SENTIMENT_REBAL_COOLDOWN_MIN` | S_LONG,S_SHORT | 3 | 648 | 0 | +0.000 | +0.000 | trace `SENTIMENT_REBAL_COOLDOWN_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BEAR_MARKET_MODE` | C_LONG,C_SHORT,S_LONG | 2 | 571 | 0 | +0.000 | +0.000 | trace `BEAR_MARKET_MODE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `PER_SYM_GATE_FLAT_OPEN_ENFORCE` | S_LONG,S_SHORT | 2 | 495 | 0 | +0.000 | +0.000 | trace `PER_SYM_GATE_FLAT_OPEN_ENFORCE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DC_DAYTRADE_TARGET_DC_BUFFER_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 484 | 0 | +0.000 | +0.000 | trace `DC_DAYTRADE_TARGET_DC_BUFFER_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DC_DAYTRADE_STOP_USE_DC_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 484 | 0 | +0.000 | +0.000 | trace `DC_DAYTRADE_STOP_USE_DC_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DC_DAYTRADE_STOP_USE_DC4_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 484 | 0 | +0.000 | +0.000 | trace `DC_DAYTRADE_STOP_USE_DC4_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 480 | 0 | +0.000 | +0.000 | trace `TRADIER_DC_DAYTRADE_TARGET_USE_DC_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 480 | 0 | +0.000 | +0.000 | trace `TRADIER_DC_DAYTRADE_TARGET_USE_DC4_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 480 | 0 | +0.000 | +0.000 | trace `TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 480 | 0 | +0.000 | +0.000 | trace `TRADIER_DC_DAYTRADE_STOP_USE_DC4_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BALANCE_FLOOR_USD` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 472 | 0 | +0.000 | +0.000 | trace `BALANCE_FLOOR_USD` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `CIRCUIT_BREAKER_THRESHOLD_PCT` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 472 | 0 | +0.000 | +0.000 | trace `CIRCUIT_BREAKER_THRESHOLD_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MIN_POSITION_SIZE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 470 | 0 | +0.000 | +0.000 | trace `MIN_POSITION_SIZE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BOTTOM_EXIT_HTF_WT_VETO_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 470 | 0 | +0.000 | +0.000 | trace `BOTTOM_EXIT_HTF_WT_VETO_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_WT_CHURN_REENTRY_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 470 | 0 | +0.000 | +0.000 | trace `HTF_WT_CHURN_REENTRY_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HTF_WT_CHURN_REENTRY_MAX_AGE_MIN` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 470 | 0 | +0.000 | +0.000 | trace `HTF_WT_CHURN_REENTRY_MAX_AGE_MIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `GAP_RISK_EXIT_ENABLED` | S_LONG,S_SHORT | 2 | 469 | 0 | +0.000 | +0.000 | trace `GAP_RISK_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BREAKEVEN_DC_FIELD_MODE` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 463 | 0 | +0.000 | +0.000 | trace `BREAKEVEN_DC_FIELD_MODE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BLACKLIST_SYMBOLS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 2 | 457 | 0 | +0.000 | +0.000 | trace `BLACKLIST_SYMBOLS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `ABSOLUTE_OPEN_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 456 | 0 | +0.000 | +0.000 | trace `ABSOLUTE_OPEN_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARD_AUGMENT_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 456 | 0 | +0.000 | +0.000 | trace `HARD_AUGMENT_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `HARD_REDUCE_LOCK_SEC` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 456 | 0 | +0.000 | +0.000 | trace `HARD_REDUCE_LOCK_SEC` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STALE_HOLD_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 456 | 0 | +0.000 | +0.000 | trace `STALE_HOLD_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `UNIVERSAL_NOLOSS_BYPASS_REASONS` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 456 | 0 | +0.000 | +0.000 | trace `UNIVERSAL_NOLOSS_BYPASS_REASONS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DD_BOUNCE_ENABLED` | C_LONG,C_SHORT,S_LONG,S_SHORT | 1 | 433 | 0 | +0.000 | +0.000 | trace `DD_BOUNCE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_REBALANCE_ENABLED` | S_LONG,S_SHORT | 2 | 433 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_REBALANCE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `INTRADAY_RATIO_REQUIRE_TOP` | S_LONG,S_SHORT | 2 | 433 | 0 | +0.000 | +0.000 | trace `INTRADAY_RATIO_REQUIRE_TOP` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EOD_SLIM_RATIO_ENABLED` | S_LONG,S_SHORT | 2 | 433 | 0 | +0.000 | +0.000 | trace `EOD_SLIM_RATIO_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `MTF_BB_REJECT_EXIT_LOOKBACK` | C_LONG,C_SHORT | 2 | 432 | 0 | +0.000 | +0.000 | trace `MTF_BB_REJECT_EXIT_LOOKBACK` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_BOUNCE_BAR_GR_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_BOUNCE_BAR_GR_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_PULLBACK_GR_SCORE_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_PULLBACK_GR_SCORE_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_DC_MID_PULLBACK_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_DC_MID_PULLBACK_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_K_RESET_GR_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_K_RESET_GR_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_15M_LRL_PULLBACK_HTF_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_15M_LRL_PULLBACK_HTF_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 350 | 0 | +0.000 | +0.000 | trace `REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `REENTRY_SMA200_GR_CONTINUATION_ENABLED` | C_LONG,C_SHORT,S_LONG | 1 | 344 | 0 | +0.000 | +0.000 | trace `REENTRY_SMA200_GR_CONTINUATION_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EMA50_15M_ENTRY_FILTER_PCT` | C_LONG,C_SHORT,S_LONG | 1 | 342 | 0 | +0.000 | +0.000 | trace `EMA50_15M_ENTRY_FILTER_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `KINDERGARTEN_CUMULATIVE_MIN_TFS` | C_LONG,C_SHORT,S_LONG | 1 | 327 | 0 | +0.000 | +0.000 | trace `KINDERGARTEN_CUMULATIVE_MIN_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `FORCE_MIN_ONE_TRADE` | C_LONG,C_SHORT,S_LONG | 1 | 322 | 0 | +0.000 | +0.000 | trace `FORCE_MIN_ONE_TRADE` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `EXIT_STDEV_BREAKOUT_FAIL_ENABLED` | C_LONG | 1 | 105 | 0 | +0.000 | +0.000 | trace `EXIT_STDEV_BREAKOUT_FAIL_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `WT_VEL_DECAY_EXIT_ENABLED` | C_LONG | 1 | 105 | 0 | +0.000 | +0.000 | trace `WT_VEL_DECAY_EXIT_ENABLED` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `DELTA_REENTRY_MIN_TF` | C_LONG | 1 | 105 | 0 | +0.000 | +0.000 | trace `DELTA_REENTRY_MIN_TF` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `KINDERGARTEN_STRICT_TFS` | C_LONG,S_LONG | 2 | 103 | 0 | +0.000 | +0.000 | trace `KINDERGARTEN_STRICT_TFS` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `VIGILANCE_MAX_LOSS_PCT` | S_SHORT | 4 | 4 | 0 | +0.000 | +0.000 | trace `VIGILANCE_MAX_LOSS_PCT` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SLOPE_SIZING_4H_MAX` | S_SHORT | 2 | 2 | 0 | +0.000 | +0.000 | trace `STDEV_SLOPE_SIZING_4H_MAX` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SLOPE_LOOKBACK_D` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `STDEV_SLOPE_LOOKBACK_D` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SLOPE_LOOKBACK_4H` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `STDEV_SLOPE_LOOKBACK_4H` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SLOPE_LOOKBACK_1H` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `STDEV_SLOPE_LOOKBACK_1H` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `STDEV_SLOPE_LOOKBACK_15M` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `STDEV_SLOPE_LOOKBACK_15M` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BAND_SLOPE_SIZING_V2_DEPTH_GAIN` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `BAND_SLOPE_SIZING_V2_DEPTH_GAIN` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |
| switch | `BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY` | S_SHORT | 1 | 1 | 0 | +0.000 | +0.000 | trace `BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY` in ez/tradier + v12_quick_engine; wire on 4 surfaces or confirm truly dead |

### 3c · NEVER HELPS — real deltas but no value ever positive (honest losers → Stage-6 throttle)

These **100** levers (98 switches, 2 filters) record real non-zero deltas but never a win in any cat_side. Not defects — they are correctly-measured losers. Action: throttle to the Stage-6 low cadence; do NOT promote; do NOT delete. Spot-check live==vec on one sym_side before throttling to rule out a sign/parity artefact.

- **switches (98):** `EMA50_15M_ENTRY_FILTER_ENABLED`, `KINDERGARTEN_EMA_GATE_ENABLED`, `AUGMENT_FALLBACK_GAIN_PCT`, `AUGMENTED_POSITIONS_GUARD_FLOOR_MULT`, `AUGMENT_WT_4H_BOUNCE_ENABLED`, `AUGMENT_ONLY_WHEN_PROFITABLE`, `FG_FEAR_THRESHOLD`, `FG_GREED_THRESHOLD`, `MANDATORY_REENTRY_WT_FILTER_MIN_TFS`, `DC_MOMENT_STRONG_THRESHOLD`, `DYNAMIC_SCORE_AUGMENT_ENABLED`, `MOVER_THRESHOLD`, `MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO`, `HA_WICK_QUALITY_SCORE`, `CRYPTO_SPIKE_FADE_THRESHOLD_PCT`, `AUGMENT_AT_LOSS_ENABLED`, `SCALP_V3_AUG_BE_STOP_ENABLED`, `AUGMENT_FALLBACK_REDUCE_ENABLED`, `MTF_GR_MIN_IND`, `FUNDING_GATE_FILTER_TF`, `HA_WICK_QUALITY_TF`, `HTF4_CONF`, `AUGMENT_BOUNCE_MIN_GAIN_PCT`, `AUGMENT_BREAKOUT_MIN_GAIN_PCT`, `HTF_GATE_APPLY_TO_AUGMENT`, `BOUNCE_AUGMENT_MIN_LOSS_PCT`, `RZ_BOT_BB_THRESHOLD`, `MACD_ZERO_CROSS_TF`, `BTC_RZ_WT_DC_MULTIFACTOR`, `ATR_ADAPTIVE_STOP_MULT`, `ENTRY_PRIMARY_TF`, `BTC_TECH_EXIT_WT_MIN_TFS`, `ATR_LONG_WINDOW`, `INTRADAY_SESSION_FORCE_EXIT_UTC`, `WT_15M_CROSS_ENTRY_ENABLED`, `BTC_BREAKOUT_ENTRY_ENABLED`, `REVERSE_ON_EXIT_ENABLED`, `MACD_ZERO_CROSS_ENABLED`, `HAIKU_ENTRY_GATE_ENABLED`, `REENTRY2_STOCH_CROSS_ENABLED`, `TRADIER_MFI_ENTRY_LONG_ENABLED`, `DELTA_GATE_BB_SQUEEZE`, `STDEV_BREAKOUT_EXIT_PCTB_FAIL`, `DC_DAYTRADE_TARGET_USE_DC_15M`, `DC_DAYTRADE_TARGET_USE_DC4_15M`, `DC_DAYTRADE_TARGET_PCT`, `TRADIER_DC_DAYTRADE_STOP_USE_DC_15M`, `MTF_DC_REJECT_EXIT_ENABLED`, `WT_4H_VEL_EXIT_K_EXTREME_HIGH`, `BB_SQUEEZE_EXIT_ENABLED`, `WT_CROSS_EXIT_ENABLED`, `HARD_BREAKEVEN_FLOOR_ENABLED`, `RECENT_REDUCTION_GUARD_WINDOW_S`, `COUNTER_TREND_ADD_BLOCK_ENABLED`, `TRADES_PER_SYM_PER_DAY_MAX`, `PEAK_GIVEBACK_DROP_TRIGGER_ENABLED`, `HTF_GATE_APPLY_TO_OPEN`, `BREAKEVEN_GAIN_EROSION_MIN_GAIN`, `SIMPLE_TP_EXIT_ENABLED`, `BREAKEVEN_GAIN_EROSION_ENABLED` …
- **filters (2):** `WT_DC_STOCH_TF`, `WT_DC_HTF_GATE_MODE`

### 3d · In the TEMPLATE universe but ABSENT from avg_delta entirely

_Every template switch produced at least one recorded delta this round._

> **How to action (3a/3b/3d):** wiring fixes use the proven 4-surface pattern (vec + ez_manage + tradier_manage + config, never a proxy) — memory `switch_wiring_pattern_20260930`. For no-op stubs, trace the real code path before touching live (memory `filter_stub_farms_and_ghost_push`). Never delete a row (DAILY_OPTIMIZATION_PLAN Stage 6 throttles losers; it does not remove them).

## 4 · Daily chain: 365D verify, REPAIR loop, live-faithful, promotions

_no chain_state.json for today (fleet scheduler has not run / not deployed yet)_

### live-faithful (backtest_v12_engine) coverage: **0** sym_sides today
- parity gaps (vec vs live-faithful): **0**

### Promotions made today (bold defaults, cat_side_promotions.json)
- CRYPTO_LONG `STDEV_SLOPE_SIZING_ENABLED` → False (avg_delta 0.410883, STDEV_SLOPE_SIZING)
- CRYPTO_LONG `DC_MOMENTUM_BOTA_SCORER_FILTER_TF` → OFF (avg_delta 0.322916, GLOBAL_RISK_GATES)
- CRYPTO_LONG `EMA_9_21_FILTER_FILTER_TF` → OFF (avg_delta 0.284345, GLOBAL_RISK_GATES)
- CRYPTO_LONG `EXIT_R1_R2_FILTER_TF` → OFF (avg_delta 1.335002, GLOBAL_RISK_GATES)
- CRYPTO_LONG `EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF` → 4h (avg_delta 1.335002, GLOBAL_RISK_GATES)
- CRYPTO_LONG `EXIT_TOP_FADE_FILTER_TF` → OFF (avg_delta 1.280512, GLOBAL_RISK_GATES)
- CRYPTO_LONG `EXIT_TO_REDUCE_ADAPTER_FILTER_TF` → OFF (avg_delta 1.335002, GLOBAL_RISK_GATES)
- CRYPTO_LONG `MANDATORY_REENTRY_WT_FILTER_TF_MODE` → 5m_or_15m (avg_delta 1.426025, GLOBAL_RISK_GATES)
- CRYPTO_LONG `MTF_ATR_TRAIL_FILTER_TF` → OFF (avg_delta 0.191286, GLOBAL_RISK_GATES)
- CRYPTO_LONG `MTF_DC_REJECT_FILTER_TF` → OFF (avg_delta 0.308876, GLOBAL_RISK_GATES)
- CRYPTO_LONG `NEWBORN_LOSS_KILL_FILTER_TF` → OFF (avg_delta 0.284345, GLOBAL_RISK_GATES)
- CRYPTO_LONG `NOLOSS_BYPASS_WT5OF5_FILTER_TF` → 1h (avg_delta 0.022256, GLOBAL_RISK_GATES)
- CRYPTO_LONG `PARTIAL_PROFIT_LOCK_V2_FILTER_TF` → OFF (avg_delta 0.076975, GLOBAL_RISK_GATES)
- CRYPTO_LONG `PEAK_GIVEBACK_BE_EROSION_FILTER_TF` → 1h (avg_delta 0.290269, GLOBAL_RISK_GATES)
- CRYPTO_LONG `WT_15M_BOUNCE_REL_VOL_GT_1` → True (avg_delta 0.435906, GLOBAL_RISK_GATES)
- CRYPTO_LONG `DAYTRADE_DC_TARGET_TF` → 1h (avg_delta 0.185551, EXIT_STRUCTURAL)
- CRYPTO_LONG `UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED` → False (avg_delta 6.750516, AUGMENT_RISK_SIZING)
- CRYPTO_LONG `BREAKEVEN_GAIN_EROSION_FILTER_TF` → 15m (avg_delta 0.172429, REDUCE_PROFIT_LOCK)
- CRYPTO_LONG `EXECUTE_NOW_MAX_MARK_AGE_S` → 90.0 (avg_delta 0.013814, GLOBAL_RISK_GATES)
- CRYPTO_LONG `UNIVERSAL_NOLOSS_GATE` → True (avg_delta 0.021348, GLOBAL_RISK_GATES)
- CRYPTO_LONG `HTF_GATE_MIN_CONFIRMATIONS` → 2 (avg_delta 0.359206, GLOBAL_RISK_GATES)
- CRYPTO_LONG `HTF_BULL_ENTRY_FILTER_TF` → OFF (avg_delta 0.674127, ENTRY_BREAKOUT_CHANNEL)
- CRYPTO_LONG `EMA_BLANKET_FILTER_ENABLED` → False (avg_delta 0.017848, GLOBAL_RISK_GATES)
- CRYPTO_LONG `DC_BREACH_REDUCE_FILTER_TF` → OFF (avg_delta 0.051121, GLOBAL_RISK_GATES)
- CRYPTO_LONG `DC_BREAK_FILTER_TF` → OFF (avg_delta 0.247578, GLOBAL_RISK_GATES)
- CRYPTO_LONG `FAST_RISER_FILTER_TF` → OFF (avg_delta 0.076818, GLOBAL_RISK_GATES)
- CRYPTO_LONG `LIVE_ENTRY_ENGINE_FILTER_TF` → 15m (avg_delta 0.361837, GLOBAL_RISK_GATES)
- CRYPTO_LONG `MOMENTUM_BREAKOUT_FILTER_TF` → OFF (avg_delta 0.015281, GLOBAL_RISK_GATES)
- CRYPTO_LONG `MTF_ARMED_ENTRIES_FILTER_TF` → 15m (avg_delta 0.152812, GLOBAL_RISK_GATES)
- CRYPTO_LONG `DC_BREAKOUT_TF` → 1h (avg_delta 0.063742, ENTRY_BREAKOUT_CHANNEL)
- CRYPTO_LONG `TECHNICAL_DC_TARGET_TF` → OFF (avg_delta 0.037668, EXIT_STRUCTURAL)
- CRYPTO_LONG `MTF_DC_REJECT_EXIT_TF` → 1h (avg_delta 0.019009, EXIT_VELOCITY)
- CRYPTO_LONG `DC_HARD_STOP_TF` → 4h (avg_delta 0.025288, EXIT_VELOCITY)
- CRYPTO_LONG `REENTRY2_DC_BREAK_FILTER_TF` → 3m (avg_delta 0.012355, REENTRY_WINDOWED)
- CRYPTO_SHORT `BREAKOUT_RETEST_FILTER_TF` → 1h (avg_delta 0.012629, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `DC_BREAK_FILTER_TF` → 1h (avg_delta 0.091809, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `EXIT_R1_R2_FILTER_TF` → 1h (avg_delta 0.823011, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF` → OFF (avg_delta 0.419172, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `EXIT_TOP_FADE_FILTER_TF` → D (avg_delta 0.749528, GLOBAL_RISK_GATES)
- CRYPTO_SHORT `EXIT_TO_REDUCE_ADAPTER_FILTER_TF` → 1h (avg_delta 0.823011, GLOBAL_RISK_GATES)
_191 promotions today_

