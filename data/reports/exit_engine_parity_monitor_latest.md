# Exit-engine parity monitor

generated: 2026-10-03T23:13:38.844252+00:00 · lookback: 48.0h

**exit_engine rows in window: 5987** · last: 2026-10-03T23:12:51.083657+00:00

## 🔴 LEAKS (gate-disabled families that fired = illegal trades): 0
- none — no gate-disabled family fired. ✅ parity holds for mapped exits.

## ✅ GATE_ON (legitimately allowed live, has gate): 1002
- GOLDEN_RULE: 1002

## ❔ UNMAPPED (no gate mapping — candidate good-paths to evaluate): 4985
-  4441  ALL_ALL_GREEN_DIRECT_CLOSE_winners_all
-   236  DAYTRADE_TARGET_dc_15m_high_-0.10%_TAR
-    81  ALL_ALL_RED_DIRECT_CLOSE_losers_all_re
-    50  BREAK_EVEN_GUARD_EXIT
-    46  DC_BREACH_REDUCE_UNHEDGED_LOW_15m_pric
-    42  ALL_ALL_GREEN_DIRECT_OPEN_winners_all_
-    31  ALL_ALL_RED_DIRECT_OPEN_losers_all_red
-    14  RATIO_REDUCE_LONG_L100_S0_tgt75/25_bre
-     9  DC_BASIS_3M_REDUCE: price_below_dc_bas
-     7  MTF_BB_REJECT_1h
-     5  RATIO_REDUCE_LONG_L100_S0_tgt30/70_bre
-     4  WT_LOWER_CROSS_EXIT_wt_1h_lower_wt+pri
-     3  PPL_TP_gain2.04_URL2_50pct
-     3  [HANDLE_SIGNAL]:BEARISH_dc_basis_cross
-     2  HARDCODED_RALLY_REENTRY_LONG_close0.38
-     1  CRYPTO_SPIKE_FADE_LONG_ret=-11.5%
-     1  CRYPTO_SPIKE_FADE_SHORT_ret=+10.2%
-     1  RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain
-     1  HAIKU_WINNER_AUG_3.0pct
-     1  HAIKU_WINNER_AUG_3.3pct

## actions in window: {'CLOSE': 4866, 'OPEN': 1069, 'REDUCE': 35, 'AUGMENT': 13, 'QUICK_REDUCE': 4}
