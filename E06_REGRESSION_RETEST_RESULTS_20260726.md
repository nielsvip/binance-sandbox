# E06 regression extreme / channel reentry cohort — 2026-07-26

## Outcome

The frozen top-10 LONG/bottom-10 SHORT cohort completed 20 × 192 = 3,840
same-entry E06 settings. There are **zero strict survivors**, so exact replay
is empty. Path-fleet job 46 is `SCREENED` with 20 gray result rows and zero
errors.

All selected validation winners are connected: every one has completed-4h raw
signals and actual next-RTH exit fills. The rejected performance rows are gray.
Inert parameter ranges are separately red so final-MTM holds cannot masquerade
as E06 results.

## Registry/code reconciliation

The registry and active research function are not the same contract:

| item | registry | active `_e06_signal` | campaign treatment |
|---|---|---|---|
| lookback | 40/60/100/160 | research generator 40/60/100/150/250 | tested registry 40/60/100/160 |
| arm z | 1.5/2/2.5/3 | 1.5/2/2.5/3 | exact |
| exit z | 0/0.5/1 | research generator 0.75/1/1.5/2 | tested registry 0/0.5/1 |
| fourth knob | `rebound_atr` .25/.5/.7/1 | `corr_gate`; no ATR rebound | values mapped and labeled as correlation gates |
| confirmation | “retest” | channel reentry **OR first adverse completed-4h break** | active semantics documented |
| live config | implied tradeable path | no Tradier E06 key/switch | `DISCONNECTED_RESEARCH_ONLY` |

The code uses a prior-only log regression center/sigma/slope/correlation. It
arms at a side-specific regression extreme and records the extreme price as
the reclaim reference. It does not implement an ATR rebound or a distinct
post-break retest state. Those stale semantics must be fixed before this
research path can be called a connected live knob.

The four registered fourth-knob values were retained as traceable
`effective_corr_gate` values; they were not silently presented as ATR tests.

## Range wiring evidence

Across the 3,840 symbol/settings, 1,098 (28.6%) produced zero raw events and
zero fills.

| parameter | value | candidates | inert | aggregate actual fills |
|---|---:|---:|---:|---:|
| corr gate | 0.25 | 960 | 21 | 8,103 |
| corr gate | 0.50 | 960 | 30 | 6,188 |
| corr gate | 0.70 | 960 | 87 | 4,365 |
| corr gate | 1.00 | 960 | **960** | **0** |
| lookback | 40 | 960 | 249 | 6,471 |
| lookback | 60 | 960 | 255 | 4,686 |
| lookback | 100 | 960 | 279 | 3,746 |
| lookback | 160 | 960 | 315 | 3,753 |
| arm z | 1.5 | 960 | 246 | 8,658 |
| arm z | 2.0 | 960 | 249 | 5,248 |
| arm z | 2.5 | 960 | 267 | 3,044 |
| arm z | 3.0 | 960 | 336 | 1,706 |

Correlation gate 1.0 is a red range: exact rolling correlation is effectively
unreachable, making all 960 arms inert. It must not be retested unchanged.
Increasing lookback/arm z also reduces frequency, but those ranges remain
connected. Exit-z values each had the same 366 inert cases because exit z is
evaluated only after arming; it cannot repair an arm that never occurs.

## Frozen validation winners

| key | raw/fills | alpha B&H | alpha E02 | TIM | open obligations | verdict |
|---|---:|---:|---:|---:|---:|---|
| AMD_LONG | 4/4 | +792.629pp | -253.728pp | 99.21% | 3 | gray |
| ARM_LONG | 4/4 | +737.530pp | -218.304pp | 93.65% | 2 | gray |
| DINO_LONG | 9/9 | +439.629pp | +45.092pp | 85.97% | 3 | gray |
| INTC_LONG | 2/2 | +522.039pp | -302.747pp | 96.67% | 1 | gray |
| MPC_LONG | 3/3 | +132.670pp | +103.689pp | 53.04% | 0 | gray: low TIM |
| MRVL_LONG | 4/4 | +802.586pp | -746.558pp | 85.80% | 2 | gray |
| MU_LONG | 1/1 | +867.993pp | -303.331pp | 73.46% | 1 | gray |
| PBF_LONG | 3/3 | +562.175pp | -23.834pp | 94.56% | 2 | gray |
| SNDK_LONG | 1/1 | +2,117.405pp | +258.855pp | 99.63% | 1 | gray: hold-like |
| VLO_LONG | 2/2 | +344.579pp | +42.177pp | 94.36% | 1 | gray |
| ACN_SHORT | 7/7 | +344.894pp | -11.587pp | 89.72% | 2 | gray |
| ALB_SHORT | 7/7 | +148.270pp | +81.967pp | 40.29% | 1 | gray |
| ASTS_SHORT | 3/3 | +74.759pp | +30.452pp | 40.51% | 1 | gray |
| EGO_SHORT | 5/5 | +34.174pp | +9.181pp | 29.20% | 1 | gray |
| HL_SHORT | 6/6 | +63.808pp | +35.509pp | 23.53% | 2 | gray |
| LAC_SHORT | 2/2 | +84.144pp | +11.789pp | 20.92% | 0 | gray: low TIM |
| LDOS_SHORT | 13/12 | +138.218pp | +107.963pp | 29.63% | 1 | gray |
| TTD_SHORT | 7/7 | +482.037pp | +54.467pp | 96.24% | 1 | gray |
| UEC_SHORT | 4/4 | +115.174pp | +72.773pp | 26.20% | 1 | gray |
| UUUU_SHORT | 5/5 | +66.060pp | +24.020pp | 21.67% | 1 | gray |

No row passes the combined contract. MU is in the validation exposure band but
loses 303.33pp to identical-entry E02 and leaves a reclaim open. DINO, MPC,
SNDK, VLO and nine SHORT rows beat both comparators in validation, but exposure
is outside 70–80% and/or reclaim obligations remain open. SNDK's 99.63% TIM is
a leveraged hold, not evidence of a successful top exit. LDOS's 13 raw events
but 12 fills is valid observability: one signal arrived while no fillable
position/next-bar action was available.

## Contract, speed and artifacts

- Frozen entry schedule hashes are checked for all candidates/folds.
- LONG/SHORT accounting and B&H remain separate.
- Completed 4h source timestamps must be no later than observation.
- Next-RTH fills, costs, $16,000 capacity and persistent reclaim obligations
  use the compiled same-entry scanner.
- Acceptance requires actual signals/fills and every-fold positive alpha
  versus B&H and E02, 70–80% discovery/validation TIM, zero future HTF,
  insolvency, capacity breach, flat-beyond-reclaim and open obligations.
- Runtime: 3.308–7.692 seconds/key; median 4.739; 96.933 seconds total.
- S1 cohort:
  `data/reports/vec_research/e06_top10_long_bottom10_short_20260726T2000Z`
- S1 fleet:
  `data/reports/path_fleet/job_46_EXIT_E06_REGRESSION_RETEST/summary.json`
- Code:
  `tools/vec_same_entry_e06_adapter.py`,
  `tools/run_same_entry_e06_cohort.py`,
  `tools/path_fleet_e06_ingest.py`.

Rollback is limited to those files plus path-registry/digest documentation.
Frozen controls, NPZs, live config and matrix cells were not modified.
