# Exit-engine parity monitor

generated: 2026-09-30T08:49:47.173871+00:00 · lookback: 48.0h

**exit_engine rows in window: 10809** · last: 2026-09-30T08:48:10.616637+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 54
- **MOMENTUM_WATCHDOG**: 54

top leak reasons:
  -    29  MOMENTUM_WATCHDOG_DC_15m_BREAKOUT_SHOR
  -    10  MOMENTUM_WATCHDOG_DC_1h_BREAKOUT_SHORT
  -     8  MOMENTUM_WATCHDOG_DC_D_BREAKOUT_SHORT
  -     7  MOMENTUM_WATCHDOG_DC_4h_BREAKOUT_SHORT

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 276
- GOLDEN_RULE: 276

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 10479
-  6376  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-  2457  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   558  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   462  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-   123  BREAK_EVEN_GUARD_EXIT
-   113  MTF_ATR_TRAIL_15m_x2.0
-    17  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-    12  HAIKU_WINNER_AUG_3.1pct
-    11  HAIKU_WINNER_AUG_4.1pct
-     8  HAIKU_WINNER_AUG_3.8pct
-     8  HAIKU_WINNER_AUG_4.8pct
-     7  HAIKU_WINNER_AUG_4.6pct
-     6  HAIKU_WINNER_AUG_4.0pct
-     6  HAIKU_WINNER_AUG_4.3pct
-     6  HAIKU_WINNER_AUG_4.4pct
-     5  HAIKU_WINNER_AUG_3.6pct
-     5  HAIKU_WINNER_AUG_4.9pct
-     5  HAIKU_WINNER_AUG_4.5pct
-     4  HAIKU_WINNER_AUG_3.9pct

## actions in window: {'OPEN': 328, 'CLOSE': 10326, 'AUGMENT': 136, 'REDUCE': 19}
