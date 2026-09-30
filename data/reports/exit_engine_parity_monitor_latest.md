# Exit-engine parity monitor

generated: 2026-09-30T09:49:47.330500+00:00 · lookback: 48.0h

**exit_engine rows in window: 9593** · last: 2026-09-30T09:46:33.104667+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 272
- GOLDEN_RULE: 272

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 9321
-  5342  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-  2445  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   560  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   369  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
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

## actions in window: {'CLOSE': 9168, 'OPEN': 270, 'AUGMENT': 136, 'REDUCE': 19}
