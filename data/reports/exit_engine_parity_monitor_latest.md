# Exit-engine parity monitor

generated: 2026-10-04T07:50:25.827677+00:00 · lookback: 48.0h

**exit_engine rows in window: 6179** · last: 2026-10-04T07:08:10.736170+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 963
- GOLDEN_RULE: 957
- ALL_TF_AGAINST: 6

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 5216
-  4776  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   236  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    53  BREAK_EVEN_GUARD_EXIT
-    46  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    25  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    17  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-    12  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-     9  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     9  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     7  MTF_BB_REJECT_1h
-     3  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     3  PPL_TP_gain2.04_URL2_50pct
-     3  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  HARDCODED_RALLY_REENTRY_LONG_close0.38
-     2  CRYPTO_SPIKE_FADE_LONG_ret=-12.1%
-     1  CRYPTO_SPIKE_FADE_SHORT_ret=+10.2%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain
-     1  HAIKU_WINNER_AUG_3.0pct
-     1  HAIKU_WINNER_AUG_3.3pct
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.9%

## actions in window: {'CLOSE': 5167, 'OPEN': 966, 'REDUCE': 29, 'AUGMENT': 13, 'QUICK_REDUCE': 4}
