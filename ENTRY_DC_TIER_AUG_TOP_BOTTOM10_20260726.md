# DC-tier augment causal campaign — 2026-07-26

## Wiring verdict

`DC_TIER_AUG_ENABLED` is connected with default `True`, preserving the prior behavior of the underlying tier block inside `evaluate_augment` after the profit/cooldown gates. `False` disables only that tier block. The campaign numbers remain research evidence, not live promotion.

## Cohorts

| cohort | rows | candidate | B&H | control | signals/requests/fills | survivors |
|---|---:|---:|---:|---:|---:|---:|
| TOP_10_LONG | 10 | 16106.39% | 2379.11% | 15248.93% | 34907/134/134 | 0 |
| BOTTOM_10_SHORT | 10 | -1934.61% | -508.15% | -1928.76% | 42265/290/290 | 0 |

Zero keys beat the identical control in every fold and zero kept every validation fold inside 70–80% TIM. Exact replay was therefore not run.

The source setting is gain 3%, 0.10% buffer, 1/2/3/5x targets, 75% fill gate, and tier-4 maturity guard off. All other values are labeled research extensions. The most frequent selected fold setting was the aggressive 1/2/4/8x profile with gain 1%, zero buffer, 90% fill gate, and no maturity guard; frequency is not promotion evidence.

SNDK generated qualifying tier states but zero requests/fills because its frozen ladder position already exceeded the selected tier targets. That inert selected row remains discard evidence, not a performance claim.

## Per key

| key | candidate | B&H | control | TIM folds | signals/requests/fills | selected fold settings | status |
|---|---:|---:|---:|---|---:|---|---|
| AMD_LONG | 1798.70% | 204.78% | 1683.80% | 74.5/94.1/97.1% | 3433/11/11 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR | DISCARD_GRAY |
| ARM_LONG | 1116.63% | 123.02% | 1150.82% | 91.8/56.5/87.5% | 3204/2/2 | g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=5%,buf=0.200%,tiers=1/2/4/8,fill=75%,mat=0.5ATR &#124; g=5%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| DINO_LONG | 785.39% | 118.22% | 531.31% | 81.5/74.9/80.0% | 3017/17/17 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=0.7ATR &#124; g=3%,buf=0.200%,tiers=1/2/4/8,fill=50%,mat=0.7ATR | DISCARD_GRAY |
| INTC_LONG | 1411.21% | 214.03% | 1466.30% | 77.9/83.5/89.3% | 3727/12/12 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=3%,buf=0.000%,tiers=1/2/3/5,fill=90%,mat=0.5ATR &#124; g=1%,buf=0.000%,tiers=1/1.5/2.5/4,fill=50%,mat=0.5ATR | DISCARD_GRAY |
| MPC_LONG | 361.98% | 103.59% | 163.19% | 81.6/75.8/81.8% | 5293/29/29 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=1%,buf=0.200%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.200%,tiers=1/2/4/8,fill=75%,mat=off | DISCARD_GRAY |
| MRVL_LONG | 1308.05% | 104.11% | 1283.09% | 93.1/82.9/73.7% | 3104/1/1 | g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=5%,buf=0.100%,tiers=1/1.5/2.5/4,fill=90%,mat=0.7ATR &#124; g=5%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| MU_LONG | 2739.89% | 381.95% | 2503.08% | 79.3/90.8/86.2% | 3374/14/14 | g=1%,buf=0.100%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=5%,buf=0.200%,tiers=1/2/4/8,fill=75%,mat=0.5ATR | DISCARD_GRAY |
| PBF_LONG | 736.35% | 127.67% | 642.13% | 64.2/67.6/87.4% | 4380/31/31 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR | DISCARD_GRAY |
| SNDK_LONG | 5414.04% | 888.47% | 5414.04% | 96.6/97.4% | 2019/0/0 | g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR | DISCARD_GRAY |
| VLO_LONG | 434.13% | 113.27% | 411.17% | 82.9/67.7/88.9% | 3356/17/17 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=0.5ATR &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR | DISCARD_GRAY |
| ACN_SHORT | 521.13% | 69.84% | 407.15% | 54.1/67.4/88.4% | 3916/15/15 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=0.7ATR | DISCARD_GRAY |
| ALB_SHORT | -643.43% | -82.31% | -818.37% | 80.9/87.9/74.2% | 4628/30/30 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=3%,buf=0.100%,tiers=1/1.5/2.5/4,fill=75%,mat=0.5ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| ASTS_SHORT | -891.24% | -153.31% | -589.06% | 68.3/67.1/69.6% | 5600/49/49 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=50%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| EGO_SHORT | -706.94% | -85.44% | -223.69% | 66.8/43.2/72.1% | 3060/45/45 | g=1%,buf=0.100%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| HL_SHORT | -923.24% | -219.36% | -734.70% | 77.1/32.0/81.0% | 3532/30/30 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.100%,tiers=1/2/3/5,fill=75%,mat=0.7ATR &#124; g=1%,buf=0.100%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| LAC_SHORT | 28.61% | -11.54% | -304.93% | 92.1/77.1/62.3% | 5030/11/11 | g=1%,buf=0.200%,tiers=1/2/4/8,fill=90%,mat=0.7ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=0.7ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=off | DISCARD_GRAY |
| LDOS_SHORT | 211.19% | 13.86% | -30.35% | 61.4/55.4/84.3% | 3918/35/35 | g=1%,buf=0.100%,tiers=1/2/4/8,fill=90%,mat=0.7ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| TTD_SHORT | 1129.85% | 141.54% | 747.74% | 80.5/90.7/86.1% | 4747/13/13 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=5%,buf=0.100%,tiers=1/2/4/8,fill=75%,mat=off &#124; g=5%,buf=0.200%,tiers=1/2/4/8,fill=50%,mat=off | DISCARD_GRAY |
| UEC_SHORT | -409.20% | -48.38% | -162.77% | 86.6/53.9/76.6% | 4157/27/27 | g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=0.7ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
| UUUU_SHORT | -251.33% | -133.05% | -219.78% | 80.6/60.2/74.4% | 3677/35/35 | g=5%,buf=0.100%,tiers=1/1.5/2.5/4,fill=50%,mat=0.7ATR &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off &#124; g=1%,buf=0.000%,tiers=1/2/4/8,fill=90%,mat=off | DISCARD_GRAY |
