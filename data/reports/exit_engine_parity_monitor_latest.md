# Exit-engine parity monitor

generated: 2026-10-01T14:08:57.087204+00:00 · lookback: 48.0h

**exit_engine rows in window: 4182** · last: 2026-10-01T14:08:39.381728+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 865
- GOLDEN_RULE: 865

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3317
-  2679  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   539  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    45  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3220, 'OPEN': 962}
