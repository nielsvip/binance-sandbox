# Causal top-exit + guaranteed-reclaim phase 3

Date: 2026-07-27

Tier: `VEC_RESEARCH_CAUSAL_PARENT_CLOSE`

Verdict: **0 strict survivors; no exact replay, matrix write, or promotion**

## Question and frozen contract

This campaign asked whether exits that wait for top evidence can improve the
same immutable ladder entry schedule, instead of selling a first `dc_low4`
break at the bottom.  The focused LONG cohort was MU, MRVL, SNDK, DINO and
ARM, with VT as a negative control.

Every candidate used:

- the source artifact's frozen per-fold ladder requests, curve, multipliers
  and target/add semantics;
- completed 1h/4h/D inputs on
  `SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1`;
- the first strictly later availability batch for fills;
- the same costs as the source ladder;
- $2,000 fixed-unit B&H, $16,000 strategy capacity and $10,000 account
  solvency;
- the identical-entry 4h/N30 E02 result as the stronger control;
- persistent zero-buffer reclaim at the greater of LONG exit fill and stored
  peak/top reference, evaluated before discretionary entry requests;
- discovery-fold selection only, followed by one untouched chronological
  final fold.

Promotion required an actual exit, positive alpha over both B&H and E02,
70–80% weighted time in market, positive account equity, no capacity breach,
zero future HTF sources, and zero bars flat after price exceeded the reclaim
level in **every** fold.

## Preregistered vector registry

There were 36 single-path books and six fixed OR combinations, 42 candidates
per key and 252 evaluations total:

- eight confirmed 4h price/RSI divergence -> structural break -> rebound ->
  later rollover books;
- four confirmed 4h lower-high/lower-low -> rebound -> failed-retest books;
- four 4h EMA break -> later lower-top/retest-failure books;
- four completed 4h/D RSI+ATR exhaustion -> completed 1h damage books;
- four prior-only 4h regression excursion -> channel re-entry/adverse-break
  books;
- four prior-only higher-high -> later failed-higher-high books;
- six completed 4h+D WT top-roll books;
- two 4h/D Chandelier books labeled emergency controls, which were forbidden
  from promotion;
- six fixed two-family OR combinations.

The registry contains no first-break `dc_low4` profit exit.  Chandelier is a
comparison control, not a top-harvest claim.

## Results

All six keys completed without data or execution errors.  There were zero
discovery-strict and zero all-fold-strict candidates.  Consequently no exact
spec was emitted.

| key | discovery-frozen path | fold returns, % | E02 returns, % | weighted TIM, % | exits | decisive failure |
|---|---|---:|---:|---:|---:|---|
| MU_LONG | break/retest EMA34, 0.25 ATR rebound | 203.04 / 1027.96 / 1328.64 | 198.74 / 1000.16 / 1369.95 | 34.21 / 95.07 / 76.86 | 3 / 2 / 4 | discovery exposure outside 70–80%; final -41.31pp vs E02 |
| MRVL_LONG | failed HH N20, 0.25 ATR failure | -135.92 / 351.59 / 585.45 | -173.19 / 170.74 / 1672.73 | 96.79 / 77.98 / 68.77 | 2 / 8 / 8 | fold 1 insolvent; final -1087.27pp vs E02 |
| SNDK_LONG | divergence/retest P2, RSI div 8 | 3739.94 / 3654.72 | 3136.51 / 2325.64 | 99.00 / 99.99 | 0 / 0 | no actual exits; exposure is effectively hold |
| DINO_LONG | divergence OR structure | 30.85 / 55.79 / 562.03 | 46.82 / 51.26 / 485.24 | 18.99 / 70.23 / 78.51 | 4 / 3 / 6 | fold 1 -15.98pp vs E02 and 18.99% TIM |
| ARM_LONG | divergence/retest P2, RSI div 8 | 420.82 / -169.69 / 1127.82 | 411.88 / -187.87 / 1068.86 | 98.30 / 91.97 / 94.91 | 1 / 1 / 0 | exposure high in every fold; fold 2 below B&H; no final exit |
| VT_LONG | divergence/retest P3, RSI div 8 | -6.43 / 95.51 / -5.54 | -11.56 / 95.51 / -16.73 | 98.77 / 92.55 / 82.55 | 0 / 0 / 0 | inert and below B&H; negative control behaves as expected |

DINO is the useful near miss: the same structural family passed every gate in
folds 2 and 3, including +76.79pp final alpha over E02 at 78.51% TIM, but its
first discovery fold was far too sparse and below control.  That is evidence
for testing entry-density/account-state interaction; it is not permission to
retune on the known final fold.

MU also confirms that good top timing alone does not repair the frozen
ladder's cross-fold exposure instability: its candidate improved E02 in both
discovery folds, but those folds sat at 34.21% and 95.07% TIM.  SNDK and ARM
show the opposite failure—top evidence is too rare, leaving exposure near
100%.  MRVL's first-fold insolvency means its immutable entry schedule is not
a promotable base regardless of final-fold headline return.

## Evidence and fleet write

Machine evidence:

- `data/reports/vec_research/top_exit_reclaim_phase3_manifest_20260727.json`
- `data/reports/vec_research/top_exit_reclaim_phase3_20260727T0340Z/summary.json`
- one complete `result.json` under each `<SYMBOL>_LONG/` directory

Six discovery-frozen final rows were appended as
`VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS / GRAY_REJECTED`.  The queue was
backed up first as
`queue.db.bak_top_exit_reclaim_phase3_20260727T0318Z`; an immediate
idempotence rerun appended zero and skipped all six.

No live config, canonical NPZ, ENGINE result, switch-matrix cell, exact queue,
or position state was changed.

## Rollback

Research rollback is deletion of
`data/reports/vec_research/top_exit_reclaim_phase3_20260727T0340Z/`.
Fleet rollback should delete only rows whose `stage` equals
`VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS` and whose payload
`campaign_artifact` ends in
`top_exit_reclaim_phase3_20260727T0340Z`; the pre-ingest queue backup is the
full fallback.  Code rollback is the commit that added the phase-3 runner,
cohort wrapper, ingest helper and tests.  No broader database restore is
needed and none should be done after unrelated newer fleet writes.
