# SWITCH_MATRIX_TRB progress digest — 2026-07-26 19:39:51Z

> Monitoring only. A green-looking screen is not promotable until a fresh Tier-2 replay has real closes, complete metrics, a changed trade fingerprint, and beats B&H.

## Freshness

- Current repaired matrix latest row: `none` (unknown old).
- Generic DB activity (includes historical/stage tables): `2026-07-26T19:39:47Z` (0m old); it is not matrix freshness.
- Current repaired-contract ENGINE rows: **0**; new current rows in 24h: **0**.
- Raw repaired-campaign pilot rows since cutoff: **762**; **762** are preserved but invalidated by the newer code+NPZ+side fingerprint, and **0** fail validation status. Blank current cells must be regenerated; they are not silently backfilled from old code.
- Historical/pre-fix ENGINE rows quarantined from current rankings: **7,384/7,384**. They remain preserved as evidence.
- Current contract: campaign `stocks_repaired_20260725_c2`, cutoff `2026-07-26T04:15:00Z`, exact code+NPZ+side fingerprint required.
- VEC diagnostic rows: **83,604** (never matrix proof).
- `SWITCH_MATRIX_TRB.xlsx`: 2026-07-26 19:37:02Z (2m old, 545,805 bytes)
- `SWITCH_MATRIX_TRB.csv.gz`: 2026-07-26 19:39:24Z (0m old, 57,889 bytes)
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

- Jobs: **69**; states: ADAPTER_REQUIRED=49, OBSERVABILITY_ONLY=2, QUARANTINED=7, SCREENED=11.
- Frozen tradeable hashes: LONG `e0acfe4c1139dd01f6138445c53071eb307fa4ad0600fcd8dddcd7e794a78955`; SHORT `00c085e4069a105826f040cf9ff95373afaa4684f9ebc02f1a72edff4a0aa361`.
- Top LONG cohort: SNDK, MRVL, ARM, MU, INTC, AMD, PBF, MPC, DINO, VLO.
- Bottom SHORT cohort: LAC, UUUU, UEC, ASTS, ACN, TTD, HL, EGO, ALB, LDOS.

