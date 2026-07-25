# Robust top-exit walk-forward campaign — 2026-07-25

## Outcome

E03, E06, E08, and E09 are now implemented as causal, vectorized research
candidates and paired exclusively with E11 lower-price re-entry plus E10
zero-buffer mandatory reclaim. The cost-correct, nested walk-forward result is
a rejection, not a promotion:

- `MU_LONG`: strict frozen validation gained **47.41%** versus **75.22%**
  compounded side-aware B&H, or **0.6302x B&H**, at **73.14% RTH TIM**.
- `VT_LONG`: no frozen validation fold satisfied the complete strict policy.
  The two data-clean frozen folds gained **3.97%** versus **9.92% B&H**, or
  **0.3996x B&H**, at **90.08% RTH TIM**.
- E06 regression-channel re-entry was available to selection but was never
  selected in any outer fold for either symbol.
- E08 was selected in two MU folds. It beat B&H in one fold at 68.1% TIM, then
  underperformed badly in the next at 81.2% TIM. It did not change the strict
  result and was never selected for VT.
- No candidate is eligible for the ENGINE matrix, live config, or promotion.

These results are materially less flattering than the prior full-period
optimized headlines. That is expected and is the point of frozen validation.

## Exact artifacts on s1

- MU:
  `/home/niels/binance-sandbox/data/reports/vec_research/walkforward_top_exit_20260725T185650Z_MU_LONG`
- VT:
  `/home/niels/binance-sandbox/data/reports/vec_research/walkforward_top_exit_20260725T185705Z_VT_LONG`

Each artifact contains:

- a run manifest;
- a detailed walk-forward digest;
- compressed frozen-fold rows;
- source snapshots of the Python runner, vector campaign, and C scanner.

## Candidate definitions

### E03 — confirmed damage, rebound, failed retest

For a long, consecutive completed 4h lower-high/lower-low bars plus an
ATR-scaled decline arm the path. A separate favorable rebound bar marks the
retest. Only a later lower-high/lower-low bar closing below the prior low exits
at the next RTH open. The SHORT path is mirrored.

The arm and rebound bars are explicitly unable to exit. This is the critical
semantic difference from the damaged implementation that tried to sell the
favorable retest bar and then lost its pending obligation when a structural
veto rejected it.

Grid:

- confirmation bars: 2, 3;
- damage: 0.5, 1.0, 1.5 ATR;
- rebound: 0.25, 0.5, 1.0 ATR;
- maximum wait: 8, 12, 20 completed 4h bars.

### E06 — regression excursion and re-entry

A rolling OLS fit on log price uses only the preceding completed 4h bars. A
positive-slope upper-channel excursion arms a LONG exit; a later channel
re-entry or adverse prior-low break exits. SHORT is mirrored.

Grid:

- lookback: 40, 60, 100, 150, 250;
- arm z-score: 1.5, 2.0, 2.5, 3.0;
- exit z-score: 0.75, 1.0, 1.5, 2.0, always below the arm;
- absolute correlation gate: 0.5, 0.7, 0.85.

### E09 — HTF exhaustion, LTF structure

Completed 4h or daily RSI plus an ATR extension above/below a prior-only EMA
arms exhaustion. A completed 1h adverse structure bar triggers the next-open
exit. Unfinished HTF bars are never read.

Grid:

- arm timeframe: 4h, D;
- RSI threshold: 65, 70, 75 and mirrored for SHORT;
- extension: 0.5, 1.0, 1.5 ATR;
- expiry: 12, 24, 40 completed 1h bars;
- LTF confirmation bars: 1, 2.

### E08 — MFE-activated profit lock

The path has no pre-activation stop. Once maximum favorable excursion since
the latest entry reaches the configured ATR threshold, a monotonic Chandelier
lock becomes active. Entry-dependent MFE, activation, trail state, and resets
on re-entry live in the compiled state scan. Exit evaluation occurs only on a
completed 4h or daily bar, then fills at the next RTH open.

Grid:

- activation: 2, 3, 4, 6, 8 ATR;
- tight Chandelier distance: 1.5, 2.0, 2.5, 3.0, 3.5 ATR;
- evaluation timeframe: 4h, D.

### Re-entry

All evaluated rows use one of:

- `E11_G0.5+E10_RB0`;
- `E11_G1+E10_RB0`;
- `E11_G1.5+E10_RB0`;
- `E11_G2+E10_RB0`.

E11 first attempts a lower-price re-entry after the configured ATR gap and a
completed 1h HH+HL bar. E10 is the mandatory fallback and re-enters at the next
RTH open when the stored structural top/exit level is reclaimed. There is no
E11-only policy in this campaign.

