# Agent brief: resolve lifecycle switch and filter attachments

## Objective

Turn every `UNRESOLVED` lifecycle-pilot setting into a proved attachment, or a
documented exclusion. The required hierarchy is:

1. ablation/master switch;
2. lifecycle group (`ENTRY`, `EXIT`, `REENTRY`, `AUGMENT`, or `REDUCE`);
3. individual switch;
4. that switch's settings and sub-settings.

A setting must never be swept while a controlling switch is off. Do not attach
fields from name similarity alone.

## Read first

- `BACKTEST_BIBLE.md` and `data/reports/BACKTEST_BIBLE.md`
- `tools/opt/lifecycle_pilot.py`
- `data/reports/switch_lab_catalog_20260729.json`
- `data/reports/V12_CURATED_CONNECTION_INVENTORY.csv`
- `data/reports/ALL_PATHS_ALLOWLIST.csv`
- `data/reports/V12_LIVE_VECTOR_PARITY_INVENTORY.csv`
- `data/reports/gui_lab/opt/switch_groups.json`

Use the unresolved section of each current
`data/reports/lifecycle_pilot/hierarchy_20260826/*.hierarchy.json` file as the
initial work queue, then use each run's `*.queue.json` for subsequent dynamic
ordering. Work in current historical-priority order, not alphabetical order.

## Evidence required for every attachment

For each unresolved field:

1. Find every declaration and read with `rg`. Trace the read into the actual
   decision site in `v12_quick_engine.py`, `backtest_v8_engine.py`, and the live
   manager. Record file and line evidence.
2. Classify each consuming lifecycle exactly: `ENTRY`, `EXIT`, `REENTRY`,
   `AUGMENT`, `REDUCE`, `SIZING`, or `GLOBAL`. A field may have more than one.
3. Identify the complete activation dependency chain. The closest boolean that
   gates the read is the individual/main switch; continue upward through every
   group or family master. Record the chain in execution order.
4. Classify applicability:
   - `SOME`: exact named parent paths are proved;
   - `ALL`: the engine applies the value to every path in that lifecycle;
   - `ANY`: one shared engine filter can affect any eligible path according to
     runtime conditions, but is not hard-wired to all of them.
5. Run a differential probe on one crypto and one stock side where applicable:
   same frozen data, same live per-symbol baseline, setting A versus B, with all
   required parents explicitly on. Also run the same A/B probe with the nearest
   parent off. The parent-off pair must be inert; the parent-on pair must change
   the exact action/trade fingerprint. If it does not, do not claim attachment.
6. If several parents are plausible, test each independently. Attach only to
   parents for which the on/off interaction is demonstrated. Preserve all raw
   receipts and deltas, including zero and negative results.

The differential probe discovers wiring, not profitability. A negative delta
can still prove the correct attachment. Never select a parent merely because it
produces the best P&L.

## Timeframe and backtest contract

- Do not use synthetic/fake `3m` or `5m` data.
- Use the causal completed-parent `15m` clock used by `lifecycle_pilot.py`.
- Crypto window: final 30 calendar days.
- Stock window: final 20 distinct trading sessions.
- Use complete chronological simulations; do not combine arithmetic deltas.
- Serious runs are S1-only. Keep total RAM below 90% and do not interrupt other
  campaigns.

## Outputs

Create an append-only CSV under `data/reports/lifecycle_pilot/` named
`UNRESOLVED_ATTACHMENT_EVIDENCE_<UTC>.csv` with at least:

`field, lifecycle_consumers, applicability, exact_parents, activation_chain,
crypto_probe, stock_probe, parent_on_fingerprint_changed,
parent_off_inert, delta_samples, engine_read_sites, live_read_sites, status,
reason, tested_at, engine_sha256`

Allowed statuses are:

- `PROVED_SOME`
- `PROVED_ANY`
- `PROVED_ALL`
- `INERT_OR_DEAD`
- `VENUE_INAPPLICABLE`
- `NEEDS_ENGINE_WIRING`
- `INSUFFICIENT_EVIDENCE`

Then update the authoritative catalog/connection/allowlist rows with the proved
consumer, parent, and applicability data. Preserve old evidence and hashes; do
not silently replace it. Add tests showing that `lifecycle_pilot.py` places the
field under the proved hierarchy and that `SwitchTrial.patch()` turns on the
full parent chain before applying the child value.

## Acceptance criteria

- No unresolved field is attached by token overlap or best observed P&L.
- Every resolved setting has static read-site evidence plus an interaction
  probe, or remains fail-closed with an explicit status.
- Entry settings cannot appear under exit/reentry/augment/reduce parents, and
  vice versa, unless multiple consumers are explicitly proved.
- Queue JSON reports exact hierarchy, applicability, evidence source, and all
  still-unresolved fields.
- Unit tests and `git diff --check` pass.
- Do not modify live per-symbol settings and do not promote anything.
