# Exit-engine parity monitor

generated: 2026-10-01T15:08:59.592789+00:00 · lookback: 48.0h

**exit_engine rows in window: 4272** · last: 2026-10-01T15:08:43.994693+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 921
- GOLDEN_RULE: 921

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3351
-  2722  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   524  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    51  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3248, 'OPEN': 1024}
