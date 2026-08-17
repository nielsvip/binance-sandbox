# Independent TRB win-rate audit — 2026-08-01

> Result: no ledger-certified WR is available on this Mac. The referenced `cell__*.jsonl` ledgers live on the remote sweep host and are not mounted here. `raw_returns` contains closed-trade returns plus a possible final MTM row; its full-trace sign percentage is shown only as a diagnostic and is not WR.

## Findings

- The four apparent 50.00% values (MU, NVDA, ACN, PLTR) each came from a two-element trace with one positive and one negative value while `real_closes=1`; the V8 engine appends a final `MTM_FINAL_BAR_NOLIES_RULE2` row for an open position. Excluding that final MTM row gives an engine-derived closed-trace estimate of 0/1 = **0.00%**, not 50.00%.
- The latest apply log contains longer traces for VT (39 values/38 closes) and LAC (57 values/56 closes); their engine-derived closed-trace sign estimates are 9/38 = 23.68% and 54/56 = 96.43%, respectively. These are still not a substitute for the co-located ledger and must not be labeled certified WR.
- MU, NVDA, ACN, GM, PLTR and LAC currently have active fingerprints matching their latest apply snapshot; VT has snapshot drift (active config remains the older 3-trace C4 cell while the latest apply log is a 39-trade C5 cell). Reports must keep these snapshots separate.

## Per-key reconciliation

| key | active real closes | active raw trace (+/-/0; sign diagnostic) | engine closed estimate | latest audit closes | latest raw trace (+/-/0; sign diagnostic) | ledger WR | status |
|---|---:|---|---:|---:|---|---:|---|
| MU_LONG | 1 | 1/1/0 (50.00% diagnostic) | 0.00% (n=1) | 1 | 1/1/0 (50.00% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| NVDA_LONG | 1 | 1/1/0 (50.00% diagnostic) | 0.00% (n=1) | 1 | 1/1/0 (50.00% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| ACN_SHORT | 1 | 1/1/0 (50.00% diagnostic) | 0.00% (n=1) | 1 | 1/1/0 (50.00% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| VT_LONG | 2 | 1/2/0 (33.33% diagnostic) | 0.00% (n=2) | 38 | 10/29/0 (25.64% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| GM_LONG | 1 | 2/0/0 (100.00% diagnostic) | 100.00% (n=1) | 1 | 2/0/0 (100.00% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| PLTR_SHORT | 1 | 1/1/0 (50.00% diagnostic) | 0.00% (n=1) | 1 | 1/1/0 (50.00% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |
| LAC_SHORT | 56 | 55/2/0 (96.49% diagnostic) | 98.21% (n=56) | 56 | 55/2/0 (96.49% diagnostic) | — | UNVERIFIABLE_LEDGER_NOT_MOUNTED |

## Correction rule

The scorecard and digests must render `WR=—`/`N/A` whenever the matching ledger cannot be opened and reconciled to the `trades_fingerprint`. A count of positive values in the full `raw_returns` trace must never be promoted to a win rate. The engine-derived closed estimate above is safe only because V8's source appends one final `MTM_FINAL_BAR_NOLIES_RULE2` row when `len(raw_returns) == real_closes + 1`; it remains provisional until the ledger confirms action labels. Once the remote ledger is available, compute WR as `closed trades with pnl_pct > 0 / closed trades`, excluding all open/mark-to-market rows and preserving the exact fingerprint.

Machine-readable output: `/Users/niels/Documents/binance/data/reports/wr_independent_audit_20260801.json`.
