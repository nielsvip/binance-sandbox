# Exit-engine parity monitor

generated: 2026-10-02T04:29:25.703791+00:00 · lookback: 48.0h

**exit_engine rows in window: 4647** · last: 2026-10-02T04:27:09.390741+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1469
- GOLDEN_RULE: 1469

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3178
-  2543  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   385  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   191  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     2  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     2  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-     1  TRADIER_IMPULSE_REENTRY_PRICE_CROSS_BA

## actions in window: {'CLOSE': 2930, 'OPEN': 1713, 'REDUCE': 4}
