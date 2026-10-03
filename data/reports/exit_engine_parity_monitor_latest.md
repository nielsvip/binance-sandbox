# Exit-engine parity monitor

generated: 2026-10-03T20:13:34.640667+00:00 · lookback: 48.0h

**exit_engine rows in window: 5745** · last: 2026-10-03T20:12:39.504072+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1115
- GOLDEN_RULE: 1115

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 4630
-  4015  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   234  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-   155  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    90  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    46  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    42  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    12  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     7  MTF_BB_REJECT_1h
-     6  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     5  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     4  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-     3  PPL_TP_gain2.04_URL2_50pct
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.5%
-     1  CRYPTO_SPIKE_FADE_SHORT_ret=+10.2%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain
-     1  HAIKU_WINNER_AUG_3.0pct
-     1  HAIKU_WINNER_AUG_3.3pct
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.9%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt43/57_gain
-     1  PPL_TP_gain1.54_URL2_50pct

## actions in window: {'OPEN': 1243, 'CLOSE': 4461, 'REDUCE': 28, 'AUGMENT': 9, 'QUICK_REDUCE': 4}
