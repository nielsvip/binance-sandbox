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
