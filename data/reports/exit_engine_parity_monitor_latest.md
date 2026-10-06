# Exit-engine parity monitor

generated: 2026-10-06T02:02:50.790620+00:00 · lookback: 48.0h

**exit_engine rows in window: 2022** · last: 2026-10-06T02:02:27.186864+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 752
- GOLDEN_RULE: 752

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 1270
-   993  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    43  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
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
-     6  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     4  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  EXIT_VELOCITY_WT_4h_vel-42.5-against-l
-     3  CRYPTO_SPIKE_FADE_LONG_ret=-11.0%
-     3  EXIT_VELOCITY_WT_4h_vel-33.0-against-l
-     2  EXIT_VELOCITY_WT_D_vel-8.5-against-lon
-     2  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre

## actions in window: {'OPEN': 774, 'CLOSE': 1234, 'REDUCE': 14}
