# Causal top-exit research backlog — 2026-07-26

This backlog converts external exit ideas into falsifiable path jobs. It does
not treat a book, website, video, indicator popularity, or a single optimized
chart as performance evidence. Every idea must consume the frozen same-entry
ladder schedule and pass the existing nested B&H/control/exposure contract.

## What the completed screens already rule out

- A slower Donchian exit mostly increases leveraged hold time.
- A fast Donchian exit usually lowers exposure by adding churn.
- A multi-timeframe WaveTrend vote can exit often, but its exposure regime is
  unstable. VLO is the clearest near-miss: a strong final fold with weak
  discovery exposure.
- A structural lower-low then lower-price/WT-top state is causal and connected,
  but the full 15,360-candidate grid produced no strict cohort survivor.
- `dc_low4_5m` remains an entry-quality diagnostic, not a profit exit.

## Source-derived jobs

### 1. Ratcheted ATR ceiling after structural arm

Chuck LeBeau's Chandelier exit anchors a one-way stop below the highest high
for LONG (above the lowest low for SHORT) by an ATR multiple. It is a
volatility-adjusted trend-retention mechanism, not a top predictor. Sources:
[formula and background](https://www.incrediblecharts.com/indicators/chandelier_exits.php)
and [formula summary](https://corporatefinanceinstitute.com/resources/equities/chandelier-exit/).

Repository mapping: `EXIT_E01_CHANDELIER`, then `EXIT_MTF_ATR_TRAIL`.

Preregistered adaptation:

- do not activate from entry;
- arm only after a profitable completed-4h structural break or adverse WT
  divergence;
- ratchet the line one-way;
- compare full exit with 15/25/33% clip plus E02 runner;
- sweep lookback 10/20/30/55 and ATR 1.5/2/2.5/3/4;
- retain at least one actual exit per validation fold.

This directly tests whether ATR can confirm the *post-top* decline without
turning the path into a routine loss stop.

### 2. Directional-movement SafeZone after a failed new high

Elder's SafeZone family measures directional penetrations rather than using
only average volatility; the Chandelier overview distinguishes it from ATR
stops on that basis
([comparison](https://www.incrediblecharts.com/indicators/chandelier_exits.php)).

Repository mapping: first audit `EXIT_TECH_BREAKDOWN_ENABLED`,
`EXIT_STRUCT_HLNM_EXIT_ENABLED`, and `EXIT_MTF_ATR_TRAIL`; create a new family
only if none implements a monotonic directional-penetration stop.

Preregistered adaptation:

- arm after a failed higher high/new-high attempt;
- estimate adverse penetrations only from completed bars;
- exit/clip on a selected percentile or multiple of prior adverse
  penetrations;
- require a lower price high or adverse WT slope so it cannot fire on the
  first ordinary pullback.

### 3. WT extreme cross plus price-structure interaction

The commonly reproduced LazyBear WaveTrend formulation uses a normalized
channel index and a WT1/WT2 signal line; extreme-level crosses are the classic
mean-reversion trigger
([open implementation and formula](https://docs.rs/quantwave-core/latest/src/quantwave_core/indicators/wavetrend.rs.html)).

Repository mapping: the completed `EXIT_WT_MTF` grid is the control. Do not
widen its thresholds again.

Preregistered adaptation:

- require an extreme WT rollover plus a failed price high or bearish
  divergence on the same completed timeframe;
- test a partial clip first, not only a full close;
- keep an E02 runner and a separate reclaim obligation for every clip;
- compare VLO's unstable near-miss against this interaction without selecting
  on its final fold.

### 4. Peak-giveback sizing, not binary liquidation

Drawdown-modulated control research treats drawdown as a state that changes
exposure rather than as a single universal exit threshold
([drawdown feedback paper](https://arxiv.org/abs/1710.01503)).

Repository mapping: `EXIT_PEAK_GIVEBACK`, then `EXIT_PARTIAL_RUNNER`.

Preregistered adaptation:

- compute mark-to-market excursion and giveback from the position's own causal
  equity peak;
- clip 15/25/33/50% at multiple giveback levels;
- retain a slow E02 or Chandelier runner;
- report realized partial P&L separately;
- prohibit an unfilled clip-reclaim obligation at the end of a fold.

## Rejection discipline

Each job is gray and should not be repeated unchanged when any of these holds:

- a headline return is a zero-exit final mark;
- validation exposure is outside 70–80% for the frozen top/bottom cohort;
- discovery or any untouched fold loses B&H or the identical-entry control;
- negative B&H is used to manufacture a positive ratio;
- side P&L is pooled or inverted;
- an HTF source is not completed;
- insolvency, entry-capacity breach, future-source use, or a lost reclaim
  obligation occurs.

External sources determine only the next bounded hypothesis. They never
determine a setting, a promotion, or a live trade.
