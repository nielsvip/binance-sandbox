# HAO versioned NPZ recovery and quarantine — 2026-07-27

## Outcome

HAO's missing 4h/daily regression fields were caused by insufficient source
history, not by an indicator calculation error. A new isolated candidate was
built from adjusted provider bars under:

`/home/niels/binance-sandbox/data/npz_recovery/hao_recovery_20260727T0010Z`

It is complete enough for research, but it is **not safe to promote**. The
canonical HAO and VT NPZ files were hash-checked before and after the build
and did not change.

## Source and corporate-action result

The provider returned native adjusted 5m and 15m HAO bars from 2024-07-26
through 2026-07-24 despite a request beginning 2024-01-01. That 728.6-day
span is sufficient for the 400-bar 4h and 200-bar daily long-regression
windows.

- Native 5m: 30,513 rows, SHA-256
  `19947d47c5ea784b6c26e5e7ba96d0c81b5687e63e259037af7546475e28223c`.
- Native 15m: 14,064 rows, SHA-256
  `43448a562df73145de2ba8c5be2c3029a65e120124d5b69acaf21756216bbfb3`.
- The real June 8 range remains intact: high 2.04, low 0.679.
- The May 21 reverse-split boundary is already adjusted. The adjacent 15m
  close ratios are 1.013 and 0.934, not an artificial 128x discontinuity.

The isolated control permits one additional real, native discontinuity only
when both the candidate NPZ hash and timestamp match: HAO moved from 0.246 to
0.465 at 2026-07-14 10:25–10:30 UTC. Daily historical data shows a July 14
high around 0.43 and contemporary news describes HAO as a premarket gainer.
This exception does not weaken the canonical data contract.

## Candidate validation

The recovery NPZ has 44,879 rows, 1,054 fields, and SHA-256
`17907495bd909a1ea7ebcd6c7846134a7e70ae966b0fe7e79c3006107bb36332`.
All required 4h/daily `lrL` fields are present with 100% finite coverage.
Daily Stoch finite coverage rose from 66.6% to 93.69%.

Completed-source future counts are zero for 1h, 4h, D, W, and M. The file
contains 30,513 native and 14,366 explicitly flagged 15m-derived 5m rows
(32.01%). Their parent-close timestamps and `synthetic_5m` flags remain
present; they are never described as native.

## Isolated HAO_SHORT controls

The frozen ladder plus 4h E02 screen produced an apparently attractive
fold-sum result: +2,181.39% versus +187.80% side-specific B&H, or 11.615x,
at 73.72% TIM. It nevertheless fails the basic account control: one fold is
insolvent, minimum equity is -$910.61, and maximum account drawdown is
107.73%. It is not matrix-eligible.

The bounded quick top-exit screen tested E03, E06, E08, and E09 with mandatory
E11 lower/higher-price re-entry plus E10 zero-buffer reclaim. Across ten
frozen folds it compounded to -100% versus +895.08% short B&H, used 88.79%
TIM, and included one insolvent fold. No strict-policy result exists and the
row remains gray/quarantined.

The missing-spec tooling blocker was fixed first with a deterministic v2
bundle. Its initial HAO smoke correctly failed closed because 1,381
interpolated final-fold rows were still being exposed before parent close.
The later shared v3 research clock now releases those rows at
`synthetic_5m_parent_close_ts` in both vector and exact replay, retains stable
source-row identity, and binds that ordered clock in the spec. Clock support
is therefore no longer HAO's blocker. HAO remains ineligible because its
original candidate was insolvent and the preregistered solvency grid found
zero strict discovery survivors. No HAO v3 exact replay has been claimed;
replaying a rejected row would be diagnostic only.

Machine receipt:
`data/reports/vec_research/hao_recovery_20260727T0010Z/research_ladder_replay/receipt.json`.
The cited receipt remains the historical v2 fail-close. No HAO v3 replay spec
was emitted, and no canonical NPZ, matrix cell, symbol universe, or live
configuration was changed.

## Rollback and promotion rule

No rollback action is needed: production files were never replaced. Keep:

- canonical HAO SHA-256
  `2865773e683700abd8222e7e9e17e24a5704bdf22a62ef3c09e130798b355b98`;
- canonical VT SHA-256
  `8b6f80c0b9031b0f2e58086161c97cfb4ab8f101d933326e497f17a406f71505`.

Promotion requires, at minimum, a solvent frozen walk-forward result, account
drawdown below 100%, side-correct B&H superiority in every required fold,
70–80% TIM in every required fold, and a generated exact replay spec that
passes engine parity. None of those result gates is waived by the successful
data repair.

Machine evidence is under
`data/reports/vec_research/hao_recovery_20260727T0010Z/`.
