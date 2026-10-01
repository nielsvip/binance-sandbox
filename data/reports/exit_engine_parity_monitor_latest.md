# Exit-engine parity monitor

generated: 2026-10-01T19:19:22.155019+00:00 · lookback: 48.0h

**exit_engine rows in window: 4417** · last: 2026-10-01T19:19:17.052815+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1167
- GOLDEN_RULE: 1167

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3250
-  2652  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   441  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   101  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-     1  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     1  TRADIER_IMPULSE_REENTRY_PRICE_CROSS_BA

## actions in window: {'CLOSE': 3095, 'OPEN': 1321, 'REDUCE': 1}
