# Bollinger failed-break recovery entry campaign — 2026-07-26

## Verdict

The 96-setting completed-bar screen finished over the frozen top-10 LONG and bottom-10 SHORT cohorts. It tested 15m/1h/4h recovery within 1/2/4/8 completed TF bars after 0/0.25/0.5/1 ATR band excursions, as a direct entry and union with the frozen green schedule.

**Zero candidates passed B&H, the identical ladder+E02 control, every validation fold, and 70–80% weighted TIM together.** Exact replay was not launched. All 20 rows remain `DISCARD_GRAY`; live config stayed off.

## Cohort aggregates

| cohort | rows | candidate sum | B&H sum | control sum | all-fold >B&H | all-fold >control | TIM pass | survivors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TOP_10_LONG | 10 | 14615.58% | 2379.11% | 15248.93% | 8 | 0 | 2 | 0 |
| BOTTOM_10_SHORT | 10 | -2758.23% | -508.15% | -1928.76% | 2 | 1 | 1 | 0 |

## Per-key gray evidence

| key | candidate | B&H | control | TIM | folds >B&H/control | fold settings |
|---|---:|---:|---:|---:|---|---|
| AMD_LONG | 1590.29% | 204.78% | 1683.80% | 72.32% | yes/no | union-with-green; TF=1h; recover<=8 bars; excursion=0 ATR &#124; direct; TF=15m; recover<=2 bars; excursion=0.5 ATR &#124; direct; TF=4h; recover<=4 bars; excursion=0.5 ATR |
| ARM_LONG | 874.77% | 123.02% | 1150.82% | 75.17% | no/no | direct; TF=15m; recover<=2 bars; excursion=0.5 ATR &#124; direct; TF=1h; recover<=1 bars; excursion=0.25 ATR &#124; direct; TF=1h; recover<=1 bars; excursion=0.25 ATR |
| DINO_LONG | 628.03% | 118.22% | 531.31% | 66.42% | yes/no | union-with-green; TF=15m; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=4h; recover<=8 bars; excursion=1 ATR &#124; direct; TF=1h; recover<=4 bars; excursion=0.5 ATR |
| INTC_LONG | 1657.82% | 214.03% | 1466.30% | 83.13% | yes/no | union-with-green; TF=1h; recover<=4 bars; excursion=0.5 ATR &#124; union-with-green; TF=1h; recover<=1 bars; excursion=0.5 ATR &#124; direct; TF=1h; recover<=4 bars; excursion=0.5 ATR |
| MPC_LONG | 201.41% | 103.59% | 163.19% | 54.73% | no/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=4h; recover<=2 bars; excursion=0.5 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |
| MRVL_LONG | 1430.34% | 104.11% | 1283.09% | 58.72% | yes/no | direct; TF=1h; recover<=1 bars; excursion=0.5 ATR &#124; direct; TF=4h; recover<=8 bars; excursion=1 ATR &#124; direct; TF=15m; recover<=1 bars; excursion=0.5 ATR |
| MU_LONG | 2586.52% | 381.95% | 2503.08% | 65.20% | yes/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=4h; recover<=2 bars; excursion=1 ATR &#124; union-with-green; TF=1h; recover<=8 bars; excursion=1 ATR |
| PBF_LONG | 957.76% | 127.67% | 642.13% | 50.56% | yes/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; direct; TF=1h; recover<=4 bars; excursion=0.5 ATR |
| SNDK_LONG | 4174.24% | 888.47% | 5414.04% | 68.47% | yes/no | direct; TF=15m; recover<=1 bars; excursion=1 ATR &#124; direct; TF=4h; recover<=2 bars; excursion=0 ATR |
| VLO_LONG | 514.39% | 113.27% | 411.17% | 66.21% | yes/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=1 bars; excursion=1 ATR &#124; union-with-green; TF=4h; recover<=1 bars; excursion=1 ATR |
| ACN_SHORT | 440.90% | 69.84% | 407.15% | 73.40% | yes/no | union-with-green; TF=1h; recover<=8 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=8 bars; excursion=1 ATR &#124; direct; TF=1h; recover<=4 bars; excursion=0.5 ATR |
| ALB_SHORT | -715.29% | -82.31% | -818.37% | 57.90% | no/no | direct; TF=15m; recover<=2 bars; excursion=1 ATR &#124; direct; TF=1h; recover<=4 bars; excursion=0.5 ATR &#124; union-with-green; TF=15m; recover<=2 bars; excursion=0 ATR |
| ASTS_SHORT | -503.61% | -153.31% | -589.06% | 48.19% | no/no | direct; TF=15m; recover<=8 bars; excursion=1 ATR &#124; direct; TF=4h; recover<=2 bars; excursion=0.25 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |
| EGO_SHORT | -144.34% | -85.44% | -223.69% | 26.71% | no/yes | direct; TF=15m; recover<=8 bars; excursion=0.25 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |
| HL_SHORT | -1481.60% | -219.36% | -734.70% | 52.29% | no/no | direct; TF=1h; recover<=2 bars; excursion=0.5 ATR &#124; union-with-green; TF=1h; recover<=1 bars; excursion=1 ATR &#124; union-with-green; TF=4h; recover<=2 bars; excursion=0 ATR |
| LAC_SHORT | -440.37% | -11.54% | -304.93% | 59.84% | no/no | direct; TF=15m; recover<=8 bars; excursion=1 ATR &#124; direct; TF=15m; recover<=8 bars; excursion=1 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |
| LDOS_SHORT | -44.34% | 13.86% | -30.35% | 33.07% | no/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=15m; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0.5 ATR |
| TTD_SHORT | 794.26% | 141.54% | 747.74% | 60.18% | yes/no | union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; direct; TF=15m; recover<=2 bars; excursion=1 ATR &#124; direct; TF=1h; recover<=2 bars; excursion=0.5 ATR |
| UEC_SHORT | -384.63% | -48.38% | -162.77% | 44.13% | no/no | direct; TF=1h; recover<=2 bars; excursion=0.25 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |
| UUUU_SHORT | -279.21% | -133.05% | -219.78% | 43.29% | no/no | union-with-green; TF=1h; recover<=1 bars; excursion=1 ATR &#124; direct; TF=15m; recover<=4 bars; excursion=0 ATR &#124; union-with-green; TF=1h; recover<=4 bars; excursion=0 ATR |

## Decision

- Several LONG aggregates exceeded the control, but none combined all-fold robustness with the exposure band. PBF/MPC/DINO/VLO again show that the low-exposure problem belongs to ladder trigger/multiplier tuning.
- ACN_SHORT reached 73.40% TIM and beat B&H in every fold, but did not beat the identical control in every fold. It remains gray.
- The path is sweep-only and default-off. No live switch or symbol file changed.
