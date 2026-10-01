# Exit-engine parity monitor

generated: 2026-10-01T21:19:23.531601+00:00 · lookback: 48.0h

**exit_engine rows in window: 4500** · last: 2026-10-01T21:17:48.966416+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1286
- GOLDEN_RULE: 1286

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3214
-  2627  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   404  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   126  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    52  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-     1  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     1  TRADIER_IMPULSE_REENTRY_PRICE_CROSS_BA
-     1  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre

## actions in window: {'OPEN': 1465, 'CLOSE': 3033, 'REDUCE': 2}
