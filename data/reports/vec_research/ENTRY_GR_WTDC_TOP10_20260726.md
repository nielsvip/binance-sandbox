# Golden Rule and repaired WT_DC entry overlays — 2026-07-26

## Verdict

The two entry families now use completed-HTF causal adapters and the exact same frozen ladder sizing/capacity, E02 4h N=30 exit, next-RTH fills, and mandatory resting reclaim as the control. All settings below B&H, below the stronger control, non-robust across folds, or outside the 70–80% top-cohort exposure target are retained as **DISCARD_GRAY**. No candidate passed every gate, so exact V8 replay was correctly not run.

SHORT was not evaluated in this bounded overlay run. Mirrored frozen SHORT
controls now exist and are the next side-isolated extension; no LONG return was
inverted or pooled.

## Latest exposure-constrained results

| family | symbol | candidate | B&H | control | vs B&H | vs control | TIM | all folds > B&H/control | status |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| GOLDEN_RULE | AMD_LONG | 1553.85% | 204.78% | 1683.80% | 7.588x | 0.923x | 69.81% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | ARM_LONG | 1129.03% | 123.02% | 1150.82% | 9.178x | 0.981x | 72.97% | no/no | DISCARD_GRAY |
| GOLDEN_RULE | DINO_LONG | 542.31% | 118.22% | 531.31% | 4.587x | 1.021x | 64.45% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | INTC_LONG | 1226.15% | 214.03% | 1466.30% | 5.729x | 0.836x | 80.49% | no/no | DISCARD_GRAY |
| GOLDEN_RULE | MPC_LONG | 251.41% | 103.59% | 163.19% | 2.427x | 1.541x | 54.26% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | MRVL_LONG | 1015.20% | 104.11% | 1283.09% | 9.752x | 0.791x | 75.42% | no/no | DISCARD_GRAY |
| GOLDEN_RULE | MU_LONG | 2473.80% | 381.95% | 2503.08% | 6.477x | 0.988x | 67.43% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | PBF_LONG | 707.82% | 127.67% | 642.13% | 5.544x | 1.102x | 47.57% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | SNDK_LONG | 4816.57% | 888.47% | 5414.04% | 5.421x | 0.890x | 85.87% | yes/no | DISCARD_GRAY |
| GOLDEN_RULE | VLO_LONG | 357.11% | 113.27% | 411.17% | 3.153x | 0.869x | 58.71% | yes/no | DISCARD_GRAY |
| WT_DC | AMD_LONG | 1388.34% | 204.78% | 1683.80% | 6.780x | 0.825x | 71.92% | yes/no | DISCARD_GRAY |
| WT_DC | ARM_LONG | 1243.42% | 123.02% | 1150.82% | 10.107x | 1.080x | 79.48% | no/no | DISCARD_GRAY |
| WT_DC | DINO_LONG | 778.97% | 118.22% | 531.31% | 6.589x | 1.466x | 72.07% | yes/no | DISCARD_GRAY |
| WT_DC | INTC_LONG | 1254.51% | 214.03% | 1466.30% | 5.861x | 0.856x | 81.24% | yes/no | DISCARD_GRAY |
| WT_DC | MPC_LONG | 287.13% | 103.59% | 163.19% | 2.772x | 1.760x | 55.97% | yes/yes | DISCARD_GRAY |
| WT_DC | MRVL_LONG | 1537.37% | 104.11% | 1283.09% | 14.767x | 1.198x | 65.72% | no/no | DISCARD_GRAY |
| WT_DC | MU_LONG | 2345.59% | 381.95% | 2503.08% | 6.141x | 0.937x | 63.25% | yes/no | DISCARD_GRAY |
| WT_DC | PBF_LONG | 953.88% | 127.67% | 642.13% | 7.471x | 1.485x | 49.58% | yes/no | DISCARD_GRAY |
| WT_DC | SNDK_LONG | 4272.77% | 888.47% | 5414.04% | 4.809x | 0.789x | 77.52% | yes/no | DISCARD_GRAY |
| WT_DC | VLO_LONG | 434.01% | 113.27% | 411.17% | 3.832x | 1.056x | 63.79% | yes/no | DISCARD_GRAY |

