# Audit of `research_20260725_1511.md`

## Verdict

The accepted trade source does **not** have a LONG/SHORT sign inversion. Its reported return matches the correct side-aware price formula on every accepted row. The report is nevertheless invalid for strategy decisions because it pooled both sides and every symbol, labeled pooled decision-tree leaves by only their dominant side, and presented scale-weighted raw dollars as if they described a common strategy.

- Source rows: 13,700
- Accepted: 13,698
- Rejected malformed rows: 2 (CSV source rows [4777, 11514])
- Accounts: 70; symbols: 179
- Max reported/recomputed return error: 0.00005000 pp
- PnL sign mismatches: 0
- PnL-implied notional within 1% of reported notional: 99.93%

## Correct side-separated recomputation

| Scope | Trades | Win rate | Mean return/trade | Median return/trade | Raw PnL (context only) |
|---|---:|---:|---:|---:|---:|
| ALL_SYMBOLS LONG | 7,659 | 49.5% | -2.337% | -0.040% | $-813,930.53 |
| ALL_SYMBOLS SHORT | 6,039 | 57.9% | +1.239% | +0.282% | $+154,969.38 |

If the side were inverted, both mean-return signs would reverse. That counterfactual disagrees with the reported PnL signs and prices; it is not the source defect.

## Why the headline loss looked extreme

The worst source account contributed $-456,552.68, or 69.3% of the pooled net raw loss. Raw cash PnL weights a $449k BTC position far more than a $1k altcoin position. It is a source reconciliation field, not a return.

## Exact generator defects

- compare_to_our_system pooled symbols and LONG/SHORT regime dollars
- decision-tree patterns were trained on pooled sides and merely labeled dominant_side
- email findings[:15] omitted the later SIDE provenance line
- TrackerLogIngester coerced missing/unknown sides to SHORT
- Bitget closed-order parser coerced missing/unknown sides to SHORT
- report omitted symbol/account/position-side/order-side trace fields
- regime membership was not persisted per trade, preventing exact retrospective regime reconciliation
- merged CSV contained two malformed concatenated rows; permissive ingestion silently skipped them

## Corrective contract

Every research row must carry `source_account`, `symbol`, `position_side`, `entry_order_side`, `exit_order_side`, entry/exit prices, reported PnL, recomputed side-aware return, and formula. Missing or unknown identity now fails closed. Patterns, regimes, summaries, and emails must state `ALL_SYMBOLS` or a concrete symbol and must never pool LONG with SHORT.

**Promotion eligible: no. Live/config/matrix writes: none.**
