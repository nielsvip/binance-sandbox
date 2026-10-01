# Exit-engine parity monitor

generated: 2026-10-01T05:08:52.290119+00:00 · lookback: 48.0h

**exit_engine rows in window: 3135** · last: 2026-10-01T05:07:28.679396+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 438
- GOLDEN_RULE: 438

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2697
-  2170  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   478  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    47  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'OPEN': 485, 'CLOSE': 2650}
