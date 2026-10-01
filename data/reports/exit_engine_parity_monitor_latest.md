# Exit-engine parity monitor

generated: 2026-10-01T17:09:00.236026+00:00 · lookback: 48.0h

**exit_engine rows in window: 4347** · last: 2026-10-01T17:07:05.932548+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1033
- GOLDEN_RULE: 1033

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3314
-  2695  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   480  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    85  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3177, 'OPEN': 1170}
