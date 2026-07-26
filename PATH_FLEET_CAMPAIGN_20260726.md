# Top/Bottom Stock Path Fleet

This campaign assigns one bounded research job to every actionable ENTRY or
EXIT family. The frozen cohort is the ten strongest recent stocks as LONG and
the ten weakest recent stocks as SHORT, ranked directly from source NPZ closes.
The cohort is regenerated only between campaigns, never while a job is running.

The point-in-time `symbols_trb_long.json` and `symbols_trb_short.json` contents
and hashes are frozen in the manifest. Top LONGs are selected only from the
LONG list; bottom SHORTs only from the SHORT list. Off-universe NPZs are
diagnostic only. NPZs older than 14 days or with less than 60 days of observed
history are excluded. Adjusted close is preferred; raw-close histories with a
near-integer split discontinuity or any adjacent move over 80% are quarantined
instead of being allowed to define the cohort.

The queue imports the authoritative `TRADIER_ENTRY_PATHS` and
`TRADIER_EXIT_PATHS` tables. All 24 entry rows and all 40 exit rows must be
retained or initialization fails. Rows with the same event family and actual
config switch are grouped as aliases; rows controlled by different switches
remain separate jobs. Source reason, file/line, function, switch, default, and
category are preserved in the JSON registry.

The queue is implemented by `tools/path_fleet_campaign.py`. Agents atomically
claim one family from SQLite, so two workers cannot silently duplicate or
overwrite a path. The generated `PATH_FLEET_REGISTRY.json` contains a plain
description, parameter ranges, runner, adapter state, frozen entry, and frozen
exit control for every path. `PROGRESS.md` is the human-readable rolling ledger.

## Comparison contract

Every path follows the same sequence:

1. vectorized discovery on chronological training folds;
2. freeze one range/setting before observing the next fold;
3. untouched OOS validation, separately for each symbol and side;
4. exact `backtest_v8_engine` replay for survivors.

For exits, beating B&H is not enough. A candidate must also beat the strongest
frozen result with the *identical ladder entry/fill schedule*. This prevents an
exit from looking good merely because its test entered at a different time.
Entries use the same E02 4h N=30 exit and mandatory lower/resting reclaim.

The accounting unit is $2,000 B&H versus up to $16,000 strategy capacity. LONG
and SHORT never share P&L. Completed higher-timeframe source timestamps must be
at or before the execution-bar observation timestamp. Native 5m is used where
available; documented historical 15m interpolation remains allowed.

## Fail-closed adapter rule

Some existing fast exit runners begin fully invested. They are useful diagnostic
scanners, but they are not the accepted band ladder and therefore cannot answer
the same-entry question. Those jobs are marked `ADAPTER_REQUIRED` until they can
consume the frozen ladder schedule. They must not populate promotion cells in
`PARAM_BASELINE_STOCKS` or `SWITCH_MATRIX_TRB`.

The initial runnable job is the authentic LONG ladder + E02 control cohort.
Structure and alternative E02 settings remain blocked until the accepted entry
schedule is frozen, because re-selecting entries while testing an exit would
invalidate the comparison. The SHORT mirror, WT_DC repair lane, Golden Rule
weights, structural lower-top exit, WT exits, and partial-runner paths remain
explicit queue items rather than disappearing from the workbook.

## Commands

On S1:

```bash
python3 tools/path_fleet_campaign.py init --replace \
  --npz-dir backtest_v8/indicators --lookback-days 180 --top 10 --bottom 10
python3 tools/path_fleet_campaign.py status
python3 tools/path_fleet_campaign.py claim --worker agent-name
python3 tools/path_fleet_campaign.py report
```

Result ingestion is schema-checked and fail-closed:

```bash
python3 tools/path_fleet_campaign.py add-result /path/to/result.json
```

A discovery row labelled `PASS` is automatically downgraded to
`RESEARCH_ONLY`. Promotion requires untouched OOS, exact replay, zero future HTF
observations, positive alpha versus B&H, and positive alpha versus the
same-entry control.

## 2026-07-26 entry overlay screens

The causal entry-overlay adapter has now screened Golden Rule, repaired WT_DC,
and structural Stoch HH/HL against frozen ladder+E02 controls. Golden Rule and
WT_DC contributed 10 LONG untouched-OOS rows each. Structural Stoch contributed
10 top-cohort LONG plus 10 bottom-cohort SHORT rows using:

- LONG: completed HH+HL with low and rising Stoch;
- SHORT: completed LH+LL with high and falling Stoch;
- K thresholds 15/20/25/30/35/40 on 1h/4h/D;
- one or two confirming TFs; direct and union-with-green roles.

All three path jobs are `SCREENED` in `data/reports/path_fleet/queue.db`.
Structural Stoch produced zero candidates that passed B&H, the identical
ladder+E02 control, every validation fold, and 70–80% weighted TIM together.
Exact replay was therefore not launched. The 20 Stoch rows remain gray evidence
in `ENTRY_STOCH_HHHL_TOP_BOTTOM10_20260726.{json,md}`. PBF_LONG beat both return
comparisons in every fold but reached only 46.88% weighted TIM; low-exposure
PBF/MPC/VLO now route to ladder multiplier/trigger tuning rather than another
Stoch threshold sweep.

