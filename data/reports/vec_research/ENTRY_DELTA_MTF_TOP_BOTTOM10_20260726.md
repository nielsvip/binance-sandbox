# DELTA_MTF causal entry campaign — 2026-07-26

## Verdict

The actual entry inputs were mapped before the sweep: weighted favorable WT velocity/acceleration counts on 5m/15m/1h/4h/D, the 1h delta red-zone, and optional side-mirrored 4h structure. All HTFs are observed only after their completed timestamp. The actual path is direct-only.

`DELTA_EXIT_DECAY_RATIO` is an exit knob, not a live entry knob. The requested 0.25/0.50/0.75 values were therefore tested only as a clearly labeled research directional-retention ratio; no live setting was changed.

**Zero candidates passed B&H, the identical control, every validation fold, and 70–80% TIM in every fold.** All 20 rows remain gray.

VLO_LONG initially appeared to survive when exposure was aggregated (74.34%), but its fold TIM was 33.68%/89.00%/96.55%. Exact V8 schedule parity passed and exposed the last-fold 96.55% value; the gate was fixed to require every fold, VLO was rerun, and it is not a survivor.

## Cohort aggregates

| cohort | rows | candidate sum | B&H sum | control sum | fills | folds >B&H | folds >control | all-fold TIM | survivors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TOP_10_LONG | 10 | 14625.92% | 2379.11% | 15248.93% | 365 | 7 | 2 | 0 | 0 |
| BOTTOM_10_SHORT | 10 | -2346.08% | -508.15% | -1928.76% | 667 | 2 | 1 | 0 | 0 |

## Per-key gray evidence

| key | candidate | B&H | control | TIM folds | fills | folds >B&H/control | fold settings |
|---|---:|---:|---:|---|---:|---|---|
| AMD_LONG | 1654.06% | 204.78% | 1683.80% | 35.7/87.1/90.0% | 28 | yes/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False |
| ARM_LONG | 1427.26% | 123.02% | 1150.82% | 83.7/52.5/90.6% | 35 | no/yes | direct; favorable_TFs>=4; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=True &#124; direct; favorable_TFs>=2; retention=0.75; struct4h=False |
| DINO_LONG | 624.76% | 118.22% | 531.31% | 38.0/70.3/71.0% | 50 | yes/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=True &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=True |
| INTC_LONG | 919.33% | 214.03% | 1466.30% | 80.1/68.7/78.0% | 42 | yes/no | direct; favorable_TFs>=2; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False |
| MPC_LONG | 230.45% | 103.59% | 163.19% | 40.4/77.4/39.6% | 47 | no/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| MRVL_LONG | 1500.27% | 104.11% | 1283.09% | 84.7/65.4/73.2% | 33 | no/no | direct; favorable_TFs>=4; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.75; struct4h=True &#124; direct; favorable_TFs>=4; retention=0.25; struct4h=True |
| MU_LONG | 2345.15% | 381.95% | 2503.08% | 34.3/87.3/65.3% | 35 | yes/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.25; struct4h=False |
| PBF_LONG | 914.46% | 127.67% | 642.13% | 21.1/21.3/88.7% | 43 | yes/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=2; retention=0.75; struct4h=False |
| SNDK_LONG | 4393.12% | 888.47% | 5414.04% | 87.8/87.1% | 12 | yes/no | direct; favorable_TFs>=4; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.75; struct4h=False |
| VLO_LONG | 617.04% | 113.27% | 411.17% | 33.7/89.0/96.5% | 40 | yes/yes | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=2; retention=0.75; struct4h=False |
| ACN_SHORT | 417.74% | 69.84% | 407.15% | 25.4/85.9/85.7% | 60 | yes/no | direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| ALB_SHORT | -588.52% | -82.31% | -818.37% | 61.4/65.4/29.0% | 62 | no/no | direct; favorable_TFs>=4; retention=0.75; struct4h=True &#124; direct; favorable_TFs>=4; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| ASTS_SHORT | -679.58% | -153.31% | -589.06% | 74.3/73.8/20.8% | 74 | no/no | direct; favorable_TFs>=4; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| EGO_SHORT | -189.66% | -85.44% | -223.69% | 25.9/26.8/22.7% | 57 | no/no | direct; favorable_TFs>=2; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=False |
| HL_SHORT | -1547.66% | -219.36% | -734.70% | 85.6/70.5/20.3% | 89 | no/no | direct; favorable_TFs>=3; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| LAC_SHORT | -124.82% | -11.54% | -304.93% | 89.4/77.6/20.6% | 73 | no/yes | direct; favorable_TFs>=4; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=4; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| LDOS_SHORT | -106.85% | 13.86% | -30.35% | 47.4/24.6/17.2% | 76 | no/no | direct; favorable_TFs>=2; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False |
| TTD_SHORT | 730.52% | 141.54% | 747.74% | 19.0/85.5/69.7% | 61 | yes/no | direct; favorable_TFs>=3; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=2; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=True |
| UEC_SHORT | -6.37% | -48.38% | -162.77% | 73.0/28.4/22.7% | 54 | no/no | direct; favorable_TFs>=4; retention=0.75; struct4h=False &#124; direct; favorable_TFs>=2; retention=0.5; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.25; struct4h=False |
| UUUU_SHORT | -250.88% | -133.05% | -219.78% | 80.6/28.3/20.6% | 61 | no/no | direct; favorable_TFs>=4; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=1; retention=0.25; struct4h=False &#124; direct; favorable_TFs>=3; retention=0.5; struct4h=False |

## Exact diagnostic

- `data/reports/vec_research/v8_exact_ladder_replay_20260726T193228Z_VLO_LONG`: PASS; signal parity True; return 485.27%; TIM 96.55%; actions 11; future HTF 0; promotion remains false.
