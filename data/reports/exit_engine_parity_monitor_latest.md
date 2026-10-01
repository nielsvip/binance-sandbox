# Exit-engine parity monitor

generated: 2026-10-01T12:08:54.487724+00:00 · lookback: 48.0h

**exit_engine rows in window: 3867** · last: 2026-10-01T12:08:32.753627+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 745
- GOLDEN_RULE: 745

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3122
-  2538  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   530  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3070, 'OPEN': 797}
