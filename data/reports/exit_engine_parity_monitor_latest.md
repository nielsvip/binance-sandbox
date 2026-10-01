# Exit-engine parity monitor

generated: 2026-10-01T18:09:00.631602+00:00 · lookback: 48.0h

**exit_engine rows in window: 4359** · last: 2026-10-01T18:08:25.549058+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1079
- GOLDEN_RULE: 1079

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3280
-  2672  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   453  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   101  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'CLOSE': 3127, 'OPEN': 1232}
