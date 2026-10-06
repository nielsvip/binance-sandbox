# Exit-engine parity monitor

generated: 2026-10-06T01:02:50.591025+00:00 · lookback: 48.0h

**exit_engine rows in window: 2058** · last: 2026-10-06T01:02:40.487778+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 763
- GOLDEN_RULE: 757
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 1295
-  1018  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    49  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    29  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    12  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-     9  EXIT_VELOCITY_WT_4h_vel-34.5-against-l
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     7  EXIT_VELOCITY_WT_4h_vel-27.4-against-l
-     4  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     4  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  EXIT_VELOCITY_WT_4h_vel-42.5-against-l
-     3  CRYPTO_SPIKE_FADE_LONG_ret=-11.0%
-     3  EXIT_VELOCITY_WT_4h_vel-33.0-against-l
-     2  EXIT_VELOCITY_WT_D_vel-8.5-against-lon
-     2  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre

## actions in window: {'CLOSE': 1268, 'OPEN': 779, 'REDUCE': 11}
