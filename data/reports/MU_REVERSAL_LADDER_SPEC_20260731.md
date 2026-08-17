# MU reversal-ladder preregistration (research only)

Tool: `tools/mu_reversal_ladder_campaign.py`

## Signal contract

- SHORT: completed `dc_low_N` break → subsequent rebound by the tested
  percentage → completed-bar rollover (close deterioration or lower low) →
  short at the next 5-minute open.
- LONG: completed `dc_high_N` break → subsequent pullback → completed-bar
  turn-up → long at the next 5-minute open.
- Timeframes: 5m, 15m, 1h, 4h, D.
- Donchian lookbacks: 5, 10, 20, 30, 55 parent bars.
- Rebound/pullback: 0.5%, 1%, 2%, 4%.
- Rollover/turn-up: 0%, 0.5%, 1%.
- Targets: 1%, 2%, 4%, 8%, 15%; stops: 1%, 2%, 4%, 8%.
- Requested size: 1x, 2x, 4x, 8x, 10x of the `$2,000` base unit.

## Exit variants (registered 2026-08-01)

The campaign now exposes `--exit-timeframes` separately from the parent
break/rebound timeframe.  This is required to compare same-clock and
cross-clock exits without labeling every result as a 5m exit:

- `STRUCTURE`: SHORT exits only after a completed higher-high **and** higher-low
  confirmation; LONG uses the mirrored lower-low and lower-high confirmation.
- `WT`: exits only on the directionally opposing WaveTrend cross at the chosen
  confirmation timeframe.
- `WT_OR_STRUCTURE` and `WT_AND_STRUCTURE` remain combined diagnostics and must
  not be reported as either isolated family.

The supported exit confirmation clocks are 5m, 15m, 1h, 4h and D; execution is
still the next causal 5m open.  The default remains 5m for compatibility, so a
full campaign must explicitly pass `--exit-timeframes 5m 15m 1h 4h D`.
Both SHORT-first and mirrored LONG rows are required.  This is a registration
and test-contract change only; it creates no performance result or matrix/live
credit until an exact V8 adapter reproduces the event fingerprint and ledger.

## Capital and evidence rules

The declared capacity is `$16,000`, so a requested 10x order is filled at
8x and the clamp is recorded; no result may silently use 10x notional. Gain
is reported both on capacity and base-unit denominators. Every trade is in the
receipt ledger, including entry/exit timestamps and reason.

The output is diagnostic until exact V8 replay reproduces the event fingerprint
and ledger. A candidate can enter the pending exact queue only with at least
30 closed trades, positive B&H delta, zero capacity clamps, and no data
contract quarantine. This file does not authorize matrix or live writes.

Expected result root (on the worker):

`data/reports/vec_research/mu_reversal_ladder_MU_<UTC_TIMESTAMP>/`

Files: `result.json`, `RESULTS.md`, `ranked_trade_ledgers.json`, and
`matrix_ingestion_proposal.json` (the latter is always `PENDING_EXACT_V8` and
is not an ingestion action). Summary rows retain a ledger digest; the full
diagnostic ledgers are kept for the top 200 rows to avoid duplicating huge
trade arrays in every all-variation summary row.
