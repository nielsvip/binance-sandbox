# Exit-engine parity monitor

generated: 2026-10-04T20:30:54.863754+00:00 · lookback: 48.0h

**exit_engine rows in window: 5270** · last: 2026-10-04T20:29:58.559921+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 875
- GOLDEN_RULE: 869
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 4395
-  4001  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   236  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    53  BREAK_EVEN_GUARD_EXIT
-    25  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    17  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    12  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     7  MTF_BB_REJECT_1h
-     7  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     6  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     5  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-     3  PPL_TP_gain2.04_URL2_50pct
-     3  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     2  HARDCODED_RALLY_REENTRY_LONG_close0.38
-     2  CRYPTO_SPIKE_FADE_LONG_ret=-12.1%
-     2  EXIT_VELOCITY_WT_D_vel-8.5-against-lon
-     1  HAIKU_WINNER_AUG_3.0pct
-     1  HAIKU_WINNER_AUG_3.3pct
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.9%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt43/57_gain

## actions in window: {'CLOSE': 4353, 'OPEN': 878, 'REDUCE': 22, 'AUGMENT': 13, 'QUICK_REDUCE': 4}