## Frozen settings selected by fold

- `AMD_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | filter; min_ind=5; min_tfs=1; weights=equal; frac=0.65 | filter; min_ind=5; min_tfs=1; weights=htf; frac=0.65
- `ARM_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=3; weights=htf; frac=0.65 | filter; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=6; min_tfs=2; weights=equal; frac=0.65
- `DINO_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=5; min_tfs=1; weights=equal; frac=0.65 | direct; min_ind=4; min_tfs=2; weights=daily; frac=0.5
- `INTC_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=4; min_tfs=3; weights=daily; frac=0.65 | direct; min_ind=6; min_tfs=2; weights=htf; frac=0.35 | filter; min_ind=5; min_tfs=1; weights=equal; frac=0.5
- `MPC_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=5; min_tfs=1; weights=equal; frac=0.65 | direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5
- `MRVL_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=4; min_tfs=2; weights=htf; frac=0.65 | filter; min_ind=5; min_tfs=3; weights=htf; frac=0.65 | direct; min_ind=5; min_tfs=3; weights=htf; frac=0.65
- `MU_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | filter; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=5; min_tfs=1; weights=htf; frac=0.5
- `PBF_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=3; min_tfs=3; weights=daily; frac=0.5
- `SNDK_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=2; weights=daily; frac=0.5 | direct; min_ind=6; min_tfs=1; weights=equal; frac=0.8
- `VLO_LONG ENTRY_GOLDEN_RULE` — direct; min_ind=5; min_tfs=1; weights=equal; frac=0.5 | direct; min_ind=3; min_tfs=1; weights=daily; frac=0.65 | filter; min_ind=4; min_tfs=2; weights=htf; frac=0.8
- `AMD_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | filter; thr=20; HTF=none; align=2; stoch=100 | direct; thr=20; HTF=4h_D; align=0; stoch=60
- `ARM_LONG ENTRY_WT_DC` — filter; thr=45; HTF=none; align=0; stoch=100 | direct; thr=45; HTF=none; align=2; stoch=60 | filter; thr=20; HTF=none; align=0; stoch=100
- `DINO_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=60
- `INTC_LONG ENTRY_WT_DC` — direct; thr=45; HTF=none; align=2; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=100 | filter; thr=45; HTF=none; align=0; stoch=100
- `MPC_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=60 | direct; thr=20; HTF=none; align=0; stoch=60
- `MRVL_LONG ENTRY_WT_DC` — direct; thr=75; HTF=4h; align=0; stoch=60 | filter; thr=45; HTF=none; align=2; stoch=100 | direct; thr=20; HTF=4h_D; align=0; stoch=60
- `MU_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | filter; thr=20; HTF=none; align=0; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=60
- `PBF_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | direct; thr=20; HTF=none; align=0; stoch=60 | direct; thr=45; HTF=none; align=2; stoch=60
- `SNDK_LONG ENTRY_WT_DC` — direct; thr=45; HTF=4h_D; align=2; stoch=100 | filter; thr=20; HTF=4h_D; align=0; stoch=60
- `VLO_LONG ENTRY_WT_DC` — direct; thr=20; HTF=none; align=0; stoch=60 | direct; thr=20; HTF=4h_D; align=0; stoch=60 | direct; thr=20; HTF=4h_D; align=0; stoch=60

## Interpretation

- WT_DC again beats aggregate B&H on most top-cohort names after repairing its numeric cross/router/switch defects. That confirms it is no longer the zero-trade/inverted path seen before the repair.
- Beating B&H is not enough here because the frozen ladder+E02 control is already very strong. Exposure-constrained WT_DC candidates that landed in 70–80% TIM still failed the control or one validation fold.
- Golden Rule also produces several aggregate >B&H rows, but its strongest control improvements remain below the requested exposure band or fail a fold. Those are useful ranges, not promotion candidates.
- Entry-only hold proofs were not used. Every number includes E02 exits and mandatory reclaim with side-isolated stateful accounting.

Machine-readable ranges and every retained gray row are in the companion JSON.
