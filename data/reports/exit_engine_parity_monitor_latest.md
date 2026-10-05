# Exit-engine parity monitor

generated: 2026-10-05T23:02:49.880485+00:00 · lookback: 48.0h

**exit_engine rows in window: 2131** · last: 2026-10-05T23:01:53.828396+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 687
- GOLDEN_RULE: 681
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 1444
-  1184  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    35  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    25  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    11  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    10  BREAK_EVEN_GUARD_EXIT
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     5  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     4  EXIT_VELOCITY_WT_4h_vel-42.5-against-l
-     3  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     3  CRYPTO_SPIKE_FADE_LONG_ret=-11.0%
-     2  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  CRYPTO_SPIKE_FADE_LONG_ret=-12.1%
-     2  EXIT_VELOCITY_WT_D_vel-8.5-against-lon

## actions in window: {'CLOSE': 1407, 'REDUCE': 17, 'OPEN': 707}
