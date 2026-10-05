# Exit-engine parity monitor

generated: 2026-10-05T19:08:05.395755+00:00 · lookback: 48.0h

**exit_engine rows in window: 2926** · last: 2026-10-05T19:06:33.135403+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 783
- GOLDEN_RULE: 777
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 2143
-  1842  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    53  BREAK_EVEN_GUARD_EXIT
-    29  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    25  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    11  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-     7  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     5  MTF_BB_REJECT_1h
-     4  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     3  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     3  CRYPTO_SPIKE_FADE_LONG_ret=-11.0%
-     2  HARDCODED_RALLY_REENTRY_LONG_close0.38
-     2  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  CRYPTO_SPIKE_FADE_LONG_ret=-12.1%

## actions in window: {'CLOSE': 2103, 'OPEN': 800, 'AUGMENT': 4, 'REDUCE': 19}
