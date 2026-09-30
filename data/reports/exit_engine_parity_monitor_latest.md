# Exit-engine parity monitor

generated: 2026-09-30T10:49:47.425997+00:00 · lookback: 48.0h

**exit_engine rows in window: 8365** · last: 2026-09-30T10:47:18.673391+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 264
- GOLDEN_RULE: 264

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 8101
-  4247  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-  2398  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   560  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   291  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-   123  BREAK_EVEN_GUARD_EXIT
-    92  MTF_ATR_TRAIL_15m_x2.0
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

## actions in window: {'CLOSE': 7948, 'OPEN': 262, 'AUGMENT': 136, 'REDUCE': 19}
