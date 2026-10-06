# Exit-engine parity monitor

generated: 2026-10-06T04:02:51.552454+00:00 · lookback: 48.0h

**exit_engine rows in window: 2321** · last: 2026-10-06T04:01:56.951667+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 64
- **QUICK_OPEN_STRONG**: 64

top leak reasons:
  -    54  QUICK_OPEN_STRONG_BUY
  -    10  QUICK_OPEN_STRONG_SELL

→ For each: if the path is GOOD (profitable), add its twin to v12_quick_engine + set the gate default ON in config so best cat_side stays default. If BAD, close the code gate so it honors the disabled knob.

## ✅ GATE_ON (legitimately allowed live, has gate): 758
- GOLDEN_RULE: 758

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 1499
-  1140  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-    39  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    29  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    28  EXIT_VELOCITY_WT_4h_vel-10.2-against-l
-    28  EXIT_VELOCITY_WT_1h_vel-21.6-against-l
-    27  EXIT_VELOCITY_WT_1h_vel-52.1-against-l
-    19  EXIT_VELOCITY_WT_1h_vel-40.3-against-l
-    15  EXIT_VELOCITY_WT_1h_vel-56.8-against-l
-    13  EXIT_VELOCITY_WT_4h_vel-1.1-against-lo
-    12  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    10  EXIT_VELOCITY_WT_1h_vel-32.5-against-l
-     9  EXIT_VELOCITY_WT_4h_vel-34.5-against-l
-     7  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  EXIT_VELOCITY_WT_1h_vel-72.6-against-l
-     7  EXIT_VELOCITY_WT_4h_vel-27.4-against-l
-     6  [HANDLE_SIGNAL]:BEARISH_sma_crossunder
-     5  QUICK_BB_RECOVERY_EXIT_TRADIER_LONG
-     4  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     4  EXIT_VELOCITY_WT_4h_vel-42.5-against-l
-     4  VEC_DRIVEN_OPEN_WT_DC

## actions in window: {'CLOSE': 1416, 'OPEN': 803, 'REDUCE': 22, 'AUGMENT': 2, 'QUICK_OPEN': 64, 'STRONG_REDUCE': 7, 'NO_PROFIT': 5, 'SCALP_REDUCE': 2}
