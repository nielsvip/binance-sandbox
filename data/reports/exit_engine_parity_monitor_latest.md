# Exit-engine parity monitor

generated: 2026-09-28T18:45:02.496540+00:00 · lookback: 48.0h

**exit_engine rows in window: 10389** · last: 2026-09-28T18:44:04.017357+00:00

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

## ✅ GATE_ON (legitimately allowed live, has gate): 70
- GOLDEN_RULE: 70

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 9115
-  6376  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-   848  MTF_ATR_TRAIL_15m_x2.0
-   741  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   606  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-   123  BREAK_EVEN_GUARD_EXIT
-    68  STOP_FUNCTIONS_KILL_2 time_since_entry
-    17  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-    12  HAIKU_WINNER_AUG_3.1pct
-    10  HAIKU_WINNER_AUG_4.1pct
-     5  HAIKU_WINNER_AUG_3.6pct
-     5  HAIKU_WINNER_AUG_4.0pct
-     5  HAIKU_WINNER_AUG_4.6pct
-     4  HAIKU_WINNER_AUG_3.0pct
-     4  HAIKU_WINNER_AUG_3.8pct
-     4  HAIKU_WINNER_AUG_4.4pct
-     4  HAIKU_WINNER_AUG_3.2pct
-     4  HAIKU_WINNER_AUG_6.1pct
-     3  HAIKU_WINNER_AUG_3.9pct
-     3  HAIKU_WINNER_AUG_3.4pct

## actions in window: {'CLOSE': 8932, 'OPEN': 1272, 'REDUCE': 87, 'AUGMENT': 98}
