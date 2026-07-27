# HAO_SHORT isolated ladder solvency grid — 2026-07-27

## Outcome

The bounded ladder reduction grid removed the original insolvency from every
discovery-frozen finalist, but it produced **zero strict survivors**. The
isolated HAO candidate remains quarantined. Nothing was written to canonical
indicators, live configuration, the switch matrix, or the path-fleet result
database.

The run evaluated 75 preregistered ladder settings:

- global delivered-request scale: 0.50, 0.625, 0.75, 0.875, 1.0;
- delivered multiplier cap: 4x, 5x, 6x, 7x, 8x;
- per-timeframe profile: exact frozen ladder, D at 75%, or D at 75% plus 4h
  at 87.5%.

Each setting retained the frozen fold's trigger, mode, target/add semantics
and entry timing. It was crossed with exactly three exits: E02 4h N30,
fixed E05 4h divergence/break/retest, and fixed 4%/50% full peak-giveback.
That is 225 discovery candidates, not 225 ladder shapes.

## Selection and leakage control

Only folds 1 and 2 were evaluated for ranking. The runner persisted
`preregistration.json`, then `discovery_freeze.json`, before it simulated fold
3. The freeze contains no final metric. Its file SHA-256 is
`6ffbfbb70c4fe16053563c20a22ef188495a7adab7add4d06139b35a2241849b`;
the ordered selected-candidate digest stored inside it is
`c7aef4a3f49dc43356fc62a63133c6ff2fb79e5e55124b862fa20463799702e0`.
The frozen union is top four per exit plus top eight overall, yielding 12
unique candidates. Mutating an unrelated post-discovery field cannot change
the freeze, and a final-fold field presented to the freeze function fails
closed. Four focused tests pass, including an E05 call through its validated
compiled execution route.

Fold 3 is therefore reporting evidence only. It did not choose the 12 rows or
alter their order.

## Results

There were zero discovery-all-fold strict candidates and zero all-three-fold
strict survivors. All 12 frozen candidates were solvent in all three folds,
so they are useful gray diagnostics, not eligible winners.

| exit | discovery settings | solvent in folds 1+2 | beat B&H in both | beat E02 in both | TIM 70–80 in both |
|---|---:|---:|---:|---:|---:|
| E02 4h N30 control | 75 | 74 | 75 | 0 by definition | 0 |
| E05 divergence/retest | 75 | 74 | 73 | 0 | 4 |
| peak giveback | 75 | 33 | 0 | 0 | 0 |

The closest interpretable row was the fixed E05 exit with global scale 1.0
and a 6x delivered cap. Exact and D75 profiles were discovery-equivalent:

| fold | profile | return | short B&H | same-entry E02 | TIM | max account DD | solvent |
|---|---|---:|---:|---:|---:|---:|---|
| discovery 1 | exact / D75 | 293.84% | 67.39% | 339.70% | 70.50% | 62.30% | yes |
| discovery 2 | exact / D75 | 53.41% | 20.64% | 64.84% | 79.84% | 82.08% | yes |
| untouched final | exact | 1,986.91% | 99.78% | 1,455.22% | 70.45% | 74.39% | yes |
| untouched final | D75 | 1,932.12% | 99.78% | 1,400.43% | 70.00% | 74.39% | yes |

E05 therefore solved solvency and exposure for these rows and beat B&H in all
three folds. It still lost to the identical-entry E02 control by 45.86
percentage points in discovery fold 1 and 11.43 points in discovery fold 2.
Its approximately +531.69 point final lift over E02 is final-only behavior and
cannot rescue the discovery rejection.

Peak-giveback is structurally unsuitable here: even its frozen best rows lost
money in discovery fold 2 and missed the exposure band in folds 1 and 3.

## Insolvency attribution

All 44 observed insolvent evaluations occurred in discovery fold 2:

- peak-giveback: 42;
- E02: 1;
- E05: 1.

The 0.50 global scale produced no insolvencies. At higher scales the counts
were 4 at 0.625, 15 at 0.75, 15 at 0.875 and 10 at 1.0. This is not a monotonic
cap effect because target/add semantics, fills, exits and mandatory reclaims
change the path. The important causal result is that the original insolvency
was concentrated in the July–December 2025 fold and peak-giveback amplified
it; blanket scaling is not an alpha-preserving solution because it also drove
fold-1 TIM below target.

## Data and replay status

The isolated NPZ hash before and after was
`17907495bd909a1ea7ebcd6c7846134a7e70ae966b0fe7e79c3006107bb36332`.
Canonical HAO remained
`2865773e683700abd8222e7e9e17e24a5704bdf22a62ef3c09e130798b355b98`.

Exact replay is **not eligible**. As documented in the recovery audit, the
isolated final fold contains 15m-derived 5m rows whose containing-bar OHLC
must not be available until parent close. Until the exact engine enforces that
availability clock, no causal HAO replay spec may be emitted. This is a
separate blocker from the zero-survivor result.

Machine evidence is in:

`data/npz_recovery/hao_recovery_20260727T0010Z/controls/solvency_grid/hao_short_ladder_grid_20260727T0515Z/`

The raw `result.json` SHA-256 is
`d267b0cd06851b0adc8861ad4a613c07b125f1d97e0ce07e92f5adb1a044fc31`.

The compact repository receipt is
`data/reports/vec_research/HAO_SOLVENCY_LADDER_RECEIPT_20260727.json`.
