# Exit-engine parity monitor

generated: 2026-10-01T07:08:53.044144+00:00 · lookback: 48.0h

**exit_engine rows in window: 3271** · last: 2026-10-01T07:07:51.260740+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 491
- GOLDEN_RULE: 491

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2780
-  2234  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   495  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    49  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 2731, 'OPEN': 540}
