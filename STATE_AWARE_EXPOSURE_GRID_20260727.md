# State-aware exposure × bottom-exit beam — preregistration

## Question

The global market-regime beam moved the same symbol's discovery folds in
opposite directions. This experiment asks whether causal state of one already
frozen entry schedule can adjust density without using market labels.

The only entry schedules are:

- `MU_LONG` — frozen `ENTRY_DC_TIER_AUG_ENABLED`
- `ARM_LONG` — frozen `ENTRY_4H_DEEP_VALUE`
- `PBF_LONG` — frozen `ENTRY_DC_TIER_AUG_ENABLED`

They remain separate. No entry-family blend, symbol threshold, live switch, or
canonical NPZ edit is permitted.

## Frozen state and causality

At each completed execution row the state transformer may see only:

- the frozen entry request and completed 1h request-event slot;
- scheduled capacity utilization under that frozen schedule;
- completed-1h bars since its last accepted request;
- a latched source-E02 exit/reclaim anchor and whether it has been touched.

The source-E02 state is an **exogenous frozen entry-schedule state** shared by
every exit candidate. It is not the candidate exit's future account ledger.
An outstanding anchor cannot be overwritten by another source event and stays
latched until its OHLC touch or the fold ends. No broad-market feature is used.

Utilization states use fixed global cutoffs: underfilled `<50%`, neutral
`50–<75%`, saturated `>=75%`. Each row below is
scale / minimum completed-1h request gap / cap:

| policy | underfilled | neutral | saturated | underfilled floor | drought bars / scale | reclaim scale |
|---|---|---|---|---:|---|---:|
| state_refill_gentle | 1.25 / 0 / 8x | 1 / 0 / 8x | .875 / 1 / 6x | 4x | 8 / 1.25 | 1 |
| state_refill_balanced | 1.5 / 0 / 8x | 1 / 1 / 6x | .75 / 2 / 5x | 5x | 10 / 1.375 | 1.125 |
| state_refill_strong | 1.75 / 0 / 8x | 1 / 1 / 6x | .625 / 3 / 4x | 6x | 12 / 1.5 | 1.25 |
| state_reclaim_priority | 1.25 / 0 / 8x | 1 / 0 / 8x | .875 / 1 / 6x | 4x | 8 / 1.25 | 1.5 |

Scales decrease, entry gaps increase, and caps decrease as utilization rises.
Every request remains at or below 8x/$16,000.

## Exit beam and freeze boundary

Each entry/policy pair screens only:

- the E02 grid, for same-entry control and comparison;
- extended bottom A protective ATR/STDEV/trailing exits;
- extended bottom B delayed lower-top exits.

The vector adapters use completed bars, $16,000 strategy capacity, $2,000
side-specific B&H, and isolated LONG/SHORT accounting. Candidates are ranked
on discovery folds only. The top eight per frozen entry schedule are hashed
and frozen before their final fold is reported.

A strict survivor must beat both B&H and the same conditioned-entry E02 control
in every discovery fold and the untouched final fold; have 70–80% weighted
time in market; make an actual exit; have no insolvency, capacity breach,
future HTF source, forgotten reclaim, or emergency-share violation. Only such
survivors may enter exact replay. Selected failures are retained as gray
evidence and no result changes live configuration.

## Invariants

Automated tests prove:

- prefix causality and completed-1h slot monotonicity;
- the 8x cap and monotonic fixed grid;
- mutation of future rows cannot alter the discovery schedule;
- mutation of final metrics cannot alter the discovery-ranked exit freeze;
- a source-E02 reclaim anchor cannot be silently replaced or forgotten.

