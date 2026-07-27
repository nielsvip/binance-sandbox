# Switch-matrix export collision repair — 2026-07-27

## Outcome

`SWITCH_MATRIX_TRB.xlsx` is reserved for the current repaired ENGINE contract.
Explicit historical campaigns now write campaign-suffixed artifacts such as:

`SWITCH_MATRIX_TRB_ENGINE_HIST_STOCKS_BASELINE_V2_S4H.xlsx`

VEC evidence remains isolated in:

`SWITCH_MATRIX_TRB_VEC_DIAGNOSTIC.xlsx`

## Root cause

A long-lived remote reporting session repeatedly launched:

`tools/export_switch_matrix_xls.py --campaign stocks_baseline_v2_s4h`

The exporter previously used the canonical filename for every ENGINE campaign.
Consequently, a valid 12-sheet current-contract workbook was replaced minutes
later by the historical campaign's 10-sheet workbook. The result database was
not damaged; only the generated report artifact was wrong.

Process evidence captured on s1:

- historical exporter PID `3936000`;
- parent remote session PID `2115484`;
- command started `2026-07-27T02:06:23Z`;
- historical write moved into the canonical filename at
  `2026-07-27T02:07:02Z`.

## Repair

`tools/export_switch_matrix_xls.py` now selects output names by evidence tier
and campaign:

- current ENGINE campaign: canonical unsuffixed filename;
- explicit historical ENGINE campaign: `_ENGINE_HIST_<CAMPAIGN>`;
- VEC: `_VEC_DIAGNOSTIC`.

The repaired current export was then rebuilt under the watchdog lock. It had
12 sheets, including `Engine Coverage` and `Exact Engine Evidence`. The
historical export remained available as a separate 10-sheet workbook.

Focused verification:

`python3 -m pytest -s -ra test_switch_matrix_engine_coverage.py test_switch_matrix_descriptions.py`

Result: 8 passed.

## Rollback

Revert the `output_suffix` change only if every external historical report job
has first been stopped or changed to use an explicit noncanonical output path.
Otherwise rollback will restore the overwrite race.
