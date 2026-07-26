# Trend-resume augment reconstruction — 2026-07-26

## Verdict

`AUGMENT_TREND_RESUME_ENABLED` is not wired in the active stock manager or config. The inventory row was stale. This is a research-only reconstruction of the last real July 21 code, not a live-path parity claim.

The reconstructed path adds only to an already-open profitable position: LONG requires close above the 5m Donchian basis, K>D, and RSI below the declared boundary; SHORT is mirrored. The sweep varied minimum gain, RSI boundary, and additive START_POSITION_SIZE fraction while preserving the frozen ladder, E02 4h N30 exit, reclaim, $16k cap, and costs.

**Zero of 20 keys passed B&H, the identical control, and 70–80% weighted TIM in every validation fold. No exact replay ran and no live setting changed.**

TTD_SHORT beat B&H and control in every fold but aggregated at 89.79% TIM and failed the per-fold exposure gate. It is retained gray.

## Cohort aggregates

| cohort | rows | candidate sum | B&H sum | control sum | augment fills | all folds >B&H | >control | all-fold TIM | survivors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TOP_10_LONG | 10 | 16372.84% | 2379.11% | 15248.93% | 1412 | 5 | 0 | 0 | 0 |
| BOTTOM_10_SHORT | 10 | -2493.71% | -508.15% | -1928.76% | 6050 | 2 | 1 | 1 | 0 |

## Per-key gray evidence

| key | candidate | B&H | control | TIM folds | augments | folds >B&H/control | selected settings |
|---|---:|---:|---:|---|---:|---|---|
| AMD_LONG | 1865.44% | 204.78% | 1683.80% | 85.5/94.1/97.1% | 87 | yes/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| ARM_LONG | 1149.22% | 123.02% | 1150.82% | 91.8/58.7/87.5% | 47 | no/no | gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=0.5%, RSI=60, add=0.5x |
| DINO_LONG | 732.26% | 118.22% | 531.31% | 87.6/79.1/81.3% | 224 | yes/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| INTC_LONG | 1450.49% | 214.03% | 1466.30% | 81.8/84.6/89.5% | 137 | yes/no | gain>=1%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=60, add=0.5x &#124; gain>=3%, RSI=60, add=0.25x |
| MPC_LONG | 440.91% | 103.59% | 163.19% | 86.9/80.9/85.6% | 230 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x &#124; gain>=1%, RSI=70, add=0.5x |
| MRVL_LONG | 1289.89% | 104.11% | 1283.09% | 93.1/82.8/74.0% | 40 | no/no | gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| MU_LONG | 2808.88% | 381.95% | 2503.08% | 87.4/90.8/85.0% | 151 | yes/no | gain>=1%, RSI=80, add=0.5x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| PBF_LONG | 733.62% | 127.67% | 642.13% | 73.4/75.6/88.3% | 270 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x &#124; gain>=3%, RSI=60, add=0.25x |
| SNDK_LONG | 5424.71% | 888.47% | 5414.04% | 96.7/97.4% | 5 | yes/no | gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| VLO_LONG | 477.43% | 113.27% | 411.17% | 87.0/73.3/93.9% | 221 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=70, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x |
| ACN_SHORT | 544.08% | 69.84% | 407.15% | 54.3/72.6/93.6% | 531 | yes/no | gain>=0.5%, RSI=70, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x &#124; gain>=2%, RSI=70, add=0.25x |
| ALB_SHORT | -641.32% | -82.31% | -818.37% | 78.9/87.9/79.2% | 494 | no/no | gain>=3%, RSI=100, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=0.5%, RSI=60, add=0.5x |
| ASTS_SHORT | -1142.62% | -153.31% | -589.06% | 76.8/72.2/79.0% | 646 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=70, add=0.5x &#124; gain>=0.5%, RSI=60, add=0.5x |
| EGO_SHORT | -947.11% | -85.44% | -223.69% | 77.1/66.0/83.4% | 661 | no/no | gain>=0.5%, RSI=80, add=0.5x &#124; gain>=0.5%, RSI=80, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x |
| HL_SHORT | -1000.54% | -219.36% | -734.70% | 84.5/31.0/81.5% | 670 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=2%, RSI=60, add=0.25x &#124; gain>=0.5%, RSI=100, add=0.25x |
| LAC_SHORT | -4.69% | -11.54% | -304.93% | 92.7/81.3/66.0% | 255 | no/no | gain>=3%, RSI=60, add=0.25x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=1%, RSI=60, add=0.5x |
| LDOS_SHORT | 259.64% | 13.86% | -30.35% | 78.4/70.0/93.4% | 909 | no/no | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x &#124; gain>=0.5%, RSI=100, add=0.5x |
| TTD_SHORT | 1201.09% | 141.54% | 747.74% | 89.4/90.8/89.3% | 803 | yes/yes | gain>=0.5%, RSI=100, add=0.5x &#124; gain>=3%, RSI=60, add=0.25x &#124; gain>=1%, RSI=100, add=0.5x |
| UEC_SHORT | -440.19% | -48.38% | -162.77% | 86.6/53.9/78.7% | 389 | no/no | gain>=3%, RSI=60, add=0.25x &#124; gain>=1%, RSI=80, add=0.25x &#124; gain>=0.5%, RSI=70, add=0.5x |
| UUUU_SHORT | -322.04% | -133.05% | -219.78% | 84.1/73.6/76.4% | 692 | no/no | gain>=1%, RSI=60, add=0.25x &#124; gain>=0.5%, RSI=100, add=0.25x &#124; gain>=1%, RSI=100, add=0.5x |
