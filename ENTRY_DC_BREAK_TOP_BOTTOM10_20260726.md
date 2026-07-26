# Donchian-break entry wiring and reconstruction — 2026-07-26

## Verdict

`DC_BREAK_ENTRY_ENABLED` is a stale, disconnected matrix switch: it is not declared in `config_tradier.py` and no active router reads it. The old swing branch instead reads `DC_BREAK_ENTRY_DISABLED` with fail-closed `True`; the launched daytrade loop is a separate strategy controlled by `DC_DAYTRADE_ENABLED` / `TRADIER_DC_DAYTRADE_ENABLED`.

The real named path therefore has zero signals, requests, fills, and TIM. Those rows are retained red. A 192-setting causal reconstruction was also run over the frozen top-10 LONG and bottom-10 SHORT cohorts. It uses the same ladder sizing, $16k capacity, E02 N30 exit, costs, next-RTH fills, and mandatory reclaim as the control. **No row passed both benchmarks, every validation fold, and 70–80% TIM in every fold.** All reconstructed rows remain gray and no exact replay or live setting change was made.

## Cohort aggregates

| cohort | rows | candidate sum | B&H sum | control sum | all-fold >B&H | all-fold >control | all-fold TIM | survivors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TOP_10_LONG | 10 | 15105.30% | 2379.11% | 15248.93% | 8 | 5 | 0 | 0 |
| BOTTOM_10_SHORT | 10 | -2009.44% | -508.15% | -1928.76% | 1 | 1 | 0 | 0 |

## Per-key retained evidence

