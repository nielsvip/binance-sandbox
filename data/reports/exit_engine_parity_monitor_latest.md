# Exit-engine parity monitor

generated: 2026-10-01T08:08:53.232786+00:00 · lookback: 48.0h

**exit_engine rows in window: 3404** · last: 2026-10-01T08:08:48.611058+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 536
- GOLDEN_RULE: 536

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2868
-  2312  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   502  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 2816, 'OPEN': 588}
