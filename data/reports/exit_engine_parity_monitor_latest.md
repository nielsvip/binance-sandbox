# Exit-engine parity monitor

generated: 2026-09-28T08:44:59.057268+00:00 · lookback: 48.0h

**exit_engine rows in window: 1918** · last: 2026-09-28T08:44:58.386774+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 1037
- **MOMENTUM_WATCHDOG**: 1037

top leak reasons:
  -   538  MOMENTUM_WATCHDOG_DC_15m_BREAKOUT_SHOR
  -   158  MOMENTUM_WATCHDOG_DC_D_BREAKOUT_SHORT
  -   143  MOMENTUM_WATCHDOG_DC_1h_BREAKOUT_SHORT
  -   138  MOMENTUM_WATCHDOG_DC_4h_BREAKOUT_SHORT
  -    45  MOMENTUM_WATCHDOG_SMA15M_WT3M_SHORT
  -    11  MOMENTUM_WATCHDOG_SMA15M_WT3M_LONG
  -     4  MOMENTUM_WATCHDOG_DC_D_BREAKOUT_LONG

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 0

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 881
-   666  MTF_ATR_TRAIL_15m_x2.0
-   128  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    65  STOP_FUNCTIONS_KILL_2 time_since_entry
-    22  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all

## actions in window: {'CLOSE': 816, 'OPEN': 1037, 'REDUCE': 65}
