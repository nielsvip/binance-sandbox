# Repaired SWITCH_MATRIX_TRB 24/7 lane — 2026-07-25

## Why the old rows are historical

Every ENGINE row through `2026-07-25T02:02:43Z` predates the July 25 closed-HTF,
delayed-top, MTF-clock and accounting repairs. The old `stocks_baseline_v2_s4h`
runner also:

- simulated LONG and SHORT together;
- did not seed the required $2,000 B&H position;
- summed trade percentages despite variable quantities and partial closes;
- compared that sum with a 100%-notional price return;
- reused cells across campaigns and source fingerprints.

Those rows remain in SQLite as evidence, but the digest cannot count or rank them as current.

## Current row contract

Campaign: `stocks_repaired_20260725_c2`. The c1 rows are preserved as
mutable-input diagnostics: the idle NPZ regeneration job changed their byte fingerprint while
the campaign was running, so c1 can never count as current.

A current row must have all of:

1. timestamp on or after `2026-07-26T04:15:00Z`;
2. exact SHA-256 contract fingerprint over symbol, side, an immutable campaign NPZ snapshot,
   fixed end-exclusive date (`2026-07-25`), engine, Tradier manager,
   WT/delta, MTF timing, re-entry and data-contract sources;
3. `V8_LADDER_INITIAL_BH_SEED` as the first position;
4. only the requested side opened;
5. valid closed-HTF NPZ contract;
6. nonzero sizing telemetry, at least 90% requested/fill ratio and zero clamps;
7. zero pending/violating mandatory re-entries;
8. complete capital-weighted metrics.

Runs with no real close are retained as `INCOMPLETE_NO_REAL_CLOSE`; they prove the floor but
cannot be ranked or promoted. A changed trade fingerprint is required for a non-inert
candidate. Below-B&H results remain gray/rejected and are never promoted.

Accounting is now apples-to-apples: realised plus partial plus final-MTM `pnl_usd` divided by
$10,000 accounting capital. B&H deploys $2,000 and pays the same round-trip cost. Strategy
capacity remains $16,000 (8x the benchmark unit).

## S1 worker state

Frozen inputs live under
`data/matrix_npz/stocks_repaired_20260725_c2/{MU,VT}.npz`. Workers pass that directory
explicitly to the engine; background regeneration of `backtest_v8/indicators` therefore cannot
invalidate or alter a c2 row.

The original `data/GRID_SUSPENDED` marker remains in place. It still prevents every legacy
param/lab/vector/combo lane from launching.

Only `data/MATRIX_REPAIRED_ENABLE` authorizes:

- `rm1..rm3`: `MU_LONG`, nice 18;
- `rv1..rv3`: `VT_LONG`, nice 18.

Each engine waits for at least 5 GB available RAM. The existing `*/10`
`watchdog_lab_matrix.sh` cron self-heals these six workers and refreshes the spreadsheet and
digest. No live trading config or process is touched.

`HAO_SHORT` is excluded. Its quarantine report is
`data/reports/MATRIX_QUARANTINE_HAO_SHORT.json`; HTF timestamps still alias the base bar,
required regression fields are absent, daily stochastic is incomplete, and the source has a
65.2% discontinuity. It must be regenerated and pass the exact data contract before a worker
can be added.

## Verified live state

At `2026-07-26T04:45:06Z` the digest counted 22 c2 ENGINE rows and excluded 6,622
historical/c1 ENGINE rows. The latest timestamps continued advancing while the `*/10`
`idle_npz_regen.sh` cron remained enabled.

Frozen hashes:

```text
MU  f57ce885f72655026e594ce93fe883f61c20eab647568ee91ee5d7a4e9973fb3
VT  4c7ff8d9008d8a4ad271b70a1413a7c62197ad64122c6d714fcb35b959579cce
```

The first repaired MU baseline is deliberately red:
`PASS_WITH_CAPACITY_CLAMPS`, 3,181 real closes, 22.39% TIM, +1.4135%/month versus
+4.7610%/month B&H. Clamp-bearing runs are stored so the matrix can discover the settings
that fix them, but the digest/exporter exclude them from best/promotable candidates.

The first repaired VT baseline is structurally clean but losing:
498 real closes, -0.2820%/month versus +0.2744%/month B&H. The result confirms the current
function stack churns in the wrong direction; it is not an old LONG/SHORT-mixed or
percentage-summed artifact.

## Rollback

Compute-only rollback, without touching any result:

```text
mv /home/niels/binance-sandbox/data/MATRIX_REPAIRED_ENABLE \
   /home/niels/binance-sandbox/data/MATRIX_REPAIRED_ENABLE.disabled
pkill -f 'param_matrix_daemon.py --tag rm'
pkill -f 'param_matrix_daemon.py --tag rv'
```

The cron will then see `GRID_SUSPENDED` and run reporting only. Existing repaired rows remain
recoverable in `param_results_stocks.db`. Source rollback is the parent of the Git commit that
adds this document and the repaired runner files. Do not delete either the database rows or
the HAO quarantine artifact.
