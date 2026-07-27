# Qualified-arm delayed lower-top exits — v2 result (2026-07-27)

## Outcome

The preregistered vector pass completed **21,384** candidate evaluations with zero execution errors. It tested one immutable side-specific entry schedule for each top/bottom-20 key, plus VT_LONG and isolated/versioned HAO_SHORT. No candidate passed all chronological folds; the exact-engine queue is empty and no live setting or canonical NPZ changed.

The first adverse break never sells. ATR, rolling-STDEV, or prior-window DC/support displacement on completed 15m/1h bars only arms the state. A later completed 5m/15m/1h rebound must form a lower price/WT1 top (exact SHORT mirror), then a distinct adverse price and/or WT rollover confirms the next-RTH exit. Emergency overlays are limited to ATR6, STDEV7, or eight uninterrupted adverse bars and must remain at or below 10% of exits.

## Candidate funnel

| family | candidates | final-only | discovery-only | all folds |
|---|---:|---:|---:|---:|
| BOTTOM_B_STRUCTURAL_V2 | 21,120 | 106 | 0 | 0 |
| BOTTOM_C_STRUCTURAL_V2_EMERGENCY | 264 | 4 | 0 | 0 |

Global disjoint outcomes: FINAL_ONLY=110, NO_PARTITION_SURVIVOR=21,274.

## Fold failure counts

### BOTTOM_B_STRUCTURAL_V2

| fold | pass | fail | not >B&H | not >E02 | TIM | insolvent | capacity | future | reclaim | no exit | emergency >10% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| discovery 1 | 428 | 20,692 | 10,176 | 12,264 | 19,340 | 286 | 0 | 0 | 0 | 0 | 0 |
| discovery 2 | 32 | 20,128 | 10,499 | 11,325 | 18,310 | 1,996 | 0 | 0 | 0 | 0 | 0 |
| final | 106 | 21,014 | 6,496 | 19,724 | 19,566 | 214 | 0 | 0 | 0 | 0 | 0 |

### BOTTOM_C_STRUCTURAL_V2_EMERGENCY

| fold | pass | fail | not >B&H | not >E02 | TIM | insolvent | capacity | future | reclaim | no exit | emergency >10% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| discovery 1 | 12 | 252 | 99 | 138 | 216 | 12 | 0 | 0 | 0 | 0 | 118 |
| discovery 2 | 0 | 252 | 160 | 172 | 211 | 50 | 0 | 0 | 0 | 0 | 131 |
| final | 4 | 260 | 56 | 249 | 207 | 0 | 0 | 0 | 0 | 0 | 121 |

## Discovery-selected winners

