# SWITCH_MATRIX_TRB progress digest — 2026-07-26 21:40:39Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `none` (unknown old).
- Generic DB activity (includes historical/stage tables): `2026-07-26T21:40:39Z` (0m old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **0**; new current rows in 24h: **0**.
- Raw repaired-campaign pilot rows since cutoff: **852**; **852** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **7,474/7,474**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260725_c2`, cutoff `2026-07-26T04:15:00Z`, exact code+NPZ+side fingerprint required.
- VEC diagnostic rows: **83,604** (never matrix proof).
- `SWITCH_MATRIX_TRB.xlsx`: 2026-07-26 21:40:39Z (0m old, 310,772 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-07-26 21:40:03Z (0m old, 34,057 bytes)
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

- Jobs: **72**; states: ADAPTER_REQUIRED=45, OBSERVABILITY_ONLY=2, QUARANTINED=7, SCREENED=18.
- Frozen tradeable hashes: LONG `e0acfe4c1139dd01f6138445c53071eb307fa4ad0600fcd8dddcd7e794a78955`; SHORT `00c085e4069a105826f040cf9ff95373afaa4684f9ebc02f1a72edff4a0aa361`.
- Top LONG cohort: SNDK, MRVL, ARM, MU, INTC, AMD, PBF, MPC, DINO, VLO.
- Bottom SHORT cohort: LAC, UUUU, UEC, ASTS, ACN, TTD, HL, EGO, ALB, LDOS.

| path | key | stage | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 67.828% | 37.502% | 1.809× | 30.325pp | 1.210pp | 18.03% | 7 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LDOS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -75.350% | 13.860% | -5.436× | -89.210pp | -45.001pp | 31.20% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -25.564% | 19.412% | -1.317× | -44.976pp | -29.435pp | 35.37% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ALB_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -467.793% | -82.314% | —× | -385.479pp | 350.580pp | 55.37% | 26 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 31.261% | 23.025% | 1.358× | 8.236pp | 26.422pp | 28.01% | 11 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | EGO_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -179.458% | -85.439% | —× | -94.019pp | 44.229pp | 29.03% | 49 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -44.350% | 21.954% | -2.020× | -66.304pp | -59.390pp | 22.01% | 10 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | HL_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -952.312% | -219.355% | —× | -732.957pp | -217.613pp | 43.76% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 334.506% | 54.325% | 6.157× | 280.180pp | 53.627pp | 91.54% | 7 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | TTD_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 822.601% | 141.541% | 5.812× | 681.060pp | 74.863pp | 63.02% | 16 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 309.131% | 44.497% | 6.947× | 264.634pp | -78.940pp | 87.96% | 3 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ACN_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 424.512% | 69.837% | 6.079× | 354.675pp | 17.367pp | 66.58% | 22 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 52.911% | 22.648% | 2.336× | 30.264pp | 19.251pp | 24.68% | 10 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UEC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -276.204% | -48.382% | —× | -227.823pp | -113.434pp | 44.08% | 32 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -32.074% | 22.270% | -1.440× | -54.345pp | -14.953pp | 26.07% | 17 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | ASTS_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -593.437% | -153.308% | —× | -440.129pp | -4.380pp | 56.33% | 27 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 2.290% | 26.197% | 0.087× | -23.907pp | 0.306pp | 23.55% | 14 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | UUUU_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -395.420% | -133.049% | —× | -262.371pp | -175.640pp | 45.43% | 39 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 119.394% | 36.977% | 3.229× | 82.417pp | 38.937pp | 22.27% | 13 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | LAC_SHORT | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | -316.576% | -11.541% | —× | -305.035pp | -11.648pp | 60.31% | 25 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 395.895% | 84.905% | 4.663× | 310.989pp | 33.608pp | 96.37% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | VLO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 561.563% | 113.267% | 4.958× | 448.296pp | 150.390pp | 69.78% | 15 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 525.689% | 90.701% | 5.796× | 434.987pp | 85.281pp | 82.03% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | DINO_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 753.918% | 118.221% | 6.377× | 635.697pp | 222.605pp | 69.96% | 18 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 167.798% | 88.480% | 1.896× | 79.318pp | 60.906pp | 39.31% | 8 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | MPC_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 295.149% | 103.592% | 2.849× | 191.557pp | 131.964pp | 55.00% | 21 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 766.682% | 121.263% | 6.322× | 645.419pp | 190.810pp | 86.15% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | PBF_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 842.540% | 127.673% | 6.599× | 714.867pp | 200.413pp | 48.31% | 30 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 932.947% | 134.451% | 6.939× | 798.496pp | -192.160pp | 89.84% | 4 |
| `ENTRY_DC_BREAK_ENTRY_ENABLED` | AMD_LONG | VEC_NESTED_FOLD_AGGREGATE | DISCARD_GRAY_RESEARCH_RECONSTRUCTION | 1492.826% | 204.780% | 7.290× | 1288.046pp | -190.973pp | 75.44% | 18 |

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260725_c2 | 852 | 2026-07-26T21:40:39Z | 0m |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: positive but below B&H; retain as evidence, not as a winner.
- Gray: non-viable/losing result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
