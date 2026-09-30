# Exit-engine parity monitor

generated: 2026-09-30T14:49:54.183691+00:00 · lookback: 48.0h

**exit_engine rows in window: 4542** · last: 2026-09-30T14:46:18.934922+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 236
- GOLDEN_RULE: 236

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 4306
-  2273  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   831  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-   560  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-   184  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    82  MTF_ATR_TRAIL_15m_x2.0
-    35  BREAK_EVEN_GUARD_EXIT
-    15  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-     7  HAIKU_WINNER_AUG_3.1pct
-     7  HAIKU_WINNER_AUG_4.8pct
-     5  HAIKU_WINNER_AUG_4.3pct
-     5  HAIKU_WINNER_AUG_4.6pct
-     4  HAIKU_WINNER_AUG_4.2pct
-     4  HAIKU_WINNER_AUG_4.5pct
-     4  HAIKU_WINNER_AUG_6.1pct
-     4  HAIKU_WINNER_AUG_4.9pct
-     4  HAIKU_WINNER_AUG_3.8pct
-     3  HAIKU_WINNER_AUG_4.4pct
-     3  HAIKU_WINNER_AUG_5.4pct
-     3  HAIKU_WINNER_AUG_5.3pct

## actions in window: {'CLOSE': 4202, 'OPEN': 234, 'AUGMENT': 89, 'REDUCE': 17}
