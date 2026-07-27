# Online exposure-density walk-forward protocol — frozen 2026-07-27

## Purpose and status

This is the bounded follow-up to the state-aware priority beam. PBF_LONG's
frozen DC-tier entry plus delayed-lower-top exit passed both discovery folds
at 70.54% and 75.06% time in market, but the already-observed final fold
drifted to 83.44% and returned 519.70% versus 641.10% from identical-entry
E02. The new controller addresses exposure drift using completed schedule
history. It does not retune the PBF exit or reuse the observed final as an
acceptance holdout.

The implementation is `tools/online_exposure_walkforward.py`. It is
research-only and makes no live, configuration, or NPZ changes.

## Globally frozen controller

There is one primary policy, `online_wf126_v1`; there is no symbol parameter
grid.

- Target: 75% source-schedule occupation with a fixed 70–80% deadband.
- Update boundary: after 126 completed 1h slots. The new level becomes
  effective only on the next completed-1h slot.
- State: five bounded density levels from -2 through +2, initialized at zero.
- Allowed inputs: prior-window source-schedule occupation error versus 75%;
  completed-1h request drought; an E02 reclaim anchor latched until a causal
  OHLC touch or a better-price entry; and prior-window accepted-entry versus
  realized-source-E02-exit density.
- Prohibited inputs: market indicators/regimes, symbol thresholds, candidate
  exit state, future window state, final-fold values, and blended entries.
- Capacity: every emitted request is capped at 8x/$16,000.
- Persistence: a reclaim obligation bypasses request-density rejection. A
  causal touch emits the prior exited multiplier; a frozen-family fill at a
  better price explicitly satisfies the obligation. It is never overwritten.

At a completed-window boundary, utilization alone chooses direction.
Below 70%, density rises one level; it rises two only when the same completed
window is not entry-heavy and the request drought is at least eight completed
1h slots. Above 80%, density falls one level; it falls two only when the
completed window is entry-heavy. Inside 70–80%, the level is held. Levels
change request scale/gap/cap by a fixed lookup table in the implementation.

## Frozen exits and staged order

PBF_LONG is the only first-stage key. Its entry schedule is frozen from:

`entry_overlay_ENTRY_DC_TIER_AUG_ENABLED_20260726T215909Z_PBF_LONG`

The first stage crosses exactly three exits, frozen before new holdout data:

1. identical-entry E02 Donchian 4h N30;
2. the discovery-selected bottom-B delayed-lower-top geometry: 4h arm, 1h
   price-only confirmation for three bars, prebreak lookback 3, rebound 1.5
   ATR, maximum wait 72 hours, and no emergency overlay;
3. one bottom-A protective geometry frozen from discovery evidence and
   recorded by full parameter JSON before execution.

No A/B parameter grid is permitted in the new holdout. MU_LONG and ARM_LONG
remain blocked until PBF passes. A top/bottom-20 cross-sectional holdout is
optional only after the same global controller and exit-freeze rule pass PBF;
symbols may not receive separate controller values.

## Leakage-safe evaluation

All dates through 2026-07-25 have already been inspected and cannot be called
untouched again. They may be used only for deterministic protocol checks and
for freezing the three exits above.

The acceptance holdout starts strictly after the frozen PBF NPZ ending
2026-07-24 20:00 UTC. Accrual is append-only. The primary verdict requires at
least 126 completed 1h slots plus one subsequent complete controller window;
the intended minimum is two later calendar months. At every update, hashes of
the preceding window inputs, emitted schedule, controller state, frozen entry,
frozen exits, code, and NPZ version are recorded before the next window is
evaluated.

The observed 2026-01-01–2026-07-25 fold is excluded from acceptance. Nested
walk-forward replay on the two 2025 discovery folds is diagnostic only and
must be labeled `RETROSPECTIVE_PROTOCOL_CHECK`, never OOS or a survivor.

## Gates and disposition

Every newly accrued evaluation window must:

- beat side-correct B&H and identical-entry E02;
- remain within 70–80% weighted time in market;
- have no insolvency, capacity breach, future HTF read, forgotten reclaim,
  or LONG/SHORT mixing.

Only a row passing every window enters exact-engine replay. Any failed row is
appended idempotently as gray evidence so it cannot be silently retested.
Promotion is forbidden in this vector stage. PBF failure ends v1; it does not
authorize tuning against the new holdout. A materially changed v2 requires a
new preregistration and a later holdout.
