# Structural WT lower-top / higher-bottom cohort — 2026-07-26

## Outcome

The exact registry grid is complete for the frozen top-10 LONG and bottom-10
SHORT cohorts: 20 symbol/sides × 768 settings = 15,360 candidates. There are
**zero strict vector survivors**, so no exact replay was queued and no switch
matrix/live setting was promoted.

This is tested gray evidence, not a broken or untested path. Every symbol's
compiled discovery winner reproduced through the Python
`StructuralWtRetestExitBook` with no metric differences. The path-fleet ledger
stores all 20 untouched-validation rows as `GRAY_REJECTED` in job 37.

## Frozen contract

- Entry requests, multipliers, target/add semantics, next-RTH fills, costs and
  the $16,000 capacity are frozen from each accepted ladder artifact.
- LONG and SHORT use separate artifacts, accounting and side-mirrored states.
- ARM: completed 1h or 4h structural lower low for LONG; higher high for SHORT.
- CONFIRM: completed 15m or 1h lower price/WT1 rebound top for LONG; mirrored
  higher bottom for SHORT.
- Grid: ARM 2 × CONFIRM 2 × WT rebound 4 × pivot lookback 4 × maximum wait 4 ×
  profit gate 3 = 768.
- Maximum wait is wall-clock normalized: 12/20/30/48 hours becomes 48/80/120/192
  completed 15m bars or 12/20/30/48 completed 1h bars.
- A break only arms the path. `dc_low4_5m` is never an exit.
- Every exit retains the lower-ladder and persistent resting reclaim contract.
- Acceptance requires every discovery fold and the untouched validation fold
  to beat both side-specific $2,000 B&H and same-entry E02, remain solvent and
  causal, and keep weighted TIM in 70–80% in discovery and validation.

## Untouched validation results

Alpha is percentage points versus the named comparator. These are the
chronologically frozen discovery winners—not the best settings selected on
validation.

| key | alpha vs B&H | alpha vs E02 | TIM | verdict |
|---|---:|---:|---:|---|
| ACN_SHORT | +216.845 | -139.636 | 60.49% | gray: loses control, low TIM |
| ALB_SHORT | +40.790 | -25.513 | 42.51% | gray: loses control, low TIM |
| AMD_LONG | +594.695 | -451.662 | 96.52% | gray: loses control, high TIM |
| ARM_LONG | +1,213.620 | +257.786 | 81.13% | gray: discovery not robust; TIM high |
| ASTS_SHORT | +44.856 | +0.548 | 22.87% | gray: discovery loses B&H; TIM low |
| DINO_LONG | +435.589 | +41.051 | 89.51% | gray: discovery/validation TIM fail |
| EGO_SHORT | +37.892 | +12.899 | 20.22% | gray: discovery loses B&H; TIM low |
| HL_SHORT | +21.559 | -6.739 | 19.32% | gray: loses control; TIM low |
| INTC_LONG | +940.356 | +115.571 | 94.60% | gray: discovery/validation TIM high |
| LAC_SHORT | +38.610 | -33.745 | 17.89% | gray: loses control; TIM low |
| LDOS_SHORT | +9.232 | -21.024 | 12.99% | gray: loses control; TIM low |
| MPC_LONG | +48.813 | +19.833 | 20.79% | gray: discovery/validation TIM low |
| MRVL_LONG | +827.117 | -722.027 | 78.99% | gray: loses control |
| MU_LONG | +1,127.000 | -44.324 | 65.21% | gray: loses control; TIM low |
| PBF_LONG | +464.956 | -121.052 | 89.49% | gray: loses control; TIM high |
| SNDK_LONG | +5,091.187 | +3,232.637 | 98.70% | gray: 99% hold is not an exit algorithm |
| TTD_SHORT | +398.199 | -29.371 | 90.46% | gray: loses control; TIM high |
| UEC_SHORT | +41.546 | -0.855 | 17.68% | gray: loses control; TIM low |
| UUUU_SHORT | +53.935 | +11.896 | 19.10% | gray: discovery loses B&H; TIM low |
| VLO_LONG | +310.395 | +7.992 | 75.83% | gray: discovery TIM 46.43% |

The most useful near-misses are not universal winners:

- VLO validates in-band and above both comparators, but its frozen discovery
  exposure is only 46.43%.
- ARM validates just outside the band at 81.13% and above both comparators, but
  the selected recipe lost 56.87pp to E02 in discovery.
- INTC and DINO beat both comparators across discovery and validation, but
  exposure is materially too high or unstable.
- SNDK's large return comes with 99% exposure; it is effectively a leveraged
  hold and is deliberately rejected.
- MU's discovery winner is the previously preserved setting
  `arm=4h, confirm=15m, rebound=4 ATR, lookback=6, wait=20h, gate=0.25%`.
  Validation gains +1,127.00pp over B&H but loses 44.324pp to the identical-entry
  E02 control, so the exact grid must not be rerun unchanged.

## Vectorization and parity

`tools/vec_same_entry_structural_scan.c` executes the accounting, capacity,
reclaim and structural state transitions while
`tools/vec_same_entry_exit_adapter.py` supplies causal completed-HTF arrays and
the frozen entry schedule.

- Full compiled grid time: 1.187–2.136 seconds/key; median 1.582 seconds.
- Total compiled grid time across 20 keys: 32.110 seconds.
- Estimated speedup from the winner's Python-oracle timing: 69.4×–334.1×;
  median 144.6×.
- MU cohort artifact: 1.825 seconds for 768 settings, estimated 213.7× speedup.
- All 20 selected winners: `compiled_python_parity=PASS`, zero differences.
- Deterministic unit parity covers an actual structural exit, next-open fill
  and persistent reclaim; the focused suite passes.

Only the frozen discovery winner is replayed through Python. A parity failure
removes it from survivor eligibility. Exact V8 replay remains mandatory, but
is intentionally skipped here because the strict survivor queue is empty.

## Durable artifacts and rollback

- S1 cohort:
  `data/reports/vec_research/structural_wt_top10_long_bottom10_short_20260726T184102Z`
- S1 path-fleet result:
  `data/reports/path_fleet/job_37_EXIT_STRUCTURAL_WT_LOWER_TOP/summary.json`
- Queue state: `SCREENED`, attempts 1, 20 gray rows, 0 errors, exact queue 0.
- Code:
  `tools/vec_same_entry_structural_scan.c`,
  `tools/vec_same_entry_exit_adapter.py`,
  `vec_paths/structural_wt_retest_exit.py`,
  `tools/path_fleet_structural_ingest.py`.

Rollback is limited to those structural scanner/adapter/state/ingest changes.
The frozen ladder controls, NPZ files, live configuration and switch matrix
were not modified by this campaign.
