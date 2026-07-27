# SHORT guard decomposition and exact-replay result — 2026-07-27

## Outcome

The original SHORT vector evidence is invalidated, not gray. Its simulator
gave SHORT sells and covers favorable slippage. The repaired contract is:

- LONG open `raw*(1+slip)`, LONG close `raw*(1-slip)`;
- SHORT open/scale `raw*(1-slip)`, SHORT cover `raw*(1+slip)`.

`tools/research_fill_contract.py` is the canonical Python formula and
`test_research_fill_contract.py` checks all four side/action combinations.
An audit of the vector kernels found the other side-aware Python and C
families already adverse: band ladder, trend augment, top-exit, partial
regime, same-entry structural/partial/peak-giveback, and entry/exit beam.
The structural-WT retest probe is LONG-only and its formulas are correct.
The affected families were `vec_asymmetric_short_campaign.py` and its
`vec_short_guard_decomposition.py` consumer.

The fleet was backed up before mutation. Eight asymmetric rows and 77
guard-decomposition rows are now
`INVALIDATED_REVERSED_SLIPPAGE`. Eleven zero-trade bear-cohort guard rows did
not exercise a fill and remain gray. The latest backup is
`data/reports/path_fleet/queue.db.bak_short_slippage_20260727T025628Z`.

## Benchmark and denominator repair

The strategy may deploy up to $16,000, B&H uses one fixed $2,000 unit, and
the $10,000 account is the solvency/DD ledger. Therefore:

- `capital_return_pct = total P&L / $2,000`, used against fixed-unit B&H;
- `account_return_pct = total P&L / $10,000`, used for solvency;
- a candidate must beat `max(short B&H, cash=0)` in D1 and D2 before FINAL;
- when short B&H is nonpositive, no B&H multiple is reported.

The initial selector checked positive account return but not the opportunity
floor in every discovery fold. Tests now fail a row that makes +7% on the
fixed unit against +31% short B&H. This invalidates ARM G21: its corrected
D2 still fails the fixed-$2,000 benchmark, so the adapter refuses to emit an
exact spec. The earlier mechanically matching ARM bundle
`v8_exact_short_guard_20260727T024633Z_ARM_G21_BULL_STATE_BLOCK` is
`INVALIDATED_DISCOVERY_BENCHMARK` and is not evidence.

## Reconciled LRCX G14 result

Only `LRCX_SHORT / G14_BULL_D_CANDLE` passed the corrected frozen candidate
contract:

| fold | capital return ($2k) | account return ($10k) | short B&H | floor | exits | binary TIM |
|---|---:|---:|---:|---:|---:|---:|
| D1 | +12.6289% | +2.5258% | +1.2458% | +1.2458% | 9 | 0.6356% |
| D2 | +0.1901% | +0.0380% | -77.3967% | 0% cash | 2 | 0.6997% |
| FINAL | +9.6534% | +1.9307% | -69.9969% | 0% cash | 8 | 4.2142% |

The canonical exact bundle is
`data/reports/vec_research/v8_exact_short_guard_20260727T025514Z_LRCX_G14_BULL_D_CANDLE/`.
The exact engine executed 25/25 actions with no refusal or mismatch:

- total P&L $193.0681;
- fixed-unit capital return +9.6534061902%;
- account return +1.9306812380%;
- accounting deltas 0 bp (capital) and approximately `-1e-12` bp (account);
- binary TIM 4.2142468733% and weighted TIM 0.7631200384%, both zero delta;
- peak post-fill notional $8,000 under the $16,000 cap;
- eight closed lifecycles and one ATR emergency cover;
- zero future HTF sources; first-strictly-later availability fills;
- NPZ SHA-256
  `e993b5aa7a74b50a9f57b48b1502ed51eebf42916258d4361cbeb9e77bf99055`;
- schedule SHA-256
  `249e0dbae8b84af33194866d2445c7848fc16cc416dac00d6ba375b2aedc9199`.

It is `EXACT_PARITY_ONLY`: `matrix_written=false`,
`promotion_allowed=false`. The older LRCX receipt is
`SUPERSEDED_ACCOUNTING_AUDIT`.

The first exact attempt also exposed an engine-only integration error: the
new private reason prefix was not included in the research replay allowlist,
so ordinary augmentation sizing reduced two scheduled adds to 25%. The engine
correctly failed schedule, TIM, and accounting parity. Adding the private
backtest-only prefix restored the scheduled quantities. No live router
recognizes this prefix.

## Why the SHORT books remain separate

These results support a correction book that is patient in a bullish regime,
not a generic inverse-LONG strategy. A separate continuation book should
activate only after a confirmed bearish state. This is also consistent with
[Daniel and Moskowitz, *Momentum Crashes*](https://www.nber.org/papers/w20439):
loser-short momentum risk is concentrated in panic/high-volatility rebounds,
and conditional timing materially improves the static strategy. [Daniel,
Jagannathan and Kim, *Tail Risk in Momentum Strategy
Returns*](https://www.nber.org/papers/w18169) describes the short leg as
option-like and highly levered in turbulent rebound states. These papers
motivate the split; immutable empirical backtests remain the acceptance
authority.

## Rollback

Code rollback is removal/reversion of the SHORT fill helper, exact adapter and
runner, the engine's private replay-kind dispatch/prefix, selector changes,
tests, and this documentation. Data rollback is restoring a listed fleet DB
backup and deleting only the `v8_exact_short_guard_*` research directories.
Do not restore the invalidated vector statuses as valid evidence.
