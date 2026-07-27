# VT immutable NPZ lineage audit — 2026-07-27

## Verdict

VT does **not** need another blind canonical NPZ regeneration. The rolling
canonical file has already absorbed every currently retained authentic 5m bar
and is materially better than the frozen July 25 snapshot. A full-history
replacement remains fail-closed because the authoritative inputs still do not
contain April–May 2026 intraday bars.

The negative unchanged-ladder result is nevertheless a real strategy result,
not an artifact of that missing interval:

- the untouched final validation window is 2025-09-02 through
  2026-03-27, before the source hole;
- it contains 13,780 base rows and zero synthetic rows;
- vector return was -61.846244% versus +1.574993% B&H;
- exact v3 replay executed 27/27 actions, found zero future HTF sources and
  reproduced -61.846244% with `5.68e-12` bp accounting error and zero TIM
  delta.

The conclusion is bounded: the accepted ladder/E02 behavior is genuinely poor
on that continuous native window. The missing interval still prevents any
claim about a calendar-continuous two-year VT result.

Nothing in this audit wrote the switch matrix, live configuration, symbol
lists, canonical indicators, or a promotion verdict.

## Immutable artifacts

| label | path on s1 | SHA-256 | rows | synthetic rows | role |
|---|---|---|---:|---:|---|
| frozen | `data/matrix_npz/stocks_repaired_20260725_c2/VT.npz` | `4c7ff8d9008d8a4ad271b70a1413a7c62197ad64122c6d714fcb35b959579cce` | 41,824 | 2,267 | frozen matrix source; no explicit parent field |
| parent clock | `data/npz_recovery/vt_parent_clock_20260727/VT.npz` | `aeb5148fe792950a03b7c248fa3b1282076bf065e0b1a602e87f2f68ea4b320b` | 41,824 | 2,267 | exact-replay research copy |
| rolling | `backtest_v8/indicators/VT.npz` | `8b6f80c0b9031b0f2e58086161c97cfb4ab8f101d933326e497f17a406f71505` | 41,951 | 761 | current rolling archive; not interchangeable with frozen evidence |

The parent-clock copy is array-identical to the frozen file across all 1,053
original arrays. Its sole extra array is
`synthetic_5m_parent_close_ts`. Synthetic lags are exactly 0/300/600 seconds
(756/756/755 rows), matching `ceil(timestamp/900)*900`. The field is
synthetic-only provenance; native execution continues to use the native base
timestamp.

The rolling file is not a mere 127-row append. Continuous collection replaced
1,506 previously interpolated rows with authentic 5m observations and added
127 authentic rows. Consequently, downstream rolling indicators changed only
from 2026-07-01 23:25 UTC onward (about 2,100 warm-down rows, depending on the
field). This is expected native-data improvement, not historical
non-determinism, but it means old exact receipts must remain bound to their
frozen SHA.

## Raw-source coverage and corporate-action checks

| source | SHA-256 | rows | start | end |
|---|---|---:|---|---|
| `VT_5m.json` | `c5b33ac33d4108e265ffb0833d618d4f9fe98fdf66deeb8c483990e80b11d733` | 41,190 | 2024-07-11 11:00 UTC | 2026-07-24 22:00 UTC |
| `VT_15m.json` | `c77b50f3b499c96293e2b628d06f826605558e055b4df920e52400f9bf3624fc` | 787 | 2026-06-30 08:00 UTC | 2026-07-24 22:00 UTC |

Both sources are strictly increasing, duplicate-free, 100% finite OHLCV, have
zero invalid OHLC rows, zero negative-volume rows and zero split-like 0.5x/2x
events. The largest adjacent 5m-source price change is +15.3685%, but it is the
cross-gap comparison from 134.2914 to 154.93—not a one-bar price discontinuity:

`2026-03-30 23:25 UTC -> 2026-06-08 12:30 UTC`

The exact missing span is 6,008,700 seconds (1,669.0833 hours, 69.545 days).
The 15m source starts June 30, so it supplies none of the missing April–May
bars. There is no evidence of an unadjusted split in retained data, but there
is also no authoritative intraday source from which to invent the absent path.

The rolling NPZ matches the raw 5m source on all 41,190 authentic timestamps.
OHLC differences are zero within float32 rounding (`max 7.62e-6`); volume
differences are float32 rounding only. Synthetic share is now 1.814% of all
rows and 0.2273% of RTH rows, down from 3.7635% RTH in the frozen snapshot.

## HTF and indicator integrity

All three NPZs have monotone completed-source timestamps and zero future
source rows for 15m, 1h, 4h and D. The rolling archive contains 15,796/5,589/
1,406/484 unique completed sources respectively.

On the rolling file:

- WT1 is 100% finite at 15m/1h/4h/D with
  15,796/5,587/1,405/483 unique values;
- Stoch K is 99.80%/99.39%/97.80%/93.37% finite after legitimate leading
  warm-up, with 15,122/5,316/1,303/438 unique values;
- Donchian position is 100% finite at all four TFs, with
  11,988/4,967/1,357/466 unique values;
- 1h/4h/D long-regression percent/slope fields are 100% finite and
  non-constant.

Thus VT's old "empty HTF" failure is fixed. The remaining negative native
holdout is strategy behavior—entry/exit timing and sizing—not absent HTF data,
future HTF exposure, a split, reversed prices, or vector/exact disagreement.

## Fail-closed repair sequence

Massive/Polygon must first return the exact missing native range. The downloader
now has an explicit retention-safe repair mode that ignores the stale
"completed" marker but append/merges without deleting any retained timestamp:

```bash
python3 download_stock_klines_5m.py \
  --repair-range --symbols VT \
  --from-date 2026-03-30 --to-date 2026-06-08
```

After that request, rerun the lineage audit. It must report no material source
gap before any rebuild:

```bash
python3 tools/audit_tradier_npz_lineage.py \
  --symbol VT \
  --raw-5m klines_cache_backtest/tradier/VT_5m.json \
  --raw-15m klines_cache_backtest/tradier/VT_15m.json \
  --candidate rolling=backtest_v8/indicators/VT.npz \
  --output data/reports/VT_NPZ_LINEAGE_AUDIT_AFTER_BACKFILL.json
```

Only then build to a new versioned directory; never overwrite the canonical or
frozen file during evaluation:

```bash
python3 backtest_v8_precompute.py \
  --symbol VT --mode tradier \
  --out-dir data/npz_recovery/vt_complete_20260727/backtest_v8/indicators
```

The new NPZ must pass the same lineage audit, the standard ladder data
contract, the unchanged causal ladder negative control, and exact final replay.
Its result gets a new fingerprint. It must not rewrite the old receipt or
promote a matrix cell.

## Evidence and rollback

- Machine receipt:
  `data/reports/VT_NPZ_LINEAGE_AUDIT_20260727.json`
- Prior exact result:
  `data/reports/vec_research/parent_clock_v3_20260727/`
  `v8_exact_ladder_replay_20260727T025047Z_VT_LONG/run_summary.json`
- Audit tool: `tools/audit_tradier_npz_lineage.py`
- Tests: `test_audit_tradier_npz_lineage.py`,
  `test_native_5m_retention.py`,
  `test_backtest_v8_precompute_source.py`,
  `test_backtest_v8_precompute_availability.py`,
  `test_research_availability_clock.py`

Rollback is code-only: remove the read-only lineage tool and its test, remove
`--repair-range`, and remove the optional precompute `--out-dir`. There is no
data rollback because this audit intentionally created no NPZ and mutated no
source or canonical file.