### DELTA_MTF entry

`ENTRY_DELTA_MTF` was traced to the actual direct-entry path before testing.
It counts side-favorable WT velocity/acceleration on completed
5m/15m/1h/4h/D bars, applies the 1h delta red-zone, and can require mirrored
4h price/Heikin-Ashi structure. The 24-setting causal sweep covered one through
four favorable TFs, optional structure, and a research-only 0.25/0.50/0.75
directional-retention ratio. That ratio is not `DELTA_EXIT_DECAY_RATIO`, which
is an exit-only live knob; no live configuration was changed.

All ten top LONG and ten bottom SHORT keys completed untouched-OOS screening.
No key passed B&H, the identical ladder+E02 control, mandatory reclaim, zero
future-HTF observations, and 70–80% weighted TIM in every fold together. The
fleet job is `SCREENED`; all 20 results are retained gray in
`ENTRY_DELTA_MTF_TOP_BOTTOM10_20260726.{json,md}`.

VLO_LONG exposed an acceptance bug worth preserving as a regression. Its
aggregate TIM was 74.34%, while fold TIM was 33.68%/89.00%/96.55%. An exact V8
diagnostic reproduced the current-engine schedule and accounting, including
96.55% last-fold TIM, but remained non-promotable. Vector acceptance now
requires every validation fold—not only the aggregate—to remain in the
70–80% band, and dedicated tests prevent aggregate exposure from hiding
fold-level failures.

### Trend-resume augment inventory repair

The authoritative inventory listed `AUGMENT_TREND_RESUME_ENABLED`, but the
active stock config and `tradier_manage.py` contain neither that switch nor the
`Augment_Trend_Resume` reason. Active `evaluate_augment` now implements a
different DC-tier path. The inventory item was therefore disconnected, not an
untested live switch.

For recoverable evidence, the last real implementation in
`backups/before_desktop_tradier_fixes_20260721.py` was reconstructed as a
research-only adapter. It adds to an already-open profitable position when
price is on the favorable side of the 5m Donchian basis, Stoch K/D is aligned,
and 5m RSI is not exhausted. The 32-setting sweep covered:

- minimum gain 0.5%/1%/2%/3%;
- mirrored RSI boundaries 60/40, 70/30, 80/20, and disabled 100/0;
- additive 0.25x/0.5x `START_POSITION_SIZE`.

The frozen 20-key cohort completed with zero future-HTF observations and no
capacity breaches. Zero rows passed B&H, the identical ladder+E02 control, and
70–80% weighted TIM in every fold. TTD_SHORT beat both return comparisons in
every fold, but its aggregate TIM was 89.79%, so it remains gray and exact
replay was not launched. All results are preserved in
`ENTRY_AUGMENT_TREND_RESUME_TOP_BOTTOM10_20260726.{json,md}`. The active live
path remains unchanged; reconnecting it requires an explicit implementation
decision after a viable OOS setting exists.

### Donchian-break stale switch and causal reconstruction

`ENTRY_DC_BREAK_ENTRY_ENABLED` was not a live knob. The inventory named
`DC_BREAK_ENTRY_ENABLED`, but `config_tradier.py` declares no such setting and
`tradier_manage.py` never reads it. The old swing branch instead reads
`DC_BREAK_ENTRY_DISABLED` with fail-closed default `True`. A separately
launched `StockDaytradeWing` reads `DC_DAYTRADE_ENABLED` /
`TRADIER_DC_DAYTRADE_ENABLED`; it must not be confused with the stale row.
Job 50 therefore retains 20 red wiring rows with zero requests/fills.

A research-only causal reconstruction screened 192 combinations over all
frozen top-10 LONG and bottom-10 SHORT keys: 5m/15m/1h/4h prior-channel breaks,
0/0.05/0.10/0.20% buffers, optional 1h channel expansion, no/exhaustion/
directional-Stoch confirmation, and direct/union-with-green roles. All rows
used the frozen ladder sizing, $16k capacity, E02 N30, costs, next-RTH fills,
and mandatory reclaim. The screen produced real requests and fills on all 20
keys with zero future-HTF, capacity, or reclaim violations, but zero strict
survivors. MU_LONG beat both controls in every fold but missed the exposure
policy (68.29% aggregate and at least one failed fold); MRVL_LONG met aggregate
TIM and aggregate controls but failed a control fold. No exact replay or live
change followed.

The original job-50 ingestion also exposed a reporting-unit bug: sums of three
outer validation folds were labeled `VEC_UNTOUCHED_OOS`. Those 20 historical
rows remain untouched. An append-only normalization added 20 explicitly scoped
`VEC_NESTED_FOLD_AGGREGATE` rows and 20 true final chronological
`VEC_UNTOUCHED_OOS` rows, each with return/TIM/trade units and aggregation
metadata. The pre-migration DB backup is
`queue.db.bak_job50_metric_scope_20260726T2035Z`; the migration is idempotent.
Full evidence is in `ENTRY_DC_BREAK_TOP_BOTTOM10_20260726.{json,md}`.
