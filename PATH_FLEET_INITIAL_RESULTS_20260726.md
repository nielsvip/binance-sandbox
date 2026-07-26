# Path Fleet Initial Control Results

The first real S1 fleet job completed on 2026-07-26. It froze the point-in-time
Tradier LONG universe, selected its top ten recent performers, and ran the
causal nested vector band-ladder with the E02 4h Donchian N=30 exit and
mandatory lower/resting reclaim.

These rows establish the control that later entry and exit paths must beat.
They are untouched-OOS vector evidence with zero future-HTF observations, but
they are not exact-engine promotion proof. Candidate exits must use the
identical frozen entry schedule and beat both B&H and this control.

| symbol | strategy | B&H | multiple | weighted TIM |
|---|---:|---:|---:|---:|
| SNDK_LONG | 5414.04% | 888.47% | 6.09x | 97.0% |
| MRVL_LONG | 1283.09% | 104.11% | 12.32x | 82.6% |
| ARM_LONG | 1150.82% | 123.02% | 9.35x | 77.0% |
| MU_LONG | 2503.08% | 381.95% | 6.55x | 65.3% |
| INTC_LONG | 1466.30% | 214.03% | 6.85x | 82.3% |
| AMD_LONG | 1683.80% | 204.78% | 8.22x | 72.4% |
| PBF_LONG | 642.13% | 127.67% | 5.03x | 44.0% |
| MPC_LONG | 163.19% | 103.59% | 1.58x | 39.9% |
| DINO_LONG | 531.31% | 118.22% | 4.49x | 52.0% |
| VLO_LONG | 411.17% | 113.27% | 3.63x | 57.6% |

The target exposure band is not universal. ARM and AMD are near the desired
70–80%; MRVL and INTC are slightly high; SNDK is much too high; PBF, MPC, DINO,
and VLO are below the top-performer target. Those are ladder-entry tuning
problems, not reasons to weaken the benchmark for exit research.

The causal SHORT mirror is now implemented and job 33 has been backfilled
without replacing these LONG rows. ACN_SHORT returned +407.15% versus +69.84%
side-aware B&H (5.83x), and TTD_SHORT returned +747.74% versus +141.54%
(5.28x). The other eight failed; LAC, HL, and ALB crossed 100% account
drawdown/insolvency. A negative short-and-hold denominator is reported as N/A,
never as a misleading positive multiple. Full semantics, cohort rows, and
exact-replay evidence are in
`SHORT_LADDER_MIRROR_RESULTS_20260726.md`.

ACN and TTD subsequently passed exact current-engine replay of their latest
untouched folds: ACN +388.07% versus +44.50% B&H (37/37 actions), TTD +280.88%
versus +54.33% (32/32 actions). Both remained fail-closed with promotion and
matrix writes disabled.
