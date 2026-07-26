# SWITCH_MATRIX_TRB progress digest — 2026-07-26 23:00:40Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `none` (unknown old).
- Generic DB activity (includes historical/stage tables): `2026-07-26T23:00:03Z` (0m old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **0**; new current rows in 24h: **0**.
- Raw repaired-campaign pilot rows since cutoff: **916**; **916** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **7,538/7,538**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260725_c2`, cutoff `2026-07-26T04:15:00Z`, exact code+NPZ+side fingerprint required.
- VEC diagnostic rows: **83,604** (never matrix proof).
- `SWITCH_MATRIX_TRB.xlsx`: 2026-07-26 23:00:39Z (0m old, 310,772 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-07-26 23:00:26Z (0m old, 57,987 bytes)
- Description coverage in current CSV: **3,522/3,522** rows.

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
| MU_LONG | 0/3,522 | 0.0% | — | unknown | 0 |
| VT_LONG | 0/3,522 | 0.0% | — | unknown | 0 |
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
| — | — | — | — | — | — | — | — | — | NO STRATEGY RESULTS |

### VT_LONG

| time | campaign | path/value | gain/mo | B&H/mo | vs B&H/mo | capture | TIM | trades | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| — | — | — | — | — | — | — | — | — | NO STRATEGY RESULTS |

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

- Jobs: **80**; states: ADAPTER_REQUIRED=41, OBSERVABILITY_ONLY=2, QUARANTINED=8, SCREENED=29.
- Frozen tradeable hashes: LONG `e0acfe4c1139dd01f6138445c53071eb307fa4ad0600fcd8dddcd7e794a78955`; SHORT `00c085e4069a105826f040cf9ff95373afaa4684f9ebc02f1a72edff4a0aa361`.
- Top LONG cohort: SNDK, MRVL, ARM, MU, AMD, INTC, PBF, MPC, DINO, VLO.
- Bottom SHORT cohort: LAC, UUUU, UEC, ASTS, HL, ACN, TTD, EGO, CDE, ALB.

| path | key | stage | metric scope / units | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `EXIT_ALGO_STOCH_4H_ROLL` | VLO_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 420.580% | 84.905% | 4.954× | 335.675pp | 33.272pp | 70.20% | 7 |
| `EXIT_ALGO_STOCH_4H_ROLL` | UUUU_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 88.415% | 26.197% | 3.375× | 62.218pp | 20.178pp | 19.25% | 5 |
| `EXIT_ALGO_STOCH_4H_ROLL` | UEC_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 95.033% | 22.648% | 4.196× | 72.385pp | 29.984pp | 19.74% | 3 |
| `EXIT_ALGO_STOCH_4H_ROLL` | TTD_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 468.405% | 54.325% | 8.622× | 414.080pp | -13.490pp | 88.08% | 13 |
| `EXIT_ALGO_STOCH_4H_ROLL` | SNDK_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 1586.480% | 467.087% | 3.397× | 1119.393pp | -739.157pp | 96.75% | 11 |
| `EXIT_ALGO_STOCH_4H_ROLL` | PBF_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 646.008% | 121.263% | 5.327× | 524.745pp | -61.263pp | 91.68% | 5 |
| `EXIT_ALGO_STOCH_4H_ROLL` | MU_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 519.097% | 204.905% | 2.533× | 314.192pp | -857.132pp | 49.33% | 8 |
| `EXIT_ALGO_STOCH_4H_ROLL` | MRVL_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 763.894% | 123.583% | 6.181× | 640.311pp | -908.833pp | 72.83% | 6 |
| `EXIT_ALGO_STOCH_4H_ROLL` | MPC_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 103.778% | 88.480% | 1.173× | 15.298pp | -13.682pp | 19.31% | 24 |
| `EXIT_ALGO_STOCH_4H_ROLL` | LDOS_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 67.584% | 37.502% | 1.802× | 30.082pp | -0.173pp | 15.28% | 9 |
| `EXIT_ALGO_STOCH_4H_ROLL` | LAC_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 81.124% | 36.977% | 2.194× | 44.147pp | -28.208pp | 19.37% | 5 |
| `EXIT_ALGO_STOCH_4H_ROLL` | INTC_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 753.721% | 139.232% | 5.413× | 614.490pp | -210.296pp | 84.19% | 17 |
| `EXIT_ALGO_STOCH_4H_ROLL` | HL_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 59.589% | 21.954% | 2.714× | 37.635pp | 9.337pp | 18.77% | 7 |
| `EXIT_ALGO_STOCH_4H_ROLL` | EGO_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 53.481% | 23.025% | 2.323× | 30.455pp | 5.463pp | 18.34% | 6 |
| `EXIT_ALGO_STOCH_4H_ROLL` | DINO_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 480.593% | 90.701% | 5.299× | 389.892pp | -4.646pp | 84.55% | 6 |
| `EXIT_ALGO_STOCH_4H_ROLL` | ASTS_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 89.904% | 22.270% | 4.037× | 67.634pp | 23.326pp | 23.33% | 13 |
| `EXIT_ALGO_STOCH_4H_ROLL` | ARM_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 904.976% | 126.604% | 7.148× | 778.372pp | -177.462pp | 87.95% | 7 |
| `EXIT_ALGO_STOCH_4H_ROLL` | AMD_LONG | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 869.356% | 134.451% | 6.466× | 734.905pp | -311.453pp | 98.30% | 5 |
| `EXIT_ALGO_STOCH_4H_ROLL` | ALB_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 77.926% | 19.412% | 4.014× | 58.514pp | -7.789pp | 40.35% | 5 |
| `EXIT_ALGO_STOCH_4H_ROLL` | ACN_SHORT | VEC_UNTOUCHED_OOS | LEGACY_UNSCOPED; return=LEGACY_UNSCOPED (LEGACY_UNSCOPED); TIM=LEGACY_UNSCOPED (LEGACY_UNSCOPED) | GRAY_REJECTED | 375.867% | 44.497% | 8.447× | 331.370pp | -25.110pp | 71.33% | 9 |
| `ENTRY_4H_DEEP_VALUE` | UUUU_SHORT | VEC_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD) | DISCARD_GRAY | 36.405% | 26.197% | 1.390× | 10.208pp | 34.421pp | 16.75% | 6 |
| `ENTRY_4H_DEEP_VALUE` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | NESTED_OUTER_VALIDATION_FOLD_AGGREGATE; return=SUM_OF_FOLD_CAPITAL_RETURN_PCT (SUM_ACROSS_OUTER_VALIDATION_FOLDS); TIM=PCT (ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS) | DISCARD_GRAY | -59.969% | -133.049% | —× | 73.080pp | 159.811pp | 28.63% | 21 |
| `ENTRY_4H_DEEP_VALUE` | UEC_SHORT | VEC_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD) | DISCARD_GRAY | 34.348% | 22.648% | 1.517× | 11.700pp | 0.688pp | 18.73% | 6 |
| `ENTRY_4H_DEEP_VALUE` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | NESTED_OUTER_VALIDATION_FOLD_AGGREGATE; return=SUM_OF_FOLD_CAPITAL_RETURN_PCT (SUM_ACROSS_OUTER_VALIDATION_FOLDS); TIM=PCT (ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS) | DISCARD_GRAY | -66.389% | -48.382% | —× | -18.007pp | 96.381pp | 36.15% | 23 |
| `ENTRY_4H_DEEP_VALUE` | TTD_SHORT | VEC_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD) | DISCARD_GRAY | 271.412% | 54.325% | 4.996× | 217.087pp | -9.467pp | 72.48% | 5 |
| `ENTRY_4H_DEEP_VALUE` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | NESTED_OUTER_VALIDATION_FOLD_AGGREGATE; return=SUM_OF_FOLD_CAPITAL_RETURN_PCT (SUM_ACROSS_OUTER_VALIDATION_FOLDS); TIM=PCT (ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS) | DISCARD_GRAY | 599.360% | 141.541% | 4.235× | 457.819pp | -148.377pp | 50.79% | 17 |
| `ENTRY_4H_DEEP_VALUE` | LDOS_SHORT | VEC_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD) | DISCARD_GRAY | 68.628% | 37.502% | 1.830× | 31.126pp | 2.011pp | 15.28% | 4 |
| `ENTRY_4H_DEEP_VALUE` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | NESTED_OUTER_VALIDATION_FOLD_AGGREGATE; return=SUM_OF_FOLD_CAPITAL_RETURN_PCT (SUM_ACROSS_OUTER_VALIDATION_FOLDS); TIM=PCT (ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS) | DISCARD_GRAY | -64.566% | 13.860% | -4.658× | -78.427pp | -34.217pp | 19.76% | 22 |
| `ENTRY_4H_DEEP_VALUE` | LAC_SHORT | VEC_UNTOUCHED_OOS | FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD; return=CAPITAL_RETURN_PCT (NONE_SINGLE_FOLD); TIM=PCT (NONE_SINGLE_FOLD) | DISCARD_GRAY | 27.115% | 36.977% | 0.733× | -9.862pp | -53.341pp | 13.51% | 6 |
| `ENTRY_4H_DEEP_VALUE` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | NESTED_OUTER_VALIDATION_FOLD_AGGREGATE; return=SUM_OF_FOLD_CAPITAL_RETURN_PCT (SUM_ACROSS_OUTER_VALIDATION_FOLDS); TIM=PCT (ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS) | DISCARD_GRAY | -947.126% | -11.541% | —× | -935.584pp | -642.198pp | 49.55% | 17 |

Metric guardrail: `VEC_NESTED_FOLD_AGGREGATE` returns are sums of outer-validation-fold capital-return percentages and are not a single holdout return. Only `FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD` rows use single-fold capital-return percentages; `LEGACY_UNSCOPED` rows are historical evidence and must not drive promotion.

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260725_c2 | 916 | 2026-07-26T22:58:24Z | 2m |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: positive but below B&H; retain as evidence, not as a winner.
- Gray: non-viable/losing result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
