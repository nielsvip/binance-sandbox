# Exit-engine parity monitor

generated: 2026-10-02T18:39:40.312534+00:00 · lookback: 48.0h

**exit_engine rows in window: 5078** · last: 2026-10-02T18:39:07.194032+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1691
- GOLDEN_RULE: 1691

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 3387
-  2748  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   338  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-   191  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    86  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    12  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     2  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     1  DELTA_EXIT_DC15M_FLOOR_BREAK
-     1  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-     1  TRADIER_IMPULSE_REENTRY_PRICE_CROSS_BA
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.5%
-     1  CRYPTO_SPIKE_FADE_SHORT_ret=+10.2%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain

## actions in window: {'CLOSE': 3088, 'OPEN': 1971, 'REDUCE': 19}
