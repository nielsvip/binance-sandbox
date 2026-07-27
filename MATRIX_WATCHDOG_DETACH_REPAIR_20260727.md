# Exact-matrix watchdog detach repair — 2026-07-27

## Outcome

The repaired exact-matrix lane is again self-healing across engine-contract
fingerprint changes. `rm1..rm3` (`MU_LONG`) and `rv1..rv3` (`VT_LONG`) are six
independently detached sessions, a dead generation cannot keep the cron shell
open, and the following ten-minute cycle can replace it. Reporting still writes
the unsuffixed, current-contract, 12-sheet `SWITCH_MATRIX_TRB.xlsx`.

This repair changes orchestration only. It does not change a strategy, live
configuration, canonical NPZ, matrix result, or promotion verdict.

## Incident

The repaired lane deliberately exits when its source fingerprint changes:

```text
matrix contract changed during engine run; result quarantined
matrix contract source changed after worker start; restart required
```

That fail-closed behavior is correct. The launch wrapper was not. It used
`nohup ... &` followed by `disown`, even though cron runs non-interactive Bash
without useful interactive job control. The worker therefore did not have a
strong independent-session contract. During the rapid source-fingerprint
changes on July 27, successive generations exited and were repeatedly relaunched
while watchdog shells remained coupled to their jobs/reporting lifecycle.

No quarantined result was accepted. The defect was worker continuity, not
result validation.

## Repair

`launch_repaired` now executes:

```bash
setsid -f nohup nice -n 18 ... param_matrix_daemon.py ...
```

`setsid -f` returns control to the watchdog after forking the worker into its
own session. The watchdog no longer uses `&`/`disown` for the repaired lane.
The six exact workers consequently have both `PPID=1` and `SID=PID`.

Three `MATRIX_*` environment overrides provide an isolated regression seam for
the sandbox, Python and log roots. Production cron sets none of them, so the
production defaults remain `/home/niels/binance-sandbox`, the Binance conda
Python and `/home/niels/logs`.

## Regression

`test_watchdog_repaired_launcher.py` uses a private temporary sandbox and a
private `pgrep` view, so it cannot see or interfere with the real S1 fleet. Its
fake exact daemons:

1. record PID, parent PID and session ID;
2. identify their exit as `contract_fingerprint_changed`;
3. exit with status 42;
4. are relaunched by a second watchdog cycle under six new PIDs.

It asserts the first watchdog returns without waiting, every generation has
exactly `rm1..rm3/rv1..rv3`, all workers have `PPID=1` and `SID=PID`, the first
generation is dead before cycle two, and the two PID sets do not overlap.

S1 result:

```text
.
----------------------------------------------------------------------
Ran 1 test in 1.534s

OK
```

The test is intentionally skipped on macOS because Darwin does not provide
Linux `setsid` or `/proc`. It can be run directly on S1 without pytest:

```bash
/usr/bin/python3 test_watchdog_repaired_launcher.py
```

## S1 acceptance audit

`tools/audit_repaired_matrix_fleet.py` is a read-only, fail-closed operational
check. It requires:

- exactly one correctly pinned worker for every `rm1..rm3/rv1..rv3` tag;
- independent `PPID=1`, `SID=PID` worker sessions;
- every repaired-campaign engine/timeout process to descend from one of those
  workers;
- zero claims owned by dead PIDs;
- the exact 12-sheet current canonical workbook contract.

The 2026-07-27 receipt is
`data/reports/MATRIX_WATCHDOG_AUDIT_20260727.json`. It returned `PASS`:

- 6/6 expected workers, all independently detached;
- 8/8 active repaired engine/timeout processes owned by live workers;
- 5 total claims, 0 dead claims;
- 12/12 canonical sheets, size 387,433 bytes;
- no audit failures.

At audit time the current fingerprint's baselines/cells were being rebuilt, so
the workbook correctly showed current-generation coverage rather than reviving
stale prior-generation rows. Historical ENGINE and VEC workbooks remain
separately suffixed by the export collision repair.

## Reproduction and rollback

Health check:

```bash
cd /home/niels/binance-sandbox
/usr/bin/python3 tools/audit_repaired_matrix_fleet.py
```

Rollback is limited to reverting the `setsid -f` launch and the `MATRIX_*` test
seams in `watchdog_lab_matrix.sh`, and removing the test/audit files. Do not
delete the repaired enable marker, result DB, claims DB, workbook, or current
workers as part of rollback.

## Evidence hashes

```text
watchdog_lab_matrix.sh
f5ce5c6af0f6fa3a0fd8ddfb2db054d15c31ec6a3461e03852f0f7e2f9e0d67f

test_watchdog_repaired_launcher.py
3b2f2f1af51d3a7c4199e9cfb591052b5a32573cd9c48d082ef989f148e32226

tools/audit_repaired_matrix_fleet.py
c560a871cebfd054a113134c7ef43ae74b40a72ae2f1230ad4f4f766d8cd16ad

data/reports/MATRIX_WATCHDOG_AUDIT_20260727.json
3a3bd32a783a31690f22433e23cd4311b3279f67c57d3c4a9b879ec4ba8c3700

SWITCH_MATRIX_TRB.xlsx (S1 acceptance snapshot)
cd36a32ed6312c9e16f968e57511ee087f1daf03748fffeeef26fa3a0c59e28c
```
