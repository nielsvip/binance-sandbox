# Exit-engine parity monitor

generated: 2026-10-01T16:08:59.855286+00:00 · lookback: 48.0h

**exit_engine rows in window: 4304** · last: 2026-10-01T16:05:14.818668+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 972
- GOLDEN_RULE: 972

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3332
-  2699  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   497  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    82  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3198, 'OPEN': 1106}
