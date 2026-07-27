# SHORT metric polarity and asymmetry audit — 2026-07-27

## Outcome

The active SHORT stack is not globally upside down. Its executed WT/DC entry
and exit comparators, adverse fills, P&L and side-aware B&H formulas are now
directionally correct. The audit found a more important policy error:
correction shorts and bear-continuation shorts still pass through one common
disaster guard. That guard rejects an up day, RSI at or above 65, bullish D/4h
candles, missing bearish D/4h confirmation and bullish daily WT. Those are
sensible protections for a blind continuation short, but they are the exact
context that arms `TOP_REJECTION_SHORT` in NVDA/MU/SNDK/MRVL/ARM. This is an
accidental inverse-LONG *policy*, not a numeric double inversion.

The bounded vector screen evaluated 24 coherent profile/key combinations in
48 discovery folds. Five correction keys and IBIT had valid causal data; MSTR
and COIN failed closed on their data contracts. MRVL was the only key with a
discovery-strict candidate. Its hash-frozen untouched FINAL then lost
`-6.8872%` on the fixed $2,000 capital unit versus the 0% cash floor. No
candidate reached exact replay, no matrix cell became green and no live
setting changed.

## Complete routed metric classification

The canonical machine-readable map contains 54 metric/family rows in
`METRIC_INVENTORY.json` inside the canonical artifact. It classifies every
metric used by the audited routed stack: active WT/DC entry and exit,
Delta/top-rejection correction, common disaster guard, active variance vetoes,
the two SHORT-native research books, cover management, execution and
reporting. The tracked compact receipt binds its SHA-256.

| class | count | correct SHORT treatment | important examples |
|---|---:|---|---|
| sign-inverted | 8 | reverse direction once | WT `wt1<wt2` for bear entry, BEAR 1h cross, BULL cross / `wt1>wt2` for cover |
| threshold-complemented | 6 | complement bounded threshold, do not negate raw value | Stoch `>60` entry versus `<40` LONG; cover `<=100-K`; DC `>1-threshold` |
| side-neutral / non-inverted | 9 | use raw magnitude | ATR expansion, relative volume, ADX/regime, commission, absolute capacity, account equity |
| genuinely SHORT-native | 31 | use a different state machine | rally extension/top rejection, downside velocity, LH+LL, failed reclaim, low-water trail, squeeze emergency, fixed-unit short B&H |

Two implementation details prevent false diagnoses:

- `wt_dc_entry_scorer.score_entry()` returns through
  `score_entry_multitf()`. The large `_score_short()` mirror below it is a
  live-inert fallback and must not be credited as executed metric coverage.
- the TRC 5m ranker deliberately returns `-raw` for SHORT and then sorts
  descending. A fast negative relative return therefore ranks highly. That is
  one correct sign inversion, not a double inversion.

No wrong active comparator or double negation was found in the active WT/DC
entry/exit path. The earlier reversed SHORT slippage and negative-B&H reporting
defects remain repaired by §15.25 and are covered again by regression tests:
SHORT sells use `raw*(1-slip)`, covers use `raw*(1+slip)`, P&L is
`(entry-cover)*qty`, and fixed-unit short B&H is `(start-end)/start`.

## The structural defects that remain

### One guard still governs two incompatible books

`wt_dc_delta.py:1102-1111` arms a patient correction short at a completed top
only after bearish 1h/micro velocity and high micro Stoch. The common entry
guard then applies all of these continuation vetoes:

- daily gain at least 2.5%;
- RSI 15m or 1h at least 65;
- bullish D or 4h candle while above 15m SMA200;
- no bearish D/4h confirmation;
- bullish daily WT.

The signal asks for an extended bullish state followed by the first breakdown;
the guard demands an established bearish state. A future production repair
must be path-scoped: a named, bounded `TOP_REJECTION` permission can use its own
capacity, max-hold and squeeze emergency without weakening the continuation
guard for generic SHORT entries. This audit deliberately did not change live
policy.

### The tested covers churn on ordinary rebounds

Across all candidate discovery folds, bullish structural reclaim caused
120/170 correction covers (70.6%) and 65/72 bear covers (90.3%). Emergency ATR
covered only four correction trades and no bear trade. This rules out an
emergency-stop inversion as the main loss source. Entries are either too early,
or the current `close > prior completed 1h high + buffer` reclaim is too eager
for the “fast down, slower rebound” holding objective.

