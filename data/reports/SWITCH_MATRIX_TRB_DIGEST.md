# SWITCH_MATRIX_TRB progress digest — 2026-07-27 15:30:42Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `2026-07-27T15:26:39Z` (4m old).
- Generic DB activity (includes historical/stage tables): `2026-07-27T15:26:39Z` (4m old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **541**; new current rows in 24h: **541**.
- Raw repaired-campaign pilot rows since cutoff: **987**; **446** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **7,068/7,609**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260725_c2`, cutoff `2026-07-26T04:15:00Z`, exact code+NPZ+side fingerprint required.
- VEC diagnostic rows: **83,604** (never matrix proof).
- `SWITCH_MATRIX_TRB.xlsx`: 2026-07-27 15:30:42Z (0m old, 399,977 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-07-27 15:30:06Z (0m old, 37,827 bytes)
- Description coverage in current CSV: **3,570/3,570** rows.

## Stocks 5m execution provenance

Historical native 5m availability is provider-limited. Older rows use the disclosed containing-15m interpolation; native bars replace it permanently as they are collected.
Retention report scope: **224 symbols**, **224 native archives**, **6,864,191 native rows**, **0 availability warnings**, **0 hard errors**.

| key | native rows | native coverage | native range | interpolated rows | interpolated coverage | retention |
|---|---:|---:|---|---:|---:|---|
| MU_LONG | 15526 | — | 2026-03-23 → 2026-07-24 | — | — | PASS |
| VT_LONG | 41190 | — | 2024-07-11 → 2026-07-24 | — | — | PASS |
| HAO_SHORT | 5977 | — | 2026-04-09 → 2026-07-24 | — | — | PASS |

The append/merge ledger blocks any refresh that shrinks native row count, advances the first timestamp, regresses the last timestamp, or introduces duplicate/out-of-order timestamps. `synthetic_5m_parent_close_ts` records the bounded 0/5/10-minute parent lag.

## Pilot-key matrix coverage

| key | actionable cells filled | coverage | latest Tier-2 row | age | strategy tests |
|---|---:|---:|---|---:|---:|
| MU_LONG | 107/3,522 | 3.0% | 2026-07-27T15:26:39Z | 4m | 65 |
| VT_LONG | 352/3,522 | 10.0% | 2026-07-27T15:25:41Z | 5m | 150 |
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
| 2026-07-27T14:56:52Z | stocks_repaired_20260725_c2 | `LR_PCTB_D_LONG_ENTRY_THRESHOLD=0.25` | 1.4135 | 4.7610 | -3.3475 | 0.297× | 22.39% | 3180 | RED: CAPACITY CLAMPS |
| 2026-07-27T14:50:13Z | stocks_repaired_20260725_c2 | `LR_PCTB_D_LONG_ENTRY_THRESHOLD=0.2` | 1.4135 | 4.7610 | -3.3475 | 0.297× | 22.39% | 3180 | RED: CAPACITY CLAMPS |
| 2026-07-27T14:49:56Z | stocks_repaired_20260725_c2 | `LR_PCTB_D_LONG_ENTRY_THRESHOLD=0.15` | 1.4135 | 4.7610 | -3.3475 | 0.297× | 22.39% | 3180 | RED: CAPACITY CLAMPS |
| 2026-07-27T14:29:40Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_DEADBAND` | 0.9717 | 4.7610 | -3.7893 | 0.204× | 46.89% | 2059 | RED: CAPACITY CLAMPS |
| 2026-07-27T13:49:04Z | stocks_repaired_20260725_c2 | `STOP_PACK=ARROW_TH05` | 0.8577 | 4.7609 | -3.9032 | 0.180× | 46.88% | 2049 | RED: CAPACITY CLAMPS |
| 2026-07-27T13:30:52Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_REGIME_L` | 0.9717 | 4.7610 | -3.7893 | 0.204× | 46.89% | 2059 | RED: CAPACITY CLAMPS |
| 2026-07-27T13:30:16Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_REGIME_TRIM_L` | 0.9717 | 4.7610 | -3.7893 | 0.204× | 46.89% | 2059 | RED: CAPACITY CLAMPS |
| 2026-07-27T13:30:00Z | stocks_repaired_20260725_c2 | `STOP_PACK=RIDE_REGIME_TRIM` | 0.9717 | 4.7610 | -3.7893 | 0.204× | 46.89% | 2059 | RED: CAPACITY CLAMPS |

Best trading candidate: **none with at least two trades and complete return metrics**.

### VT_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-27T15:25:41Z | stocks_repaired_20260725_c2 | `ABLATION_DISABLE_QUICK_EXIT=False` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T15:25:25Z | stocks_repaired_20260725_c2 | `ABLATION_DISABLE_SPIKE_FADE_EXIT=False` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T15:25:13Z | stocks_repaired_20260725_c2 | `ABLATION_DISABLE_SPIKE_FADE_EXIT=True` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T15:19:24Z | stocks_repaired_20260725_c2 | `ABLATION_DISABLE_QUICK_EXIT=True` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T13:51:24Z | stocks_repaired_20260725_c2 | `WT_DC_ENTRY_K5M_MAX_LONG=150` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T13:51:10Z | stocks_repaired_20260725_c2 | `WT_DC_ENTRY_K5M_MAX_LONG=125` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T13:50:29Z | stocks_repaired_20260725_c2 | `WT_DC_ENTRY_K5M_MAX_LONG=100` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |
| 2026-07-27T13:48:22Z | stocks_repaired_20260725_c2 | `WT_DC_ENTRY_K5M_MAX_LONG=75` | -0.2820 | 0.2743 | -0.5563 | -1.028× | 2.97% | 497 | INERT / RECONNECT |

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

## MU stable-ladder Pareto holdout

| candidate | discovery | untouched holdout | B&H | multiple | TIM | exact | verdict |
|---|---|---:|---:|---:|---:|---|---|
| C151 `DAILY_DEEP_center_plateau_green_TARGET_CAP8_close_confirm_next_availability` | PASS | 1170.700% | 205.252% | 5.704× | 68.62% | PASS | GRAY / HOLDOUT REJECT (weighted_tim_70_80, return_sacrifice_compensated) |

This lane used a preregistered Pareto contract before opening the final fold. A strong return cannot repair a missed exposure gate after the result is known; the row therefore remains gray and is not written to the promotion matrix.

## HAO SHORT-native phase 3

| contract | candidates | E02 beat side benchmark | E05 beat side benchmark | TIM-valid | final | exact | verdict |
|---|---:|---:|---:|---:|---|---|---|
| `HAO_SHORT_NATIVE_PHASE3_V4` | 48 | 12 | 12 | 0 | SEALED_NOT_EVALUATED | NOT_RUN_DISCOVERY_GATE_FAILED | GRAY_REJECTED_QUARANTINED |

V1–V3 were explicitly invalidated. V4 fixes the persistent-reentry state contract, uses the shared completed-parent clock, and keeps the final fold sealed because no discovery candidate met every exposure/control gate.

## HAO SHORT exposure/persistence phase 4

| contract | discovery strict | D1 strategy / B&H / TIM | D2 strategy / B&H / TIM | final strategy / B&H / TIM | exact | verdict |
|---|---:|---|---|---|---|---|
| `HAO_SHORT_EXPOSURE_PHASE4_V3_RECLAIM_CONFIRM` | 3 | 536.61% / 67.39% / 77.95% | 174.14% / 20.64% / 71.28% | 1746.50% / 99.78% / 36.05% | NOT_RUN_FINAL_GATE_FAILED | GRAY_DISCOVERY_PASS_FINAL_TIM_FAIL |

The frozen phase-4 E02 policy achieved 70–80% weighted exposure and beat side-aware B&H plus the phase-3 same-exit control in both discovery folds. Its untouched final return remained strong but weighted TIM fell to 36.05%; the row is gray, exact did not run, and no matrix/live state changed.

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
| `ENTRY_WT_DC` | IBIT_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -1.697% | 8.890% | -0.191× | -10.587pp | 0.000pp | 2.62% | 12 |
| `ENTRY_DELTA_MTF` | ARM_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 14.638% | 15.974% | 0.916× | -1.335pp | 0.000pp | 2.78% | 13 |
| `ENTRY_DELTA_MTF` | MRVL_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_FINAL_REJECTED | 8.452% | 0.000% | —× | 8.452pp | 0.000pp | 1.30% | 5 |
| `ENTRY_DELTA_MTF` | SNDK_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -3.460% | 5.765% | -0.600× | -9.225pp | 0.000pp | 0.97% | 6 |
| `ENTRY_DELTA_MTF` | MU_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 10.510% | 0.000% | —× | 10.510pp | 0.000pp | 1.12% | 14 |
| `ENTRY_DELTA_MTF` | NVDA_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V2 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -0.772% | 0.000% | —× | -0.772pp | 0.000pp | 0.30% | 1 |
| `ENTRY_WT_DC` | IBIT_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -1.697% | 8.890% | -0.191× | -10.587pp | 0.000pp | 2.62% | 12 |
| `ENTRY_DELTA_MTF` | ARM_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 14.638% | 15.974% | 0.916× | -1.335pp | 0.000pp | 2.78% | 13 |
| `ENTRY_DELTA_MTF` | MRVL_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_FINAL_REJECTED | 8.452% | 0.000% | —× | 8.452pp | 0.000pp | 1.30% | 5 |
| `ENTRY_DELTA_MTF` | SNDK_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -3.460% | 5.765% | -0.600× | -9.225pp | 0.000pp | 0.97% | 6 |
| `ENTRY_DELTA_MTF` | MU_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | 10.510% | 0.000% | —× | 10.510pp | 0.000pp | 1.12% | 14 |
| `ENTRY_DELTA_MTF` | NVDA_SHORT | SHORT_METRIC_POLARITY_AUDIT_DISCOVERY_V1 | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_DISCOVERY_REJECTED | -0.772% | 0.000% | —× | -0.772pp | 0.000pp | 0.30% | 1 |
| `ENTRY_STOCH_HHHL` | DINO_LONG | VEC_DINO_PHASE3_JOINT_STABILITY_DISCOVERY_ONLY | SUM_OF_TWO_DISCOVERY_VALIDATION_FOLDS; return=CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT (LEGACY_UNSCOPED); TIM=UNWEIGHTED_MEAN_OF_DISCOVERY_FOLD_WEIGHTED_CAPACITY_PCT (LEGACY_UNSCOPED) | GRAY_REJECTED | 249.630% | 27.520% | 9.071× | 222.110pp | -10.611pp | 76.14% | 7 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 846.161% | 177.045% | 4.779× | 669.116pp | -306.970pp | 71.52% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 846.071% | 177.045% | 4.779× | 669.026pp | -307.061pp | 71.47% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1072.092% | 177.045% | 6.055× | 895.047pp | -81.039pp | 77.62% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 642.377% | 177.045% | 3.628× | 465.332pp | -510.755pp | 62.70% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 723.534% | 177.045% | 4.087× | 546.489pp | -429.598pp | 58.14% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 723.534% | 177.045% | 4.087× | 546.489pp | -429.598pp | 58.14% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 861.399% | 177.045% | 4.865× | 684.354pp | -295.238pp | 73.65% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 861.426% | 177.045% | 4.866× | 684.381pp | -295.211pp | 73.57% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1098.579% | 177.045% | 6.205× | 921.534pp | -58.058pp | 80.52% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 692.519% | 177.045% | 3.912× | 515.474pp | -464.118pp | 66.86% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 762.524% | 177.045% | 4.307× | 585.479pp | -394.113pp | 63.75% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 762.524% | 177.045% | 4.307× | 585.479pp | -394.113pp | 63.75% | 4 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 858.519% | 177.045% | 4.849× | 681.474pp | -320.420pp | 72.04% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 858.307% | 177.045% | 4.848× | 681.263pp | -320.632pp | 71.99% | 2 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 1146.008% | 177.045% | 6.473× | 968.964pp | -32.931pp | 81.22% | 1 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 695.993% | 177.045% | 3.931× | 518.948pp | -482.946pp | 65.20% | 3 |
| `EXIT_E06_REGRESSION_RETEST` | MU_LONG | VEC_MU_LADDER_TOP_EXIT_DISCOVERY | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_RESEARCH_REJECTED | 772.444% | 177.045% | 4.363× | 595.399pp | -406.495pp | 62.54% | 4 |

Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of outer-validation-fold capital-return percentages and are not a single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` rows are historical evidence and must not drive promotion.

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260725_c2 | 987 | 2026-07-27T15:26:39Z | 4m |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: positive but below B&H; retain as evidence, not as a winner.
- Gray: non-viable/losing result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
