# Exit-engine parity monitor

generated: 2026-09-28T10:45:00.267646+00:00 · lookback: 48.0h

**exit_engine rows in window: 4545** · last: 2026-09-28T10:44:56.647959+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 1204
- **MOMENTUM_WATCHDOG**: 1204

top leak reasons:
  -   633  MOMENTUM_WATCHDOG_DC_15m_BREAKOUT_SHOR
  -   184  MOMENTUM_WATCHDOG_DC_D_BREAKOUT_SHORT
  -   169  MOMENTUM_WATCHDOG_DC_1h_BREAKOUT_SHORT
  -   158  MOMENTUM_WATCHDOG_DC_4h_BREAKOUT_SHORT
  -    45  MOMENTUM_WATCHDOG_SMA15M_WT3M_SHORT
  -    11  MOMENTUM_WATCHDOG_SMA15M_WT3M_LONG
  -     4  MOMENTUM_WATCHDOG_DC_D_BREAKOUT_LONG

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 12
- GOLDEN_RULE: 12

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3329
-  2044  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-   758  MTF_ATR_TRAIL_15m_x2.0
-   313  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   146  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    68  STOP_FUNCTIONS_KILL_2 time_since_entry

## actions in window: {'CLOSE': 3261, 'OPEN': 1216, 'REDUCE': 68}
