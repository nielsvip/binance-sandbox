# Full param-value assessment worklist — EVERY value that needs assessing

**13320 value-cells across 3760 params.** Backtestable: 2103. Ever-tested (any value touched in history): 83 (0.6%). **Untested: 13237 (99.4%).**

- **crypto**: 6877 cells / 2001 params · backtestable=1167 · tested_ever=20 · long/short params=383 (untested L/S params=383)
- **tradier**: 6443 cells / 1759 params · backtestable=936 · tested_ever=63 · long/short params=322 (untested L/S params=320)

## How each non-backtestable bucket gets assessed (honest)
- **BACKTESTABLE**: covered by the OFAT grind on S1 (NPZ, representative syms). The only set we can sweep faithfully.
- **FORWARD_TEST (live-only)**: the live code reads these but NO backtest engine path exists — they cannot be swept; they must be judged on forward live results. Many long/short gates live here.
- **DEAD_REVIEW**: referenced in no engine/live source — verify truly dead and remove; sweeping them = 0-effect lying rows (banned).

Sorted so long/short / direction / ratio / hedge params come FIRST. Filter the CSV: `is_long_short=Y` + `tested_ever=''` = the L/S settings hurting you that have never been assessed.
