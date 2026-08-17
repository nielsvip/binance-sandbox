# C4 isolated-recipe repair deployment

Status: code and tests prepared locally. This changes matrix orchestration only;
it does not change `tradier_manage.py`, `backtest_v8_engine.py`, frozen NPZs, or
`SWITCH_MATRIX_INTERDEPENDENCY_20260729.json`, so the c4 engine fingerprint is
unchanged.

## Repaired recipes

- `WT_3M_FORCE`: exact runs seed $2,000 and previously retained the live
  $2,000 build target. The reader therefore had zero room before ENABLED,
  TF_LADDER, or TF_LADDER_MULT could act. The isolated recipe now uses the
  existing $16,000 backtest capacity as a matrix-only target.
- `DELTA_EXIT`: true was paired with `TF=None`, `TYPE=None`, and
  `NOLOSS_MIN_PROFIT_PCT_TRADIER=9999`. The repaired recipe restores
  engine-on, 15m, speed_decay, and 0.01.
- `MTF_EXIT_USE`: this switch is an umbrella with no action of its own.
  Its true probe now enables the already-audited 1h/N=5 MTF DC-reject child.
  False remains a baseline alias.
- Existing WT_DC/STDEV companion repairs remain in force.

## Receipts intentionally invalidated

After deployment, `mtf_companion_receipt_valid()` rejects and same-contract
replacement is allowed for:

- every WT_3M_FORCE family receipt missing
  `WT_3M_FORCE_OPEN_TARGET_USD=16000.0`, BUILD_TO_TARGET=true, or
  USE_SMA200=true;
- every DELTA_EXIT family receipt missing engine=true, TF=15m,
  TYPE=speed_decay, or no-loss=0.01;
- every MTF_EXIT_USE receipt missing the enabled 1h/N=5 DC child;
- the previously documented WT_DC and STDEV incomplete companion receipts.

No numeric result is synthesized. A completed repaired recipe may still be
inert when the frozen symbol/side has no qualifying event; that remains a
legitimate domain plateau.

## Controlled deployment

1. Let the adaptive vector cycle and digest update finish.
2. Stop matrix-daemon workers and their watchdog; let child exact engines
   finish or terminate them through the existing controlled worker stop.
3. Deploy `tools/param_matrix_daemon.py` and its focused test only.
4. Run `pytest -q test_param_matrix_speed_gates.py`.
5. Confirm the c4 fingerprint before/after deployment is identical.
6. Release claims belonging to stopped worker PIDs.
7. Start one worker and prioritize:
   WT force ENABLED/TF_LADDER/MULT, DELTA enabled, and MTF compound on the
   pilot keys.
8. Inspect each stored `overrides_json` for the companion receipt and require
   a causal counter/result difference only where frozen events exist.
9. Restart the remaining workers after the one-worker receipts pass.

Rollback is the inverse daemon-only change followed by a controlled worker
restart. Existing repaired receipts retain their exact recipe in
`overrides_json`; do not relabel or delete them.
