# SHORT guard decomposition protocol — frozen 2026-07-27

This is a research-only preregistration. It does not change `config_tradier`,
live symbol files, or order routing.

## Question

Which parts of the shared disaster guard prevent a fully qualified
`TOP_REJECTION_SHORT`, and does bypassing those parts improve an asymmetric
correction-short book without weakening emergency/solvency controls?

## Invariants that cannot be ablated

- valid NPZ/data contract;
- completed source timestamp no later than observation;
- first strictly later availability-batch fill;
- $16,000 hard capacity from a $2,000 unit;
- ATR emergency cover;
- account solvency and drawdown below 100%;
- commission and slippage;
- actual completed-1h bearish rollover: LH+LL, close below prior low, WT1<WT2.

Operational live safeguards such as broker-state synchronization remain outside
the vector simulator and are not candidates for bypass.

## Conflicting veto components

`DAY_GAIN`, `RSI_15M`, `RSI_1H`, `BULL_D_CANDLE`, `BULL_4H_CANDLE`,
`NO_BEAR_HTF_CONFIRM`, and `BULL_D_WT`.

The frozen profiles are:

1. all vetoes;
2. seven single-component ablations;
3. overbought block (`DAY_GAIN + RSI_15M + RSI_1H`);
4. bullish-state block (`D/4h candle + HTF confirm + D WT`);
5. full conflict bypass, scoped only to a qualified top plus causal rollover.

No other combination is searched.

## Selection and acceptance

Each profile chooses one bounded entry/scale/fast-cover setting using D1
(2024-03-26→2025-07-01) and D2 (2025-07-01→2026-01-01) only. FINAL
(2026-01-01→2026-07-25) remains untouched until selection freezes.

Every discovery and final fold requires positive fixed-$2,000-unit capital
return, at least two actual exits, solvency, drawdown below 100%, zero future
completed-source observations, the invariant emergency controls, and capital
return above the side-specific short B&H opportunity floor (`max(short B&H,
cash=0)`). Account return remains separately normalized by the $10,000
solvency ledger. Final reporting includes raw strategy,
realized cash, open MTM, cash=0, fixed-notional short B&H, long B&H opportunity,
drawdown, TIM, correction capture, exit-reason counts and emergency covers. A
multiple is undefined when short B&H is nonpositive.

Only a solvent all-fold survivor may enter exact replay. All other completed
rows are gray and retained idempotently.

## Post-freeze contract correction

The first artifact exposed two fail-closed defects before promotion:

- SHORT entry/scale and cover slippage signs were reversed in the vector
  simulator. All 85 fleet rows that exercised that simulator are marked
  `INVALIDATED_REVERSED_SLIPPAGE`.
- the initial discovery predicate required positive cash return but did not
  require beating the fixed-$2,000 opportunity benchmark in every discovery
  fold. The repaired predicate does. ARM G21 therefore fails before spec
  emission.

The only reconciled exact receipt is LRCX G14. Its result, hashes, emergency
cover and rollback evidence are in
`SHORT_GUARD_DECOMPOSITION_RESULTS_20260727.md`.
