# Exit-engine parity monitor

generated: 2026-10-01T13:08:54.779742+00:00 · lookback: 48.0h

**exit_engine rows in window: 4040** · last: 2026-10-01T13:08:49.825899+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 817
- GOLDEN_RULE: 817

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3223
-  2603  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   537  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    29  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3142, 'OPEN': 898}
