# Exit-engine parity monitor

generated: 2026-09-28T13:45:00.775354+00:00 · lookback: 48.0h

**exit_engine rows in window: 7625** · last: 2026-09-28T13:44:58.070994+00:00

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

## ✅ GATE_ON (legitimately allowed live, has gate): 41
- GOLDEN_RULE: 41

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 6380
-  4782  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-   763  MTF_ATR_TRAIL_15m_x2.0
-   364  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   354  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    68  STOP_FUNCTIONS_KILL_2 time_since_entry
-    10  HAIKU_WINNER_AUG_4.1pct
-     5  HAIKU_WINNER_AUG_4.0pct
-     5  HAIKU_WINNER_AUG_3.1pct
-     4  HAIKU_WINNER_AUG_3.6pct
-     4  HAIKU_WINNER_AUG_3.8pct
-     3  HAIKU_WINNER_AUG_4.4pct
-     2  HAIKU_WINNER_AUG_3.9pct
-     2  HAIKU_WINNER_AUG_3.0pct
-     2  HAIKU_WINNER_AUG_3.4pct
-     2  HAIKU_WINNER_AUG_4.6pct
-     2  HAIKU_WINNER_AUG_3.2pct
-     2  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-     1  HAIKU_WINNER_AUG_4.3pct
-     1  HAIKU_WINNER_AUG_4.8pct
-     1  HAIKU_WINNER_AUG_4.9pct

## actions in window: {'CLOSE': 6263, 'OPEN': 1245, 'REDUCE': 70, 'AUGMENT': 47}
