# Exit-engine parity monitor

generated: 2026-10-01T03:08:51.899044+00:00 · lookback: 48.0h

**exit_engine rows in window: 3012** · last: 2026-10-01T03:05:45.064029+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 427
- GOLDEN_RULE: 427

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2585
-  2119  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   464  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric

## actions in window: {'OPEN': 427, 'CLOSE': 2585}
