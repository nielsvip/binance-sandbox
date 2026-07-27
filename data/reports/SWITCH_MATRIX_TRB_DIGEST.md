# SWITCH_MATRIX_TRB progress digest — 2026-07-27 03:30:47Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `2026-07-27T03:26:25Z` (4m old).
- Generic DB activity (includes historical/stage tables): `2026-07-27T03:26:25Z` (4m old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **15**; new current rows in 24h: **15**.
- Raw repaired-campaign pilot rows since cutoff: **987**; **972** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **7,594/7,609**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260725_c2`, cutoff `2026-07-26T04:15:00Z`, exact code+NPZ+side fingerprint required.
- VEC diagnostic rows: **83,604** (never matrix proof).
- `SWITCH_MATRIX_TRB.xlsx`: 2026-07-27 03:30:47Z (0m old, 389,160 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-07-27 03:30:06Z (0m old, 35,922 bytes)
- Description coverage in current CSV: **3,534/3,534** rows.

## Stocks 5m execution provenance

Historical native 5m availability is provider-limited. Older rows use the disclosed containing-15m interpolation; native bars replace it permanently as they are collected.
Retention report scope: **3 symbols**, **3 native archives**, **62,693 native rows**, **0 availability warnings**, **0 hard errors**.

| key | native rows | native coverage | native range | interpolated rows | interpolated coverage | retention |
|---|---:|---:|---|---:|---:|---|
| MU_LONG | 15526 | 13.54% | 2026-03-23 → 2026-07-24 | 99115 | 86.46% | PASS |
| VT_LONG | 41190 | 98.19% | 2024-07-11 → 2026-07-24 | 761 | 1.81% | PASS |
| HAO_SHORT | 5977 | 55.14% | 2026-04-09 → 2026-07-24 | 4862 | 44.86% | PASS |

The append/merge ledger blocks any refresh that shrinks native row count, advances the first timestamp, regresses the last timestamp, or introduces duplicate/out-of-order timestamps. `synthetic_5m_parent_close_ts` records the bounded 0/5/10-minute parent lag.

## Pilot-key matrix coverage

| key | actionable cells filled | coverage | latest Tier-2 row | age | strategy tests |
|---|---:|---:|---|---:|---:|
| MU_LONG | 0/3,522 | 0.0% | 2026-07-27T03:23:02Z | 7m | 3 |
| VT_LONG | 0/3,522 | 0.0% | 2026-07-27T03:26:25Z | 4m | 12 |
| HAO_SHORT | 0/3,522 | 0.0% | — | unknown | 0 |

Coverage counts exact `(switch,value)` cells in the current actionable manifest. VEC rows do not fill Tier-2 cells, and duplicate campaigns do not inflate coverage.

## Path interpretation guardrails

- `DC_LOW4_STOP_ENABLED` and stock `R1_DC_LOW4_3M_EMERGENCY_ENABLED` (legacy name; actual stock level is `dc_low4_5m`/`dc_high4_5m`) are **ENTRY-QUALITY FAILURE DIAGNOSTICS / LOSING-CHURN EXIT EVIDENCE**. They close a recently failed entry at a tight loss; they are not top/profit-taking exits. Below-B&H observations stay gray and preserved so they are not blindly retested.
- The current `LONG_STRUCT_EXIT_TF` / `SHORT_STRUCT_EXIT_TF` path closes immediately on its selected structural break. The first armed-break → lower price/WT1 rebound-top baseline is **VEC-REJECTED / NO Tier-2 result**: `data/reports/vec_research/structural_wt_rebound_20260726T062654Z` scored 0/6 contract-valid LONG folds above side-and-hold, median alpha -4.70pp, mean TIM 77.9%, and 50 exits / 30 losing. HAO_SHORT's +20.18pp is quarantined because its NPZ contract is invalid. Profit/MFE-gated variants remain research candidates and do not fill matrix cells.
- The nested profit-gated grid `structural_wt_profit_grid_20260726T063646Z` selected one universal setting on MU+VT 2025Q4, then froze it. Validation had 9/9 winning exits but only 1/4 folds above B&H (median alpha -1.04pp). The remaining loss is reentry execution: delayed E10 reclaim filled 10.01% worse on recent MU and 1.46% worse on recent VT. A persistent resting-reclaim model is now the priority; this grid remains VEC-only.
- Frozen exit settings with causal resting reclaim (`resting_reclaim_compare_20260726T064428Z`) improved median validation alpha to +1.94pp and cut mean reclaim overshoot from 0.80% to 0.02%. Recent MU returned +13.59% versus B&H +2.60%, but VT still trailed; this is evidence for the execution fix, not a universal promotion.
- MU ladder discovery `struct_wt_resting_reclaim_probe_20260726T070000Z_MU_LONG` reached +409.93% versus B&H +204.90% (2.0006×) at 52.3% weighted exposure, but is **REJECTED versus the same-entry research control**. The source frozen 2026 ladder + 4h N=30 E02 fold returned +1,316.02% versus B&H +205.25% (6.4117×) at 77.1% exposure. The structural candidate discarded 906.09pp of return; beating B&H alone is not sufficient. Both paths remain VEC-only pending exact replay.
- Nested/frozen same-ladder validation (`struct_wt_nested_frozen_20260726T073000Z`) selected only on MU Jan-Mar, then scored MU Apr-Jul without reselection. Structural returned +359.24% (2.191× B&H) but the same ladder returned +1,380.56% (8.420× B&H): -1,021.32pp and only 0.260× of ladder return. The universal VT score improved a losing ladder (-8.90% versus -61.85%) but remained below B&H (+1.57%). Verdict **REJECT / NO MATRIX**; every exit candidate must beat both B&H and the strongest same-entry/same-window causal control.
- The complete registered `EXIT_STRUCTURAL_WT_LOWER_TOP` screen now supersedes the exploratory structural grids: 20 frozen top/bottom symbol-sides × 768 settings, with completed HTF bars, side isolation, resting reclaim and a compiled-to-Python parity gate. All 20 selected winners reproduced exactly, but **0/20 passed** both B&H + same-entry E02 and the 70–80% discovery/validation TIM gates. MU validated +1,127.00pp over B&H but -44.324pp versus E02 at 65.21% TIM; SNDK's headline used 98.70% TIM. Path-fleet job 37 preserves all rows gray and correctly queues no exact replay.
- `EXIT_PARTIAL_RUNNER` completed the corrected 192-setting registry grid on 20 frozen keys (the listed 4×4×3×2×2 fields cannot equal 128; all clip sums are valid). It produced **0 strict survivors**. Five selected validation winners had zero fast partial fills; E05/E06 filled in only 25%/40% of their validation settings versus WT 100%. The winners created 919 per-exit reclaim obligations, filled 633 and left 286 open. MU made one partial (-$107.98 net) versus ten slow full exits and lost 122.07pp to E02. Job 39 preserves all 20 rows gray and queues no exact replay.
- `EXIT_E06_REGRESSION_RETEST` completed 3,840 same-entry candidates with **0 strict survivors**. The registry is stale: `rebound_atr` is actually passed to `corr_gate`, active code has no ATR rebound/later retest state, and there is no live Tradier E06 config key. Correlation gate 1.0 was 960/960 zero-signal/zero-fill (red); all 20 frozen winners had actual signals/exits. MU was in-band at 73.46% but lost 303.33pp to E02 and left one reclaim open. Job 46 preserves 20 gray rows and queues no exact replay.
- Reentry invariant for structural research: after an exit, the stored exit/top level and reopen obligation remain latched. WT/stochastic vetoes may postpone reopening but must never erase it or allow price to outrun the stored level without reopening. This is a design requirement, not a measured performance claim.

### Preserved dc_low4 diagnostic evidence (pre-repair; gray/quarantined)

| key | switch | gain/mo | TIM | trades | status |
|---|---|---:|---:|---:|---|
| MU_LONG | False | 0.0275 | 0.13% | 17 | PRE-REPAIR / validation NULL |
| MU_LONG | True | -0.1045 | 0.21% | 103 | LOSING-CHURN SIGNATURE; validation NULL |
| MU_SHORT | False | -0.4670 | 6.89% | 48 | PRE-REPAIR / validation NULL |
| MU_SHORT | True | -0.1061 | 0.15% | 155 | NEGATIVE + HIGH-CHURN; validation NULL |

These historical rows are retained for diagnosis and excluded from the automatic matrix-fill queues. They are not valid proof because the repaired contract fields were absent.

## New ladder / entry / exit / interaction strategy results

### MU_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-27T03:23:02Z | stocks_repaired_20260725_c2 | `STOP_PACK=DC_FROZEN_D` | 1.4095 | 4.7610 | -3.3515 | 0.296× | 22.39% | 3184 | RED: CAPACITY CLAMPS |
| 2026-07-27T03:22:24Z | stocks_repaired_20260725_c2 | `STOP_PACK=DC_FROZEN_4H` | 1.4096 | 4.7610 | -3.3514 | 0.296× | 22.39% | 3182 | RED: CAPACITY CLAMPS |
| 2026-07-27T03:20:56Z | stocks_repaired_20260725_c2 | `STOP_PACK=NEAR_ENTRY_OFF_ONLY` | 1.4135 | 4.7610 | -3.3475 | 0.297× | 22.39% | 3180 | RED: CAPACITY CLAMPS |

Best trading candidate: **none with at least two trades and complete return metrics**.

### VT_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-27T03:26:25Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_REGIME` | -0.1297 | 0.2744 | -0.4041 | -0.473× | 15.30% | 378 | REJECT |
| 2026-07-27T03:25:54Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_BAND_FULL` | -0.1297 | 0.2744 | -0.4041 | -0.473× | 15.30% | 378 | REJECT |
| 2026-07-27T03:25:09Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_BAND_HARVEST` | -0.1870 | 0.2744 | -0.4614 | -0.681× | 11.12% | 317 | REJECT |
| 2026-07-27T03:16:54Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_DC_D` | -0.1871 | 0.2744 | -0.4615 | -0.682× | 11.12% | 317 | REJECT |
| 2026-07-27T03:15:58Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_DELTA_OFF` | -0.1839 | 0.2744 | -0.4583 | -0.670× | 11.11% | 308 | REJECT |
| 2026-07-27T03:15:18Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_EXITS_OFF` | -0.1839 | 0.2744 | -0.4583 | -0.670× | 11.11% | 308 | REJECT |
| 2026-07-27T03:05:13Z | stocks_repaired_20260725_c2 | `STOP_PACK=BB_FROZEN_D` | -0.2957 | 0.2743 | -0.5700 | -1.078× | 2.78% | 548 | REJECT |
| 2026-07-27T03:04:59Z | stocks_repaired_20260725_c2 | `STOP_PACK=BB_FROZEN_W` | -0.2847 | 0.2744 | -0.5591 | -1.038× | 2.97% | 505 | REJECT |

Best trading candidate: **none with at least two trades and complete return metrics**.

### HAO_SHORT

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| — | — | — | — | — | — | — | — | — | NO STRATEGY RESULTS |

## Frozen walk-forward verdict

| key | policy | discovery | frozen validation | validation TIM | verdict |
|---|---|---:|---:|---:|---|
| MU_LONG | `E02_4h_N20 + E11_G2+E10_RB0` | 32.169× B&H | 0.373× B&H | 67.75% | FAIL / NO PROMOTION |
| VT_LONG | — | — | — | — | PENDING |
| HAO_SHORT | — | — | — | — | PENDING |

This table freezes the discovery choice before reading validation. It takes precedence over each window's separately re-optimized best row.

## New causal-exit nested walk-forward

| key | families | strict frozen result | strict TIM | clean frozen result | stability | verdict |
|---|---|---:|---:|---:|---:|---|
| MU_LONG | E03/E06/E08/E09 | 47.407% vs 75.225% B&H (0.630×) | 73.14% | 575.341% vs 971.953% B&H (0.592×) | 16.7% exact repeat | REJECT / NO PROMOTION |
| VT_LONG | E03/E06/E08/E09 | NO STRICT FOLDS | — | 3.965% vs 9.922% B&H (0.400×) | 20.0% exact repeat | REJECT / NO PROMOTION |
| HAO_SHORT | — | — | — | — | — | PENDING |

This lane uses nested 12-month discovery with frozen three-month validation, faithful-engine cost semantics, and excludes VT folds overlapping its known source gap.

## Partial-runner and regime nested walk-forward

| key | family | frozen strategy vs B&H | weighted TIM | partial P&L / exits | stability | verdict |
|---|---|---:|---:|---:|---:|---|
| MU_LONG | E12 PARTIAL THEN RUNNER | 562.105% vs 971.953% (0.618× equity) | 81.69% | 0.8782 / 47 | UNSTABLE | REJECT / NO PROMOTION |
| MU_LONG | E13 REGIME SWITCHED | 525.663% vs 971.953% (0.584× equity) | 70.21% | 0.0000 / 0 | UNSTABLE | REJECT / NO PROMOTION |
| VT_LONG | E12 PARTIAL THEN RUNNER | 6.015% vs 9.922% (0.964× equity) | 83.40% | 0.0103 / 10 | STABLE | REJECT / NO PROMOTION |
| VT_LONG | E13 REGIME SWITCHED | 3.047% vs 9.922% (0.937× equity) | 79.26% | 0.0000 / 0 | STABLE | REJECT / NO PROMOTION |
| HAO_SHORT | — | — | — | — | — | PENDING |

E12 reports net realized partial P&L separately. Positive partial clips do not constitute edge when lost runner exposure and re-add timing leave compounded equity below B&H.

## Causal ladder multiplier walk-forward

| key | frozen OOS strategy | B&H | multiple | weighted TIM | exact-engine parity | verdict |
|---|---:|---:|---:|---:|---|---|
| MU_LONG | 2503.082% | 381.950% | 6.553× | 65.26% | PASS | RESEARCH EDGE; PROMOTION BLOCKED |
| VT_LONG | -15.364% | 13.786% | -1.114× | 80.32% | PENDING | REJECT / NO PROMOTION |
| HAO_SHORT | — | — | — | — | — | QUARANTINED / INVALID DATA |

The MU exact replay covers the latest frozen fold: 34/34 actions, +1,316.021% return, 77.09% weighted TIM, zero future HTF sources, zero clamps, and exact signal/fill/accounting parity. It remains research-only because the campaign explicitly sets `promotion_allowed=false`. VT fails frozen OOS; HAO remains data-quarantined.

## Top-10 ladder exposure retune

> Frozen nested-OOS research. Aggregate exposure can hide unstable folds; a row is not promotable unless every fold also passes the fixed-control, causality, capacity, reclaim, and exposure gates.

| key | strategy | B&H | multiple | identical control | alpha control | TIM | fold TIM | fills | clamps | exact | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|
| AMD_LONG | 1815.895% | 204.780% | 8.868× | 1683.799% | 132.095pp | 89.18% | 85.08% / 94.05% / 88.33% | 50.9% | 86 | — | REJECT / NO PROMOTION |
| ARM_LONG | 1227.431% | 123.020% | 9.977× | 1150.824% | 76.608pp | 73.68% | 79.94% / 53.85% / 86.19% | 89.6% | 18 | — | REJECT / NO PROMOTION |
| DINO_LONG | 726.483% | 118.221% | 6.145× | 531.313% | 195.170pp | 72.21% | 90.61% / 66.77% / 61.07% | 42.6% | 146 | PASS | RESEARCH EDGE; FOLD/CONTROL BLOCKED |
| INTC_LONG | 942.703% | 214.032% | 4.405× | 1466.298% | -523.595pp | 61.14% | 76.39% / 21.57% / 83.70% | 36.3% | 168 | — | REJECT / NO PROMOTION |
| MPC_LONG | 291.563% | 103.592% | 2.815× | 163.185% | 128.378pp | 61.95% | 87.54% / 77.21% / 25.52% | 42.2% | 97 | — | REJECT / NO PROMOTION |
| MRVL_LONG | 1354.261% | 104.106% | 13.008× | 1283.089% | 71.172pp | 74.83% | 81.65% / 69.44% / 73.70% | 75.2% | 54 | — | REJECT / NO PROMOTION |
| MU_LONG | 2062.320% | 381.950% | 5.399× | 2503.082% | -440.761pp | 58.53% | 85.41% / 21.08% / 69.04% | 54.2% | 76 | — | REJECT / NO PROMOTION |
| PBF_LONG | 672.250% | 127.673% | 5.265× | 642.127% | 30.123pp | 39.52% | 18.76% / 10.71% / 84.08% | 100.0% | 0 | — | REJECT / NO PROMOTION |
| SNDK_LONG | 3068.497% | 888.467% | 3.454× | 5414.039% | -2345.543pp | 80.44% | 70.82% / 89.16% | 100.0% | 0 | — | REJECT / NO PROMOTION |
| VLO_LONG | 442.428% | 113.267% | 3.906× | 411.173% | 31.255pp | 53.11% | 10.75% / 71.17% / 73.74% | 100.0% | 0 | — | REJECT / NO PROMOTION |

The retune keeps exits fixed at completed-4h E02 N=30. It changes only the bounded ladder curve/trigger semantics, so alpha against the identical control does not come from a different exit or a different B&H budget.

## Isolated vector research — not matrix evidence

> Fast causal screens only. These rows never fill or color Tier-2 cells. Promotion requires an independent audit, a faithful-engine replay, stable out-of-sample behavior, real closes, and a changed trade fingerprint.

| key | window | best visible candidate | gain | B&H | multiple | TIM | data/policy status |
|---|---|---|---:|---:|---:|---:|---|
| MU_LONG | 2024-01-01 → 2025-07-01 | `E02_4h_N20 + E11_G2+E10_RB0` | 70.164% | 2.181% | 32.169× | 76.01% | EXPOSURE/RECLAIM PASS; FAITHFUL REPLAY PENDING |
| MU_LONG | 2024-01-01 → present | `E02_4h_N30 + E11_G2+E10_RB0` | 1377.872% | 666.389% | 2.068× | 75.05% | EXACT EXECUTION REPLAY PASS; ROBUSTNESS/PROMOTION BLOCKED |
| MU_LONG | 2025-07-01 → present | `E02_4h_N20 + E11_G0.5+E10_RB0` | 458.076% | 651.969% | 0.703× | 76.28% | BELOW B&H; REJECT |
| VT_LONG | 2024-01-01 → present | `E04_4h_EMA20_B0_R0.25_W12 + E11_G1+E10_RB0` | 30.364% | 32.656% | 0.930× | 78.99% | BELOW B&H; REJECT |
| HAO_SHORT | unknown → present | — | — | — | — | — | QUARANTINED / INVALID DATA |

The full-period MU multiple is an optimization-screen headline, not a robust claim. Read the holdout row beside it: exposure drift or sub-B&H holdout performance blocks promotion even when the full-period row is above B&H.

## Top/bottom-10 entry/exit path fleet

> Claimable vector-first research queue. Control rows establish the frozen benchmark that later paths must beat; they are not exact-engine promotion evidence.

- Jobs: **80**; states: ADAPTER_REQUIRED=40, OBSERVABILITY_ONLY=2, QUARANTINED=8, SCREENED=30.
- Frozen tradeable hashes: LONG `e0acfe4c1139dd01f6138445c53071eb307fa4ad0600fcd8dddcd7e794a78955`; SHORT `00c085e4069a105826f040cf9ff95373afaa4684f9ebc02f1a72edff4a0aa361`.
- Top LONG cohort: SNDK, MRVL, ARM, MU, AMD, INTC, PBF, MPC, DINO, VLO.
- Bottom SHORT cohort: LAC, UUUU, UEC, ASTS, ACN, HL, TTD, EGO, CDE, ALB.

| path | key | stage | metric scope / units | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `EXIT_E05_DIVERGENCE_RETEST` | VT_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | -5.540% | 1.575% | -3.517× | -7.115pp | 11.188pp | 82.55% | 0 |
| `EXIT_E05_DIVERGENCE_RETEST` | ARM_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 1127.820% | 126.604% | 8.908× | 1001.216pp | 58.956pp | 94.91% | 0 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | DINO_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 562.031% | 90.701% | 6.196× | 471.329pp | 76.792pp | 78.51% | 6 |
| `EXIT_E05_DIVERGENCE_RETEST` | SNDK_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 3654.717% | 467.087% | 7.824× | 3187.629pp | 1329.079pp | 99.99% | 0 |
| `EXIT_TECH_BREAKDOWN_ENABLED` | MRVL_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 585.452% | 123.583% | 4.737× | 461.870pp | -1087.274pp | 68.77% | 8 |
| `EXIT_STRUCTURAL_WT_LOWER_TOP` | MU_LONG | VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_FOLD; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 1328.640% | 205.252% | 6.473× | 1123.388pp | -41.313pp | 76.86% | 4 |
| `ENTRY_WT_DC` | IBIT_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 8.737% | 28.318% | 0.309× | -19.581pp | 0.000pp | 5.82% | 8 |
| `ENTRY_DELTA_MTF` | PLTR_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 6.727% | 31.041% | 0.217× | -24.313pp | 0.000pp | 1.60% | 2 |
| `ENTRY_DELTA_MTF` | LRCX_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -45.603% | -69.997% | —× | 24.394pp | 0.000pp | 6.32% | 20 |
| `ENTRY_DELTA_MTF` | MRVL_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -6.887% | -119.050% | —× | 112.163pp | 0.000pp | 2.01% | 5 |
| `ENTRY_DELTA_MTF` | ARM_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -25.120% | -125.397% | —× | 100.277pp | 0.000pp | 7.97% | 19 |
| `ENTRY_DELTA_MTF` | SNDK_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -46.467% | -457.391% | —× | 410.924pp | 0.000pp | 5.78% | 12 |
| `ENTRY_DELTA_MTF` | MU_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -57.648% | -201.702% | —× | 144.054pp | 0.000pp | 6.04% | 14 |
| `ENTRY_DELTA_MTF` | NVDA_SHORT | VEC_SHORT_NATIVE_PHASE2_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | -7.624% | -7.914% | —× | 0.290pp | 0.000pp | 2.31% | 5 |
| `ENTRY_DISASTER_GUARD_ENABLED` | LRCX_SHORT | V8_EXACT_REPLAY | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=FIXED_2000_USD_CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD); binary=4.214%; weighted=0.763%; fold=FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD | EXACT_PARITY_ONLY | 9.653% | -69.997% | —× | 79.650pp | 0.000pp | 4.21% | 8 |
| `ENTRY_DISASTER_GUARD_ENABLED` | LRCX_SHORT | V8_EXACT_REPLAY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | SUPERSEDED_ACCOUNTING_AUDIT | 1.931% | -69.997% | —× | 71.928pp | 0.000pp | 4.21% | 8 |
| `ENTRY_DISASTER_GUARD_ENABLED` | ARM_SHORT | V8_EXACT_REPLAY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | INVALIDATED_DISCOVERY_BENCHMARK | 4.989% | -125.397% | —× | 130.386pp | 0.000pp | 3.95% | 8 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | IBIT_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 0.000% | 28.318% | 0.000× | -28.318pp | 0.000pp | 0.00% | 0 |
| `ENTRY_DISASTER_GUARD_ENABLED` | LRCX_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | INVALIDATED_REVERSED_SLIPPAGE | 3.692% | -69.997% | —× | 73.689pp | 1.316pp | 9.62% | 21 |
| `ENTRY_DISASTER_GUARD_ENABLED` | LRCX_SHORT | VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | INVALIDATED_REVERSED_SLIPPAGE | 2.159% | -69.997% | —× | 72.156pp | -0.216pp | 9.49% | 19 |

Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of outer-validation-fold capital-return percentages and are not a single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` rows are historical evidence and must not drive promotion.

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260725_c2 | 987 | 2026-07-27T03:26:25Z | 4m |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: positive but below B&H; retain as evidence, not as a winner.
- Gray: non-viable/losing result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
