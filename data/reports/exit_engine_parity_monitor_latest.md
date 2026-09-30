# Exit-engine parity monitor

generated: 2026-09-30T12:49:52.593313+00:00 · lookback: 48.0h

**exit_engine rows in window: 6226** · last: 2026-09-30T12:48:39.175570+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 253
- GOLDEN_RULE: 253

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 5973
-  2323  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-  2259  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-   560  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   253  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-   123  BREAK_EVEN_GUARD_EXIT
-    91  MTF_ATR_TRAIL_15m_x2.0
-    17  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-    10  HAIKU_WINNER_AUG_3.1pct
-     7  HAIKU_WINNER_AUG_3.8pct
-     7  HAIKU_WINNER_AUG_4.8pct
-     6  HAIKU_WINNER_AUG_4.1pct
-     5  HAIKU_WINNER_AUG_4.5pct
-     5  HAIKU_WINNER_AUG_4.3pct
-     5  HAIKU_WINNER_AUG_4.6pct
-     4  HAIKU_WINNER_AUG_4.4pct
-     4  HAIKU_WINNER_AUG_3.2pct
-     4  HAIKU_WINNER_AUG_4.2pct
-     4  HAIKU_WINNER_AUG_6.1pct
-     4  HAIKU_WINNER_AUG_4.9pct

## actions in window: {'CLOSE': 5846, 'AUGMENT': 110, 'OPEN': 251, 'REDUCE': 19}