| key | family | discovery gates | final return / B&H / E02 | TIM | exits | final failures |
|---|---|---|---:|---:|---:|---|
| ACN_SHORT | B_STRUCTURAL_V2 | fail/fail | 145.35 / 44.50 / 400.98 | 52.89% | 42 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| ACN_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 289.20 / 44.50 / 400.98 | 77.04% | 21 | NOT_ABOVE_SAME_ENTRY_E02, EMERGENCY_NOT_RARE |
| ALB_SHORT | B_STRUCTURAL_V2 | fail/fail | 16.03 / 19.41 / 85.72 | 25.49% | 15 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| ALB_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 22.28 / 19.41 / 85.72 | 22.42% | 32 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| AMD_LONG | B_STRUCTURAL_V2 | fail/fail | 824.33 / 134.45 / 1180.81 | 86.41% | 29 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| AMD_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 879.21 / 134.45 / 1180.81 | 90.82% | 19 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| ARM_LONG | B_STRUCTURAL_V2 | fail/fail | 893.93 / 126.60 / 1082.44 | 63.90% | 23 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| ARM_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 873.39 / 126.60 / 1082.44 | 79.24% | 14 | NOT_ABOVE_SAME_ENTRY_E02 |
| ASTS_SHORT | B_STRUCTURAL_V2 | fail/fail | 79.05 / 22.27 / 66.58 | 12.11% | 50 | TIM_OUTSIDE_70_80 |
| ASTS_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 0.18 / 22.27 / 66.58 | 20.34% | 16 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| DINO_LONG | B_STRUCTURAL_V2 | fail/fail | 408.50 / 90.70 / 485.24 | 72.55% | 18 | NOT_ABOVE_SAME_ENTRY_E02 |
| DINO_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 349.06 / 90.70 / 485.24 | 74.08% | 19 | NOT_ABOVE_SAME_ENTRY_E02, EMERGENCY_NOT_RARE |
| EGO_SHORT | B_STRUCTURAL_V2 | fail/fail | 36.41 / 23.03 / 48.02 | 9.91% | 59 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| EGO_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 36.26 / 23.03 / 48.02 | 12.33% | 21 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| HAO_SHORT | B_STRUCTURAL_V2 | fail/fail | 1936.14 / 99.78 / 1551.41 | 71.86% | 10 | pass |
| HAO_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 1936.14 / 99.78 / 1551.41 | 71.86% | 10 | pass |
| HL_SHORT | B_STRUCTURAL_V2 | fail/fail | 44.20 / 21.95 / 50.25 | 9.09% | 136 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| HL_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 24.70 / 21.95 / 50.25 | 18.10% | 15 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| INTC_LONG | B_STRUCTURAL_V2 | fail/fail | 631.23 / 139.23 / 964.02 | 76.73% | 16 | NOT_ABOVE_SAME_ENTRY_E02 |
| INTC_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 553.05 / 139.23 / 964.02 | 78.40% | 20 | NOT_ABOVE_SAME_ENTRY_E02, EMERGENCY_NOT_RARE |
| LAC_SHORT | B_STRUCTURAL_V2 | fail/fail | 22.97 / 36.98 / 109.33 | 15.18% | 15 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| LAC_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 50.22 / 36.98 / 109.33 | 15.46% | 18 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| LDOS_SHORT | B_STRUCTURAL_V2 | fail/fail | 53.95 / 37.50 / 67.76 | 11.74% | 38 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| LDOS_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 57.17 / 37.50 / 67.76 | 15.09% | 17 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| MPC_LONG | B_STRUCTURAL_V2 | fail/fail | 100.80 / 88.48 / 117.46 | 21.97% | 20 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| MPC_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 87.89 / 88.48 / 117.46 | 19.35% | 30 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| MRVL_LONG | B_STRUCTURAL_V2 | pass/fail | 474.56 / 123.58 / 1672.73 | 50.67% | 32 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| MRVL_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 235.45 / 123.58 / 1672.73 | 43.34% | 28 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| MU_LONG | B_STRUCTURAL_V2 | fail/fail | 370.68 / 204.90 / 1376.23 | 44.48% | 18 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| MU_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 362.12 / 204.90 / 1376.23 | 44.57% | 18 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| PBF_LONG | B_STRUCTURAL_V2 | fail/fail | 20.14 / 121.26 / 707.27 | 47.69% | 102 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| PBF_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 327.25 / 121.26 / 707.27 | 74.13% | 22 | NOT_ABOVE_SAME_ENTRY_E02, EMERGENCY_NOT_RARE |
| SNDK_LONG | B_STRUCTURAL_V2 | fail | 1525.17 / 467.09 / 2325.64 | 85.98% | 41 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| SNDK_LONG | C_STRUCTURAL_V2_EMERGENCY | fail | 1657.83 / 467.09 / 2325.64 | 82.46% | 39 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80, EMERGENCY_NOT_RARE |
| TTD_SHORT | B_STRUCTURAL_V2 | fail/pass | 377.92 / 54.33 / 481.90 | 77.44% | 22 | NOT_ABOVE_SAME_ENTRY_E02 |
| TTD_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 392.32 / 54.33 / 481.90 | 85.20% | 13 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| UEC_SHORT | B_STRUCTURAL_V2 | fail/fail | 48.31 / 22.65 / 65.05 | 6.01% | 120 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| UEC_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 102.22 / 22.65 / 65.05 | 15.34% | 11 | TIM_OUTSIDE_70_80 |
| UUUU_SHORT | B_STRUCTURAL_V2 | fail/fail | 71.99 / 26.20 / 68.24 | 9.62% | 59 | TIM_OUTSIDE_70_80 |
| UUUU_SHORT | C_STRUCTURAL_V2_EMERGENCY | fail/fail | -24.15 / 26.20 / 68.24 | 18.88% | 15 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| VLO_LONG | B_STRUCTURAL_V2 | fail/fail | 229.65 / 84.91 / 387.31 | 64.34% | 16 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| VLO_LONG | C_STRUCTURAL_V2_EMERGENCY | fail/fail | 229.65 / 84.91 / 387.31 | 64.34% | 16 | NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| VT_LONG | B_STRUCTURAL_V2 | pass/fail | -38.35 / 1.57 / -16.73 | 44.56% | 31 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |
| VT_LONG | C_STRUCTURAL_V2_EMERGENCY | pass/fail | -38.12 / 1.57 / -16.73 | 52.95% | 38 | NOT_ABOVE_BH, NOT_ABOVE_SAME_ENTRY_E02, TIM_OUTSIDE_70_80 |

## Emergency overlay effect

| brake | pairs | mean / median final delta | positive pairs |
|---|---:|---:|---:|
| ADVERSE_ATR_6 | 88 | -33.74 / 0.00pp | 8 |
| ADVERSE_STDEV_7 | 88 | -32.46 / -3.29pp | 26 |
| CONTINUED_8 | 88 | -4.36 / 0.00pp | 16 |

## Decision

All rows remain attributable gray research evidence. A high final-only return cannot enter exact replay. HAO remains isolated/versioned even if a future vector setting passes. The result confirms the mechanics are now tested as requested, but arm qualification alone does not repair the cross-fold TIM/control instability inherited from the frozen entry schedules.

Artifacts:

- `bottom_structural_v2_20260727T014708Z/preregistered_contract.json`
- `bottom_structural_v2_20260727T014708Z/campaign_result.json`
- `bottom_structural_v2_20260727T014708Z/analysis.json`
