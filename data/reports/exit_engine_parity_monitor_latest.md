# Exit-engine parity monitor

generated: 2026-10-05T18:08:05.064887+00:00 · lookback: 48.0h

**exit_engine rows in window: 3070** · last: 2026-10-05T06:02:53.620557+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 801
- GOLDEN_RULE: 795
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2269
-  1982  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    53  BREAK_EVEN_GUARD_EXIT
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    26  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    25  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    11  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-     7  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     7  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     6  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     5  MTF_BB_REJECT_1h
-     3  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     2  HARDCODED_RALLY_REENTRY_LONG_close0.38
-     2  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  CRYPTO_SPIKE_FADE_LONG_ret=-12.1%
-     2  EXIT_VELOCITY_WT_D_vel-8.5-against-lon

## actions in window: {'OPEN': 804, 'CLOSE': 2239, 'AUGMENT': 5, 'REDUCE': 22}