| key | candidate | B&H | control | TIM | signals / requests / fills | folds >B&H/control/TIM | selected fold settings |
|---|---:|---:|---:|---:|---:|---|---|
| AMD_LONG | 1492.83% | 204.78% | 1683.80% | 75.44% | 693 / 539 / 33 | yes/no/no | union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.10%; expand1h=False; confirm=directional-stoch &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=not-exhausted |
| ARM_LONG | 1036.13% | 123.02% | 1150.82% | 76.64% | 504 / 206 / 31 | no/no/no | direct; tf=1h; buffer=0.20%; expand1h=False; confirm=directional-stoch &#124; direct; tf=5m; buffer=0.20%; expand1h=False; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=not-exhausted |
| DINO_LONG | 753.92% | 118.22% | 531.31% | 69.96% | 769 / 711 / 73 | yes/yes/no | union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=True; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=none |
| INTC_LONG | 1050.80% | 214.03% | 1466.30% | 84.69% | 874 / 296 / 51 | no/no/no | direct; tf=15m; buffer=0.00%; expand1h=False; confirm=none &#124; direct; tf=5m; buffer=0.00%; expand1h=True; confirm=directional-stoch &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=directional-stoch |
| MPC_LONG | 295.15% | 103.59% | 163.19% | 55.00% | 972 / 658 / 37 | yes/yes/no | direct; tf=5m; buffer=0.00%; expand1h=False; confirm=directional-stoch &#124; direct; tf=5m; buffer=0.10%; expand1h=False; confirm=directional-stoch &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| MRVL_LONG | 1512.09% | 104.11% | 1283.09% | 74.68% | 432 / 165 / 27 | yes/no/no | direct; tf=15m; buffer=0.10%; expand1h=True; confirm=directional-stoch &#124; direct; tf=15m; buffer=0.10%; expand1h=True; confirm=not-exhausted &#124; direct; tf=15m; buffer=0.10%; expand1h=True; confirm=not-exhausted |
| MU_LONG | 2577.11% | 381.95% | 2503.08% | 68.29% | 984 / 667 / 35 | yes/yes/no | union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.05%; expand1h=True; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.05%; expand1h=True; confirm=not-exhausted |
| PBF_LONG | 842.54% | 127.67% | 642.13% | 48.31% | 1257 / 893 / 71 | yes/yes/no | union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.05%; expand1h=True; confirm=not-exhausted |
| SNDK_LONG | 4983.17% | 888.47% | 5414.04% | 80.84% | 607 / 179 / 8 | yes/no/no | direct; tf=5m; buffer=0.20%; expand1h=True; confirm=none &#124; direct; tf=4h; buffer=0.20%; expand1h=True; confirm=directional-stoch |
| VLO_LONG | 561.56% | 113.27% | 411.17% | 69.78% | 1557 / 535 / 33 | yes/yes/no | direct; tf=5m; buffer=0.00%; expand1h=False; confirm=none &#124; direct; tf=5m; buffer=0.20%; expand1h=False; confirm=none &#124; direct; tf=15m; buffer=0.10%; expand1h=False; confirm=not-exhausted |
| ACN_SHORT | 424.51% | 69.84% | 407.15% | 66.58% | 1993 / 587 / 156 | yes/no/no | union-with-green; tf=5m; buffer=0.00%; expand1h=False; confirm=none &#124; direct; tf=1h; buffer=0.05%; expand1h=False; confirm=none &#124; direct; tf=15m; buffer=0.10%; expand1h=True; confirm=not-exhausted |
| ALB_SHORT | -467.79% | -82.31% | -818.37% | 55.37% | 662 / 507 / 135 | no/no/no | direct; tf=5m; buffer=0.10%; expand1h=True; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=directional-stoch &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| ASTS_SHORT | -593.44% | -153.31% | -589.06% | 56.33% | 1396 / 822 / 203 | no/no/no | direct; tf=5m; buffer=0.10%; expand1h=True; confirm=not-exhausted &#124; direct; tf=5m; buffer=0.10%; expand1h=True; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.00%; expand1h=False; confirm=none |
| EGO_SHORT | -179.46% | -85.44% | -223.69% | 29.03% | 2282 / 1105 / 245 | no/yes/no | direct; tf=5m; buffer=0.00%; expand1h=False; confirm=none &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=none &#124; direct; tf=5m; buffer=0.00%; expand1h=False; confirm=none |
| HL_SHORT | -952.31% | -219.36% | -734.70% | 43.76% | 675 / 447 / 97 | no/no/no | direct; tf=15m; buffer=0.10%; expand1h=True; confirm=not-exhausted &#124; direct; tf=15m; buffer=0.05%; expand1h=True; confirm=directional-stoch &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| LAC_SHORT | -316.58% | -11.54% | -304.93% | 60.31% | 1317 / 646 / 149 | no/no/no | direct; tf=5m; buffer=0.10%; expand1h=True; confirm=directional-stoch &#124; union-with-green; tf=4h; buffer=0.20%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| LDOS_SHORT | -75.35% | 13.86% | -30.35% | 31.20% | 690 / 690 / 124 | no/no/no | direct; tf=5m; buffer=0.05%; expand1h=False; confirm=directional-stoch &#124; union-with-green; tf=5m; buffer=0.10%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| TTD_SHORT | 822.60% | 141.54% | 747.74% | 63.02% | 1000 / 492 / 181 | no/no/no | union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; direct; tf=1h; buffer=0.20%; expand1h=True; confirm=none &#124; direct; tf=5m; buffer=0.20%; expand1h=False; confirm=directional-stoch |
| UEC_SHORT | -276.20% | -48.38% | -162.77% | 44.08% | 1159 / 869 / 147 | no/no/no | direct; tf=5m; buffer=0.10%; expand1h=True; confirm=directional-stoch &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |
| UUUU_SHORT | -395.42% | -133.05% | -219.78% | 45.43% | 1158 / 873 / 179 | no/no/no | direct; tf=5m; buffer=0.20%; expand1h=True; confirm=none &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted &#124; union-with-green; tf=5m; buffer=0.05%; expand1h=False; confirm=not-exhausted |

## Interpretation

- The reconstruction is causal and wired: all 20 rows produced requests and fills, with zero future-HTF, capacity, or reclaim violations.
- MU_LONG beat B&H and the control in every fold, but its aggregate TIM was 68.29% and the fold exposure constraint failed. This is a useful range, not a survivor.
- MRVL_LONG met aggregate TIM and exceeded both aggregate benchmarks, but failed the control in at least one fold.
- SHORT behavior was poor and unstable overall. Positive-looking ratios against negative returns were not treated as wins; strict comparisons use signed side-specific returns fold by fold.
- Reconnecting the stale switch is a separate live-code decision. These research settings cannot be promoted under the current contract.

