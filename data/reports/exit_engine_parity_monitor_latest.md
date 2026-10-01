# Exit-engine parity monitor

generated: 2026-10-01T04:08:52.121264+00:00 · lookback: 48.0h

**exit_engine rows in window: 3020** · last: 2026-10-01T04:08:38.658584+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 415
- GOLDEN_RULE: 415

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2605
-  2132  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   471  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'OPEN': 415, 'CLOSE': 2605}