## Walk-forward protocol

The campaign evaluated **407 exit parameterizations** and **1,628
exit/re-entry combinations** per symbol.

For every rolling outer fold:

1. Use the trailing 12 months as discovery data.
2. Split discovery chronologically into three inner folds.
3. Score candidates by median log strategy/B&H equity ratio, penalizing
   cross-fold instability, distance from 75% TIM, and any mandatory-reclaim
   breach.
4. Require discovery mean TIM of 70–80%, at least two of three inner folds in
   that band, and perfect mandatory-reclaim behavior when any candidate meets
   those constraints.
5. Freeze the exact exit and E10/E11 parameters.
6. Evaluate once on the next three-month outer window with completed HTF bars,
   next-RTH-open fills, 5 bp one-way commission input, and 2 bp one-way
   slippage.

The digest reports three separate aggregates:

- `strict_policy_frozen_validation`: data-clean discovery and validation,
  no selection fallback, mandatory reclaim, and frozen validation TIM 70–80%;
- `data_clean_frozen_validation`: all data-clean frozen folds, even where
  validation TIM drifted;
- `all_frozen_validation_including_gaps`: diagnostic only.

No full-period optimized headline is used for ranking or promotion.

## Cost correction

The first exact-engine replay exposed a **7.116 bp** vector/engine mismatch.
The old scanner debited one-way commission independently at entry and exit.
The faithful engine charges the configured 0.10% stock round-trip cost once at
close against entry position value.

The C scanner, Python reference replay, and B&H benchmark now use:

```text
entry_equity = current_equity
close_equity = gross_marked_equity - entry_equity * (2 * one_way_cost_rate)
```

Slippage remains encoded only in side-aware entry and exit fill prices. A
regression test proves that a flat 100-to-100 trade with 5 bp one-way cost ends
at exactly 0.999 equity.

All final figures in this document were regenerated after that correction.

## Data handling

- MU contract: valid. Interpolated 5m execution bars are disclosed and accepted
  under the campaign contract.
- VT contract: valid after regeneration, but the known source-provenance gap
  from **2026-03-30 through 2026-06-08** remains explicit. Interpolated
  timestamps must not hide it.
- Any VT outer fold whose discovery or validation overlaps that known gap is
  excluded from the data-clean and strict aggregates.
- LONG and SHORT accounting is completely separated.

## Frozen validation detail

### MU_LONG

Six data-clean outer folds compounded to **575.34%** versus **971.95% B&H**
at **75.11% TIM**: **0.5919x B&H**.

Only three folds also met the complete strict policy. They compounded to
**47.41%** versus **75.22% B&H** at **73.14% TIM**: **0.6302x B&H**.

The selected exact strategy repeated in only 1 of 6 folds. E03 was selected in
4 folds and E08 in 2. This is poor parameter stability and does not support
promotion.

### VT_LONG

Only two folds had clean discovery and clean validation. They compounded to
**3.97%** versus **9.92% B&H** at **90.08% TIM**: **0.3996x B&H**.

No fold set met the complete strict policy, so the strict result is `null`.
The selected exact strategy repeated in only 1 of 5 diagnostic folds; family
repeat rate was 60%. This is also unstable.

## Verification

`test_vec_top_exit_walkforward.py` covers:

- E03 requires distinct arm, rebound, and failed-retest bars;
- E09 cannot trigger before a completed HTF arm;
- robust selection rejects a one-period jackpot;
- scanner cost matches the faithful engine round-trip entry-value model;
- the known VT source gap cannot be hidden by continuous interpolated
  timestamps;
- E06 rolling regression features are prior-only.
- E08 stays inactive below its MFE threshold and then exits through the
  monotonic profit lock.

Result: **7 passed**.

## Decision and next work

E03/E06/E08/E09 as currently parameterized do not explain the earlier inability to
beat B&H and should remain gray research rows. The evidence points away from
blindly adding more exit OR paths: these exits often remove exposure during the
very trend B&H captures.

The next bounded probes should be:

1. E12 partial top reduction plus a slow runner, reporting exposure-weighted
   TIM rather than binary TIM.
2. E13 regime-conditioned exits, preventing fast mean-reversion exits during a
   persistent daily/4h trend.
3. Lower-price re-entry improvements: the current completed 1h HH+HL trigger
   often re-enters too late or not at a positive saved price.

These should use the same cost-correct scanner and the same frozen nested
walk-forward gate. The production matrix must not receive a green result until
the exact faithful engine reproduces the event schedule and accounting.