| path | key | stage | state | strategy | B&H | multiple | alpha B&H | same-entry alpha | TIM | trades |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `ENTRY_DELTA_MTF` | UUUU_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -250.879% | -133.049% | —× | -117.830pp | -31.099pp | 41.94% | 25 |
| `ENTRY_DELTA_MTF` | UEC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -6.375% | -48.382% | —× | 42.007pp | 156.396pp | 40.36% | 26 |
| `ENTRY_DELTA_MTF` | TTD_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 730.520% | 141.541% | 5.161× | 588.979pp | -17.218pp | 58.91% | 15 |
| `ENTRY_DELTA_MTF` | LDOS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -106.847% | 13.860% | -7.709× | -120.707pp | -76.498pp | 29.14% | 26 |
| `ENTRY_DELTA_MTF` | LAC_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -124.817% | -11.541% | —× | -113.275pp | 180.111pp | 60.79% | 19 |
| `ENTRY_DELTA_MTF` | HL_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -1547.664% | -219.355% | —× | -1328.309pp | -812.965pp | 57.19% | 37 |
| `ENTRY_DELTA_MTF` | EGO_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -189.656% | -85.439% | —× | -104.217pp | 34.031pp | 25.05% | 34 |
| `ENTRY_DELTA_MTF` | ASTS_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -679.584% | -153.308% | —× | -526.277pp | -90.527pp | 54.88% | 28 |
| `ENTRY_DELTA_MTF` | ALB_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | -588.520% | -82.314% | —× | -506.207pp | 229.853pp | 51.06% | 28 |
| `ENTRY_DELTA_MTF` | ACN_SHORT | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 417.740% | 69.837% | 5.982× | 347.903pp | 10.595pp | 66.77% | 17 |
| `ENTRY_DELTA_MTF` | VLO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 617.044% | 113.267% | 5.448× | 503.777pp | 205.871pp | 74.34% | 17 |
| `ENTRY_DELTA_MTF` | SNDK_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 4393.120% | 888.467% | 4.945× | 3504.653pp | -1020.919pp | 87.43% | 7 |
| `ENTRY_DELTA_MTF` | PBF_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 914.462% | 127.673% | 7.163× | 786.789pp | 272.335pp | 45.40% | 22 |
| `ENTRY_DELTA_MTF` | MU_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 2345.153% | 381.950% | 6.140× | 1963.203pp | -157.929pp | 62.76% | 14 |
| `ENTRY_DELTA_MTF` | MRVL_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1500.274% | 104.106% | 14.411× | 1396.168pp | 217.185pp | 74.31% | 18 |
| `ENTRY_DELTA_MTF` | MPC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 230.452% | 103.592% | 2.225× | 126.860pp | 67.267pp | 52.23% | 20 |
| `ENTRY_DELTA_MTF` | INTC_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 919.326% | 214.032% | 4.295× | 705.294pp | -546.972pp | 75.61% | 21 |
| `ENTRY_DELTA_MTF` | DINO_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 624.763% | 118.221% | 5.285× | 506.542pp | 93.450pp | 60.44% | 16 |
| `ENTRY_DELTA_MTF` | ARM_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1427.265% | 123.020% | 11.602× | 1304.245pp | 276.441pp | 76.01% | 17 |
| `ENTRY_DELTA_MTF` | AMD_LONG | VEC_UNTOUCHED_OOS | DISCARD_GRAY | 1654.057% | 204.780% | 8.077× | 1449.277pp | -29.743pp | 72.00% | 14 |
| `EXIT_E01_CHANDELIER` | VLO_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 339.743% | 84.905% | 4.001× | 254.838pp | -47.564pp | 68.64% | 10 |
| `EXIT_E01_CHANDELIER` | UUUU_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 70.932% | 26.197% | 2.708× | 44.735pp | 2.696pp | 18.68% | 2 |
| `EXIT_E01_CHANDELIER` | UEC_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 39.386% | 22.648% | 1.739× | 16.738pp | -25.663pp | 16.16% | 7 |
| `EXIT_E01_CHANDELIER` | TTD_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 487.368% | 54.325% | 8.971× | 433.043pp | 5.473pp | 79.54% | 8 |
| `EXIT_E01_CHANDELIER` | SNDK_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1999.514% | 467.087% | 4.281× | 1532.427pp | -326.123pp | 97.45% | 4 |
| `EXIT_E01_CHANDELIER` | PBF_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 515.223% | 121.263% | 4.249× | 393.959pp | -192.049pp | 90.29% | 7 |
| `EXIT_E01_CHANDELIER` | MU_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 1370.913% | 204.905% | 6.690× | 1166.008pp | -5.315pp | 63.24% | 5 |
| `EXIT_E01_CHANDELIER` | MRVL_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 292.057% | 123.583% | 2.363× | 168.474pp | -1380.669pp | 58.80% | 9 |
| `EXIT_E01_CHANDELIER` | MPC_LONG | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 104.599% | 88.480% | 1.182× | 16.119pp | -12.861pp | 20.98% | 23 |
| `EXIT_E01_CHANDELIER` | LDOS_SHORT | VEC_UNTOUCHED_OOS | GRAY_REJECTED | 36.618% | 37.502% | 0.976× | -0.884pp | -31.139pp | 11.67% | 13 |

SHORT vector controls are present but remain research-only until exact replay and the same completed-HTF, fill, capacity, solvency, exposure, and mandatory-reclaim gates pass. No LONG result is inverted or pooled.

## Campaign activity

| tier | campaign | rows | latest | age |
|---|---|---:|---|---:|
| ENGINE | stocks_repaired_20260725_c2 | 762 | 2026-07-26T19:39:47Z | 0m |

## Reading the matrix

- Green: beats same-key B&H in the faithful engine; still requires replay and fingerprint checks.
- White: positive but below B&H; retain as evidence, not as a winner.
- Gray: non-viable/losing result; retain so it is not blindly retested.
- Red: zero trades, identical fingerprints across values, or disconnected/degenerate wiring.
- Ladder multipliers remain hypotheses. The remembered D/4h/1h values are a wiring baseline, not an optimized strategy.
