# Exit-engine parity monitor

generated: 2026-10-01T11:08:54.310747+00:00 · lookback: 48.0h

**exit_engine rows in window: 3759** · last: 2026-10-01T11:08:29.113072+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 675
- GOLDEN_RULE: 675

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3084
-  2507  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   523  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3032, 'OPEN': 727}
