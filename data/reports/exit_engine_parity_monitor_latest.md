# Exit-engine parity monitor

generated: 2026-10-06T03:02:51.131573+00:00 · lookback: 48.0h

**exit_engine rows in window: 2133** · last: 2026-10-06T02:59:55.221365+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 755
- GOLDEN_RULE: 755

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 1378
-  1090  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    43  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    29  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    12  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    10  EXIT_VELOCITY_WT_1h_vel-32.5-against-l
-     9  EXIT_VELOCITY_WT_4h_vel-34.5-against-l
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     7  EXIT_VELOCITY_WT_4h_vel-27.4-against-l
-     6  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     4  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  EXIT_VELOCITY_WT_4h_vel-42.5-against-l
-     3  CRYPTO_SPIKE_FADE_LONG_ret=-11.0%
-     3  EXIT_VELOCITY_WT_4h_vel-33.0-against-l
-     3  EXIT_VELOCITY_WT_1h_vel-60.7-against-l

## actions in window: {'OPEN': 777, 'CLOSE': 1342, 'REDUCE': 14}
