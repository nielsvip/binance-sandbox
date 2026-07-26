# EXIT E05 Divergence/Break/Retest — 2026-07-26

## Outcome

E05 is now a causal, side-isolated, same-entry research adapter. It does not
exit on the first structural break. Its completed-4h state sequence is:

`confirmed prior-only divergence → later structural break → ATR rebound → later rollover exit`

The active 108-setting grid ran over the frozen top-10 LONG and bottom-10
SHORT controls: 20/20 keys, 2,160 candidates, 0 errors, 76 selected-winner
signals, 74 actual compiled exit fills, and zero future-HTF observations.

No candidate passed every chronological fold's B&H, same-entry E02,
70–80% weighted-TIM, reclaim, capacity, and solvency gates. Therefore:

- vector survivors: **0**
- exact-replay queue: **empty**
- engine/live promotion: **none**

This is a valid negative result, not a zero-trade engine failure. Fifteen
selected final-fold winners produced actual exits and were retained gray; five
selected winners were inert in the final fold and were recorded red.

## Registry and wiring audit

The old job-44 queue contract was stale:

| field | stale queue contract | active `_e05_candidates` / adapter |
|---|---|---|
| pivot radius | 2, 3, 4, 6 | 2, 3, 5 |
| RSI divergence minimum | 3, 5, 8, 12 | 3, 5, 8 |
| structural-break ATR buffer | absent | 0.0, 0.25 |
| rebound ATR | 0.25, 0.5, 1.0 | 0.25, 0.5 |
| maximum post-break wait | 6, 12, 18, 24 | 8, 12, 20 |
| candidates per key | implied 768 | 108 |

The path has no equivalent named live Tradier switch/state machine:

- `live_config_keys=[]`
- `live_switch_status=DISCONNECTED_RESEARCH_ONLY`
- research adapter: `tools/vec_same_entry_e05_adapter.py`

Tradier contains other divergence settings and consumers, but none implements
this exact four-state exit sequence. Research evidence must not be represented
as proof that a live E05 switch is connected.

## Causality and accounting contract

- Pivots are usable only at `pivot_index + radius`, after the right-hand bars
  exist.
- RSI/price divergence uses only those confirmed pivots.
- The between-pivot structural level is formed only from already observed
  completed-4h bars.
- Crossing that level changes state but cannot emit an exit.
- A later ATR rebound and a still-later lower-high/lower-low rollover (LONG),
  or mirrored higher-low/higher-high rollover (SHORT), emits the signal.
- The event maps to the execution timeline with completed source timestamp
  less than or equal to the observation timestamp.
- Signals fill through the same compiled next-fill accounting as the frozen
  ladder schedule.
- The comparator is the same-entry E02 control; every full exit creates the
  same lower/reclaim obligation and uses the same $16,000 capacity and cost
  model.
- LONG and SHORT results remain separate.

Regression tests explicitly prove the first break and rebound bars do not
exit, the later rollover does, source timestamps are causal, the 108-arm grid
matches active code, and every individual fold must satisfy all strict gates.

## Final chronological fold

These are final-fold readings only. They do not override the failed
every-fold robustness gate.

| key | signals | exits | return | B&H | E02 control | alpha control | TIM | final status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| SNDK_LONG | 1 | 1 | 4447.54% | 467.09% | 2325.64% | +2121.90pp | 98.80% | gray |
| MRVL_LONG | 2 | 1 | 1021.57% | 123.58% | 1672.73% | -651.15pp | 92.35% | gray |
| ARM_LONG | 2 | 2 | 1189.63% | 126.60% | 1082.44% | +107.19pp | 93.43% | gray |
| MU_LONG | 1 | 1 | 1316.16% | 204.90% | 1376.23% | -60.07pp | 73.13% | gray |
| INTC_LONG | 0 | 0 | 958.64% | 139.23% | 964.02% | -5.38pp | 98.61% | red/inert |
| AMD_LONG | 1 | 1 | 1373.64% | 134.45% | 1180.81% | +192.83pp | 99.44% | gray |
| PBF_LONG | 5 | 5 | 672.90% | 121.26% | 707.27% | -34.37pp | 93.87% | gray |
| MPC_LONG | 1 | 1 | 235.02% | 88.48% | 117.46% | +117.56pp | 50.21% | gray |
| DINO_LONG | 1 | 1 | 603.57% | 90.70% | 485.24% | +118.33pp | 96.55% | gray |
| VLO_LONG | 1 | 1 | 521.45% | 84.91% | 387.31% | +134.14pp | 96.02% | gray |
| LAC_SHORT | 2 | 2 | 166.29% | 36.98% | 109.33% | +56.96pp | 32.06% | gray |
| UUUU_SHORT | 0 | 0 | 68.24% | 26.20% | 68.24% | +0.00pp | 22.71% | red/inert |
| ASTS_SHORT | 5 | 5 | 135.31% | 22.27% | 66.58% | +68.73pp | 58.17% | gray |
| UEC_SHORT | 0 | 0 | 75.31% | 22.65% | 65.05% | +10.26pp | 21.70% | red/inert |
| ACN_SHORT | 0 | 0 | 429.13% | 44.50% | 400.98% | +28.15pp | 92.59% | red/inert |
| TTD_SHORT | 1 | 1 | 580.76% | 54.33% | 481.90% | +98.87pp | 97.53% | gray |
| HL_SHORT | 3 | 3 | 73.56% | 21.95% | 50.25% | +23.31pp | 44.01% | gray |
| EGO_SHORT | 0 | 0 | 46.44% | 23.03% | 48.02% | -1.58pp | 21.57% | red/inert |
| ALB_SHORT | 1 | 1 | 164.05% | 19.41% | 85.72% | +78.34pp | 46.30% | gray |
| LDOS_SHORT | 1 | 1 | 70.06% | 37.50% | 67.76% | +2.30pp | 15.84% | gray |

All 20 selected final-fold rows beat side-specific B&H, and 15 beat the
same-entry E02 control. That is encouraging path evidence, but it cannot be
promoted because the frozen candidate selected on discovery did not reproduce
all gates in every fold.

## Why no strict survivor

Across each key's complete 108-arm grid:

- no key had even one setting with 70–80% weighted TIM in **every** fold;
- 212/2,160 candidates had zero completed rollover signals across the tested
  folds;
- 270/2,160 had zero actual technical exit fills;
- the selected winners accumulated 40 end-of-fold reclaim obligations across
  all folds (nine in the final folds); and
- several settings beat B&H but failed the stronger same-entry E02 control.

An exit-only path cannot increase exposure above the frozen entry schedule.
The very low SHORT TIM values therefore identify a coupled entry/exposure
constraint, not something that can be repaired by weakening the exit
causality sequence. The next combined campaign should test E05 alongside
ladder/entry schedules already capable of the target TIM, while retaining the
same strict per-fold comparator and reclaim gates.

## Artifacts and rollback

- Cohort:
  `data/reports/vec_research/e05_top10_long_bottom10_short_20260726T2200Z/`
- Fleet result:
  `data/reports/path_fleet/job_44_EXIT_E05_DIVERGENCE_RETEST/summary.json`
- Pre-ingest fleet backup:
  `data/reports/path_fleet/queue.db.bak_job44_e05_20260726T2200Z`
- Job 44 stage: `SCREENED`
- Matrix/live writes: none
