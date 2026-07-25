# VT Tradier NPZ regeneration — 2026-07-25

Scope: rebuild only `backtest_v8/indicators/VT.npz` on s1. No matrix cell was
written, no live file was promoted, and no live process was restarted.

## Provenance and source audit

The authoritative server source directory was
`/home/niels/binance-sandbox/klines_cache_backtest/tradier`.

| TF | rows | start | end | span | finite OHLCV | max consecutive jump |
|---|---:|---|---|---:|---:|---:|
| real 5m | 39,557 | 2024-07-11 11:00 UTC | 2026-07-01 19:40 UTC | 720.4d | 100% | 15.3685% |
| real 15m | 787 | 2026-06-30 08:00 UTC | 2026-07-24 22:00 UTC | 24.6d | 100% | 1.2321% |

The real 5m file was selected for HTF construction because its span is much
longer. Real observations win on overlap. The newer 15m tail was interpolated
to 5m only where real 5m observations were absent; the output carries
`synthetic_5m` provenance. Final regular-session interpolated share is 3.763%.

The 5m source has one material internal gap: 2026-03-30 23:25 UTC through
2026-06-08 12:30 UTC, 1,669.083 hours (69.5 days). This is a coverage gap, not
a corporate action. The independent daily VT cache contains continuous prices
through that period: daily close moves from 134.19 on March 30 through the
140s/150s during April and May to 154.48 on June 8. There are no source jumps
over 30%. The apparent 15.3685% consecutive-5m move is the comparison across
the missing interval, not a single executable bar.

Consequently, the rebuilt NPZ is valid on every available bar and now includes
the recent tail, but a full-window replay still skips the missing April–May
calendar interval. The data contract reports this as a warning.

## Generator defects fixed

1. A short 15m file previously remained the mandatory HTF source and zeroed
   almost all of VT's long 5m history. Source selection now prefers 5m whenever
   the 15m wall-clock span is below 80% of the 5m span.
2. The merge previously depended only on row count. VT's 5m file had more rows
   but ended July 1, so it discarded the 15m tail through July 24. Tradier
   precompute now always merges 15m-derived coverage with real 5m, preserving
   every authentic overlap and marking every interpolated row.
3. Tradier 1h/4h/D availability uses the preceding fully closed left-labelled
   aggregate. Warm-up rows are empty, not clipped to future row zero.
4. Fifteen-minute bars rebuilt from close-labelled 5m data are now
   right-labelled/right-closed. The authentic 15m tail wins overlap. Previously
   pandas left-labelled the rebuilt aggregate while the broadcaster treated
   15m as close-labelled, exposing the current interval at its start.
5. Coverage validation now permits a contiguous leading indicator warm-up of
   at most 10% while still rejecting internal NaN gaps. VT daily Stoch has one
   legitimate leading warm-up and no later NaN gap.

Regression tests:

- `test_backtest_v8_precompute_source.py`
- `test_backtest_v8_precompute_availability.py`
- `test_backtest_data_contract.py`

## Exact field coverage before and after

Cells are `finite % / nonzero % / unique values`. `MISSING` means the key did
not exist in the old NPZ.

| field | before | after |
|---|---:|---:|
| wt1_15m | 100 / 0.255 / 41 | 100 / 99.998 / 15,688 |
| stoch_k_15m | 0.134 / 0.134 / 21 | 99.797 / 99.615 / 15,019 |
| dc_position_15m | 100 / 0.255 / 41 | 100 / 98.233 / 11,971 |
| wt1_1h | 100 / 0.255 / 15 | 100 / 99.995 / 5,585 |
| stoch_k_1h | 0 / 0 / 0 | 99.386 / 99.283 / 5,317 |
| dc_position_1h | 100 / 0.255 / 15 | 100 / 98.984 / 4,972 |
| wt1_4h | 100 / 0.225 / 6 | 100 / 99.902 / 1,405 |
| stoch_k_4h | 0 / 0 / 0 | 97.788 / 97.784 / 1,303 |
| dc_position_4h | 100 / 100 / 6 | 100 / 99.715 / 1,359 |
| wt1_D | MISSING | 100 / 99.603 / 483 |
| stoch_k_D | MISSING | 93.346 / 93.145 / 438 |
| dc_position_D | MISSING | 100 / 99.534 / 466 |
| lrL_pct_b_1h | 100 / 100 / 1 | 100 / 95.706 / 5,091 |
| lrL_slope_1h | 100 / 0 / 1 | 100 / 96.313 / 5,388 |
| lrL_pct_b_4h | MISSING | 100 / 92.543 / 894 |
| lrL_slope_4h | MISSING | 100 / 73.362 / 1,007 |
| lrL_pct_b_D | MISSING | 100 / 94.831 / 259 |
| lrL_slope_D | MISSING | 100 / 60.284 / 285 |

