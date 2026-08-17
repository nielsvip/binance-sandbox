# Why current `tradier_manage.py` and scalar V8 replay became slow

## Finding

The slowdown is architectural, not one bad threshold. Between the March
working set and the current file, the live decision graph became roughly four
times larger while the exact matrix continued to replay that entire graph in a
fresh Python process for each scalar value. The cost is then multiplied by
hundreds of cells whose meanings change whenever another path changes.

| Static measure | 2026-03-04 working set | Current | Change |
|---|---:|---:|---:|
| Lines | 6,692 | 19,651 | 2.94× |
| AST call sites | 2,980 | 10,603 | 3.56× |
| Loops | 136 | 318 | 2.34× |
| Functions | 195 | 342 | 1.75× |
| `json.load` sites | 13 | 47 | 3.62× |
| `open()` sites | 21 | 45 | 2.14× |
| Background tasks in manager `start()` | 20 | 35 | 1.75× |

The highest-frequency functions expanded much faster than the file:

| Hot function | March | Current | Consequence |
|---|---:|---:|---|
| `process_position` | 252 lines / 87 calls | 2,164 / 1,213 | Every simulated bar traverses a large interdependent branch graph. |
| `evaluate_stop` | 149 / 66 | 1,355 / 774 | Exit replay now evaluates many path families and also contains synchronous JSON reads. |
| `execute_now` | 142 / 70 | 1,037 / 548 | Order execution mixes state/config I/O, price reads, position scans and serialization. |
| `evaluate_reentry` | 136 / 119 | 495 / 351 | Every exit changes the later entry schedule, TIM, sizing and capacity path. |
| `evaluate_open` | 108 / 63 | 437 / 264 | Entry selection is no longer an isolated scalar predicate. |

Current hot-path source inspection found synchronous file reads in
`evaluate_stop`, `queue_trade_action`, `evaluate_reentry`, and several reads,
writes and serializations in `execute_now`. `process_position` can request a
price multiple times inside one call. These operations are acceptable at an
acknowledged live state boundary, but repeating them for every historical bar
and every candidate is unnecessary overhead.

## Why cell-by-cell testing failed

The exact harness starts a new interpreter per scalar cell, reloads NPZ/config,
rebuilds indicators and replays about 113k bars through the live monolith. At
the observed 4.5–10 minutes per cell, an 826-cell key costs about 62–138 core
hours before any ENTRY×EXIT×REENTER interaction search. OFAT then answers only
“what did this value do while every other current path stayed fixed”; changing
an entry changes which exits can fire, exits change reclaim/reentry timing, and
both change TIM, capacity, drawdown and later entry eligibility. The scalar
deltas therefore cannot be added into a best complete recipe.

## Framework now used

1. Build one versioned causal NPZ per symbol and load/validate it once.
2. Generate bounded entry candidates, retain behavior-unique schedules, then
   cross only those with bounded exit families and mandatory strictly-later
   reclaim in one causal lifecycle beam.
3. Rank complete recipes against side-aware B&H, TIM, real closes/week,
   drawdown, costs, clamps, causality and reclaim—not return alone.
4. Stop discovery at 25 minutes/key plus five minutes for validation and
   publication. `UNRESOLVED_USE_BH` is a valid result and preserves B&H as the
   baseline.
5. Send only the top one or two frozen, behavior-unique complete recipes to the
   exact shared live/V8 adapter. Scalar exact work remains a wiring/parity
   instrument, not an optimizer.

## Code improvements in priority order

1. Extract a pure shared lifecycle decision kernel with explicit ENTRY,
   AUGMENT, REDUCE, EXIT and REENTER state. Both live Tradier and V8 must call
   it; vector research must implement the same receipt-bound semantics.
2. Replace hot-path JSON reads with one immutable per-tick configuration/state
   snapshot refreshed by a background mtime/hash watcher. Keep synchronous
   persistence only at acknowledged order/state boundaries.
3. Cache one price and one position snapshot per symbol/account/tick and pass
   them down; prohibit nested `get_current_price()` and repeated full-account
   position scans during the same evaluation.
4. Replace the 2,164-line conditional monolith with a registry of enabled path
   evaluators. Prebind disabled families out of the loop so simulation does not
   execute hundreds of `getattr`/gate checks per bar.
5. Keep long-lived exact worker processes with memory-mapped NPZ/features and
   resettable state rather than interpreter/NPZ/config startup per cell or
   recipe.
6. Add stage timers and event-loop-lag telemetry around snapshot acquisition,
   entry, exit, reclaim and persistence. A performance change is accepted only
   with matched action fingerprints and lower wall time.
7. Content-address code/config/NPZ snapshots. Publish only compact receipts,
   hotlists and workbooks to the Mac; never copy the raw 13.7 GiB research tree
   or NPZ/DB simply to expose conclusions.

The current persistent S1 implementation and exact commands are canonical in
`BACKTEST_BIBLE.md` §16.22B–F and §16.28–§16.30.