The next preregistered research family should distinguish:

1. a genuine squeeze/emergency rise, which covers immediately;
2. an ordinary rebound after a downside impulse, which waits for a lower-top
   failure and resumes/rides the short;
3. a confirmed structural reversal, which covers only after higher-low plus
   higher-high / bullish WT confirmation.

That is a short-native sequence. Merely complementing the LONG trailing stop
would reproduce the churn.

### The bear cohort is not yet data-complete

- MSTR fails closed because 4h/D Stoch coverage is incomplete and required
  4h/D regression fields are absent.
- COIN fails closed because its 15m/1h/4h/D timestamp fields are pre-fix base
  timestamp aliases.
- IBIT is causal and usable, but all four bear profiles failed. Its best row
  lost `-10.1935%` in D1 and made `+6.7996%` in D2 versus a `+17.7800%`
  short-B&H opportunity floor.

MSTR and COIN are data-regeneration tasks, not zero-trade strategy evidence.

## Sealed vector protocol

`tools/run_short_metric_polarity_audit.py` preregisters four coherent profiles
per book instead of sweeping individual fields:

- correction: structural impulse, structural-or-WT volume confirmation,
  rejection wick and ATR-acceleration shock;
- continuation: failed reclaim, structural continuation, ATR-volume break and
  WT-acceleration continuation.

Signals use the shared parent-close availability clock and fills use the first
strictly later availability batch. Each fold uses a $10,000 solvency account,
$2,000 comparison unit, $16,000 hard capacity, 5 bp one-way commission and
2 bp adverse one-way slippage. D1 and D2 must each be solvent, close at least
two trades and beat `max(fixed-$2k short B&H, cash=0)`.

The V2 protocol writes and hashes `PREREGISTRATION.json`,
`METRIC_INVENTORY.json`, the complete `DISCOVERY_GRID.json` and
`DISCOVERY_FREEZE.json` before the first FINAL simulation. It then reloads the
NPZ and verifies its hash before revealing FINAL for a discovery survivor.
Failed keys never call the FINAL simulator. The earlier V1 trial is gray
superseded protocol evidence because its selected row was not externally
frozen before FINAL; V2 is canonical.

## Results

Returns are fixed-$2,000 capital returns; floor is
`max(fixed-unit short B&H, 0% cash)`.

| book | key | passing profiles | selected | D1 return/floor | D2 return/floor | FINAL | verdict |
|---|---|---:|---|---:|---:|---:|---|
| correction | NVDA_SHORT | 0/4 | ATR acceleration / ride | `0.0000/0.0000%` | `-1.5434/0.0000%` | sealed | gray discovery |
| correction | MU_SHORT | 0/4 | structural impulse / fast | `+26.2479/0.0000%` | `-5.2287/0.0000%` | sealed | gray discovery |
| correction | SNDK_SHORT | 0/4 | rejection wick / fast | `0.0000/11.5295%` | `-6.9208/0.0000%` | sealed | gray discovery |
| correction | MRVL_SHORT | 2/4 | ATR acceleration / ride | `+3.6785/0.0000%` | `+13.2263/0.0000%` | `-6.8872/0.0000%` | gray FINAL |
| correction | ARM_SHORT | 0/4 | structure-or-WT volume / balance | `+10.8084/0.0000%` | `+18.4680/31.9473%` | sealed | gray discovery |
| bear | IBIT_SHORT | 0/4 | ATR-volume break / fast | `-10.1935/0.0000%` | `+6.7996/17.7800%` | sealed | gray discovery |

The fleet ingest appended six gray discovery rows after backing up
`queue.db.bak_short_metric_polarity_20260727T040656Z`. They are explicitly
`matrix_eligible=false`, `promotion_allowed=false`; `matrix_written=false`.
The final source-matched rerun backed up the DB again, appended zero and
idempotently skipped all six.

Canonical source-matched full artifact:
`data/reports/vec_research/short_metric_polarity_20260727T041203Z/` on s1.
Tracked receipt:
`data/reports/vec_research/SHORT_METRIC_POLARITY_RECEIPT_20260727.json`.

## Rollback

No live config, symbol list, canonical NPZ or green matrix cell changed.
Rollback is removal of the new runner/tests/document/compact receipt and, if
desired, restoration of the listed fleet DB backup. Do not turn the gray V1 or
V2 evidence green.
