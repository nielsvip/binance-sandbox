# Stocks backtesting recovery — original request and acceptance criteria

This file preserves the user’s requested job so it remains part of the repository and can be
applied to the `ez_` crypto system after stocks are proven.

## Requested work

1. Document the work already done to `binance_sandbox` on S1 so the same or equivalent changes
   can be reproduced in the `ez_` crypto backtesting system.
2. Complete `binance/data/reports/SWITCH_MATRIX_TRB` for every tradeable key. Add a clear human
   description of every entry/exit path, including what switching it on/off does, dependencies,
   off value, scope and wiring status.
3. Fix the current zero-trade backtests and document which scripts are required besides
   `backtest_v8_engine.py` for realistic results.
4. Restore vectorized shortcuts wherever fidelity permits so numbers arrive quickly instead of
   running every symbol through slow real functions.
5. Replace field-by-field filling with an interaction-aware method: every surviving recipe must
   be recalculated when an open/close path changes because paths affect each other.
6. Start from the latest accepted per-symbol test/config, presumed closest to live behavior.
7. Target roughly 70–80% time in market for the top/bottom ten keys and 30–50% for the remaining
   keys, while retaining only results that beat or are acceptably close to legal B&H.
8. Fill known matrix fields from measured tests, identify tested/unexplored paths, vary parameters
   where a path is promising, and test newly discovered settings.
9. Assign a repair lane for red cells so every relevant `tradier_config` knob is connected;
   hedge and options must not be silently discarded.
10. Pilot `MU_LONG`, `VT_LONG` and `HAO_SHORT`; fill viable blank paths, keep below-B&H results
    gray, and do not retest gray rejects without a new reason.
11. Keep rollback/application notes so the proven stocks method can be ported to `ez_` crypto.
12. Maintain a repository/runbook suitable for renting compute that can read/write S1 only after
    a known-cell reproducibility check.

## Non-negotiable measurement rules

- A floor is valid only when it has an observed open, zero real closes, final MTM, at least 99%
  exposure, and return reconciliation to first-tradable B&H. `closes==0` by itself is invalid.
- `backtest_v8_engine.py` is Tier-2 decision evidence. `v8_vec_sweep.py` and
  `tools/vec_exposure_ladder.py` are Tier-1 shortlist tools only.
- Vector candidates require a faithful Tier-2 replay before promotion.
- A red result means wiring/measurement failure, not poor strategy performance. A gray result is
  measured but below B&H, outside the exposure band, or non-robust and should be discarded.
- The matrix workbook is generated from the result database. Workers write cells; one controlled
  exporter writes the CSV/workbook.
- No backtest-only change may restart or modify live trading processes.

## Current implementation state

- MU_LONG, VT_LONG and HAO_SHORT have corrected Tier-2 floors.
- The matrix exporter emits 3,522 actionable switch-value rows across 129 keys, descriptions on
  every row, and a separate Inventory sheet for non-actionable settings.
- The recalculating ladder/beam method and vector shortlist are implemented; MU_LONG DC_LOW4 is
  the first high-value Tier-2 replay in the current optimization pass.
- Hedge/options remain a separate fidelity lane until their execution and valuation are modeled.
