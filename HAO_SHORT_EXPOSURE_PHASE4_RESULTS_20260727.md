# HAO_SHORT exposure and reclaim persistence phase 4 — 2026-07-27

## Outcome

Phase 4 repaired the low deployed exposure seen in the accepted phase-3 V4
diagnostic, but it did **not** produce a promotable HAO_SHORT path.

The discovery-frozen E02 policy `C_STAGE7_5_REST_ADD1` passed every gate in
both discovery folds:

| fold | strategy | short B&H | phase-3 E02 | vs B&H | weighted TIM | min equity | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| D1 | +536.6057% | +67.3870% | +152.3786% | 7.963x | 77.9472% | $8,165.00 | PASS |
| D2 | +174.1376% | +20.6375% | +134.1112% | 8.438x | 71.2819% | $1,828.11 | PASS |

Only after that freeze was the untouched final fold opened. It returned
+1,746.5002% versus +99.7766% short B&H and +1,021.7546% for the phase-3
same-exit control, remained solvent, and had zero capacity/causality/reclaim
violations. It nevertheless failed the preregistered 70–80% exposure gate:
weighted TIM collapsed to 36.0456%. Exact v3 therefore did not run and the
candidate remains gray.

The result is useful but not robust. The persistence controls can turn HAO's
undersized discovery book into a side-aware 7.96–8.44x B&H result without
changing entry or exit geometry, yet the same fixed policy does not maintain
stable deployment in the untouched regime.

## Fixed boundary

The campaign was hash-bound to:

- isolated HAO NPZ
  `17907495bd909a1ea7ebcd6c7846134a7e70ae966b0fe7e79c3006107bb36332`;
- accepted sealed phase-3 V4 result
  `daa59b46e8229e8bce145c5eeef3a403435ae1388dd10e78ad379e7e2aaf62ca`;
- frozen ladder result
  `dff84d48cb54e5d796aade003d9e1888b22b13ebda7e3126f9b57992a261e3da`;
- `SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1`; and
- the phase-3 pair `F01_BEAR_REGIME`,
  `L_EXACT_0875_CAP6`, E02/E05.

Every order used the first strictly later availability batch, correct adverse
SHORT fills, commissions, a $2,000 side-specific short B&H comparison unit,
$10,000 solvency ledger, and $16,000 hard capacity. The completed-HTF audit
made 4,904 D/4h/1h source checks plus 3,258 completed-1h impulse checks and
found zero future sources. The isolated NPZ hash was unchanged after every
run.

No inverse-LONG assumption was introduced. The only extra event was a
completed-1h SHORT-native downside impulse: completed bear-regime state,
lower high, lower low, lower close, and a down body of at least 0.25 ATR. It
could stage or add only while a SHORT already existed.

## Exposure policy

The winning discovery policy did not invent a new entry or exit:

- phase-3 F01 still gates ordinary ladder requests;
- exact-timeframe ladder signals retain scale 0.875 and an 8x delivered cap;
- the first accepted ladder request seeds at least 4x;
- a later completed bearish impulse may stage the target to 7.5x;
- at most one additional 1x bearish-impulse add is allowed per position;
- an exited position keeps a resting reclaim obligation; and
- a reclaim fill is rejected if the adverse opening SHORT fill would be above
  the stored reference.

This is a coherent position-persistence policy, not 7.5 applied to every
signal. Capacity remains 8x/$16,000.

## Sequential discovery protocol

The work used three explicitly separated, final-blind discovery steps.

### V1 — bounded screen

Thirteen coherent exposure policies crossed with E02/E05 made 26 rows. They
covered the accepted .875/6x source, 1.0 scale with 7x/8x caps, staged
4→6/7 and 6/7→8 targets, resting reclaim, and one bounded bearish add.

V1 had zero strict rows. Its closest E02 row, `P11_SEED4_STAGE7_REST_ADD1`,
beat B&H and the phase-3 E02 control in both discovery folds. D1 TIM was
74.7620%; D2 was 69.5515%, only 0.4485 points below the floor. E05 had two
rows in the 70–80% band on both folds, but zero E05 rows beat identical-entry
E02 in both folds. E05 remains rejected as an exit improvement.

### V2 — boundary confirmation

Without opening final, a separate four-value cohort bracketed the V1 boundary
at 7.125/7.25/7.375/7.5x. The 7.25, 7.375 and 7.5 E02 rows satisfied every
numerical gate in both discovery folds.

They exposed a gate-semantics error: the fold was rejected whenever a valid
resting reclaim order existed at the boundary, even when price had never
reached the stored level. That is not a missed reentry. It is ordinary
finite-window state.

### V3 — reclaim-semantics confirmation and freeze

V3 preregistered only the already-confirmed 7.25/7.375/7.5 E02/E05 rows plus
the source. It distinguished:

- `open`: a stored reclaim order still rests outside the observed fold; from
- `due`: price touched/crossed the stored reference but the order remains
  unfilled.

An open-but-not-due order is valid. A due unfilled order still fails closed.
All three E02 rows then passed both discovery folds. The discovery-only rank
froze 7.5x before final was evaluated. E05 again had zero strict rows.

## Untouched final diagnosis

The frozen final result was:

| metric | value |
|---|---:|
| strategy return / $2k unit | +1,746.5002% |
| short B&H / $2k unit | +99.7766% |
| phase-3 same-exit control | +1,021.7546% |
| strategy / B&H | 17.504x |
| weighted TIM | 36.0456% |
| binary TIM | 82.1573% |
| minimum account equity | $6,607.98 |
| maximum account drawdown | 39.0385% |
| peak post-fill notional | $16,000.00 |
| entry / exit fills | 16 / 2 |
| reclaim reentries | 1 |
| future HTF sources | 0 |
| due reclaim at end | false |

The low final weighted TIM despite 82.16% binary occupancy means the remaining
problem is regime-dependent *position depth*, not simply flat time. A fixed
7.5x stage fires often enough in discovery but too rarely in final. Because
final is now observed, it must not be used to tune another value in this
campaign. A future study needs a new symbol/cohort-level rule for target depth,
selected outside this HAO final fold.

## Preserved gray evidence and invalidations

All 44 candidate records remain in their immutable s1 artifacts:

- V1: 26 rows, zero strict, final sealed;
- V2: 10 rows, zero strict under the superseded open-obligation gate, final
  sealed;
- V3: 8 rows, three E02 discovery-strict, one frozen row, final failed TIM.

The first V3 execution at `...T041407Z` had the correct metrics but mislabeled
the exact status as `NOT_RUN_DISCOVERY_GATE_FAILED`. The corrected immutable
artifact at `...T041459Z` says `NOT_RUN_FINAL_GATE_FAILED`; no metric or
selection changed.

No result is matrix-green or live eligible. No canonical indicator, Tradier
configuration, switch-matrix cell, or live state was written. The compact
fleet-compatible receipt is
`data/reports/vec_research/HAO_SHORT_EXPOSURE_PHASE4_RECEIPT_20260727.json`.

## Rollback

Rollback is deletion of the isolated
`controls/short_exposure_phase4/` artifacts, the three phase-4 runners/tests,
this document, its Bible section, and the compact receipt. There is no
canonical/live/matrix rollback because the campaign never wrote those states.
