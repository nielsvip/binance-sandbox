# Exit-engine parity monitor

generated: 2026-09-30T15:49:54.494312+00:00 · lookback: 48.0h

**exit_engine rows in window: 3606** · last: 2026-09-30T15:49:25.063954+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 247
- GOLDEN_RULE: 247

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3359
-  2231  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   559  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   237  VIGILANCE_DC4_15m_HARD_STOP_USER_LONG
-    82  MTF_ATR_TRAIL_15m_x2.0
-    70  VIGILANCE_MAX_LOSS_HARD_STOP_USER_LONG
-    64  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    15  RATIO_CLOSE_LONG_L100_S0_tgt38/62_gain
-    12  BREAK_EVEN_GUARD_EXIT
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

## actions in window: {'CLOSE': 3255, 'OPEN': 247, 'REDUCE': 17, 'AUGMENT': 87}