Rows increased from 39,557 to 41,824; unique base closes increased from 13,113
to 14,361. The NPZ grew from 11,234,290 to 18,200,642 bytes.

Old HTF timestamp aliases equalled the base timestamp on 100% of rows. After
regeneration the equality rates are 34.165% for 15m, 9.153% for 1h, 2.577% for
4h, and 0.045% for D; these are natural base rows coincident with a real
availability instant. Future-availability rate is 0% for all four.

## Commands and rollback

```text
python tools/audit_tradier_klines_source.py --symbol VT
cp -p backtest_v8/indicators/VT.npz /tmp/VT.npz.pre_regen_20260725
python tools/backtest_data_contract.py --symbol VT --profile ladder --json
python -m unittest -v test_backtest_v8_precompute_source.py \
  test_backtest_v8_precompute_availability.py
python backtest_v8_precompute.py --symbol VT --mode tradier
python -m unittest -v test_backtest_data_contract.py
python tools/backtest_data_contract.py --symbol VT --profile ladder --json
```

Rollback artifact:
`/tmp/VT.npz.pre_regen_20260725` on s1.

Final contract result: `valid=true`, zero errors. Audit JSONs:

- `/tmp/VT_source_audit_20260725.json`
- `/tmp/VT_contract_before_20260725.json`
- `/tmp/VT_contract_after_20260725.json`

Final NPZ SHA-256:
`4c7ff8d9008d8a4ad271b70a1413a7c62197ad64122c6d714fcb35b959579cce`.
Rollback SHA-256:
`76695cdf693ecd5d38847c191941440c4d6e204a0f027c1c3430c114bb154a2b`.

## Post-regeneration faithful-engine stage-0 proof

These were direct Tier-2 engine runs with side isolation, all strategy entries
and exits disabled, one first-RTH `V8_LADDER_INITIAL_BH_SEED`, $10,000
accounting capital, a $2,000 benchmark unit, and $16,000 strategy capacity. No
matrix function or result database was called. `data/GRID_SUSPENDED` remained
present.

| window | net capital return | gross tradable B&H | x B&H | TIM | opens L/S | real closes | MTM | requested/fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-06-10 12:30 UTC–2026-06-30 22:30 UTC | +0.6321787% | +0.6441787% | 0.9813716x | 99.7959% | 1 / 0 | 0 | 1 | 1x/1x; 1.0000 |
| 2024-07-11 11:00 UTC–2026-07-24 22:00 UTC | +6.5606440% | +6.5726440% | 0.9981743x | 99.9860% | 1 / 0 | 0 | 1 | 1x/1x; 1.0000 |

The short engine seed was the first legal RTH bar at 2026-06-10 13:30 UTC:
152.6999969, final mark 157.6183014. The full seed was 2024-07-11 13:30 UTC:
116.2850037, final mark 154.50. Both have zero size clamps, opposite-side
fills, pending re-entries, re-entry overshoot violations, and real exits. The
short run took 11.689 seconds; full history took 235.098 seconds.

Actual bounded-window synthetic shares were 8.9242% of all short-window rows
and 6.9355% of its RTH rows. Full history was 5.4203% synthetic across all rows
and 3.7635% during RTH. The full replay retains the disclosed 69.5-day source
gap and is therefore not calendar-continuous.

One preliminary diagnostic deliberately matters: leaving the accepted entry
paths active after injecting the B&H seed allowed `WT_3M_FORCE_OPEN` to request
another 3x augmentation. Its requested/fill ratio fell to 0.3643 with one
clamp, so the result was quarantined. A valid stage-0 floor must disable
strategy entries as well as exits and then inject exactly one benchmark unit;
`real_closes == 0` alone is not sufficient.

S1 artifacts:

- `/tmp/vt_stage0_gapfree_real5m_20260725.json`
- `/tmp/vt_stage0_postregen_gapfree_real5m_20260725`
- `/tmp/vt_stage0_full_20260725.json`
- `/tmp/vt_stage0_postregen_full_20260725`
