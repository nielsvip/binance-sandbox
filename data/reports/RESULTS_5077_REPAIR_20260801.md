# Port 5077 repair — canonical matrix results and chart evidence

Date: 2026-08-02

## What was wrong

The stock section of `/results` rendered text copied from
`data/reports/SWITCH_MATRIX_TRB_DIGEST.md`. That digest is useful integrity
metadata, but it is not the logical-cell selector and does not expose the
complete override recipe, ledger status, clamp fields, or source receipt. As
a result a stale digest could make 5077 look stale even while the canonical
matrix reporting source had a different selection.

The main stock chart also only queried the generic run catalogue. A generic
run name such as `cell` does not identify the current matrix winner, so the
chart could query an unrelated or missing trade file and draw no markers.

## Current data contract

| Surface | Source | Allowed evidence | Explicitly excluded |
|---|---|---|---|
| `/results` stock table | `tools/current_matrix_reporting.py` | capital-correct ENGINE row with validated ledger, or a clearly labeled signed active-config matrix receipt when the ledger is remote | old digest values, baseline rows, vector/proxy broadcasts |
| `/current_matrix_best` | same adapter | JSON version of the selected result plus complete hash-checked recipe | any generic run-catalogue fallback |
| `/trb_best_backtest_trades` | `read_best_trades` from the same adapter | exact selected ledger rounds and recorded action events only | inferred returns, vector trades, baseline trades |
| `/` stock chart | `/trb_best_backtest_trades` marker layer | exact entry, re-entry, augment, reduce and exit actions; labeled exact-round endpoints for older ledgers | fabricated marker positions when a ledger is absent |
| `/trb_review` modal | same exact trade endpoint | same action marker classes over visible candlesticks | vec fallback for an exact-matrix claim |

`SWITCH_MATRIX_TRB_DIGEST.md` and its provenance file are retained only to
check current campaign, contract and hash metadata. Their prose is no longer
the numerical source shown in the stock panel.

## Recipe and operational fields

Every selected exact row now carries the complete canonical `overrides_json` recipe
and its SHA-256 status. A receipt hash mismatch hides the recipe rather than
showing a potentially different live configuration. The result table/API also
exposes: evidence tier, campaign, source file, timestamp, trades, closes per
month, TIM, strategy gain/month, B&H/month, delta/month, drawdown, deployed
capital, validation status, contract/trade fingerprints, requested fill ratio,
clamp count and re-entry audit fields. Missing operational values are rendered
as `UNAVAILABLE`, never as zero.

The causal vector-lifecycle table is a separate tier and carries the complete
`recipe` plus `paths`, receipt/result/manifest/raw-result hashes, source-NPZ
identity, source-code identity, verification status, and artifact location.
The HTML no longer truncates this table to 12 keys. Vector rows never replace
exact scalar cells and never supply exact-ledger markers.

## Local verification snapshot

The local reporting adapter currently selects seven signed matrix receipts:
`ACN_SHORT`, `GM_LONG`, `LAC_SHORT`, `MU_LONG`, `NVDA_LONG`, `PLTR_SHORT`,
and `VT_LONG`. Each active-config recipe hash matches its receipt and contains
175–176 override keys. `VT_LONG` and `LAC_SHORT` have locally co-located,
fingerprint-valid exact bundles and are therefore reported as
`ENGINE_EXACT_LEDGER`; the other five remain explicitly
`ENGINE_RECEIPT_REMOTE_LEDGER_UNAVAILABLE:<reason>` until their bundles are
synced. A remote receipt is never silently upgraded to exact evidence.

## Exact marker bundle sync

`tools/sync_current_matrix_ledgers.sh` pulls only the three receipt artifacts
for each currently selected key with a fresh, non-multiplexed SSH connection:
`LAC_SHORT` / `DELTA_EXIT_ENABLED__true` and `VT_LONG` /
`WT_DC_EXIT_ENABLED__true`. It then asserts the canonical endpoint returns
exactly 57 LAC markers and 39 VT markers with a matching ledger count. The
dashboard resolver maps S1 receipt paths only to these deterministic relative
campaign paths and re-validates audit, override and trade fingerprint before
returning events; it never reads an S1 absolute path directly or falls back to
generic/vector/baseline trades.

Transfer verification on 2026-08-02: `/trb_best_backtest_trades` returns
`LAC_SHORT=57` and `VT_LONG=39` exact closed rounds, both `PASS` with matching
ledger counts. Those become 114 and 78 chart actions respectively. VT uses 78
recorded `action_events` (38 entries, one re-entry and 39 exits). LAC's older
ledger predates `action_events`, so its 114 markers are explicitly labeled
`exact_round_endpoint` (41 entries, 16 reason-proven re-entries and 57 exits).
The
artifact comparison permits only the two side-context fields (`LONG_ENABLED`,
`SHORT_ENABLED`) that the matrix runner injects outside the tested override
file; every other override field remains exact-match required.

If a declared `action_events` array is malformed, the endpoint returns no
actions for that round; it does not manufacture endpoint markers around the
bad action ledger. Empty marker responses include per-row artifact status,
expected trade count, readable-ledger count, and action count.

## Candlestick visibility

Both chart surfaces have non-zero minimum chart heights. The main chart now
prints the loaded bar count and symbol/timeframe in its price-pane label,
prints an explicit `NO BARS` diagnostic when klines are unavailable, and calls
`fitContent()` after each load. A stock whose indicator NPZ is not yet present
(currently including LAC on the main `/klines` route) falls back to its honest
on-disk Tradier bar archive for the candlesticks; indicator panes remain blank
rather than being synthesized. `/trb_review` already fits all content and now
draws the same exact action classes as the main chart. No marker is shown
without a corresponding honest ledger price and timestamp.

## Verification

- `pytest -q test_current_matrix_reporting.py test_chart_server_results_contract.py test_lifecycle_receipt_envelope.py test_lifecycle_workbook.py` — 35 passed.
- Both static JavaScript applications parse successfully with Node.
- Flask test client: `/`, `/?sym=VT&class=stocks`, `/results`,
  `/current_matrix_best`, and the VT/LAC/MU exact-trade endpoints returned HTTP
  200. Current payload at verification time: seven exact/receipt rows and 12
  vector-lifecycle rows; lifecycle materialization is still updating this
  data-driven count.
- The in-app browser had no attached tab, so browser-visible screenshot QA was
  unavailable. This is not being represented as a successful visual check.

## Live process state

The Mac LaunchAgent `com.niels.chart-server` is running as PID 1203 and still
serves static files directly from disk, so the candlestick/action-marker UI is
current immediately. This managed session cannot signal or kickstart that PID:
both scoped `launchctl kickstart -k` and scoped `SIGTERM` return
`Operation not permitted`. The Python route changes are fully exercised by the
Flask test client but require the pending LaunchAgent restart before the live
127.0.0.1 process can be claimed current. `tools/restart_mac_chart_server_if_changed.sh`
will perform that scoped restart from the outside-sandbox sync service; its
source-hash receipt must be checked before declaring live completion.
